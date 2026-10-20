"""CL04 component snapshots on real backends; full source erasure is PV-06."""

from datetime import timedelta
import json
from uuid import uuid4

import pytest

from prme.privacy.journals import SnapshotEnvelope
from prme.types import LifecycleState
from tests.privacy_journal_support import (
    LAYOUTS,
    MEMORY,
    SOURCE,
    SOURCE_ONLY,
    journal_engine,
    isolated_privacy_postgres,  # noqa: F401
    local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL04_component_actual_lifecycle_snapshots(
    tmp_path, layout, analyzer, native
):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        event_id = await engine.store(SOURCE, retrieval_content=MEMORY, user_id=owner)
        node = (await engine.get_event_nodes(event_id, user_id=owner))[0]
        node_id = str(node.id)
        # A separately classified legacy-shaped metadata leaf must not become
        # a snapshot through one of these mutation siblings.
        raw = await engine._graph_store.get_node(node_id)
        metadata = {**raw.metadata, "evidence_quote": SOURCE_ONLY}
        await engine._privacy.repository._query(
            "UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(metadata), node_id)
        )
        request = str(uuid4())
        await engine.promote(node_id, user_id=owner, request_id=request)
        await engine.promote(node_id, user_id=owner, request_id=request)
        await engine.reinforce(
            node_id, user_id=owner, evidence_id=event_id, request_id=str(uuid4())
        )
        assert (
            await engine.get_node(node_id, user_id=owner)
        ).lifecycle_state == LifecycleState.STABLE
        await engine.archive(node_id, user_id=owner)
        rows = await engine._privacy.repository._query(
            "SELECT payload FROM operations WHERE op_type IN ('LIFECYCLE_CHANGED','REINFORCE')",
            (),
        )
        assert len(rows) == 3
        for row in rows:
            payload = row["payload"] if isinstance(row, dict) else row[0]
            envelope = SnapshotEnvelope.model_validate_json(
                payload if isinstance(payload, str) else json.dumps(payload)
            )
            assert (
                envelope.manifest.dependencies
                and node.id in envelope.manifest.admitted_node_ids
            )
            assert MEMORY in envelope.record and SOURCE_ONLY not in envelope.record
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL04_component_ttl_tombstone_contains_identity_only(
    tmp_path, layout, analyzer, native
):
    from prme.storage.retention import ProtectedRetentionRecord, read_record

    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        event_id = await engine.store(
            SOURCE, retrieval_content=MEMORY, user_id=owner, ttl_days=1
        )
        node = (await engine.get_event_nodes(event_id, user_id=owner))[0]
        assert await engine._graph_store.archive_expired(
            str(node.id),
            user_id=owner,
            evaluated_at=node.created_at + timedelta(days=2),
        )
        rows = await engine._privacy.repository._query(
            "SELECT payload FROM operations WHERE op_type='TOMBSTONE_SWEEP'", ()
        )
        assert len(rows) == 1
        payload = rows[0]["payload"] if isinstance(rows[0], dict) else rows[0][0]
        record = read_record(payload)
        assert isinstance(record, ProtectedRetentionRecord)
        assert record.before.id == node.id and record.original_replay == "unavailable"
        assert (
            MEMORY not in record.model_dump_json()
            and SOURCE_ONLY not in record.model_dump_json()
        )
        assert "content_hash_of_deleted" not in record.model_dump_json()
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL05_component_profile_admitted_entity_and_keyless_recovery(
    tmp_path, layout, analyzer, native, monkeypatch
):
    from prme.types import Scope, NodeType
    from prme.privacy.journals import JOURNAL_KEY
    from prme.privacy.policy import PrivacyDenied
    from tests.privacy_journal_support import operation_bytes, processing_keys

    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer
    )
    owner = str(policy.owner_id)
    try:
        event_id = await engine.ingest(SOURCE, user_id=owner)
        with processing_keys(runtime, custody, processing):
            assert (await engine.process_extractions(user_id=owner)).processed == 1
        nodes = await engine.get_event_nodes(event_id, user_id=owner)
        entity = next(node for node in nodes if node.node_type == NodeType.ENTITY)
        fact = next(node for node in nodes if node.node_type == NodeType.FACT)
        assert entity.content == "Mara" and MEMORY in fact.content
        raw = await engine._graph_store.get_node(str(fact.id))
        await engine._privacy.repository._query(
            "UPDATE nodes SET metadata=? WHERE id=?",
            (json.dumps({**raw.metadata, "evidence_quote": SOURCE_ONLY}), str(fact.id)),
        )
        original_publish = engine._publish_prepared_profile

        async def fail_after_prepare(plan):
            raise RuntimeError("test interruption after immutable preparation")

        monkeypatch.setattr(engine, "_publish_prepared_profile", fail_after_prepare)
        with pytest.raises(RuntimeError, match="test interruption"):
            await engine.consolidate_knowledge(
                user_id=owner, scope=Scope.PERSONAL, entity_names=["Mara"]
            )
        jobs = await engine.profile_jobs(user_id=owner)
        assert len(jobs) == 1 and jobs[0]["status"] == "pending"
        plan = await engine._profile_work.get(jobs[0]["plan_id"], user_id=owner)
        assert plan.schema_version == 2 and plan.privacy_manifest is not None
        assert plan.node.metadata[JOURNAL_KEY]["source_node_ids"]
        assert MEMORY in plan.node.content and SOURCE_ONLY not in plan.model_dump_json()
        assert {node.id for node in plan.sources} == {entity.id, fact.id}
        sends = len(runtime.embedder.sends), len(runtime.extractor.sends)
        monkeypatch.setattr(engine, "_publish_prepared_profile", original_publish)
        # Evidence and processing private keys remain locked in this harness.
        assert await engine.resume_profile(str(plan.node.id), user_id=owner) == str(
            plan.node.id
        )
        assert await engine.resume_profile(str(plan.node.id), user_id=owner) == str(
            plan.node.id
        )
        result = await engine.get_node(str(plan.node.id), user_id=owner)
        assert result.metadata["entity_profile"] and MEMORY in result.content
        assert sends == (len(runtime.embedder.sends), len(runtime.extractor.sends))
        assert (await engine.profile_jobs(user_id=owner, status="complete"))[0][
            "plan_id"
        ] == str(plan.node.id)
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
        await engine.archive(str(plan.node.id), user_id=owner)
        await engine.resume_profile(str(plan.node.id), user_id=owner)
        assert (
            await engine._graph_store.get_node(
                str(plan.node.id), include_superseded=True
            )
        ).lifecycle_state == LifecycleState.ARCHIVED
        with pytest.raises(PrivacyDenied):
            await engine.consolidate_knowledge(
                user_id=owner, scope=Scope.PERSONAL, entity_names=["Unknown"]
            )
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL05_component_summary_generations_and_retirement(
    tmp_path, layout, analyzer, native, monkeypatch
):
    from prme.organizer import summarization
    from prme.organizer.summarization import SummarizationLevel
    from prme.organizer.consolidation import consolidate_cluster, MemoryCluster
    from prme.models.consolidation import ConsolidationPublication
    from tests.privacy_journal_support import operation_bytes

    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        nodes = []
        for text in (MEMORY, "Mara grows thyme."):
            event_id = await engine.store(
                f"{text} {SOURCE_ONLY}.", retrieval_content=text, user_id=owner
            )
            nodes.extend(await engine.get_event_nodes(event_id, user_id=owner))
        original_publish = summarization.publish_consolidation_plan

        async def interrupt(plan_engine, plan, **kwargs):
            raise RuntimeError("test summary interrupted after preparation")

        monkeypatch.setattr(summarization, "publish_consolidation_plan", interrupt)
        with pytest.raises(RuntimeError, match="summary interrupted"):
            await summarization._materialize_summary_node(
                engine, SummarizationLevel.DAILY, "2026-10-20", nodes, owner
            )
        saved = await engine._privacy.repository._query(
            "SELECT payload FROM operations WHERE op_type='CONSOLIDATION_PREPARED'", ()
        )
        assert len(saved) == 1
        value = saved[0]["payload"] if isinstance(saved[0], dict) else saved[0][0]
        body = json.loads(value) if isinstance(value, str) else value
        plan = ConsolidationPublication.model_validate_json(body["publication"])
        assert plan.schema_version == 2 and len(plan.privacy_manifest.dependencies) == 2
        assert SOURCE_ONLY not in plan.model_dump_json() and MEMORY in plan.node.content
        sends = len(runtime.embedder.sends)
        monkeypatch.setattr(
            summarization, "publish_consolidation_plan", original_publish
        )
        daily, _ = await summarization._materialize_summary_node(
            engine, SummarizationLevel.DAILY, "2026-10-20", nodes, owner
        )
        assert daily.id == plan.node.id and len(runtime.embedder.sends) == sends
        assert (
            await summarization._materialize_summary_node(
                engine, SummarizationLevel.DAILY, "2026-10-20", nodes, owner
            )
        )[1] is False
        weekly, _ = await summarization._materialize_summary_node(
            engine, SummarizationLevel.WEEKLY, "2026-W43", [daily], owner
        )
        monthly, _ = await summarization._materialize_summary_node(
            engine, SummarizationLevel.MONTHLY, "2026-10", [weekly], owner
        )
        assert MEMORY in monthly.content and "Mara grows thyme." in monthly.content
        cluster = MemoryCluster(
            str(nodes[0].id), [str(node.id) for node in nodes], 1.0, MEMORY
        )
        consolidated = await consolidate_cluster(engine, cluster, user_id=owner)
        assert MEMORY in consolidated.content
        assert "consolidation_content_sha256" not in consolidated.metadata
        assert "consolidation_coverage" not in consolidated.metadata
        assert await engine._graph_store.retire_consolidated(
            str(nodes[0].id),
            str(consolidated.id),
            user_id=owner,
            at=nodes[0].created_at + timedelta(days=1),
            preserve_recent_days=0,
            min_confidence_preserve=1.0,
        )
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
        assert (
            await engine._graph_store.get_node(
                str(nodes[0].id), include_superseded=True
            )
        ).lifecycle_state == LifecycleState.SUPERSEDED
    finally:
        await engine.close()
