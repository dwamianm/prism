"""Every organize job is user-scoped and loses no information (issue #518).

Each job runs against a store built from the member, chat and defect probe
conversations on both backends. A scoped run must leave another owner's graph
byte for byte unchanged, and a run that retires or merges nodes must keep every
fact, its evidence and its provenance reachable. Promotion is checked through
both the explicit job and the automatic maintenance pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from prme.config import OrganizerConfig
from prme.organizer.jobs import DEFAULT_JOBS
from prme.storage.engine import MemoryEngine
from prme.types import LifecycleState, NodeType
from tests.organizer_job_safety_support import (
    PROBE_CORPORA,
    RETIRING_JOBS,
    assert_no_loss,
    assert_unchanged,
    graph_snapshot,
    prepare_for_job,
)
from tests.test_durable_ingestion import config, user  # noqa: F401

JOB_NAMES = tuple(DEFAULT_JOBS)
EXPECTS_CHANGE = RETIRING_JOBS | {"promote", "summarize", "index_compaction"}


@pytest.mark.parametrize("job", JOB_NAMES)
async def test_a_scoped_job_leaves_another_owner_unchanged(config, user, job):  # noqa: F811
    owner, foreign = f"{user}-owner", f"{user}-foreign"
    async with MemoryEngine.open(config) as engine:
        await prepare_for_job(engine, owner, job, corpora=("defects-v1",))
        await prepare_for_job(engine, foreign, job, corpora=("defects-v1",))
        before = await graph_snapshot(engine, foreign)

        result = await engine.organize(user_id=owner, jobs=[job], budget_ms=60000)

        assert job in result.jobs_run, result
        assert result.per_job[job].errors == 0, result.per_job[job]
        assert_unchanged(before, await graph_snapshot(engine, foreign), job, foreign)


@pytest.mark.parametrize("corpus", PROBE_CORPORA)
@pytest.mark.parametrize("job", JOB_NAMES)
async def test_a_job_keeps_every_fact_evidence_and_provenance(config, user, job, corpus):  # noqa: F811
    owner = f"{user}-{corpus}-{job}"
    async with MemoryEngine.open(config) as engine:
        await prepare_for_job(engine, owner, job, corpora=(corpus,))
        before = await graph_snapshot(engine, owner)

        result = await engine.organize(user_id=owner, jobs=[job], budget_ms=60000)

        assert job in result.jobs_run, result
        job_result = result.per_job[job]
        assert job_result.errors == 0, job_result
        assert_no_loss(before, await graph_snapshot(engine, owner), job)
        if job == "snapshot_generation":
            # The snapshots are read-only views, so nodes_modified is not a
            # mutation signal; the job must still have generated one.
            assert job_result.nodes_processed > 0, job_result
        elif job in EXPECTS_CHANGE:
            assert job_result.nodes_modified > 0, (job, job_result)


async def _aged_tentative_node(engine, owner: str, content: str):
    from prme.models.nodes import MemoryNode

    node = MemoryNode(
        content=content,
        user_id=owner,
        node_type=NodeType.FACT,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    await engine._graph_store.create_node(node)
    return node


@pytest.mark.parametrize("backend", ["duckdb", "postgres"])
async def test_promotion_runs_from_explicit_organize(tmp_path, backend, monkeypatch):
    engine = await _open_backend(tmp_path, backend, monkeypatch, opportunistic=False)
    owner = f"promote-explicit-{backend}-{uuid4().hex[:8]}"
    try:
        node = await _aged_tentative_node(engine, owner, "aged explicit fact")
        await _attach_evidence(engine, node)
        result = await engine.organize(user_id=owner, jobs=["promote"], budget_ms=5000)
        assert result.per_job["promote"].nodes_modified >= 1, result
        stored = await engine.get_node(str(node.id), user_id=owner, include_superseded=True)
        assert stored.lifecycle_state == LifecycleState.STABLE
    finally:
        await engine.close()


@pytest.mark.parametrize("backend", ["duckdb", "postgres"])
async def test_promotion_runs_from_the_automatic_pass(tmp_path, backend, monkeypatch):
    engine = await _open_backend(tmp_path, backend, monkeypatch, opportunistic=True)
    owner = f"promote-auto-{backend}-{uuid4().hex[:8]}"
    try:
        node = await _aged_tentative_node(engine, owner, "aged automatic fact")
        await _attach_evidence(engine, node)
        maintenance = await engine._maintenance_runner.maybe_run(user_id=owner)
        assert maintenance is not None
        assert maintenance.nodes_promoted >= 1, maintenance
        stored = await engine.get_node(str(node.id), user_id=owner, include_superseded=True)
        assert stored.lifecycle_state == LifecycleState.STABLE
    finally:
        await engine.close()


@pytest.mark.parametrize("backend", ["duckdb", "postgres"])
async def test_promotion_runs_from_the_scheduled_request_path(tmp_path, backend, monkeypatch):
    """retrieve() launches the pass through schedule(); drain() finishes it."""
    engine = await _open_backend(tmp_path, backend, monkeypatch, opportunistic=True)
    owner = f"promote-scheduled-{backend}-{uuid4().hex[:8]}"
    try:
        node = await _aged_tentative_node(engine, owner, "aged scheduled fact")
        await _attach_evidence(engine, node)
        await engine.retrieve("no memory matches this query", user_id=owner)
        await engine._maintenance_runner.drain()
        stored = await engine.get_node(str(node.id), user_id=owner, include_superseded=True)
        assert stored.lifecycle_state == LifecycleState.STABLE
    finally:
        await engine.close()


async def _attach_evidence(engine, node) -> None:
    """Promotion needs the configured evidence count; a direct node starts empty."""
    await engine._graph_store.update_node(str(node.id), evidence_refs=[uuid4()])


async def _open_backend(tmp_path, backend, monkeypatch, *, opportunistic):
    import os

    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr(
        "prme.storage.engine.create_embedding_provider",
        lambda _: MockEmbeddingProvider(),
    )
    organizer = OrganizerConfig(
        opportunistic_enabled=opportunistic,
        opportunistic_budget_ms=5000,
        opportunistic_batch_size=50,
    )
    args = {
        "db_path": str(tmp_path / "memory.duckdb"),
        "vector_path": str(tmp_path / "vectors.usearch"),
        "lexical_path": str(tmp_path / "lexical"),
        "organizer": organizer,
        "materialization_budget_ms": 5000,
    }
    (tmp_path / "lexical").mkdir(exist_ok=True)
    if backend == "postgres":
        url = os.environ.get("PRME_TEST_DATABASE_URL")
        if not url:
            pytest.skip("PRME_TEST_DATABASE_URL not set")
        args.pop("db_path")
        args.pop("vector_path")
        args.pop("lexical_path")
        args["database_url"] = url
    from prme.config import PRMEConfig

    return await MemoryEngine.create(PRMEConfig(**args))


async def test_index_compaction_retries_failed_evictions_on_both_backends(
    config, user, monkeypatch,  # noqa: F811
):
    owner = f"{user}-compaction"
    async with MemoryEngine.open(config) as engine:
        await engine.store(
            "a note whose eviction fails", user_id=owner, node_type=NodeType.NOTE,
        )
        node = (await engine.query_nodes(user_id=owner))[0]

        original = engine._delete_from_indexes

        async def fail(node_id):
            raise RuntimeError("authored eviction failure")

        monkeypatch.setattr(engine, "_delete_from_indexes", fail)
        await engine.archive(str(node.id), user_id=owner)
        monkeypatch.setattr(engine, "_delete_from_indexes", original)
        if getattr(engine, "_conn", None) is None:
            # A node that still exists has no lexical_documents row on
            # PostgreSQL; insert one so the lexical half is observable.
            async with engine._graph_store._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO lexical_documents (node_id, content, user_id, node_type) "
                    "VALUES ($1, $2, $3, $4)",
                    str(node.id), "stale lexical", owner, "note",
                )

        assert await _index_entry_present(engine, str(node.id)), "no stale entry to reconcile"

        result = await engine.organize(
            user_id=owner, jobs=["index_compaction"], budget_ms=5000,
        )
        job_result = result.per_job["index_compaction"]
        assert job_result.errors == 0, job_result
        assert await _index_entry_present(engine, str(node.id)) is False, job_result

        # The unscoped branch reconciles the same stale entries.
        await engine.store(
            "a second note whose eviction fails", user_id=owner, node_type=NodeType.NOTE,
        )
        second = (await engine.query_nodes(
            user_id=owner, content_contains_any=["second note"], limit=10,
        ))[0]
        monkeypatch.setattr(engine, "_delete_from_indexes", fail)
        await engine.archive(str(second.id), user_id=owner)
        monkeypatch.setattr(engine, "_delete_from_indexes", original)
        assert await _index_entry_present(engine, str(second.id))
        result = await engine.organize(jobs=["index_compaction"], budget_ms=5000)
        job_result = result.per_job["index_compaction"]
        assert job_result.errors == 0, job_result
        assert await _index_entry_present(engine, str(second.id)) is False, job_result


async def _index_entry_present(engine, node_id: str) -> bool:
    if getattr(engine, "_conn", None) is not None:
        row = engine._conn.execute(
            "SELECT COUNT(*) FROM vector_metadata WHERE node_id = ?", [node_id],
        ).fetchone()
        return bool(row and row[0])
    async with engine._graph_store._pool.acquire() as conn:
        embedding = await conn.fetchval(
            "SELECT embedding IS NOT NULL FROM nodes WHERE id = $1", node_id,
        )
        lexical = await conn.fetchval(
            "SELECT COUNT(*) FROM lexical_documents WHERE node_id = $1", node_id,
        )
    return bool(embedding or lexical)
