"""A canonical memory must publish evidence, relationships and retirement together."""

import pytest
import asyncio

from prme import MemoryEngine
from prme.organizer.alias_resolution import AliasCandidate, resolve_aliases
from prme.organizer.deduplication import DuplicateCandidate, merge_duplicates
from tests import test_durable_ingestion as fixtures
from tests.test_merge_edge_preservation import seed

config = fixtures.config
user = fixtures.user


async def snapshot(engine, user):
    nodes = await engine.query_nodes(user_id=user, lifecycle_states=None)
    return ({str(n.id): n.model_dump() for n in nodes},
            {str(e.id): e.model_dump() for e in await engine._graph_store.get_edges(node_ids=[str(n.id) for n in nodes])})


async def apply(engine, keep, duplicate, alias):
    if alias:
        return await resolve_aliases(engine, [AliasCandidate(str(keep.id), str(duplicate.id), "abbreviation", .99)])
    return await merge_duplicates(engine, [DuplicateCandidate(str(keep.id), str(duplicate.id), 1., "exact")])


@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("position", [1, 2, 3])
async def test_failed_public_merge_publishes_no_partial_state(config, user, alias, position, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, edges = await seed(engine, user, alias)
        await engine._graph_store.update_node(str(duplicate.id), evidence_refs=[edges[0].provenance_event_id])
        before = await snapshot(engine, user)
        graph = engine._graph_store
        calls = 0
        if hasattr(graph, "_conn"):
            original = graph._create_edge_sync
            def failing(edge):
                nonlocal calls
                calls += 1
                if calls == position:
                    raise RuntimeError("Authored transaction fault")
                return original(edge)
            target = "_create_edge_sync"
        else:
            original = graph._create_edge_on_connection
            async def failing(conn, edge):
                nonlocal calls
                calls += 1
                if calls == position:
                    raise RuntimeError("Authored transaction fault")
                return await original(conn, edge)
            target = "_create_edge_on_connection"
        with monkeypatch.context() as patch:
            patch.setattr(graph, target, failing)
            assert await apply(engine, keep, duplicate, alias) == 0
        assert calls == position
        assert await snapshot(engine, user) == before
    async with MemoryEngine.open(config) as engine:
        assert await snapshot(engine, user) == before
        assert await apply(engine, keep, duplicate, alias) == 1


@pytest.mark.parametrize("alias", [False, True])
async def test_success_creates_one_replacement_relationship(config, user, alias):
    from prme.types import EdgeType
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user, alias)
        assert await apply(engine, keep, duplicate, alias) == 1
        links = await engine._graph_store.get_edges(source_id=str(keep.id), target_id=str(duplicate.id), edge_type=EdgeType.SUPERSEDES)
        assert len(links) == 1


@pytest.mark.parametrize("stage", ["evidence", "retirement", "journal"])
async def test_failure_at_commit_stages_rolls_back_every_mutation(config, user, stage, monkeypatch):
    from prme.storage import organizer_merge
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, edges = await seed(engine, user)
        await engine._graph_store.update_node(str(duplicate.id), evidence_refs=[edges[0].provenance_event_id])
        before = await snapshot(engine, user)
        def fail(actual):
            if actual == stage:
                raise RuntimeError("Authored transaction-stage fault")
        with monkeypatch.context() as patch:
            patch.setattr(organizer_merge, "_checkpoint", fail)
            assert await apply(engine, keep, duplicate, False) == 0
        assert await snapshot(engine, user) == before
        result = await engine._graph_store.merge_nodes(str(keep.id), str(duplicate.id), user_id=user, kind="duplicate", score=1.)
        assert result.applied


async def test_committed_retry_after_restart_does_not_reactivate_retired_memory(config, user):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        first = await engine._graph_store.merge_nodes(str(keep.id), str(duplicate.id), user_id=user, kind="duplicate", score=1.)
        assert first.applied
        await engine.archive(str(keep.id), user_id=user)
        before = await snapshot(engine, user)
    async with MemoryEngine.open(config) as engine:
        again = await engine._graph_store.merge_nodes(str(duplicate.id), str(keep.id), user_id=user, kind="duplicate", score=1.)
        assert not again.applied and again.operation_id == first.operation_id
        assert await snapshot(engine, user) == before
        assert await engine._graph_store.merge_nodes(str(keep.id), str(duplicate.id), user_id=user+"-foreign", kind="duplicate", score=1.) is None


async def test_merge_revalidates_provenance_after_candidate_selection(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        original = engine._graph_store.merge_nodes
        async def change_then_merge(*args, **kwargs):
            await engine._graph_store.update_node(str(duplicate.id), metadata={"subject": "someone else"})
            return await original(*args, **kwargs)
        monkeypatch.setattr(engine._graph_store, "merge_nodes", change_then_merge)
        assert await apply(engine, keep, duplicate, False) == 0
        from prme.types import LifecycleState
        assert (await engine.get_node(str(duplicate.id))).lifecycle_state == LifecycleState.TENTATIVE


async def test_overlapping_merges_preserve_all_evidence(config, user):
    from prme.models import MemoryNode
    from uuid import uuid4
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        third = MemoryNode(content=duplicate.content, user_id=user, node_type=duplicate.node_type,
                           confidence_base=.5, evidence_refs=[uuid4()])
        await engine._graph_store.create_node(third)
        await engine._graph_store.update_node(str(duplicate.id), evidence_refs=[uuid4()])
        original = await engine.query_nodes(user_id=user)
        expected = {ref for node in original for ref in node.evidence_refs}
        async with MemoryEngine.open(config) as second:
            await asyncio.gather(apply(engine, keep, duplicate, False), apply(second, keep, third, False))
            # A conflicting DuckDB transaction may fail safely; retry unfinished
            # work against fresh durable state rather than losing its evidence.
            await apply(engine, keep, duplicate, False)
            await apply(second, keep, third, False)
        assert set((await engine.get_node(str(keep.id))).evidence_refs) == expected


@pytest.mark.parametrize("stage", ["evidence", "edge", "retirement", "journal", "committed"])
async def test_process_exit_exposes_only_complete_merge(config, user, stage):
    import json
    import subprocess
    import sys
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, edges = await seed(engine, user)
        await engine._graph_store.update_node(str(duplicate.id), evidence_refs=[edges[0].provenance_event_id])
        before = await snapshot(engine, user)
    script = '''
import asyncio, json, os, sys
from prme import MemoryEngine, PRMEConfig
from tests.test_durable_ingestion import MockEmbeddingProvider
import prme.storage.engine
from prme.storage import organizer_merge
prme.storage.engine.create_embedding_provider = lambda _: MockEmbeddingProvider()
async def main():
    values = json.loads(sys.argv[1])
    if sys.argv[6] == "postgres":
        values["database_url"] = os.environ["PRME_TEST_DATABASE_URL"]
    engine = await MemoryEngine.create(PRMEConfig.model_validate(values))
    def crash(stage):
        if stage == sys.argv[5]:
            os._exit(42)
    organizer_merge._checkpoint = crash
    result = await engine._graph_store.merge_nodes(sys.argv[3], sys.argv[4], user_id=sys.argv[2], kind="duplicate", score=1.)
    if sys.argv[5] == "committed" and result.applied:
        os._exit(42)
    raise AssertionError("Requested crash point was not reached")
asyncio.run(main())
'''
    values = config.model_dump(mode="json")
    values["database_url"] = None
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script,
        json.dumps(values), user, str(keep.id), str(duplicate.id), stage, config.backend], capture_output=True, text=True, timeout=30)
    assert result.returncode == 42, result.stderr
    async with MemoryEngine.open(config) as engine:
        if stage != "committed":
            assert await snapshot(engine, user) == before
        again = await engine._graph_store.merge_nodes(str(keep.id), str(duplicate.id), user_id=user, kind="duplicate", score=1.)
        assert again.applied == (stage != "committed")


async def test_journal_retains_exact_inputs_and_outputs(config, user):
    import hashlib
    import json
    from prme.storage.organizer_merge import MergeRecord
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        original = {str(n.id): n for n in await engine.query_nodes(user_id=user)}
        result = await engine._graph_store.merge_nodes(str(keep.id), str(duplicate.id), user_id=user, kind="duplicate", score=1.)
        if engine._conn is not None:
            raw = engine._conn.execute("SELECT payload FROM operations WHERE id=? AND actor_id=?", [result.operation_id, user]).fetchone()[0]
        else:
            async with engine._graph_store._pool.acquire() as conn:
                raw = await conn.fetchval("SELECT payload FROM operations WHERE id=$1 AND actor_id=$2", result.operation_id, user)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        assert hashlib.sha256(payload["record"].encode()).hexdigest() == payload["sha256"]
        record = MergeRecord.model_validate_json(payload["record"])
        assert record.canonical_before == original[str(keep.id)]
        assert record.retired_before == original[str(duplicate.id)]
        assert record.canonical_after == await engine.get_node(str(keep.id), user_id=user)
        assert record.retired_after == await engine.get_node(str(duplicate.id), user_id=user, include_superseded=True)
        actual = {e.id: e for e in await engine._graph_store.get_edges(node_ids=[str(keep.id), str(duplicate.id)])}
        assert all(actual[e.id] == e for e in record.original_edges + record.published_edges)


async def test_external_cancellation_has_one_durable_outcome(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        before = await snapshot(engine, user)
        graph = engine._graph_store
        if engine._conn is not None:
            import threading
            from prme.storage import organizer_merge
            entered, release = threading.Event(), threading.Event()
            def pause(stage):
                if stage == "evidence":
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("Authored cancellation synchronization timed out")
            with monkeypatch.context() as patch:
                patch.setattr(organizer_merge, "_checkpoint", pause)
                task = asyncio.create_task(apply(engine, keep, duplicate, False))
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    task.cancel()
                    await asyncio.sleep(0)
                    assert not task.done()
                finally:
                    release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            committed = True
        else:
            entered = asyncio.Event()
            create = graph._create_edge_on_connection
            async def pause(conn, edge):
                await create(conn, edge)
                entered.set()
                await asyncio.Event().wait()
            with monkeypatch.context() as patch:
                patch.setattr(graph, "_create_edge_on_connection", pause)
                task = asyncio.create_task(apply(engine, keep, duplicate, False))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                finally:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            committed = False
        if not committed:
            assert await snapshot(engine, user) == before
        retry = await graph.merge_nodes(str(keep.id), str(duplicate.id), user_id=user, kind="duplicate", score=1.)
        assert retry.applied == (not committed)


@pytest.mark.parametrize("difference", ["owner", "scope", "self"])
async def test_direct_transaction_preserves_isolation(config, user, difference):
    from prme.models import MemoryNode
    from prme.types import NodeType, Scope
    async with MemoryEngine.open(config) as engine:
        first = MemoryNode(content="Aster", user_id=user, node_type=NodeType.ENTITY)
        second = MemoryNode(content="Aster", user_id=user+"-other" if difference == "owner" else user,
                            scope=Scope.PROJECT if difference == "scope" else Scope.PERSONAL, node_type=NodeType.ENTITY)
        for node in (first, second):
            await engine._graph_store.create_node(node)
        before = [await engine.get_node(str(node.id)) for node in (first, second)]
        result = await engine._graph_store.merge_nodes(str(first.id), str(first.id if difference == "self" else second.id),
                                                       user_id=user, kind="duplicate", score=1.)
        assert result is None
        assert [await engine.get_node(str(node.id)) for node in (first, second)] == before
        assert await engine._graph_store.get_edges(node_ids=[str(n.id) for n in (first, second)]) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_journal_must_not_silently_convert_non_finite_metadata_to_null(value):
    import json
    import math
    from prme.models import MemoryNode
    from prme.storage import _snapshot_json
    from prme.storage.organizer_merge import MergeRecord, _payload, _replayed
    from prme.types import NodeType
    from uuid import uuid4
    keep = MemoryNode(content="Aster", node_type=NodeType.ENTITY, user_id="authored")
    retired = keep.model_copy(update={"id": uuid4()})
    record = MergeRecord(operation_id=str(uuid4()), kind="duplicate", user_id="authored", score=1.,
        canonical_before=keep, retired_before=retired, canonical_after=keep, retired_after=retired,
        original_edges=(), published_edges=())
    record.canonical_before.metadata = {"diagnostic_score": value}
    payload = _payload(record)
    restored = MergeRecord.model_validate(_snapshot_json.loads(json.loads(payload)["record"]))
    actual = restored.canonical_before.metadata["diagnostic_score"]
    assert math.isnan(actual) if math.isnan(value) else actual == value
    result = _replayed(payload, record.operation_id, sorted([str(keep.id), str(retired.id)]), record.user_id, record.kind)
    assert result.applied is False


async def test_existing_compact_json_journals_keep_their_identity(config, user, monkeypatch):
    import hashlib
    import json
    from prme.storage import organizer_merge
    def legacy_payload(record):
        raw = record.model_dump_json()
        return json.dumps({"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()})
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        with monkeypatch.context() as patch:
            patch.setattr(organizer_merge, "_payload", legacy_payload)
            original = await engine._graph_store.merge_nodes(str(keep.id), str(duplicate.id), user_id=user, kind="duplicate", score=1.)
    async with MemoryEngine.open(config) as engine:
        repeated = await engine._graph_store.merge_nodes(str(keep.id), str(duplicate.id), user_id=user, kind="duplicate", score=1.)
        assert not repeated.applied and repeated.operation_id == original.operation_id
