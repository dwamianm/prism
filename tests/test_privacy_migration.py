"""EM07/AC2 real migration components; full public parity remains held on #323."""
from dataclasses import replace
from datetime import datetime
import asyncio
import difflib
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy.erasure import ErasureRequest
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import PrivacyPortabilityError
from prme.privacy.retry_commitments import NativeRetryCommitter
from prme.storage.privacy_migration import inspect_privacy_migration, migrate_privacy_pack, migration_status, _read_snapshot
from tests.privacy_portability_support import fresh_config
from tests.privacy_journal_support import journal_engine, isolated_privacy_postgres, local_analyzer_fixture  # noqa: F401
from tests.privacy_journal_support import native as native_fixture  # noqa: F401

FIXTURE = json.loads((Path(__file__).parent / "fixtures/privacy/migration-v1.json").read_text())


def hashes(path):
    return {str(file.relative_to(path)): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in path.rglob("*") if file.is_file()}


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
async def test_AC1_dry_run_reports_unsupported_legacy_sessions(tmp_path, layout, analyzer, native):
    template, runtime, policy, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    supported = (None, str(uuid4()), uuid4().hex.upper())
    named = "er08-langchain"
    feature = "legacy_non_uuid_sessions_unsupported"
    reports, expected_sessions = [], {}
    async with fresh_config(base, tmp_path / "legacy", layout) as original:
        original = original.model_copy(update={"privacy_policy": None})
        for sessions in (supported, (named,)):
            source = await MemoryEngine.create(original, embedding_provider=runtime.embedder)
            try:
                for session in sessions:
                    identifier = await source.store(
                        FIXTURE["admitted"][0] + " " + FIXTURE["source_only"],
                        user_id=str(policy.owner_id), session_id=session,
                    )
                    expected_sessions[identifier] = session
            finally:
                await source.close()
            before = await _read_snapshot(original)
            assert {str(row["id"]): row["session_id"] for row in before.tables["events"]} == expected_sessions
            if layout == "pack":
                source_files = {original.pack_path: hashlib.sha256(Path(original.pack_path).read_bytes()).hexdigest()}
            elif layout == "directory":
                source_files = hashes(tmp_path / "legacy")
            else:
                source_files = None
            report = await inspect_privacy_migration(original, policy=policy)
            assert report.source_format == "legacy" and not report.protected and report.legacy_plaintext_present
            assert report.source_events == report.admitted_nodes == len(expected_sessions)
            rendered = report.model_dump_json()
            assert all(value not in rendered for value in (*supported[1:], named, FIXTURE["admitted"][0], FIXTURE["source_only"]))
            after = await _read_snapshot(original)
            assert after.fingerprint == before.fingerprint
            assert "privacy_format" not in after.tables
            if layout == "pack":
                assert {original.pack_path: hashlib.sha256(Path(original.pack_path).read_bytes()).hexdigest()} == source_files
            elif layout == "directory":
                assert hashes(tmp_path / "legacy") == source_files
            if not reports:
                assert feature not in report.unavailable_features
            else:
                assert report.unavailable_features == (*reports[0]["unavailable_features"], feature)
            reports.append(report.model_dump(mode="json"))
        (tmp_path / "AC1-session-report.json").write_text(json.dumps(reports, indent=2) + "\n")


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres", "closed_fernet"])
async def test_EM07_direct_migration_resume_exact_identity_current_erasure(tmp_path, layout, analyzer, native):
    template, runtime, policy, custody, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    owner = str(policy.owner_id)
    local_layout = "directory" if layout == "closed_fernet" else layout
    async with fresh_config(base, tmp_path / "legacy", local_layout) as original, fresh_config(base, tmp_path / "migrated", local_layout, restore=True) as target:
        original = original.model_copy(update={"privacy_policy": None})
        if layout == "closed_fernet":
            original = type(original).model_validate({**original.model_dump(), "encryption_enabled": True, "encryption_key": "passphrase:task-owned-migration-source"})
        source = await MemoryEngine.create(original, embedding_provider=runtime.embedder)
        try:
            event_ids = []
            for memory in FIXTURE["admitted"]:
                event_ids.append(await source.store(memory + " " + FIXTURE["source_only"] + " " + FIXTURE["prohibited"],
                    retrieval_content=memory, user_id=owner))
            if layout == "closed_fernet":
                source._conn.execute("DELETE FROM vector_payloads")
        finally:
            await source.close()
        before = await _read_snapshot(original)
        source_files = hashes(tmp_path / "legacy") if local_layout == "directory" else None
        plan = await inspect_privacy_migration(original, policy=policy)
        assert not plan.protected and plan.legacy_plaintext_present
        assert [row.copy_id for row in plan.copies] == [f"C{number:02}" for number in range(1, 46)]
        assert "retry_commitment" in plan.required_key_purposes
        assert "legacy_history_requires_explicit_disposition" in plan.unavailable_features
        assert FIXTURE["source_only"] not in plan.model_dump_json() and FIXTURE["prohibited"] not in plan.model_dump_json()
        assert (await _read_snapshot(original)).fingerprint == before.fingerprint
        if source_files is not None:
            assert hashes(tmp_path / "legacy") == source_files
        with pytest.raises(PrivacyPortabilityError, match="^privacy_legacy_unclassified$"):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=tmp_path / "refused")
        assert not (tmp_path / "refused").exists()
        saved = {}
        async def interrupt(stage, index, engine):
            if stage == "after_batch" and index == 1:
                records = await engine._privacy.repository._read("1=1", ())
                records = sorted(records, key=lambda record: str(record.event_id))
                saved["erased"], saved["survives"] = records
                await engine._privacy.erasure.request(ErasureRequest(request_id=uuid4(), event_ids=(records[0].event_id,)), user_id=owner)
                raise RuntimeError("synthetic migration interruption")
        job_path = tmp_path / "migration-job"
        with pytest.raises(RuntimeError, match="synthetic migration interruption"):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=job_path, unavailable_legacy_history=True, _fault=interrupt)
        partial = migration_status(job_path)
        assert partial.state == "interrupted" and partial.completed == 2 and partial.total == 3
        result = await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
            job_path=job_path, unavailable_legacy_history=True, resume=True)
        assert result.job_id == partial.job_id and result.state == "complete"
        assert (await _read_snapshot(original)).fingerprint == before.fingerprint
        if source_files is not None:
            assert hashes(tmp_path / "legacy") == source_files
        restored = await MemoryEngine.create(target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        try:
            record = await restored._privacy.repository.get(str(saved["survives"].event_id), user_id=owner)
            assert record == saved["survives"]
            assert record.evidence.context.event_id in set(map(UUID, event_ids))
            assert record.snapshot == saved["erased"].snapshot
            rows = await restored._privacy.repository._query("SELECT id,content,evidence_refs FROM nodes", ())
            assert len(rows) == 2
            assert all(str(saved["erased"].event_id) not in str(row[2]) for row in rows)
            original_node_ids = {str(row["id"]) for row in before.tables["nodes"]}
            assert all(str(row[0]) in original_node_ids and row[1] in FIXTURE["admitted"] for row in rows)
            payload = (job_path / "protected-export.json").read_bytes()
            assert FIXTURE["source_only"].encode() not in payload and FIXTURE["prohibited"].encode() not in payload
            assert b'"payload_status":"unavailable"' in payload
            assert b"BEGIN PRIVATE KEY" not in payload
        finally:
            await restored.close()
        # Resume authentication rejects edits to the saved lifetime even when
        # the edited JSON is structurally valid; no new retry key is provisioned.
        import duckdb
        from datetime import timedelta
        from prme.privacy.policy import snapshot
        with duckdb.connect(str(job_path / "migration.duckdb")) as conn:
            body, authentication = map(json.loads, conn.execute("SELECT body,authentication FROM privacy_migration_job").fetchone())
            changed = snapshot(policy, None, saved["survives"].snapshot.admitted_at - timedelta(seconds=1)).model_dump(mode="json")
            body["snapshot"] = changed
            authentication["context"]["snapshot"] = changed
            conn.execute("UPDATE privacy_migration_job SET body=?,authentication=?", [json.dumps(body), json.dumps(authentication)])
        with pytest.raises((PrivacyDenied, PrivacyPortabilityError)):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=job_path, unavailable_legacy_history=True, resume=True)


async def mixed_legacy_family(engine, runtime, owner, *, source_contents=None):
    """One frozen EM07 family through real saved-plan/publication protocols.

    Only numerical model output is synthetic. Native event, plan, graph, vector,
    profile and consolidation writers persist the historical source controls.
    No protected ordinary-read classifier or general importer is used.
    """
    from datetime import datetime
    from uuid import uuid5
    from prme.models import Event, MemoryNode, MemoryEdge
    from prme.models.derivation import DerivationPlan, PreparedEmbedding, PreparedLexicalDocument
    from prme.models.extraction import ExtractionRecord
    from prme.models.profile import ProfilePublication
    from prme.models.consolidation import ConsolidationPublication, consolidation_key, consolidation_request_hash
    from prme.storage.derivation_staging import DuckDBStageFence
    from prme.organizer.consolidation import _stage_consolidation
    from prme.storage.graph_rows import NODE_COLUMNS as _NODE_COLUMNS, record_to_node
    from prme.types import NodeType, EpistemicType, SourceType, EdgeType, Scope
    stamp = datetime.fromisoformat(FIXTURE["clock"])
    scope = Scope.PERSONAL
    if source_contents is None:
        source_contents = (
            FIXTURE["admitted"][0] + " " + FIXTURE["source_only"] + " " + FIXTURE["prohibited"],
            FIXTURE["admitted"][1] + " " + FIXTURE["source_only"] + " " + FIXTURE["prohibited"],
            FIXTURE["admitted"][2] + " " + FIXTURE["source_only"],
        )
    else:
        # The standard matched-input control declares this projection before
        # persistence. It does not alter the original mixed-family defaults.
        assert source_contents == tuple(FIXTURE["admitted"])
    event_id = await engine.store(source_contents[0],
        retrieval_content=FIXTURE["admitted"][0], user_id=owner, event_time=stamp)
    async with engine._privacy.erasure.repository.session() as session:
        direct = record_to_node((await session.fetch(f"SELECT {_NODE_COLUMNS} FROM nodes"))[0])
    event = Event(role="user", content=source_contents[1],
        user_id=owner, scope=scope, timestamp=stamp, created_at=stamp, event_time=stamp)
    await engine._event_store.append(event)
    fact = MemoryNode(node_type=NodeType.FACT, content=event.content, user_id=owner, scope=scope,
        evidence_refs=[event.id, UUID(event_id)], metadata={"subject": "Mara", "predicate": "likes", "object": "oranges", "polarity": "positive",
            "supporting_passage": event.content, "extraction_kind": "fact"}, event_time=stamp, valid_from=stamp, created_at=stamp, updated_at=stamp)
    entity = MemoryNode(node_type=NodeType.ENTITY, content="Mara", user_id=owner, scope=scope,
        evidence_refs=[event.id], metadata={"entity_type": "person"}, event_time=stamp, valid_from=stamp, created_at=stamp, updated_at=stamp)
    async def embedding(node):
        values = (await runtime.embedder.embed([node.content]))[0]
        return PreparedEmbedding(node_id=node.id, content=node.content, model=runtime.embedder.model_name,
            version=runtime.embedder.model_version, dimension=runtime.embedder.dimension, values=values)
    extraction = ExtractionRecord(event_id=event.id, user_id=owner, scope=scope, content_hash=event.content_hash,
        provider="fixture", model="saved-external-result", created_at=stamp,
        result={"entities": [{"name": "Mara", "entity_type": "person"}], "facts": [{"subject": "Mara", "predicate": "likes", "object": "oranges", "evidence_quote": event.content}], "relationships": [], "summary": None})
    await engine._event_store.record_extraction(extraction)
    plan = DerivationPlan(event_id=event.id, user_id=owner, scope=scope, content_hash=event.content_hash,
        created_at=stamp, nodes=(fact, entity), embeddings=tuple([await embedding(node) for node in (fact, entity)]),
        lexical_documents=tuple(PreparedLexicalDocument(node_id=node.id, content=node.content) for node in (fact, entity)))
    await engine._event_store.record_derivation_plan(plan)
    if engine._pool is None:
        fence = DuckDBStageFence(engine._conn, engine._event_store._conn_lock, plan, None)
        for value in plan.embeddings:
            await engine._vector_index.stage(value, user_id=owner, fence=fence)
        await engine._lexical_index.stage(plan, fence=fence)
    await engine._graph_store.commit_derivation(plan)
    generated = []
    previous = ()
    for generation in range(2):
        node = MemoryNode(node_type=NodeType.SUMMARY, content="Mara: " + " ".join(FIXTURE["admitted"][:2]),
            user_id=owner, scope=scope, evidence_refs=[UUID(event_id), event.id],
            epistemic_type=EpistemicType.INFERRED, source_type=SourceType.SYSTEM_INFERRED,
            metadata={"entity_profile": True, "entity_name": "Mara", "source_node_ids": [str(direct.id), str(fact.id)],
                "source_count_available": 2, "source_count_included": 2, "tokens_used": 16, "profile_format_version": 2},
            event_time=stamp, valid_from=stamp, created_at=stamp, updated_at=stamp)
        profile = ProfilePublication(node=node, sources=(direct, fact), previous=previous,
            embedding=await embedding(node), generation=generation)
        await engine._profile_work.prepare(profile)
        await engine._publish_prepared_profile(profile)
        generated.append((node, "profile", profile.sources))
        previous = (node,)
    sources = tuple(sorted((direct, fact), key=lambda node: str(node.id)))
    key = consolidation_key(owner, scope, [node.id for node in sources])
    previous = ()
    for generation in range(2):
        content = " ".join(FIXTURE["admitted"][:2])
        selected = [str(node.id) for node in sources]
        digest = consolidation_request_hash(sources, content=content, confidence=0.5, salience=0.5,
            selected_ids=selected, embedding_identity=(runtime.embedder.model_name, runtime.embedder.model_version, runtime.embedder.dimension))
        node = MemoryNode(id=uuid5(UUID(key), f"{digest}:{generation}"), node_type=NodeType.SUMMARY, content=content,
            user_id=owner, scope=scope, evidence_refs=sorted({ref for dep in sources for ref in (dep.id, *dep.evidence_refs)}, key=str),
            epistemic_type=EpistemicType.INFERRED, source_type=SourceType.SYSTEM_INFERRED,
            metadata={"consolidation_summary": True, "consolidation_key": key, "consolidation_request_hash": digest,
                "consolidation_generation": generation, "source_node_ids": selected, "selected_source_node_ids": selected,
                "cluster_size": 2, "consolidation_format_version": 2}, event_time=stamp, valid_from=stamp, created_at=stamp, updated_at=stamp)
        edges = tuple(MemoryEdge(source_id=node.id, target_id=dep.id, edge_type=EdgeType.DERIVED_FROM, user_id=owner) for dep in sources)
        summary = ConsolidationPublication(node=node, sources=sources, previous=previous, edges=edges,
            embedding=await embedding(node), generation=generation, request_hash=digest)
        await engine._graph_store.prepare_consolidation(summary)
        if engine._pool is None:
            await _stage_consolidation(engine, summary)
        await engine._graph_store.publish_consolidation(summary)
        generated.append((node, "summary", summary.sources))
        previous = (node,)
    # Historical snapshot-only content and a checksum-bearing receipt remain
    # intact at source; the target receives only their typed unavailable IDs.
    from prme.storage.lifecycle import LifecycleRecord, _payload, read_record
    from prme.types import LifecycleState
    old = fact.model_copy(update={"id": uuid4()})
    lifecycle = LifecycleRecord(operation_id=uuid4(), action="promote", before=old,
        after=old.model_copy(update={"lifecycle_state": LifecycleState.STABLE}))
    lifecycle_payload = _payload(lifecycle)
    assert read_record(lifecycle_payload) == lifecycle
    from prme.models.relevance import RetrievalReceipt
    from prme.retrieval.config import ScoringWeights, PackingConfig
    receipt = RetrievalReceipt(request_id=uuid4(), user_id=owner, query=FIXTURE["source_only"], reference_time=stamp,
        scopes=(scope,), scoring=ScoringWeights(fusion="weighted", current_update_multiplier=1.0, recency_time=None),
        packing=PackingConfig(context_format="auditable", multipath_ordering="density", context_guidance_mode="off",
            context_citations=False, episode_context_top_k=0, evidence_projection_top_k=0, evidence_augmentation_top_k=0,
            evidence_augmentation_anchor_policy="all", session_context_packing=None, fold_repeated_text=False),
        context_sha256=hashlib.sha256(event.content.encode()).hexdigest(), candidates=())
    receipt_payload = json.dumps({"receipt": receipt.model_dump_json(), "receipt_checksum": receipt.checksum})
    from prme.storage.relevance import RelevanceRepository
    assert RelevanceRepository._read_receipt(receipt_payload, request_id=str(receipt.request_id), user_id=owner) == receipt
    for identifier, kind, target, payload in ((lifecycle.operation_id, "PROMOTE", old.id, lifecycle_payload),
            (uuid4(), "RETRIEVAL_REQUEST", receipt.request_id, receipt_payload)):
        await engine._privacy.repository._query("INSERT INTO operations(id,op_type,target_id,payload,actor_id,namespace_id,created_at) VALUES(?,?,?,?,?,?,?) RETURNING id",
            (str(identifier), kind, str(target), payload, owner, scope.value, stamp))
    visual = None
    if engine._pool is None:
        from prme.storage.blobs import put_blob, thumbnail_key
        raw = (FIXTURE["source_only"] + FIXTURE["prohibited"]).encode()
        digest = hashlib.sha256(raw).hexdigest()
        visual = Event(role="user", content=FIXTURE["admitted"][2], user_id=owner, scope=scope, timestamp=stamp,
            created_at=stamp, event_time=stamp, metadata={"frame_sha256": digest, "thumbnail": thumbnail_key(digest)})
        await engine._event_store.append(visual)
        put_blob(engine._conn, thumbnail_key(digest), "image/jpeg", raw)
    survivor_event = await engine.store(source_contents[2],
        retrieval_content=FIXTURE["admitted"][2], user_id=owner, event_time=stamp)
    async with engine._privacy.erasure.repository.session() as session:
        survivor = next(node for node in map(record_to_node, await session.fetch(f"SELECT {_NODE_COLUMNS} FROM nodes"))
            if UUID(survivor_event) in node.evidence_refs)
    return direct, fact, entity, generated, visual, survivor


async def test_EM07_fixed_policy_directory_to_encrypted_pack_parity(
    tmp_path, analyzer, native
):
    """Compare standard S across the real directory-to-encrypted-pack edge."""
    from datetime import timedelta

    from prme.privacy.local_keys import public_registry
    from prme.privacy.portability import ExportEvent
    from prme.storage.privacy_export import (
        export_protected_pack,
        read_protected_export,
        restore_protected_export,
    )

    template, runtime, policy, custody, evidence, _ = await journal_engine(
        tmp_path / "template", "directory", analyzer, evidence_use="runtime"
    )
    base = template._config.model_copy(
        update={
            "organizer": template._config.organizer.model_copy(
                update={"opportunistic_enabled": False}
            ),
            "packing": template._config.packing.model_copy(
                update={"evidence_projection_top_k": 3}
            ),
        }
    )
    await template.close()
    runtime.clock.now = datetime.fromisoformat(FIXTURE["clock"])
    runtime = replace(
        runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock)
    )
    owner = str(policy.owner_id)
    physical_fields = {
        "db_path",
        "vector_path",
        "lexical_path",
        "pack_path",
        "encryption_enabled",
        "encryption_key",
    }

    def write(name, value):
        (tmp_path / name).write_text(json.dumps(value, indent=2) + "\n")

    async with (
        fresh_config(base, tmp_path / "legacy", "directory") as original,
        fresh_config(base, tmp_path / "standard", "directory") as standard,
        fresh_config(base, tmp_path / "encrypted", "pack") as encrypted,
    ):
        original = original.model_copy(update={"privacy_policy": None})
        encrypted = type(encrypted).model_validate(
            {
                **encrypted.model_dump(),
                "encryption_enabled": True,
                "encryption_key": "passphrase:task-owned-fixed-policy-326",
            }
        )
        assert standard.model_dump(exclude=physical_fields) == encrypted.model_dump(
            exclude=physical_fields
        )
        async with MemoryEngine.open(
            original, embedding_provider=runtime.embedder
        ) as source:
            (
                direct,
                fact,
                entity,
                generated,
                visual,
                survivor,
            ) = await mixed_legacy_family(source, runtime, owner)
            node_ids = {
                str(node.id)
                for node in (
                    direct,
                    fact,
                    entity,
                    *(item[0] for item in generated),
                    survivor,
                )
            }
            source_ids = {
                direct.evidence_refs[0],
                entity.evidence_refs[0],
                survivor.evidence_refs[0],
            }
            assert len(node_ids) == 8 and len(source_ids) == 3
        original_snapshot = await _read_snapshot(original)
        job = tmp_path / "standard-seed-job"
        result = await migrate_privacy_pack(
            original,
            standard,
            privacy_runtime=runtime,
            embedding_provider=runtime.embedder,
            job_path=job,
            unavailable_legacy_history=True,
            remove_event_ids=() if visual is None else (visual.id,),
            allow_reembedding=True,
        )
        assert result.state == "complete"
        assert (
            await _read_snapshot(original)
        ).fingerprint == original_snapshot.fingerprint

        async def semantic_state(memory):
            records = await memory._privacy.repository._read("1=1", ())
            nodes = await memory._graph_store.get_nodes(
                sorted(node_ids), include_superseded=True
            )
            async with memory._privacy.erasure.repository.session() as session:
                events = await session.fetch(
                    "SELECT id,timestamp,role,user_id,session_id,scope,created_at,event_time "
                    "FROM events ORDER BY id"
                )
            _, head = await memory._privacy.erasure.repository.state()
            return {
                "admissions": {
                    str(record.event_id): record.model_dump(mode="json")
                    for record in records
                },
                "nodes": {str(node.id): node.model_dump(mode="json") for node in nodes},
                "events": {
                    str(event.id): event.model_dump(mode="json")
                    for event in (ExportEvent.model_validate(row) for row in events)
                },
                "ledger": head.model_dump(mode="json"),
                "config": memory._config.model_dump(
                    mode="json", exclude=physical_fields
                ),
            }

        async def capture(memory, label, access):
            saved = {}
            for query, required in zip(
                FIXTURE["queries"], FIXTURE["admitted"], strict=True
            ):
                response = await asyncio.wait_for(
                    memory.retrieve(
                        query,
                        user_id=owner,
                        scope=policy.scope,
                        reference_time=runtime.clock(),
                        include_cross_scope=False,
                        source_access=access,
                    ),
                    20,
                )
                saved[query] = {
                    "result_ids": [str(item.node.id) for item in response.results],
                    "packed_sections": [
                        [name, [str(item.node.id) for item in section]]
                        for name, section in response.bundle.sections.items()
                    ],
                    "context": response.bundle.render(),
                    "response": response.model_dump(mode="json"),
                }
                write(f"fixed-policy-{label}-retrieval.json", saved)
                assert response.results and response.bundle.included_count
                assert required in response.bundle.render()
                assert not response.metadata.backend_failures
                assert not response.metadata.embedding_mismatch
            return saved

        artifact = tmp_path / "standard-export.json"
        with custody.unlocked(
            (evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)
        ) as private:
            runtime.protector.registry = public_registry(
                custody.path, private_wrapper=private
            )
            try:
                async with MemoryEngine.open(
                    standard,
                    privacy_runtime=runtime,
                    embedding_provider=runtime.embedder,
                ) as source:
                    records = await source._privacy.repository._read("1=1", ())
                    assert {record.event_id for record in records} == source_ids
                    assert all(record.snapshot.policy == policy for record in records)
                    assert all(
                        record.snapshot.evidence_deadline > runtime.clock()
                        for record in records
                    )
                    access = await runtime.protector.authority.grant(
                        "runtime",
                        tuple(
                            (record.evidence.context, record.snapshot)
                            for record in records
                        ),
                        expires_at=runtime.clock() + timedelta(minutes=1),
                    )
                    frozen = await semantic_state(source)
                    assert set(frozen["nodes"]) == node_ids
                    assert set(frozen["events"]) == set(frozen["admissions"])
                    frozen_grant = access.model_dump(mode="json")
                    write(
                        "fixed-policy-frozen-inputs.json",
                        {
                            "fixture_sha256": hashlib.sha256(
                                (
                                    Path(__file__).parent
                                    / "fixtures/privacy/migration-v1.json"
                                ).read_bytes()
                            ).hexdigest(),
                            "queries": FIXTURE["queries"],
                            "clock": runtime.clock().isoformat(),
                            "source_state": frozen,
                            "grant": frozen_grant,
                            "source_config": standard.model_dump(mode="json"),
                            "target_config": encrypted.model_dump(mode="json"),
                        },
                    )
                    before = await capture(source, "before", access)
                    source_after = await semantic_state(source)
                    write("fixed-policy-source-after.json", source_after)
                    assert source_after == frozen
                    assert access.model_dump(mode="json") == frozen_grant
                    await export_protected_pack(source, artifact)
                    assert await semantic_state(source) == frozen

                exported = read_protected_export(artifact)
                assert {
                    str(record.event_id): record.model_dump(mode="json")
                    for record in exported.admissions
                } == frozen["admissions"]
                assert {
                    str(node.id): node.model_dump(mode="json")
                    for node in exported.nodes
                } == frozen["nodes"]
                assert {
                    str(event.id): event.model_dump(mode="json")
                    for event in exported.events
                } == frozen["events"]
                assert (
                    exported.manifest.ledger_checkpoint.model_dump(mode="json")
                    == frozen["ledger"]
                )
                restored = await restore_protected_export(
                    artifact,
                    encrypted,
                    privacy_runtime=runtime,
                    embedding_provider=runtime.embedder,
                )
                assert restored["state"] == "complete"
                assert Path(encrypted.pack_path).is_file()
                async with MemoryEngine.open(
                    encrypted,
                    privacy_runtime=runtime,
                    embedding_provider=runtime.embedder,
                ) as target:
                    target_before = await semantic_state(target)
                    write("fixed-policy-target-before.json", target_before)
                    assert target_before == frozen
                    assert (
                        target._config.encryption_enabled and target._config.pack_path
                    )
                    assert access.model_dump(mode="json") == frozen_grant
                    after = await capture(target, "after", access)
                    target_after = await semantic_state(target)
                    write("fixed-policy-target-after.json", target_after)
                    assert target_after == frozen
                    assert access.model_dump(mode="json") == frozen_grant
            finally:
                runtime.protector.registry = public_registry(custody.path)

        differences = {
            query: {
                "before_result_ids": before[query]["result_ids"],
                "after_result_ids": after[query]["result_ids"],
                "before_packed_sections": before[query]["packed_sections"],
                "after_packed_sections": after[query]["packed_sections"],
                "context_diff": "".join(
                    difflib.unified_diff(
                        before[query]["context"].splitlines(True),
                        after[query]["context"].splitlines(True),
                        fromfile="standard-directory",
                        tofile="standard-encrypted-pack",
                    )
                ),
            }
            for query in FIXTURE["queries"]
        }
        write("fixed-policy-differences.json", differences)
        for query in FIXTURE["queries"]:
            assert after[query]["result_ids"] == before[query]["result_ids"], query
            assert (
                after[query]["packed_sections"] == before[query]["packed_sections"]
            ), query
            assert after[query]["context"].encode("utf-8") == before[query][
                "context"
            ].encode("utf-8"), query


@pytest.mark.xfail(strict=True, reason=(
    "Known gap moved to #327 by the owner on 2026-10-28. Without a source grant, retrieval differs "
    "only where the legacy side folds a consolidated excerpt into the fact it repeats and the protected "
    "side does not; with the runtime grant, standard mode substitutes sanitized source passages by "
    "design. The assertions are unchanged; #327 owns the 24-query parity gate."))
@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
async def test_EM07_standard_matched_input_migration_parity(tmp_path, layout, analyzer, native):
    """Direct conversion control; legacy has no literal saved privacy policy."""
    from datetime import timedelta
    from prme.privacy.local_keys import public_registry
    from prme.storage.privacy_export import read_protected_export

    template, runtime, policy, custody, evidence, _ = await journal_engine(
        tmp_path / "template", "directory", analyzer, evidence_use="runtime")
    # Opportunistic maintenance runs during retrieve. On the legacy side it
    # promoted tentative memories after the "before" capture, so migration
    # copied a newer state than the one compared. Both sides keep it off.
    base = template._config.model_copy(update={
        "packing": template._config.packing.model_copy(update={"evidence_projection_top_k": 3}),
        "organizer": template._config.organizer.model_copy(update={"opportunistic_enabled": False})})
    await template.close()
    runtime.clock.now = datetime.fromisoformat(FIXTURE["clock"])
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    owner = str(policy.owner_id)
    source_contents = tuple(FIXTURE["admitted"])
    frozen = {
        "clock": FIXTURE["clock"], "queries": FIXTURE["queries"], "source_contents": source_contents,
        "legacy_policy": None, "target_policy": policy.model_dump(mode="json"),
        "packing": base.packing.model_dump(mode="json"), "scoring": base.scoring.model_dump(mode="json"),
        "scope": policy.scope.value, "include_cross_scope": False,
        "fixture_sha256": hashlib.sha256((Path(__file__).parent / "fixtures/privacy/migration-v1.json").read_bytes()).hexdigest(),
    }
    (tmp_path / "standard-frozen-inputs.json").write_text(json.dumps(frozen, indent=2) + "\n")

    async def capture(memory, label, *, access=None):
        saved = {}
        for query in FIXTURE["queries"]:
            response = await asyncio.wait_for(memory.retrieve(query, user_id=owner,
                scope=policy.scope, reference_time=runtime.clock(), include_cross_scope=False,
                source_access=access), 20)
            saved[query] = {
                "result_ids": [str(item.node.id) for item in response.results],
                "packed_ids": [str(item.node.id) for section in response.bundle.sections.values() for item in section],
                "context": response.bundle.render(), "response": response.model_dump(mode="json"),
            }
            # Preserve every completed public response even if a later call
            # fails. Do not strip policy guidance or source availability.
            (tmp_path / f"standard-{label}-retrieval.json").write_text(json.dumps(saved, indent=2) + "\n")
            assert response.results and response.bundle.included_count
        return saved

    async with fresh_config(base, tmp_path / "legacy", layout) as original, fresh_config(
            base, tmp_path / "standard", layout, restore=True) as target:
        original = original.model_copy(update={"privacy_policy": None})
        assert original.packing == target.packing and original.scoring == target.scoring
        async with MemoryEngine.open(original, embedding_provider=runtime.embedder) as source:
            direct, fact, entity, generated, visual, survivor = await mixed_legacy_family(
                source, runtime, owner, source_contents=source_contents)
            nodes = (direct, fact, entity, *(item[0] for item in generated), survivor)
            expected_ids = {node.id for node in nodes}
            source_ids = (direct.evidence_refs[0], entity.evidence_refs[0], survivor.evidence_refs[0])
            assert len(expected_ids) == 8 and len(set(source_ids)) == 3
            assert tuple([(await source._event_store.get(str(identifier))).content for identifier in source_ids]) == source_contents
            (tmp_path / "standard-original-family.json").write_text(json.dumps({
                "nodes": [node.model_dump(mode="json") for node in nodes],
                "source_ids": [str(identifier) for identifier in source_ids],
                "excluded_visual_id": None if visual is None else str(visual.id),
            }, indent=2) + "\n")
            before = await capture(source, "before")
        closed_source = await _read_snapshot(original)
        job = tmp_path / "standard-migration-job"
        result = await migrate_privacy_pack(original, target, privacy_runtime=runtime,
            embedding_provider=runtime.embedder, job_path=job, unavailable_legacy_history=True,
            remove_event_ids=() if visual is None else (visual.id,), allow_reembedding=True)
        assert result.state == "complete"
        assert (await _read_snapshot(original)).fingerprint == closed_source.fingerprint
        bundle = read_protected_export(job / "protected-export.json")
        assert {node.id for node in bundle.nodes} == expected_ids
        assert {record.event_id for record in bundle.admissions} == set(source_ids)
        assert all(record.snapshot.policy == policy for record in bundle.admissions)
        assert all(record.snapshot.evidence_deadline > runtime.clock() for record in bundle.admissions)
        async with MemoryEngine.open(target, privacy_runtime=runtime, embedding_provider=runtime.embedder) as restored:
            records = [await restored._privacy.repository.get(str(identifier), user_id=owner) for identifier in source_ids]
            access = await runtime.protector.authority.grant("runtime",
                tuple((record.evidence.context, record.snapshot) for record in records),
                expires_at=runtime.clock() + timedelta(minutes=1))
            with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
                runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
                try:
                    after = await capture(restored, "after", access=access)
                finally:
                    runtime.protector.registry = public_registry(custody.path)
        differences = {}
        for query in FIXTURE["queries"]:
            prior, current = before[query], after[query]
            differences[query] = {
                "before_result_ids": prior["result_ids"], "after_result_ids": current["result_ids"],
                "before_packed_ids": prior["packed_ids"], "after_packed_ids": current["packed_ids"],
                "before_availability": prior["response"]["metadata"].get("source_availability"),
                "after_availability": current["response"]["metadata"].get("source_availability"),
                "before_limitation": prior["response"]["metadata"].get("source_limitation"),
                "after_limitation": current["response"]["metadata"].get("source_limitation"),
                "context_diff": "".join(difflib.unified_diff(prior["context"].splitlines(True),
                    current["context"].splitlines(True), fromfile="legacy-matched", tofile="standard-matched")),
            }
        (tmp_path / "standard-public-differences.json").write_text(json.dumps(differences, indent=2) + "\n")
        for query, required in zip(FIXTURE["queries"], (direct, fact, survivor), strict=True):
            prior, current = before[query], after[query]
            assert current["result_ids"] == prior["result_ids"], query
            assert current["packed_ids"] == prior["packed_ids"], query
            assert current["context"].encode() == prior["context"].encode(), query
            assert str(required.id) in current["result_ids"] and str(required.id) in current["packed_ids"]
            assert all(text in current["context"] for text in source_contents)
            prior_nodes = {item["node"]["id"]: item["node"] for item in prior["response"]["results"]}
            for item in current["response"]["results"]:
                node = item["node"]
                for field in ("content", "user_id", "scope", "node_type", "epistemic_type", "source_type",
                              "event_time", "valid_from", "valid_to", "evidence_refs"):
                    assert node[field] == prior_nodes[node["id"]][field], (query, node["id"], field)


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres", "closed_fernet"])
async def test_EM07_mixed_saved_family_identity_closure_interruptions(tmp_path, layout, analyzer, native):
    from prme.storage.privacy_export import validate_export
    from prme.privacy.portability import ProtectedExport
    from prme.privacy.journals import JOURNAL_KEY, GeneratedNodeReference
    from prme.storage.graph_rows import NODE_COLUMNS as _NODE_COLUMNS, record_to_node
    template, runtime, policy, custody, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    runtime.clock.now = datetime.fromisoformat(FIXTURE["clock"])
    owner = str(policy.owner_id)
    async def public_retrieval(memory, label):
        saved = {}
        for query in FIXTURE["queries"]:
            response = await asyncio.wait_for(memory.retrieve(query, user_id=owner,
                scope=policy.scope, reference_time=runtime.clock(), include_cross_scope=False), 20)
            assert response.results and response.bundle.included_count
            saved[query] = {
                "result_ids": [str(item.node.id) for item in response.results],
                "packed_ids": [str(item.node.id) for section in response.bundle.sections.values() for item in section],
                "context": response.bundle.render(),
                "response": response.model_dump(mode="json"),
            }
        (tmp_path / (label + "-public-retrieval.json")).write_text(json.dumps(saved, indent=2) + "\n")
        return saved
    local_layout = "directory" if layout == "closed_fernet" else layout
    async with fresh_config(base, tmp_path / "legacy", local_layout) as original, fresh_config(base, tmp_path / "migrated", local_layout, restore=True) as target:
        original = original.model_copy(update={"privacy_policy": None})
        if layout == "closed_fernet":
            original = type(original).model_validate({**original.model_dump(), "encryption_enabled": True, "encryption_key": "passphrase:task-owned-migration-source"})
        async with MemoryEngine.open(original, embedding_provider=runtime.embedder) as source:
            direct, fact, entity, generated, visual, survivor = await mixed_legacy_family(source, runtime, owner)
            before_retrieval = await public_retrieval(source, "before")
            if layout == "closed_fernet":
                source._conn.execute("DELETE FROM vector_payloads")
        before = await _read_snapshot(original)
        source_files = hashes(tmp_path / "legacy") if local_layout == "directory" else None
        remove_events = () if visual is None else (visual.id,)
        if visual is not None:
            with pytest.raises(PrivacyPortabilityError, match="^privacy_legacy_unclassified$"):
                await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                    job_path=tmp_path / "visual-refused", unavailable_legacy_history=True, allow_reembedding=True)
        with pytest.raises(PrivacyPortabilityError, match="^privacy_legacy_unclassified$"):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=tmp_path / "embedding-refused", unavailable_legacy_history=True, remove_event_ids=remove_events)
        job = tmp_path / "migration-job"
        saved = {}
        async def interrupted(stage, index, engine):
            if stage == "after_generated":
                saved["records"] = await engine._privacy.repository._read("1=1", ())
                saved["nodes"] = await engine._privacy.repository._query("SELECT id,content,metadata FROM nodes", ())
                raise RuntimeError("synthetic mixed interruption")
        arguments = dict(privacy_runtime=runtime, embedding_provider=runtime.embedder, job_path=job,
            unavailable_legacy_history=True, remove_event_ids=remove_events, allow_reembedding=True)
        with pytest.raises(RuntimeError, match="synthetic mixed interruption"):
            await migrate_privacy_pack(original, target, **arguments, _fault=interrupted)
        async def lost_ack(stage, index, engine):
            if stage == "after_publication":
                raise RuntimeError("synthetic lost publication acknowledgment")
        with pytest.raises(RuntimeError, match="synthetic lost publication acknowledgment"):
            await migrate_privacy_pack(original, target, **arguments, resume=True, _fault=lost_ack)
        bundle = validate_export(ProtectedExport.model_validate_json((job / "protected-export.json").read_bytes()))
        assert bundle.operations == ()
        assert {item.id for item in bundle.unavailable_history} == {UUID(str(row["id"])) for row in before.tables["operations"]}
        assert {record.event_id: record for record in bundle.admissions} == {record.event_id: record for record in saved["records"]}
        nodes = {node.id: node for node in bundle.nodes}
        assert set(nodes) == {direct.id, fact.id, entity.id, survivor.id, *(row[0].id for row in generated)}
        assert nodes[fact.id].content == FIXTURE["admitted"][1]
        assert nodes[fact.id].evidence_refs == fact.evidence_refs
        assert nodes[fact.id].event_time == fact.event_time and nodes[fact.id].valid_from == fact.valid_from
        for original_node, kind, sources in generated:
            migrated = nodes[original_node.id]
            reference = GeneratedNodeReference.model_validate(migrated.metadata[JOURNAL_KEY])
            assert reference.kind == kind and reference.lineage_id == original_node.id
            # As in organizer publication, earlier versions are lineage
            # dependencies in the manifest, not sources (journals.py checks this).
            assert reference.source_node_ids == tuple(node.id for node in sources)
            for field in ("content", "evidence_refs", "epistemic_type", "source_type", "event_time", "valid_from", "valid_to", "created_at"):
                assert getattr(migrated, field) == getattr(original_node, field)
        payload = bundle.model_dump_json()
        assert FIXTURE["source_only"] not in payload and FIXTURE["prohibited"] not in payload
        assert "consolidation_request_hash" not in payload and "BEGIN PRIVATE KEY" not in payload
        # The complete exported dependency graph cannot lose a previous profile
        # or a cited source node and still pass as a valid protected artifact.
        current_profile = generated[1][0].id
        broken = bundle.model_copy(update={"nodes": tuple(node for node in bundle.nodes if node.id != generated[0][0].id)})
        with pytest.raises(PrivacyPortabilityError):
            validate_export(broken)
        async with MemoryEngine.open(target, privacy_runtime=runtime, embedding_provider=runtime.embedder) as restored:
            after_retrieval = await public_retrieval(restored, "after")
            # This fixture changes legacy content/policy into sealed admitted
            # memory. Preserve every observed difference; it cannot establish
            # same-input/same-policy storage parity (the separate PV-11 gate).
            observations = {}
            for query in FIXTURE["queries"]:
                prior, current = before_retrieval[query], after_retrieval[query]
                observations[query] = {
                    "before_result_ids": prior["result_ids"], "after_result_ids": current["result_ids"],
                    "before_packed_ids": prior["packed_ids"], "after_packed_ids": current["packed_ids"],
                    "context_diff": "".join(difflib.unified_diff(prior["context"].splitlines(True),
                        current["context"].splitlines(True), fromfile="legacy", tofile="sealed")),
                }
            (tmp_path / "public-retrieval-differences.json").write_text(json.dumps(observations, indent=2) + "\n")
            closed_source_nodes = {str(node.id): node for node in map(record_to_node, before.tables["nodes"])}
            for query, required in zip(FIXTURE["queries"], (direct, fact, survivor), strict=True):
                prior, current = before_retrieval[query], after_retrieval[query]
                assert set(current["result_ids"]) == set(prior["result_ids"])
                assert set(current["packed_ids"]) == set(prior["packed_ids"])
                assert str(required.id) in current["result_ids"] and str(required.id) in current["packed_ids"]
                prior_nodes = {item["node"]["id"]: item["node"] for item in prior["response"]["results"]}
                current_nodes = {item["node"]["id"]: item["node"] for item in current["response"]["results"]}
                for identifier, node in current_nodes.items():
                    previous = prior_nodes[identifier]
                    for field in ("user_id", "scope", "node_type", "epistemic_type", "source_type",
                                  "event_time", "valid_from", "valid_to", "created_at", "evidence_refs"):
                        assert node[field] == previous[field], (query, identifier, field)
                    # Legacy retrieve schedules maintenance, drained on close.
                    # Migration must preserve that actual closed-source state.
                    assert node["lifecycle_state"] == closed_source_nodes[identifier].lifecycle_state.value
                    assert datetime.fromisoformat(node["updated_at"]) == closed_source_nodes[identifier].updated_at
                    expected = FIXTURE["admitted"][1] if identifier == str(fact.id) else previous["content"]
                    assert node["content"] == expected, (query, identifier)
                for field in ("subject", "predicate", "object", "polarity"):
                    assert current_nodes[str(fact.id)]["metadata"][field] == prior_nodes[str(fact.id)]["metadata"][field]
                for admitted in FIXTURE["admitted"]:
                    assert admitted in current["context"]
                assert "[2026-10-01 12:00]" in current["context"] and "[inferred]" in current["context"]
                returned = json.dumps(current["response"])
                assert FIXTURE["source_only"] not in returned and FIXTURE["prohibited"] not in returned
                assert FIXTURE["source_only"] not in current["context"] and FIXTURE["prohibited"] not in current["context"]
                availability = current["response"]["metadata"]["source_availability"]
                assert availability and {item["state"] for item in availability} == {"sealed"}
                limitation = current["response"]["metadata"]["source_limitation"]
                assert "Source evidence is unavailable to this ordinary read." in limitation
                assert current["context"].startswith(limitation + "\n")
            # Managed memory erasure after target publication, before job ack.
            await restored._privacy.erasure.request(ErasureRequest(request_id=uuid4(), node_ids=(generated[0][0].id,)), user_id=owner)
        result = await migrate_privacy_pack(original, target, **arguments, resume=True)
        assert result.state == "complete"
        async with MemoryEngine.open(target, privacy_runtime=runtime, embedding_provider=runtime.embedder) as restored:
            async with restored._privacy.erasure.repository.session() as session:
                surviving = {node.id: node for node in map(record_to_node, await session.fetch(f"SELECT {_NODE_COLUMNS} FROM nodes"))}
            assert generated[0][0].id not in surviving and current_profile not in surviving
            assert not {fact.id, entity.id, direct.id, generated[-1][0].id}.intersection(surviving)
            assert set(surviving) == {survivor.id} and surviving[survivor.id].content == FIXTURE["admitted"][2]
        assert (await _read_snapshot(original)).fingerprint == before.fingerprint
        if source_files is not None:
            assert hashes(tmp_path / "legacy") == source_files


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
async def test_EM07_mixed_summary_erasure_closure(tmp_path, layout, analyzer, native):
    template, runtime, policy, custody, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    owner = str(policy.owner_id)
    async with fresh_config(base, tmp_path / "legacy", layout) as original, fresh_config(base, tmp_path / "migrated", layout, restore=True) as target:
        original = original.model_copy(update={"privacy_policy": None})
        async with MemoryEngine.open(original, embedding_provider=runtime.embedder) as source:
            direct, fact, entity, generated, visual, survivor = await mixed_legacy_family(source, runtime, owner)
        await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
            job_path=tmp_path / "job", unavailable_legacy_history=True, allow_reembedding=True,
            remove_event_ids=() if visual is None else (visual.id,))
        async with MemoryEngine.open(target, privacy_runtime=runtime, embedding_provider=runtime.embedder) as engine:
            repository = engine._privacy.repository
            from prme.privacy.journals import JOURNAL_KEY
            summary_id = generated[-1][0].id
            raw_metadata = (await repository._query("SELECT metadata FROM nodes WHERE id=?", (str(summary_id),)))[0][0]
            metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
            erasure = engine._privacy.erasure
            async with runtime.erasure_ledger.guard():
                before_head = await runtime.erasure_ledger.current_head((await erasure.repository.state())[1].store_id)
            async def refused():
                with pytest.raises(PrivacyDenied, match="^privacy_migration_required$"):
                    await erasure.request(ErasureRequest(request_id=uuid4(), node_ids=(summary_id,)), user_id=owner)
                assert await erasure.repository.intents() == ()
                async with runtime.erasure_ledger.guard():
                    assert await runtime.erasure_ledger.current_head(before_head.store_id) == before_head
            # Missing declared node, then a real declared node outside the owner
            # boundary, then one UUID naming both memory and an admission. Each
            # fault uses task-owned SQL and restores the exact original row.
            missing = json.loads(json.dumps(metadata))
            missing[JOURNAL_KEY]["source_node_ids"].append(str(uuid4()))
            await repository._query("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(missing), str(summary_id)))
            await refused()
            await repository._query("UPDATE nodes SET metadata=? WHERE id=?", (json.dumps(metadata), str(summary_id)))
            await repository._query("UPDATE nodes SET user_id=? WHERE id=?", (str(uuid4()), str(fact.id)))
            await refused()
            await repository._query("UPDATE nodes SET user_id=? WHERE id=?", (owner, str(fact.id)))
            await repository._query("INSERT INTO privacy_admissions(event_id,user_id,scope,request_id,request_index,body) "
                "SELECT ?,user_id,scope,NULL,request_index,body FROM privacy_admissions WHERE event_id=? RETURNING event_id",
                (str(direct.id), str(direct.evidence_refs[0])))
            await refused()
            await repository._query("DELETE FROM privacy_admissions WHERE event_id=?", (str(direct.id),))
            request = ErasureRequest(request_id=uuid4(), node_ids=(generated[-1][0].id,))
            status = await engine._privacy.erasure.request(request, user_id=owner)
            assert status.state == "blocked_from_access"
            intents = await engine._privacy.erasure.repository.intents()
            intent = next(value for value in intents if value.request.request_id == request.request_id)
            assert set(intent.source_event_ids) == set(fact.evidence_refs)
            await engine.process_erasures(user_id=owner, budget_ms=60000)
            rows = await repository._query("SELECT id,content FROM nodes", ())
            assert {str(row[0]): row[1] for row in rows} == {str(survivor.id): FIXTURE["admitted"][2]}
            assert (await engine._privacy.erasure.request(request, user_id=owner)).state == "live_purged"
