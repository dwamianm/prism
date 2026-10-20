"""PV-04 read subchecks; fixture authority is not the PV-06 release gate."""
from contextlib import asynccontextmanager

from prme.models.privacy_reads import ProtectedEventView, ProtectedProvenanceView, SourceUnavailable
from tests.privacy_source_support import (
    MEMORY, SOURCE, assert_no_source, opened, store_pair,
    backend_layout as backend_layout, isolated_privacy_postgres as isolated_privacy_postgres,
    local_analyzer_fixture as local_analyzer_fixture, native as native_fixture,  # noqa: F401
)


async def test_AC01_ordinary_reads_return_admitted_memory_without_evidence_open(tmp_path, layout, analyzer, native, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    import pytest
    from prme.privacy import PrivacyDenied
    from prme.privacy.read_graph import AdmittedGraphView
    from prme.privacy.reads import ReadContext
    from prme.retrieval.answerability import AnswerabilityEvaluator, AnswerabilityStatus
    from prme.retrieval.session_context import expand_session_context
    from tests.test_answerability import _raw, _requirement
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        session_id = str(uuid4())
        event_id = await store_pair(engine, owner, session_id=session_id)
        await store_pair(engine, owner, session_id=session_id)
        @asynccontextmanager
        async def forbidden(*args, **kwargs):
            raise AssertionError("ordinary source decrypt")
            yield
        monkeypatch.setattr(runtime.protector, "open_held", forbidden)
        event = await engine.get_event(event_id, user_id=owner)
        assert isinstance(event, ProtectedEventView) and event.source_availability.state == "sealed"
        assert_no_source(event.model_dump_json())
        assert (await engine.inspect_source(event_id, user_id=owner)).state == "sealed"
        assert isinstance(await engine.inspect_source(event_id, user_id="00000000-0000-0000-0000-000000000000"), SourceUnavailable)
        nodes = await engine.get_event_nodes(event_id, user_id=owner)
        assert len(nodes) == 1 and nodes[0].content == MEMORY
        assert_no_source(nodes[0].model_dump_json())
        provenance = await engine.get_provenance(str(nodes[0].id), user_id=owner)
        assert isinstance(provenance, ProtectedProvenanceView)
        assert_no_source(provenance.model_dump_json())
        response = await engine.retrieve(MEMORY, user_id=owner)
        assert response.results and MEMORY in response.bundle.render()
        assert_no_source(response.model_dump_json())
        assert response.metadata.source_limitation
        assert response.metadata.receipt_persisted is True  # PV-05 now writes a minimized protected receipt.
        assert all(SOURCE not in str(texts) for texts in runtime.embedder.sends)
        # Existing AC06 mutually exclusive expansion controls use the real view.
        pipeline = engine._retrieval_pipeline
        base = pipeline._packing_config.model_dump()
        for options in ({"session_context_window": 2}, {"episode_context_top_k": 1},
                        {"evidence_projection_top_k": 2}, {"evidence_augmentation_top_k": 2}):
            pipeline._packing_config = type(pipeline._packing_config).model_validate({**base, **options, "context_citations": True})
            response = await engine.retrieve(MEMORY, user_id=owner)
            assert response.results and MEMORY in response.bundle.render()
            assert_no_source(response.model_dump_json())
            if "session_context_window" in options:
                assert any("SESSION_CONTEXT" in item.paths for item in response.results)
            if "episode_context_top_k" in options:
                assert any("EPISODE_CONTEXT" in item.paths for item in response.results)
        # This standalone evaluator consumes only the released admitted bundle;
        # it has no engine/vault access. Only its external model response is fixed.
        evaluator = AnswerabilityEvaluator()
        evaluator._client = SimpleNamespace(create=AsyncMock(return_value=_raw(_requirement(
            "Known admitted memory", AnswerabilityStatus.SUPPORTED,
            [next(iter(response.bundle.context_references))],
        ))))
        assessment = await evaluator.assess(MEMORY, response.bundle)
        assert assessment.model_called
        assert_no_source(evaluator._client.create.call_args.kwargs["messages"])
        assert_no_source(assessment.model_dump_json())
        # A policy denial during the native session read must escape its inner
        # fallback before any later packing/provider stage can proceed.
        context = await ReadContext.create(engine._privacy, user_id=owner)
        native_scan = engine._graph_store.scan_nodes
        async def denied_scan(*args, **kwargs):
            rows = await native_scan(*args, **kwargs)
            runtime.authority.denied.add("disclosure")
            return rows
        monkeypatch.setattr(engine._graph_store, "scan_nodes", denied_scan)
        with pytest.raises(PrivacyDenied):
            await expand_session_context(response.results, graph_store=AdmittedGraphView(engine._graph_store, context),
                                         user_id=owner, config=pipeline._packing_config)
        runtime.authority.denied.clear()
    finally:
        await engine.close()


async def test_AP04_CL02_grounded_qualifiers_quantities_and_snapshots(tmp_path, layout, analyzer, native):
    from dataclasses import asdict
    from datetime import datetime, timedelta
    from prme.models.aggregation import QuantityAggregationQuery
    from prme.models.temporal import AssertionStateQuery
    from prme.privacy import PrivacyDenied
    from prme.types import NodeType
    from tests.privacy_source_support import FIXTURE
    import pytest

    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer, policy_options={"capture": "minimal"})
    owner = str(policy.owner_id)
    try:
        for row in sorted(FIXTURE["qualifiers"], key=lambda item: item["id"] != "Q04"):
            claim = {key: row[key] for key in ("subject", "predicate", "object", "polarity", "condition") if key in row}
            claim["evidence_quote"] = row["source"]
            claim["epistemic_type"] = "conditional" if row.get("condition") else "asserted"
            if row.get("quantity"):
                claim["quantity"] = {key: value for key, value in row["quantity"].items() if key != "grounding"}
            if row.get("relationship"):
                claim = {("source_entity" if key == "subject" else "target_entity" if key == "object" else "relationship_type" if key == "predicate" else key): value for key, value in claim.items()}
            runtime.extractor.result = {"entities": [{"name": "Alex", "entity_type": "person", "description": SOURCE}],
                                        "facts": [] if row.get("relationship") else [claim],
                                        "relationships": [claim] if row.get("relationship") else [], "summary": None}
            if row["id"] == "Q04":
                # Preserve the original missing-ref refusal, then exercise the
                # supported literal ref through actual source-time resolution.
                before = len(runtime.embedder.sends)
                with pytest.raises(PrivacyDenied, match="privacy_evidence_ambiguous"):
                    await engine.ingest(row["source"], user_id=owner, wait_for_extraction=True)
                assert len(runtime.embedder.sends) == before
                claim["temporal_ref"] = "October 21"
                source_clock = datetime.fromisoformat(FIXTURE["clock"])
                identifier = await engine.ingest(row["source"], user_id=owner,
                    event_time=source_clock, wait_for_extraction=True)
                fact = next(node for node in await engine.get_event_nodes(identifier, user_id=owner)
                            if node.node_type == NodeType.FACT)
                effective = datetime.fromisoformat(row["effective_time"])
                assert fact.event_time == fact.valid_from == effective
                assert fact.metadata["resolved_date"] == row["effective_time"]
                assert fact.content == row["expected"] and identifier in {str(ref) for ref in fact.evidence_refs}
                for moment, expected_state in ((effective - timedelta(microseconds=1), "unknown"), (effective, "single")):
                    state = await engine.get_assertion_state(AssertionStateQuery(
                        subject=row["subject"], predicate=row["predicate"], scope=policy.scope, valid_at=moment,
                    ), user_id=owner)
                    assert state.status == expected_state
                    assert state.timeline[0].valid_from == effective
                    response = await engine.retrieve("TeaDB", user_id=owner, time_to=moment,
                                                     reference_time=moment)
                    assert (fact.id in {candidate.node.id for candidate in response.results}) == (moment == effective)
                    assert_no_source(response.model_dump_json())
                    assert response.metadata.source_limitation
                # CORE-323-01: both independently consumed protected text paths
                # must retain the effective date, including the default snapshot.
                from prme.retrieval.snapshots import render_snapshot_text
                alex = next(node for node in await engine.get_event_nodes(identifier, user_id=owner)
                            if node.node_type == NodeType.ENTITY and node.content == "Alex")
                snapshot = await engine.snapshot(str(alex.id), user_id=owner)
                texts = (snapshot.summary_text, render_snapshot_text(snapshot))
                assert all(effective.date().isoformat() in text and row["expected"] in text for text in texts), texts
                assert snapshot.source_limitation
                assert_no_source(asdict(snapshot))
                continue
            identifier = await engine.ingest(row["source"], user_id=owner, speaker=row.get("speaker"),
                                             event_time=runtime.clock(), wait_for_extraction=True)
            nodes = await engine.get_event_nodes(identifier, user_id=owner)
            facts = [node for node in nodes if node.node_type != NodeType.ENTITY]
            assert len(facts) == 1, row["id"]
            fact = facts[0]
            assert fact.content == row["expected"], row["id"]
            assert fact.event_time == runtime.clock() and identifier in {str(ref) for ref in fact.evidence_refs}
            assert fact.metadata["polarity"] == row["polarity"]
            assert fact.metadata.get("condition") == row.get("condition")
            assert_no_source([node.model_dump_json() for node in nodes])
            if row.get("quantity"):
                assert fact.metadata["quantity"] == row["quantity"]
                quantities = await engine.aggregate_quantities(QuantityAggregationQuery(
                    subjects=[row["subject"]], predicates=[row["predicate"]], group_by=["unit"],
                ), user_id=owner)
                assert quantities.matched_quantity_records == 1
                assert_no_source(quantities.model_dump_json())
            state = await engine.get_assertion_state(AssertionStateQuery(
                subject=row["speaker"] if row.get("speaker_reference") else row["subject"],
                predicate=row["predicate"], scope=policy.scope, valid_at=runtime.clock(),
            ), user_id=owner)
            assert_no_source(state.model_dump_json())
        entities = await engine.query_nodes(user_id=owner, node_type=NodeType.ENTITY)
        assert entities
        alex = next(node for node in entities if node.content == "Alex")
        snapshot = await engine.snapshot(str(alex.id), user_id=owner)
        assert "Alex" in snapshot.summary_text and snapshot.source_limitation
        assert_no_source(asdict(snapshot))
        assert all(SOURCE not in str(texts) for texts in runtime.embedder.sends)
    finally:
        await engine.close()


async def test_AC07_explicitly_allowed_sensitive_memory_remains_usable(tmp_path, layout, analyzer, native):
    from prme.privacy import CategoryAction
    from tests.privacy_source_support import PROHIBITED
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer, policy_options={
        "category_actions": (CategoryAction(category="email", action="allow"),),
    })
    try:
        owner = str(policy.owner_id)
        await engine.store(f"{PROHIBITED}. {SOURCE}", retrieval_content=PROHIBITED, user_id=owner)
        response = await engine.retrieve(PROHIBITED, user_id=owner)
        assert PROHIBITED in response.bundle.render() and SOURCE not in response.model_dump_json()
        assert response.metadata.source_limitation
    finally:
        await engine.close()


async def test_CL07_staging_retry_reopen_contains_only_admitted_text(tmp_path, layout, analyzer, native, monkeypatch):
    from prme import MemoryEngine
    import pytest
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    config = engine._config
    original = engine._graph_store.commit_derivation
    plans = []
    async def interrupted(plan, **kwargs):
        plans.append(plan)
        assert_no_source(plan.model_dump_json())
        raise RuntimeError("synthetic stage interruption")
    monkeypatch.setattr(engine._graph_store, "commit_derivation", interrupted)
    try:
        with pytest.raises(RuntimeError, match="synthetic stage interruption"):
            await store_pair(engine, owner)
        assert plans and all(item.content == MEMORY for item in plans[0].embeddings)
        repository = engine._privacy.repository
        tables = ["events", "privacy_admissions", "operations", "nodes", "edges", "event_materializations", "event_extractions"]
        tables += ["lexical_documents"] if layout == "postgres" else ["vector_metadata", "vector_payloads", "vector_staging"]
        if layout == "pack":
            tables.append("vector_snapshot")
        for table in tables:
            assert_no_source(await repository._query(f"SELECT * FROM {table}", ()))
        monkeypatch.setattr(engine._graph_store, "commit_derivation", original)
        processed = await engine.process_pending(user_id=owner)
        assert processed.processed == 1 and processed.pending == 0
        response = await engine.retrieve(MEMORY, user_id=owner)
        assert response.results and MEMORY in response.bundle.render()
        # Leave one second real accepted plan abandoned before publication.
        monkeypatch.setattr(engine._graph_store, "commit_derivation", interrupted)
        with pytest.raises(RuntimeError, match="synthetic stage interruption"):
            await store_pair(engine, owner)
        monkeypatch.setattr(engine._graph_store, "commit_derivation", original)
        if layout != "postgres":
            save = engine._vector_index._save_snapshot
            def interrupted_snapshot():
                save()
                raise RuntimeError("synthetic native snapshot interruption")
            monkeypatch.setattr(engine._vector_index, "_save_snapshot", interrupted_snapshot)
            with pytest.raises(RuntimeError, match="synthetic native snapshot interruption"):
                await engine._vector_index.save()
            monkeypatch.setattr(engine._vector_index, "_save_snapshot", save)
    finally:
        await engine.close()
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SOURCE.encode() not in path.read_bytes()
    reopened = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
    try:
        response = await reopened.retrieve(MEMORY, user_id=owner)
        assert response.results and MEMORY in response.bundle.render()
        assert_no_source(response.model_dump_json())
        for table in tables:
            assert_no_source(await reopened._privacy.repository._query(f"SELECT * FROM {table}", ()))
    finally:
        await reopened.close()
    # No erasure or journal crash-recovery success is inferred here; PV-05/06 own those gates.


async def test_AC01_AC03_ordinary_final_deadlines(tmp_path, layout, analyzer, native, monkeypatch):
    """Existing ordinary-read case: A expires while B's authorization awaits."""
    import asyncio
    from datetime import timedelta
    from uuid import UUID, uuid4
    import pytest
    from prme.privacy import MemoryRetention, PrivacyDenied

    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner, initial = str(policy.owner_id), runtime.clock()
    try:
        ids = []
        for identifier, seconds in ((UUID(int=1), 60), (UUID(int=(1 << 128) - 1), 120)):
            # Deterministic opaque identities order A before B in the existing
            # repository. Sanitizer, admission, crypto and storage remain real.
            with monkeypatch.context() as fixed_ids:
                fixed_ids.setattr("prme.privacy.admission.uuid4", lambda: identifier)
                ids.append(await engine.store(f"{MEMORY}. {SOURCE}.", retrieval_content=MEMORY,
                    user_id=owner, policy=policy.model_copy(update={
                        "policy_id": uuid4(), "memory_retention": MemoryRetention(mode="erase_after", seconds=seconds),
                    })))
        ids = [str((await engine.get_event_nodes(event_id, user_id=owner))[0].id) for event_id in ids]
        rows = await engine._privacy.repository.for_owner(owner)
        assert len(rows) == 2
        early, later = (row.snapshot.memory_deadline for row in rows)
        assert early < later
        original_guard = runtime.authority.guard
        trace, trigger = [], {}

        @asynccontextmanager
        async def delayed_guard(saved, effect):
            trace.append((saved.memory_deadline, effect))
            position = len(trace)
            async with original_guard(saved, effect):
                if trigger.get("position") == position and trigger["phase"] == "entry":
                    await asyncio.sleep(0)
                    runtime.clock.now = trigger["time"]
                    trigger["advanced"] = True
                yield
                if trigger.get("position") == position and trigger["phase"] == "exit":
                    await asyncio.sleep(0)
                    runtime.clock.now = trigger["time"]
                    trigger["advanced"] = True

        monkeypatch.setattr(runtime.authority, "guard", delayed_guard)
        calls = {
            "get_nodes": lambda: engine.get_nodes(ids, user_id=owner),
            "query_nodes": lambda: engine.query_nodes(user_id=owner),
            "scan_nodes": lambda: engine.scan_nodes(user_id=owner),
            "get_events": lambda: engine.get_events(owner),
            "get_provenance": lambda: engine.get_provenance(ids[0], user_id=owner),
        }
        for name, call in calls.items():
            runtime.clock.now = initial
            trace.clear()
            trigger.clear()
            live = await asyncio.wait_for(call(), 5)
            assert live, name
            if name != "get_provenance":
                assert len(live) == 2, name
            assert_no_source(live if isinstance(live, list) else live.model_dump_json())
            # Record the actual public route's last B guard, avoiding a fixed
            # implementation call count. B is still live at A's boundary.
            position = max(i for i, (deadline, effect) in enumerate(trace, 1)
                           if deadline == later and effect == "disclosure")
            for phase in ("entry", "exit"):
                for moment in (early - timedelta(microseconds=1), early):
                    runtime.clock.now = initial
                    trace.clear()
                    trigger.update(position=position, phase=phase, time=moment, advanced=False)
                    if moment < early:
                        assert await asyncio.wait_for(call(), 5), (name, phase)
                    else:
                        with pytest.raises(PrivacyDenied, match="privacy_expired"):
                            await asyncio.wait_for(call(), 5)
                    assert trigger["advanced"], (name, phase)
            trigger.clear()
        # A resumed public iterator must recheck before disclosing the next
        # member of a page after the caller has awaited elsewhere.
        runtime.clock.now = initial
        stream = engine.iter_nodes(user_id=owner, batch_size=2)
        assert await anext(stream)
        runtime.clock.now = early
        with pytest.raises(PrivacyDenied, match="privacy_expired"):
            await anext(stream)
        await stream.aclose()
    finally:
        runtime.clock.now = initial
        await engine.close()


async def test_AC01_AC03_orphan_markers_never_select_raw_reads(tmp_path, layout, analyzer, native):
    """Existing classification case: missing enrollment cannot imply legacy."""
    import json
    from uuid import uuid4
    from prme import MemoryEngine
    from prme.models import MemoryNode
    from prme.privacy import PrivacyDenied
    from prme.privacy.records import PRIVACY_KEY
    from prme.types import NodeType

    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    identifier = await store_pair(engine, owner)
    # Simulate the reported interrupted enrollment in this disposable fixture.
    await engine._privacy.repository._query("DELETE FROM privacy_admissions WHERE user_id=?", (owner,))
    config = engine._config.model_copy(update={"privacy_policy": None})
    await engine.close()
    engine = await MemoryEngine.create(config, embedding_provider=runtime.embedder)
    try:
        # A source-bearing historical node with a malformed marker is also
        # protected by the existing metadata classifier, without a valid record.
        orphan = MemoryNode(user_id=owner, node_type=NodeType.NOTE, content=SOURCE,
                            metadata={PRIVACY_KEY: None})
        await engine._graph_store.create_node(orphan)
        legacy_owner = str(uuid4())
        legacy_event = await engine.store(MEMORY, user_id=legacy_owner)
        legacy_id = str((await engine.get_event_nodes(legacy_event, user_id=legacy_owner))[0].id)
        assert (await engine.get_nodes([legacy_id], user_id=legacy_owner))[0].content == MEMORY
        assert (await engine.get_events(legacy_owner))[0].content == MEMORY
        await engine.require_legacy_history(user_id=legacy_owner)
        calls = {
            "get_events": lambda: engine.get_events(owner),
            "query_nodes": lambda: engine.query_nodes(user_id=owner),
            "scan_nodes": lambda: engine.scan_nodes(user_id=owner),
            "get_nodes": lambda: engine.get_nodes([identifier, str(orphan.id)], user_id=owner),
            "count_nodes": lambda: engine.count_nodes(user_id=owner),
            "query_nodes_unscoped": lambda: engine.query_nodes(),
            "count_nodes_unscoped": lambda: engine.count_nodes(),
            "history": lambda: engine.require_legacy_history(user_id=owner),
            "history_unscoped": lambda: engine.require_legacy_history(user_id=None),
            "search": lambda: engine.search(SOURCE, user_id=owner),
        }
        bypassed = []
        for name, call in calls.items():
            try:
                await call()
            except PrivacyDenied:
                pass
            else:
                bypassed.append(name)
        assert not bypassed, bypassed
        # Reuse the same fixture to isolate a node-only and then event-only
        # malformed marker; both use the existing database classifier.
        repo = engine._privacy.repository
        for marked_table, cleared_table in (("nodes", "events"), ("events", "nodes")):
            await repo._query(f"UPDATE {cleared_table} SET metadata=? WHERE user_id=?", ("{}", owner))
            await repo._query(f"UPDATE {marked_table} SET metadata=? WHERE user_id=?",
                              (json.dumps({PRIVACY_KEY: "malformed"}), owner))
            for name, call in calls.items():
                try:
                    await call()
                except PrivacyDenied:
                    pass
                else:
                    raise AssertionError((marked_table, name))
    finally:
        await engine.close()
