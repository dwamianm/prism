"""AP08 and AC3: authoritative denies at real effects, no timing sleeps."""

import asyncio
from datetime import timedelta
import os
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy import PrivacyDenied
from tests.privacy_fakes import config_for, fresh_postgres_store, make_runtime, open_engine, policy_for
from tests.privacy_fakes import isolated_privacy_postgres as isolated_privacy_postgres

SOURCE = "Alex does not like tea unless it is cold."


async def plain_store_config(tmp_path, layout, **updates):
    """A separate store without a privacy runtime, where plain memories live.

    A protected store holds only protected records (#326), so the legacy
    controls these tests keep now run in their own store beside it.
    """
    if layout == "postgres":
        await fresh_postgres_store()
    return config_for(tmp_path / "plain", layout=layout).model_copy(update=updates)


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
@pytest.mark.parametrize("operation", ["store", "entity", "ingest", "ingest_fast"])
@pytest.mark.parametrize("writer", ["protected_owner", "other_owner"])
async def test_protected_store_refuses_plain_writes(tmp_path, layout, operation, writer):
    """Replaces the mixed-store races: a plain write never enters a protected store.

    The removed group-link, reingest and shared-batch race tests raced a plain
    write or a plain reingest against a protected admission in one store. Under
    #326 that plain data cannot be written, for the protected owner or anyone.
    """
    from prme.types import NodeType

    r = make_runtime()
    p = policy_for(r)
    engine = await MemoryEngine.create(config_for(tmp_path, layout=layout),
                                      embedding_provider=r.embedder, privacy_runtime=r)
    try:
        await engine.store(SOURCE, user_id=str(p.owner_id), policy=p)
        writer_id = str(p.owner_id) if writer == "protected_owner" else str(uuid4())
        calls = {
            "store": lambda: engine.store("Jordan and I prefer tea.", user_id=writer_id),
            "entity": lambda: engine.store("Jordan", user_id=writer_id, node_type=NodeType.ENTITY,
                                           metadata={"entity_type": "person"}),
            "ingest": lambda: engine.ingest("We prefer tea.", user_id=writer_id, wait_for_extraction=True),
            "ingest_fast": lambda: engine.ingest_fast("We prefer tea.", user_id=writer_id),
        }
        events = await engine._event_store.get_by_user(writer_id)
        nodes = await engine._graph_store.query_nodes(user_id=writer_id)
        sends, extracts = len(r.embedder.sends), len(r.extractor.sends)
        with pytest.raises(PrivacyDenied, match="privacy_invalid_result"):
            await calls[operation]()
        assert len(r.embedder.sends) == sends and len(r.extractor.sends) == extracts
        assert await engine._event_store.get_by_user(writer_id) == events
        assert await engine._graph_store.query_nodes(user_id=writer_id) == nodes
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
@pytest.mark.parametrize("case", ["valid", "source_owner", "target_scope", "provenance_owner",
                                 "named_in_scope", "missing_provenance", "protected_boundary"])
async def test_group_edge_native_boundary(tmp_path, layout, case):
    from prme.models import MemoryEdge
    from prme.types import EdgeType, Scope

    r = make_runtime()
    p = policy_for(r)
    owner, other = str(p.owner_id), str(uuid4())
    if case == "protected_boundary":
        # Protected records never take the legacy group-edge path (#326).
        engine = await MemoryEngine.create(config_for(tmp_path, layout=layout),
                                          embedding_provider=r.embedder, privacy_runtime=r)
        protection = {"policy": p}
    else:
        # Legacy group edges exist only in a plain store, beside any protected one.
        engine = await MemoryEngine.create(await plain_store_config(tmp_path, layout), embedding_provider=r.embedder)
        protection = {}
    try:
        source_owner = other if case == "source_owner" else owner
        source_event = await engine.store("We prefer tea.", user_id=source_owner, **protection)
        [source] = await engine.get_event_nodes(source_event, user_id=source_owner)
        target_event = await engine.store("Jordan", user_id=owner, **protection,
                                         scope=Scope.PROJECT if case == "target_scope" else Scope.PERSONAL)
        [target] = await engine.get_event_nodes(target_event, user_id=owner)
        provenance = await engine.store("We prefer tea.", user_id=other if case == "provenance_owner" else owner,
                                        **protection)
        named_in = await engine.store("Jordan and I prefer tea.", user_id=owner, **protection,
                                     scope=Scope.PROJECT if case == "named_in_scope" else Scope.PERSONAL)
        edge = MemoryEdge(source_id=source.id, target_id=target.id, edge_type=EdgeType.MENTIONS,
                          user_id=owner, provenance_event_id=uuid4() if case == "missing_provenance" else UUID(provenance),
                          metadata={"basis": "group_member", "named_in": named_in})

        async def publish():
            return await engine._privacy.repository.create_legacy_group_edge(
                engine._graph_store, edge, user_id=owner, scope=Scope.PERSONAL)

        # Exercise the native boundary without the outer group read preflight.
        if case == "valid":
            assert await engine._write_queue.submit(publish, label="privacy.group_link:test") == str(edge.id)
            assert await engine._graph_store.get_edges(source_id=str(source.id)) == [edge]
        else:
            with pytest.raises(PrivacyDenied):
                await engine._write_queue.submit(publish, label="privacy.group_link:test")
            assert await engine._graph_store.get_edges(source_id=str(source.id)) == []
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
@pytest.mark.parametrize("case", ["other_owner", "other_scope"])
async def test_group_links_keep_other_owner_and_scope_legacy_controls(tmp_path, monkeypatch, layout, case):
    from prme.ingestion import group_links
    from prme.ingestion.owner_name import OWNER_NAME_KEY, owner_name_marker
    from prme.ingestion.schema import ExtractionResult
    from prme.types import NodeType, Scope

    r = make_runtime()
    owner, session = str(uuid4()), str(uuid4())
    p = policy_for(r, owner=UUID(owner) if case == "other_scope" else uuid4(),
                   scope=Scope.PROJECT if case == "other_scope" else Scope.PERSONAL)
    protected = await MemoryEngine.create(config_for(tmp_path, layout=layout),
                                         embedding_provider=r.embedder, privacy_runtime=r)
    # The plain owner's group links run in their own store (#326), unchanged
    # by the protected owner or scope in the store beside it.
    engine = await MemoryEngine.create(await plain_store_config(tmp_path, layout, enable_owner_group_links=True),
                                      embedding_provider=r.embedder)

    async def extract(*args, **kwargs):
        return ExtractionResult.model_validate({"entities": [], "relationships": [],
            "facts": [{"subject": "We", "predicate": "prefer", "object": "tea", "evidence_quote": "We prefer tea."}]})

    monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", extract)
    try:
        await protected.store(SOURCE, user_id=str(p.owner_id), scope=p.scope, policy=p)
        targets = []
        for name in ("Alex", "Jordan"):
            metadata = {"entity_type": "person"}
            if name == "Alex":
                metadata[OWNER_NAME_KEY] = owner_name_marker(str(uuid4()), "self_introduction")
            event_id = await engine.store(name, user_id=owner, session_id=session,
                                          node_type=NodeType.ENTITY, metadata=metadata)
            [node] = await engine._graph_store.query_nodes(user_id=owner, content_contains_any=[name],
                                                         scopes=[Scope.PERSONAL])
            targets.append(node.id)
        await engine.store("Jordan and I prefer tea.", user_id=owner, session_id=session)
        event_id = await engine.ingest("We prefer tea.", user_id=owner, session_id=session, wait_for_extraction=True)
        links = [edge for edge in await engine._graph_store.get_edges()
                 if edge.user_id == owner and group_links.is_group_link(edge)]
        assert {edge.target_id for edge in links} == set(targets)
        assert all(str(edge.provenance_event_id) == event_id for edge in links)
    finally:
        await engine.close()
        await protected.close()


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
    # A protected store holds no plain source (#326), so the reopened case
    # starts from the protected source alone.
    event_id = await engine.store(SOURCE, user_id=owner, policy=p)
    event = await engine._event_store.get(event_id)
    saved = await engine._privacy.repository.get(event_id, user_id=owner)
    assert event.content == "" and saved.evidence is None
    # Completed no-evidence sources must not disappear into a zero-work result.
    if reopened:
        await engine.close()
        r.clock.now = saved.snapshot.processing_deadline + timedelta(seconds=1)
        # A protected store refuses to open without its privacy runtime (#325, #326),
        # before any provider call. The reopened refusals below run with it.
        sends = list(r.embedder.sends)
        with pytest.raises(PrivacyDenied, match="privacy_capability_unavailable"):
            await MemoryEngine.create(config, embedding_provider=r.embedder)
        assert r.embedder.sends == sends
        engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
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


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres"])
async def test_reingest_legacy_controls_keep_exact_owner_and_scope(tmp_path, layout):
    from prme.types import Scope
    from tests.test_reingest import FOUND, LAKE, Scripted, install, settings

    r = make_runtime()
    p = policy_for(r)
    owner, other = str(p.owner_id), str(uuid4())
    protected = await MemoryEngine.create(settings(config_for(tmp_path, layout=layout)),
                                         embedding_provider=r.embedder, privacy_runtime=r)
    # The same owner's plain sources live in a separate plain store (#326).
    engine = await MemoryEngine.create(settings(await plain_store_config(tmp_path, layout)),
                                      embedding_provider=r.embedder)
    try:
        await protected.store(SOURCE, user_id=owner, policy=p)
        await engine.store(LAKE, user_id=owner, scope=Scope.PROJECT)
        await engine.store(LAKE, user_id=other)
        model = install(engine, Scripted(FOUND))
        assert (await engine.reingest(owner, scope=Scope.PROJECT)).processed == 1
        assert (await engine.reingest(other)).processed == 1
        assert model.calls == [[LAKE], [LAKE]]
        with pytest.raises(PrivacyDenied):
            await protected.reingest(owner)
    finally:
        await engine.close()
        await protected.close()


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
    # Legacy objects, including the protected owner's own plain memories, live
    # in a separate plain store (#326); their unscoped calls stay unchanged.
    plain = await MemoryEngine.create(await plain_store_config(tmp_path, layout), embedding_provider=r.embedder)
    try:
        protected = await engine.store(SOURCE, user_id=str(p.owner_id), policy=p)
        protected_node = (await engine.get_event_nodes(protected, user_id=str(p.owner_id)))[0]
        legacy = await plain.store("Legacy memory", user_id="legacy-other-owner")
        legacy_node = (await plain.get_event_nodes(legacy, user_id="legacy-other-owner"))[0]
        same_owner = await plain.store("Same owner's legacy memory", user_id=str(p.owner_id))
        same_node = (await plain.get_event_nodes(same_owner, user_id=str(p.owner_id)))[0]
        assert (await plain.get_event(legacy)).content == "Legacy memory"
        assert (await plain.get_node(str(legacy_node.id))).content == "Legacy memory"
        assert (await plain.get_event(same_owner)).content == "Same owner's legacy memory"
        assert (await plain.get_node(str(same_node.id), user_id=str(p.owner_id))).content == "Same owner's legacy memory"
        await plain.archive(str(legacy_node.id))
        await plain.archive(str(same_node.id))
        assert await engine.get_event(protected, user_id="legacy-other-owner") is None
        assert await engine.get_node(str(protected_node.id), user_id="legacy-other-owner") is None
        for call in (lambda: engine.get_event(protected), lambda: engine.get_node(str(protected_node.id)),
                     lambda: engine.archive(str(protected_node.id)), lambda: engine.query_nodes(),
                     lambda: engine.get_node(str(uuid4()))):
            with pytest.raises(PrivacyDenied, match="boundary_mismatch"):
                await call()
    finally:
        await plain.close()
        await engine.close()


@pytest.mark.parametrize("evidence_kind", ["missing", "malformed", "foreign", "scope", "protected"])
@pytest.mark.parametrize("layout", ["directory", "postgres"])
async def test_legacy_reinforcement_evidence_uses_primary_boundary(tmp_path, evidence_kind, layout):
    from prme.types import Scope
    r = make_runtime()
    p = policy_for(r)
    owner = str(p.owner_id)
    engine = await MemoryEngine.create(config_for(tmp_path, layout=layout), embedding_provider=r.embedder, privacy_runtime=r)
    # The legacy node and its plain evidence live in a separate plain store
    # (#326). Evidence from another owner, scope or the protected store is not
    # in the node's owner and scope there.
    plain = await MemoryEngine.create(await plain_store_config(tmp_path, layout), embedding_provider=r.embedder)
    try:
        protected = await engine.store(SOURCE, user_id=owner, policy=p)
        legacy = await plain.store("Legacy memory", user_id=owner)
        [node] = await plain.get_event_nodes(legacy, user_id=owner)
        evidence = {"missing": str(uuid4()), "malformed": "not-a-uuid", "protected": protected}.get(evidence_kind)
        if evidence_kind in {"foreign", "scope"}:
            evidence = await plain.store("Other evidence", user_id=str(uuid4()) if evidence_kind == "foreign" else owner,
                                         scope=Scope.PROJECT if evidence_kind == "scope" else Scope.PERSONAL)
        with pytest.raises(ValueError, match="Evidence event not found in the node's owner and scope"):
            await plain.reinforce(str(node.id), evidence_id=evidence)
        assert await plain.get_node(str(node.id)) == node
        with pytest.raises(PrivacyDenied, match="privacy_boundary_mismatch"):
            await engine.reinforce(str(uuid4()), evidence_id=protected)
        [protected_node] = await engine.get_event_nodes(protected, user_id=owner)
        with pytest.raises(PrivacyDenied, match="privacy_boundary_mismatch"):
            await engine.reinforce(str(protected_node.id), evidence_id="not-a-uuid")
    finally:
        await plain.close()
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
