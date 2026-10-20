"""AP08 and AC3: authoritative denies at real effects, no timing sleeps."""

import asyncio
from datetime import timedelta
import os
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy import PrivacyDenied
from tests.privacy_fakes import config_for, make_runtime, open_engine, policy_for
from tests.privacy_fakes import isolated_privacy_postgres as isolated_privacy_postgres

SOURCE = "Alex does not like tea unless it is cold."


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
@pytest.mark.parametrize("reopened", [False, True])
async def test_reingest_protection_precedes_source_filtering(tmp_path, monkeypatch, layout, reopened):
    from unittest.mock import AsyncMock
    from prme.ingestion.reingest import encode_cursor
    from prme.storage.reingest import SourceRef
    from tests.test_reingest import settings

    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    config = settings(config_for(tmp_path, layout=layout, policy=None if reopened else p))
    engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
    if reopened:
        await engine.store("A legacy source", user_id=owner)
    event_id = await engine.store(SOURCE, user_id=owner, policy=p)
    event = await engine._event_store.get(event_id)
    saved = await engine._privacy.repository.get(event_id, user_id=owner)
    assert event.content == "" and saved.evidence is None
    # Completed no-evidence sources must not disappear into a zero-work result.
    if reopened:
        await engine.close()
        r.clock.now = saved.snapshot.processing_deadline + timedelta(seconds=1)
        engine = await MemoryEngine.create(config, embedding_provider=r.embedder)
    scan = AsyncMock(side_effect=AssertionError("protected source scan"))
    monkeypatch.setattr(engine._event_store.reingest, "scan_sources", scan)
    cursor = encode_cursor(SourceRef(event.event_time or event.timestamp, event.timestamp, event_id, None))
    try:
        for options in ({}, {"dry_run": True}, {"limit": 0}, {"cursor": cursor}):
            with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
                await engine.reingest(owner, **options)
        scan.assert_not_awaited()
    finally:
        await engine.close()


async def test_shared_batch_refuses_a_protected_participant_before_correction_embedding(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from tests.test_reingest import Scripted, install, settings

    r = make_runtime()
    p = policy_for(r)
    engine = await MemoryEngine.create(settings(config_for(tmp_path)), embedding_provider=r.embedder, privacy_runtime=r)
    owner = str(p.owner_id)
    legacy = await engine.store("A legacy source", user_id=owner)
    protected = await engine.store(SOURCE, user_id=owner, policy=p)
    events = [await engine._event_store.get(identifier) for identifier in (legacy, protected)]
    model = install(engine, Scripted())
    search = AsyncMock(side_effect=AssertionError("protected correction query"))
    monkeypatch.setattr(engine._vector_index, "search", search)
    engine._pipeline._extraction_corrections = True
    try:
        with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
            await engine._pipeline._request_batch(events[0], events, claim=None)
        assert not model.calls
        search.assert_not_awaited()
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
async def test_reingest_legacy_controls_keep_exact_owner_and_scope(tmp_path, layout):
    from prme.types import Scope
    from tests.test_reingest import FOUND, LAKE, Scripted, install, settings

    r = make_runtime()
    p = policy_for(r)
    owner, other = str(p.owner_id), str(uuid4())
    engine = await MemoryEngine.create(settings(config_for(tmp_path, layout=layout)),
                                      embedding_provider=r.embedder, privacy_runtime=r)
    try:
        await engine.store(SOURCE, user_id=owner, policy=p)
        await engine.store(LAKE, user_id=owner, scope=Scope.PROJECT)
        await engine.store(LAKE, user_id=other)
        model = install(engine, Scripted(FOUND))
        assert (await engine.reingest(owner, scope=Scope.PROJECT)).processed == 1
        assert (await engine.reingest(other)).processed == 1
        assert model.calls == [[LAKE], [LAKE]]
        with pytest.raises(PrivacyDenied):
            await engine.reingest(owner)
    finally:
        await engine.close()


@pytest.mark.parametrize("boundary", ["correction", "send", "save", "native_save", "plan", "vector", "commit"])
async def test_reingest_rechecks_after_awaited_boundaries(tmp_path, monkeypatch, boundary):
    from contextlib import asynccontextmanager
    from prme.ingestion.reingest import Reingester
    from tests.test_reingest import FOUND, LAKE, Scripted, install, settings

    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    engine = await MemoryEngine.create(settings(config_for(tmp_path)), embedding_provider=r.embedder, privacy_runtime=r)
    await engine.store(LAKE, user_id=owner)
    model = install(engine, Scripted(FOUND))
    enrolled = False
    before = None

    async def counts():
        async with engine._event_store.extraction_work.session() as session:
            values = []
            for table in ("nodes", "operations", "vector_metadata"):
                values.append((await session.fetch(f"SELECT count(*) AS n FROM {table}"))[0]["n"])
            return tuple(values)

    async def activate():
        nonlocal enrolled, before
        if not enrolled:
            enrolled = True
            # A real concurrent protected admission, not a mocked policy check.
            await engine.ingest_fast(SOURCE, user_id=owner, policy=p)
            r.authority.revisions[p.policy_id] = 2
            before = await counts()

    if boundary == "correction":
        original = engine._pipeline._correction_memories

        async def correction(*args, **kwargs):
            await activate()
            return await original(*args, **kwargs)
        monkeypatch.setattr(engine._pipeline, "_correction_memories", correction)
        engine._pipeline._extraction_corrections = True
    elif boundary == "send":
        @asynccontextmanager
        async def semaphore():
            await activate()
            yield
        monkeypatch.setattr(engine._pipeline, "_extraction_semaphore", semaphore())
    elif boundary == "native_save":
        original = engine._event_store.reingest.save_extractions

        async def save(*args, **kwargs):
            await activate()
            return await original(*args, **kwargs)
        monkeypatch.setattr(engine._event_store.reingest, "save_extractions", save)
    elif boundary == "plan":
        original = Reingester._retire

        async def retire(self, *args, **kwargs):
            await original(self, *args, **kwargs)
            await activate()
        monkeypatch.setattr(Reingester, "_retire", retire)
    else:
        original = engine._write_queue.submit
        prefix = {"save": "extraction.batch:", "vector": "reingest.vector:", "commit": "reingest.commit:"}[boundary]

        async def submit(operation, *, label):
            async def delayed():
                if label.startswith(prefix):
                    await activate()
                return await operation()
            return await original(delayed, label=label)
        monkeypatch.setattr(engine._write_queue, "submit", submit)
    try:
        with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
            await engine.reingest(owner)
        assert enrolled and await counts() == before
        assert len(model.calls) == (0 if boundary in {"correction", "send"} else 1)
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
async def test_reingest_native_commit_rechecks_before_existing_marker(tmp_path, monkeypatch, layout):
    from tests.test_reingest import FOUND, LAKE, Scripted, install, settings

    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    engine = await MemoryEngine.create(settings(config_for(tmp_path, layout=layout)),
                                      embedding_provider=r.embedder, privacy_runtime=r)
    captured = []
    original = engine._graph_store.commit_reingest

    async def commit(plan, record):
        captured.append((plan, record))
        return await original(plan, record)
    monkeypatch.setattr(engine._graph_store, "commit_reingest", commit)
    try:
        await engine.store(LAKE, user_id=owner)
        install(engine, Scripted(FOUND))
        assert (await engine.reingest(owner)).processed == 1
        await engine.ingest_fast(SOURCE, user_id=owner, policy=p)
        before = [node.model_dump_json() for node in await engine._graph_store.query_nodes(user_id=owner)]
        with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
            await original(*captured[0])
        assert [node.model_dump_json() for node in await engine._graph_store.query_nodes(user_id=owner)] == before
    finally:
        await engine.close()


async def test_shared_batch_rechecks_discovered_context_and_split_retry(tmp_path, monkeypatch):
    from prme.ingestion.errors import ExtractionError
    from tests.test_reingest import LAKE, Scripted, install, settings

    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    engine = await MemoryEngine.create(settings(config_for(tmp_path)), embedding_provider=r.embedder, privacy_runtime=r)
    legacy = [await engine.store(LAKE, user_id=owner, session_id="batch") for _ in range(2)]
    events = [await engine._event_store.get(identifier) for identifier in legacy]
    model = install(engine, Scripted())
    try:
        protected = await engine.store(SOURCE, user_id=owner, policy=p)
        node = (await engine._graph_store.get_event_nodes(protected, user_id=owner))[0]

        async def search(*args, **kwargs):
            return [{"node_id": str(node.id)}]
        monkeypatch.setattr(engine._vector_index, "search", search)
        engine._pipeline._extraction_corrections = True
        with pytest.raises(PrivacyDenied):
            await engine._pipeline._request_batch(events[0], events, claim=None)
        assert not model.calls
    finally:
        await engine.close()

    # A different legacy owner remains allowed initially; enrollment after the
    # first failed send must prevent every recursively split request.
    engine = await MemoryEngine.create(settings(config_for(tmp_path)), embedding_provider=r.embedder, privacy_runtime=r)
    owner = str(uuid4())
    p = policy_for(r, owner=UUID(owner))
    try:
        await engine.store(LAKE, user_id=owner, session_id="batch")
        await engine.store(LAKE, user_id=owner, session_id="batch")
        sends = []

        async def fail(turns):
            sends.append(turns)
            await engine.ingest_fast(SOURCE, user_id=owner, policy=p)
            raise ExtractionError("retry", reason_code="TimeoutError")
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract_batch", fail)
        with pytest.raises(PrivacyDenied):
            await engine.reingest(owner)
        assert len(sends) == 1
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", ["directory", "pack"])
@pytest.mark.parametrize("boundary", ["vector", "commit"])
async def test_cancelled_publication_keeps_authority_until_queued_effect_settles(tmp_path, monkeypatch, layout, boundary):
    engine, r, p = await open_engine(tmp_path, layout=layout)
    entered, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    order = []
    original = engine._write_queue.submit

    async def submit(operation, *, label):
        if label.startswith(f"derivation.{boundary}:"):
            async def blocked():
                entered.set()
                await release.wait()
                result = await operation()
                order.append("effect")
                return result
            return await original(blocked, label=label)
        return await original(operation, label=label)

    async def activate():
        attempted.set()
        async with r.authority._lock:
            r.authority.revisions[p.policy_id] = 2
            order.append("activation")

    monkeypatch.setattr(engine._write_queue, "submit", submit)
    task = asyncio.create_task(engine.store(SOURCE, user_id=str(p.owner_id)))
    activation = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        activation = asyncio.create_task(activate())
        await attempted.wait()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await activation
        await engine._write_queue._queue.join()
        assert order == ["effect", "activation"]
        assert await engine._graph_store.query_nodes(user_id=str(p.owner_id))
    finally:
        release.set()
        await asyncio.gather(task, *([activation] if activation else []), return_exceptions=True)
        await engine.close()


@pytest.mark.parametrize("boundary", ["after_extraction", "embedding_send"])
async def test_pending_work_expiry_prevents_embedding_at_each_guard(tmp_path, monkeypatch, boundary):
    engine, r, p = await open_engine(tmp_path)
    owner = str(p.owner_id)
    monkeypatch.setattr(engine._pipeline, "queue_extraction", lambda *args: None)
    event_id = await engine.ingest(SOURCE, user_id=owner)
    saved = await engine._privacy.repository.get(event_id, user_id=owner)
    entered, release = asyncio.Event(), asyncio.Event()
    if boundary == "after_extraction":
        original = r.extractor.extract

        async def extract(*args, **kwargs):
            result = await original(*args, **kwargs)
            r.clock.now = saved.snapshot.processing_deadline + timedelta(seconds=1)
            return result
        monkeypatch.setattr(r.extractor, "extract", extract)
    else:
        original = r.embedder.encode

        async def encode(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(r.embedder, "encode", encode)
    task = asyncio.create_task(engine.process_extractions(user_id=owner))
    try:
        if boundary == "embedding_send":
            await asyncio.wait_for(entered.wait(), 10)
            r.clock.now = saved.snapshot.processing_deadline + timedelta(seconds=1)
            release.set()
        await task
        assert len(r.extractor.sends) == 1 and r.embedder.sends == []
        assert await engine._graph_store.query_nodes(user_id=owner) == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await engine.close()


async def test_direct_memory_materialization_uses_memory_retention_not_work_deadline(tmp_path):
    engine, r, p = await open_engine(tmp_path)
    owner = str(p.owner_id)
    try:
        event_id = await engine.ingest_fast(SOURCE, user_id=owner)
        saved = await engine._privacy.repository.get(event_id, user_id=owner)
        r.clock.now = saved.snapshot.processing_deadline + timedelta(seconds=1)
        result = await engine.process_pending(user_id=owner)
        assert result.processed == 1 and r.embedder.sends
        assert await engine.get_event_nodes(event_id, user_id=owner)
    finally:
        await engine.close()


async def test_owner_group_config_cannot_publish_a_revoked_dependency(tmp_path):
    from prme.ingestion.owner_name import OWNER_NAME_KEY, owner_name_marker
    from prme.types import NodeType
    r = make_runtime()
    a = policy_for(r)
    b = policy_for(r, owner=a.owner_id)
    owner = str(a.owner_id)
    config = config_for(tmp_path)
    engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
    event_id = await engine.store("Alex", user_id=owner, policy=a, node_type=NodeType.ENTITY,
        metadata={OWNER_NAME_KEY: owner_name_marker(str(uuid4()), "self_introduction")})
    await engine.close()
    r.authority.revisions[a.policy_id] = 2
    config = config.model_copy(update={"enable_owner_group_links": True})
    engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
    r.extractor.result = {"entities": [], "facts": [{"subject": "We", "predicate": "prefer", "object": "tea"}],
                          "relationships": [], "summary": None}
    calls, sends = len(r.sanitizer.calls), len(r.embedder.sends)
    try:
        with pytest.raises(PrivacyDenied, match="privacy_configuration_invalid"):
            await engine.ingest("We prefer tea.", user_id=owner, policy=b, wait_for_extraction=True)
        assert len(r.sanitizer.calls) == calls and len(r.embedder.sends) == sends and not r.extractor.sends
        assert [str(event.id) for event in await engine._event_store.get_by_user(owner)] == [event_id]
        assert await engine._graph_store.get_edges() == []
    finally:
        await engine.close()


async def test_AP08_stale_before_publication_has_no_durable_admission(tmp_path):
    engine, r, p = await open_engine(tmp_path)

    async def activate():
        r.authority.revisions[p.policy_id] = 2

    r.embedder.after_send = activate
    try:
        with pytest.raises(PrivacyDenied, match="stale"):
            await engine.store(SOURCE, user_id=str(p.owner_id))
        assert await engine._event_store.get_by_user(str(p.owner_id)) == []
        assert await engine._graph_store.query_nodes(user_id=str(p.owner_id)) == []
    finally:
        await engine.close()


async def test_authoritative_transmission_deny_sends_and_writes_nothing(tmp_path):
    engine, r, p = await open_engine(tmp_path)
    r.authority.denied.add("transmission")
    try:
        with pytest.raises(PrivacyDenied):
            await engine.store(SOURCE, user_id=str(p.owner_id))
        with pytest.raises(PrivacyDenied):
            await engine.ingest(SOURCE, user_id=str(p.owner_id), wait_for_extraction=True)
        assert r.embedder.sends == [] and r.extractor.sends == []
        assert await engine._event_store.get_by_user(str(p.owner_id)) == []
        assert await engine._graph_store.query_nodes(user_id=str(p.owner_id)) == []
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
async def test_protected_owner_does_not_change_other_legacy_object_calls(tmp_path, layout):
    if layout == "postgres" and not os.getenv("PRME_TEST_DATABASE_URL"):
        pytest.skip("isolated PostgreSQL not configured")
    r = make_runtime()
    p = policy_for(r)
    engine = await MemoryEngine.create(config_for(tmp_path, layout=layout), embedding_provider=r.embedder, privacy_runtime=r)
    try:
        protected = await engine.store(SOURCE, user_id=str(p.owner_id), policy=p)
        protected_node = (await engine.get_event_nodes(protected, user_id=str(p.owner_id)))[0]
        legacy = await engine.store("Legacy memory", user_id="legacy-other-owner")
        legacy_node = (await engine.get_event_nodes(legacy, user_id="legacy-other-owner"))[0]
        same_owner = await engine.store("Same owner's legacy memory", user_id=str(p.owner_id))
        same_node = (await engine.get_event_nodes(same_owner, user_id=str(p.owner_id)))[0]
        assert (await engine.get_event(legacy)).content == "Legacy memory"
        assert (await engine.get_node(str(legacy_node.id))).content == "Legacy memory"
        assert (await engine.get_event(same_owner)).content == "Same owner's legacy memory"
        assert (await engine.get_node(str(same_node.id), user_id=str(p.owner_id))).content == "Same owner's legacy memory"
        await engine.archive(str(legacy_node.id))
        await engine.archive(str(same_node.id))
        assert await engine.get_event(protected, user_id="legacy-other-owner") is None
        assert await engine.get_node(str(protected_node.id), user_id="legacy-other-owner") is None
        for call in (lambda: engine.get_event(protected), lambda: engine.get_node(str(protected_node.id)),
                     lambda: engine.archive(str(protected_node.id)), lambda: engine.query_nodes(),
                     lambda: engine.get_node(str(uuid4()))):
            with pytest.raises(PrivacyDenied, match="boundary_mismatch"):
                await call()
    finally:
        await engine.close()


@pytest.mark.parametrize("evidence_kind", ["missing", "malformed", "foreign", "scope", "protected"])
@pytest.mark.parametrize("layout", ["directory", "postgres"])
async def test_legacy_reinforcement_evidence_uses_primary_boundary(tmp_path, evidence_kind, layout):
    from prme.types import Scope
    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    engine = await MemoryEngine.create(config_for(tmp_path, layout=layout), embedding_provider=r.embedder, privacy_runtime=r)
    try:
        protected = await engine.store(SOURCE, user_id=owner, policy=p)
        legacy = await engine.store("Legacy memory", user_id=owner)
        [node] = await engine.get_event_nodes(legacy, user_id=owner)
        evidence = {"missing": str(uuid4()), "malformed": "not-a-uuid", "protected": protected}.get(evidence_kind)
        if evidence_kind in {"foreign", "scope"}:
            other = policy_for(r, owner=uuid4() if evidence_kind == "foreign" else p.owner_id,
                               scope=Scope.PROJECT if evidence_kind == "scope" else Scope.PERSONAL)
            evidence = await engine.store(SOURCE, user_id=str(other.owner_id), scope=other.scope, policy=other)
        expected = "privacy_boundary_mismatch" if evidence_kind == "protected" else "Evidence event not found in the node's owner and scope"
        with pytest.raises(ValueError, match=expected):
            await engine.reinforce(str(node.id), evidence_id=evidence)
        assert await engine.get_node(str(node.id)) == node
        with pytest.raises(PrivacyDenied, match="privacy_boundary_mismatch"):
            await engine.reinforce(str(uuid4()), evidence_id=legacy)
        [protected_node] = await engine.get_event_nodes(protected, user_id=owner)
        with pytest.raises(PrivacyDenied, match="privacy_boundary_mismatch"):
            await engine.reinforce(str(protected_node.id), evidence_id="not-a-uuid")
    finally:
        await engine.close()


async def test_mixed_store_global_feedback_remains_denied(tmp_path):
    from prme.quality.feedback import FeedbackSignal, FeedbackSignalType
    r = make_runtime()
    p = policy_for(r)
    engine = await MemoryEngine.create(config_for(tmp_path), embedding_provider=r.embedder, privacy_runtime=r)
    signal = FeedbackSignal(query="legacy query", surfaced_node_ids=[], signal_type=FeedbackSignalType.USED)
    try:
        await engine.feedback(signal)
        assert len(engine._feedback_tracker) == 1
        await engine.store(SOURCE, user_id=str(p.owner_id), policy=p)
        with pytest.raises(PrivacyDenied, match="privacy_boundary_mismatch"):
            await engine.feedback(signal)
        assert len(engine._feedback_tracker) == 1
    finally:
        await engine.close()


async def test_AP08_stale_before_send_and_restart_preserve_saved_policy(tmp_path, monkeypatch):
    engine, r, p = await open_engine(tmp_path)
    owner = str(p.owner_id)
    monkeypatch.setattr(engine._pipeline, "queue_extraction", lambda *args: None)
    event_id = await engine.ingest(SOURCE, user_id=owner)
    saved = await engine._privacy.repository.get(event_id, user_id=owner)
    config = engine._config.model_copy(update={"privacy_policy": None})
    await engine.close()
    r.authority.revisions[p.policy_id] = 2
    reopened = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
    try:
        # Real work claim and saved snapshot, with the default removed on reopen.
        await reopened.process_extractions(user_id=owner)
        assert r.extractor.sends == [] and r.embedder.sends == []
        assert await reopened._graph_store.query_nodes(user_id=owner) == []
        assert (await reopened._privacy.repository.get(event_id, user_id=owner)).snapshot == saved.snapshot
        with pytest.raises(PrivacyDenied, match="stale"):
            await reopened.get_event(event_id, user_id=owner)
    finally:
        await reopened.close()


async def test_AP08_disclosure_rechecks_after_await(tmp_path, monkeypatch):
    engine, r, p = await open_engine(tmp_path)
    owner = str(p.owner_id)
    event_id = await engine.store(SOURCE, user_id=owner)
    nodes = await engine.get_event_nodes(event_id, user_id=owner)
    original = engine._graph_store.get_node

    async def changed(*args, **kwargs):
        node = await original(*args, **kwargs)
        r.authority.denied.add("disclosure")
        return node

    monkeypatch.setattr(engine._graph_store, "get_node", changed)
    try:
        with pytest.raises(PrivacyDenied):
            await engine.get_node(str(nodes[0].id), user_id=owner)
    finally:
        await engine.close()


async def test_processing_deadline_is_enforced_using_fake_clock(tmp_path, monkeypatch):
    engine, r, p = await open_engine(tmp_path)
    owner = str(p.owner_id)
    monkeypatch.setattr(engine._pipeline, "queue_extraction", lambda *args: None)
    event_id = await engine.ingest(SOURCE, user_id=owner)
    saved = await engine._privacy.repository.get(event_id, user_id=owner)
    r.clock.now = saved.snapshot.processing_deadline + timedelta(seconds=1)
    try:
        await engine.process_extractions(user_id=owner)
        assert r.extractor.sends == [] and not r.embedder.sends
        assert await engine._graph_store.query_nodes(user_id=owner) == []
    finally:
        await engine.close()


def test_generated_discard_logs_have_no_rejected_values(monkeypatch):
    import structlog
    from structlog.testing import LogCapture
    from prme.ingestion import grounding
    from prme.ingestion.schema import ExtractionResult
    captured = LogCapture()
    monkeypatch.setattr(grounding, "logger", structlog.wrap_logger(
        None, processors=[captured], wrapper_class=structlog.BoundLogger,
    ))
    sentinel = "private-generated-sentinel"
    result = ExtractionResult.model_validate({"entities": [{"name": sentinel, "entity_type": "person"}],
                                               "facts": [], "relationships": []})
    checked = grounding.validate_grounding(result, "safe source", content_free_logs=True)
    assert not checked.entities
    assert captured.entries == [{"event": "grounding_entity_discarded", "log_level": "warning"}]
    assert sentinel not in str(captured.entries)
