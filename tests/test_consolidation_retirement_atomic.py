"""Consolidation must recheck retirement eligibility inside its commit boundary."""
from datetime import datetime, timezone

import pytest

from prme import MemoryEngine
from prme.types import LifecycleState
from prme.organizer.consolidation import consolidate_cluster, forget_consolidated
from tests.test_consolidation_safety import _cluster
from tests.test_durable_ingestion import config, user  # noqa: F401


@pytest.mark.parametrize('change', ['summary_archive', 'summary_coverage', 'source_event_time', 'source_pin', 'source_confidence'])
async def test_change_after_preflight_preserves_source(config, user, monkeypatch, change):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        # Target just one fully represented source. Inject at the storage API
        # boundary, after the old caller's preflight and before native locks.
        cluster.member_ids = [str(nodes[0].id)]
        store = engine._graph_store
        method = 'retire_consolidated' if hasattr(store, 'retire_consolidated') else 'supersede'
        original = getattr(store, method)
        injected = False
        async def race(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                if change == 'summary_archive':
                    await store.archive(str(summary.id))
                elif change == 'summary_coverage':
                    await store.update_node(str(summary.id), metadata={})
                elif change == 'source_event_time':
                    await store.update_node(str(nodes[0].id), event_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
                elif change == 'source_pin':
                    await store.update_node(str(nodes[0].id), pinned=True)
                else:
                    await store.update_node(str(nodes[0].id), confidence=.95)
            return await original(*args, **kwargs)
        monkeypatch.setattr(store, method, race)
        retired = await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0)
        assert injected
        assert retired == 0
        source = await engine.get_node(str(nodes[0].id), include_superseded=True)
        assert source.lifecycle_state == LifecycleState.TENTATIVE
        assert source.superseded_by is None


async def _records(engine, owner):
    sql = "SELECT payload FROM operations WHERE op_type='CONSOLIDATION_RETIRED' AND actor_id={}"
    if engine._pool is not None:
        async with engine._pool.acquire() as conn:
            return [r[0] for r in await conn.fetch(sql.format('$1'), owner)]
    async with engine._event_store._conn_lock:
        return [r[0] for r in engine._conn.execute(sql.format('?'), [owner]).fetchall()]


@pytest.mark.parametrize('stage', ['validated', 'mutated', 'journaled'])
async def test_failure_rolls_back_source_edge_and_journal(config, user, monkeypatch, stage):  # noqa: F811
    from prme.storage import consolidation_retirement as retirement
    from prme.types import EdgeType
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        cluster.member_ids = [str(nodes[0].id)]
        def fail(current):
            if current == stage:
                raise RuntimeError('injected retirement transaction failure')
        with monkeypatch.context() as patch:
            patch.setattr(retirement, '_checkpoint', fail)
            with pytest.raises(RuntimeError, match='injected retirement'):
                await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0)
        source = await engine.get_node(str(nodes[0].id), include_superseded=True)
        assert source.lifecycle_state == LifecycleState.TENTATIVE
        assert source.superseded_by is None
        assert not await engine._graph_store.get_edges(target_id=str(source.id), edge_type=EdgeType.SUPERSEDES)
        assert await _records(engine, user) == []
        assert await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0) == 1
        record = retirement.read_record((await _records(engine, user))[0])
        assert record.before.id == source.id
        assert record.summary.id == summary.id
        assert record.after.lifecycle_state == LifecycleState.SUPERSEDED


async def test_concurrent_retirements_commit_once_and_survive_restart(config, user):  # noqa: F811
    import asyncio
    from prme.storage.consolidation_retirement import read_record
    from prme.types import EdgeType
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        cluster.member_ids = [str(nodes[0].id)]
        results = await asyncio.gather(*(forget_consolidated(engine, cluster, str(summary.id), user_id=user,
                                                            preserve_recent_days=0) for _ in range(6)))
        assert sum(results) == 1
        records = await _records(engine, user)
        assert len(records) == 1
        assert read_record(records[0]).after.id == nodes[0].id
    async with MemoryEngine.open(config) as engine:
        assert await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0) == 0
        assert len(await _records(engine, user)) == 1
        edges = await engine._graph_store.get_edges(target_id=str(nodes[0].id), edge_type=EdgeType.SUPERSEDES)
        assert len(edges) == 1
        assert (await engine.get_node(str(nodes[0].id), include_superseded=True)).superseded_by == summary.id


@pytest.mark.parametrize('change', ['summary_archive', 'source_pin', 'summary_coverage'])
async def test_duck_summary_write_conflicts_while_retirement_holds_snapshot(config, user, monkeypatch, change):  # noqa: F811
    import asyncio
    import threading
    import duckdb
    from prme.storage import consolidation_retirement as retirement
    if config.database_url:
        pytest.skip('DuckDB no-op row claim behavior; PostgreSQL uses explicit row locks')
    async with MemoryEngine.open(config) as engine, MemoryEngine.open(config) as other:
        cluster, nodes = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        cluster.member_ids = [str(nodes[0].id)]
        entered, release = threading.Event(), threading.Event()
        def pause(stage):
            if stage == 'validated':
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('test release missing')
        monkeypatch.setattr(retirement, '_checkpoint', pause)
        task = asyncio.create_task(forget_consolidated(engine, cluster, str(summary.id), user_id=user,
                                                       preserve_recent_days=0))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(duckdb.TransactionException):
                if change == 'summary_archive':
                    await other.archive(str(summary.id), user_id=user)
                elif change == 'source_pin':
                    await other._graph_store.update_node(str(nodes[0].id), pinned=True)
                else:
                    await other._graph_store.update_node(str(summary.id), metadata={})
        finally:
            release.set()
            result = await task
        assert result == 1
        assert (await engine.get_node(str(summary.id))).lifecycle_state == LifecycleState.TENTATIVE


async def test_reopen_removes_legacy_lifecycle_index_without_changing_nodes(config, user):  # noqa: F811
    import duckdb
    if config.database_url:
        pytest.skip('DuckDB pack index migration')
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store('A source surviving index migration', user_id=user)
        original = (await engine.get_event_nodes(event_id, user_id=user))[0]
    with duckdb.connect(config.db_path) as conn:
        conn.execute('CREATE INDEX idx_nodes_lifecycle ON nodes(lifecycle_state)')
        assert conn.execute("SELECT count(*) FROM duckdb_indexes() WHERE index_name='idx_nodes_lifecycle'").fetchone()[0] == 1
    async with MemoryEngine.open(config) as engine:
        assert engine._conn.execute("SELECT count(*) FROM duckdb_indexes() WHERE index_name='idx_nodes_lifecycle'").fetchone()[0] == 0
        current = await engine.get_node(str(original.id))
        assert current.model_dump(mode='json') == original.model_dump(mode='json')
        assert (await engine.get_event(event_id, user_id=user)).content == original.content


async def test_new_source_evidence_is_not_discarded_by_old_summary(config, user):  # noqa: F811
    from uuid import UUID
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        cluster.member_ids = [str(nodes[0].id)]
        extra = await engine.store('New evidence for deployment rule', user_id=user, scope=nodes[0].scope)
        await engine._graph_store.update_node(str(nodes[0].id), evidence_refs=[*nodes[0].evidence_refs, UUID(extra)])
        assert await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0) == 0
        assert (await engine.get_node(str(nodes[0].id))).lifecycle_state == LifecycleState.TENTATIVE
        assert await _records(engine, user) == []
