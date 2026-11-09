"""Incremental organizer watermarks on PostgreSQL (dwamianm/prism#517).

Requires PRME_TEST_DATABASE_URL. Mirrors the DuckDB coverage: the watermark
store, the keyset scan, an incremental run that converges, tenant isolation,
and the nothing-new path over 10,000 nodes finishing under one second.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from prme.config import OrganizerConfig, PRMEConfig
from prme.organizer.watermarks import INCREMENTAL_JOBS, scope_key
from prme.storage.engine import MemoryEngine
from prme.types import LifecycleState, NodeType, Scope

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("PRME_TEST_DATABASE_URL"),
        reason="PRME_TEST_DATABASE_URL not set",
    ),
]


@pytest.fixture
async def pg_pool():
    import asyncpg

    pool = await asyncpg.create_pool(os.environ["PRME_TEST_DATABASE_URL"], min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def graph_store(pg_pool):
    from prme.storage.pg.graph_store import PgGraphStore
    from prme.storage.pg.schema import initialize_pg_database

    await initialize_pg_database(pg_pool)
    return PgGraphStore(pg_pool)


@pytest.fixture
async def pg_engine():
    config = PRMEConfig(
        database_url=os.environ["PRME_TEST_DATABASE_URL"],
        organizer=OrganizerConfig(
            incremental_enabled=True,
            opportunistic_enabled=False,
        ),
    )
    engine = await MemoryEngine.create(config)
    yield engine
    await engine.close()


async def test_watermark_round_trip(graph_store):
    scope = scope_key(f"scope-{uuid4().hex[:8]}")
    assert await graph_store.get_organizer_watermark(scope_key=scope, job="deduplicate") is None

    completed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    await graph_store.set_organizer_watermark(
        scope_key=scope, job="deduplicate", completed_through=completed,
    )
    row = await graph_store.get_organizer_watermark(scope_key=scope, job="deduplicate")
    assert row.completed_through == completed
    assert row.resume_cursor is None

    resume_at = datetime(2026, 1, 2, 3, 5, 0, tzinfo=timezone.utc)
    resume_id = str(uuid4())
    await graph_store.set_organizer_watermark(
        scope_key=scope,
        job="deduplicate",
        completed_through=completed,
        resume_updated_at=resume_at,
        resume_id=resume_id,
    )
    row = await graph_store.get_organizer_watermark(scope_key=scope, job="deduplicate")
    assert row.resume_cursor == (resume_at, resume_id)


async def test_scan_changed_nodes_windows_and_keyset(graph_store):
    from prme.models.nodes import MemoryNode

    owner = f"user-{uuid4().hex[:8]}"
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    ids = []
    for index in range(4):
        node = MemoryNode(
            user_id=owner,
            node_type=NodeType.FACT,
            scope=Scope.PERSONAL,
            content=f"changed {index}",
            created_at=base + timedelta(minutes=index),
            updated_at=base + timedelta(minutes=index),
        )
        ids.append(await graph_store.create_node(node))

    changed = await graph_store.scan_changed_nodes(
        user_id=owner,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        updated_after=base,
        updated_at_or_before=base + timedelta(minutes=2),
    )
    assert [str(node.id) for node in changed] == ids[1:3]

    # The keyset cursor is exclusive on (updated_at, id).
    page = await graph_store.scan_changed_nodes(
        user_id=owner,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        updated_after=base + timedelta(minutes=1),
        after_id=ids[1],
        updated_at_or_before=base + timedelta(minutes=5),
        limit=1,
    )
    assert [str(node.id) for node in page] == [ids[2]]


async def _store_many(engine, contents, *, user_id="pg-incremental-user"):
    for content in contents:
        await engine.store(content, user_id=user_id, node_type=NodeType.FACT, scope=Scope.PERSONAL)


async def test_incremental_deduplicate_run_converges(pg_engine):
    user = f"pg-incremental-{uuid4().hex[:8]}"
    valid_from = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for content in ("same", "same"):
        await pg_engine.store(
            content, user_id=user, node_type=NodeType.FACT,
            scope=Scope.PERSONAL, valid_from=valid_from,
        )

    first = await pg_engine.organize(user_id=user, jobs=["deduplicate"])
    assert first.per_job["deduplicate"].nodes_modified == 1

    second = await pg_engine.organize(user_id=user, jobs=["deduplicate"])
    assert second.per_job["deduplicate"].nodes_modified == 0

    nodes = await pg_engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState))
    assert sorted(node.lifecycle_state.value for node in nodes) == ["superseded", "tentative"]


async def test_scoped_incremental_scan_excludes_another_owner(pg_engine):
    valid_from = datetime(2025, 1, 1, tzinfo=timezone.utc)
    owner_a = f"pg-owner-a-{uuid4().hex[:8]}"
    owner_b = f"pg-owner-b-{uuid4().hex[:8]}"
    for user in (owner_a, owner_b):
        for content in ("shared", "shared"):
            await pg_engine.store(
                content, user_id=user, node_type=NodeType.FACT,
                scope=Scope.PERSONAL, valid_from=valid_from,
            )

    result = await pg_engine.organize(user_id=owner_a, jobs=["deduplicate"])
    assert result.per_job["deduplicate"].nodes_modified == 1

    other = await pg_engine.query_nodes(user_id=owner_b, lifecycle_states=list(LifecycleState))
    assert sorted(node.lifecycle_state.value for node in other) == ["tentative", "tentative"]


async def test_nothing_new_over_ten_thousand_nodes_under_one_second(pg_engine):
    user = f"pg-10k-{uuid4().hex[:8]}"
    past = datetime.now(timezone.utc) - timedelta(days=1)
    async with pg_engine._graph_store._pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO nodes (id, node_type, user_id, scope, content, lifecycle_state, "
            "created_at, updated_at, valid_from, last_reinforced_at) "
            "SELECT gen_random_uuid(), 'fact', $1, 'personal', 'memory ' || i, 'stable', "
            "$2, $2, $2, $2 FROM generate_series(1, 10000) i",
            user, past,
        )

    # A completed run writes its ceiling less the 60-second overlap; these
    # nodes are a day old, so a scope with no writes since has nothing to do.
    for job in INCREMENTAL_JOBS:
        await pg_engine.set_organizer_watermark(
            scope_key=scope_key(user), job=job,
            completed_through=datetime.now(timezone.utc) - timedelta(seconds=61),
        )

    started = time.monotonic()
    result = await pg_engine.organize(user_id=user, jobs=sorted(INCREMENTAL_JOBS), budget_ms=5000)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"incremental organize took {elapsed:.3f}s"
    for job in INCREMENTAL_JOBS:
        assert result.per_job[job].nodes_processed == 0
        assert result.per_job[job].nodes_modified == 0
