"""A scoped organize stays inside one protected owner's namespace (issue #518).

Both owners hold records under their own privacy policy in the same protected
store. One full scheduled pass for the first owner must not change, merge or
link anything the second owner holds, on DuckDB and on PostgreSQL.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from prme.organizer.jobs import DEFAULT_JOBS
from prme.types import LifecycleState, NodeType
from tests.organizer_job_safety_support import (
    RETIRING_JOBS,
    age_owner_nodes,
    assert_unchanged,
    graph_snapshot,
)
from tests.privacy_fakes import (  # noqa: F401
    isolated_privacy_postgres,
    make_runtime,
    open_engine,
    policy_for,
)


@pytest.fixture(params=["directory", "postgres"], name="layout")
def protected_layout(request):
    if request.param == "postgres" and not os.environ.get("PRME_TEST_DATABASE_URL"):
        pytest.skip("PRME_TEST_DATABASE_URL not set")
    return request.param


async def _store(engine, owner, policy, content, **kwargs):
    return await engine.store(
        content, user_id=owner, policy=policy, retrieval_content=content, **kwargs,
    )


async def _seed_protected_owner(engine, owner, policy) -> None:
    await _store(engine, owner, policy, "Mara likes tea.", node_type=NodeType.FACT)
    await _store(engine, owner, policy, "PostgreSQL", node_type=NodeType.ENTITY)
    await _store(engine, owner, policy, "postgres", node_type=NodeType.ENTITY)
    await _store(engine, owner, policy, "expired note", node_type=NodeType.NOTE, ttl_days=0)
    for _ in range(3):
        await _store(engine, owner, policy, "the telescope is blue", node_type=NodeType.NOTE)
    for index in range(5):
        await _store(engine, owner, policy, f"daily note {index}", node_type=NodeType.NOTE)


async def test_one_full_pass_leaves_another_protected_owner_unchanged(layout):
    runtime = make_runtime()
    # Protected summarize and consolidate admit generated output against the
    # ingress snapshot; the component double only echoes it on request.
    runtime.sanitizer.echo_snapshot = True
    first_policy = policy_for(runtime)
    second_policy = policy_for(runtime)
    first, second = str(first_policy.owner_id), str(second_policy.owner_id)

    with tempfile.TemporaryDirectory(prefix="prme_pv_518_") as directory:
        engine, runtime, _ = await open_engine(
            Path(directory) / "pack",
            runtime=runtime,
            policy=first_policy,
            layout=layout,
        )
        # Each owner stores under an explicit policy, so no single default
        # policy may stand in for the whole store.
        engine._privacy.default = None
        try:
            await _seed_protected_owner(engine, first, first_policy)
            await _seed_protected_owner(engine, second, second_policy)
            await age_owner_nodes(engine, first)
            await age_owner_nodes(engine, second)
            before = await graph_snapshot(engine, second)
            before_states = {
                str(node.id): node.lifecycle_state
                for node in await engine.query_nodes(
                    user_id=second, lifecycle_states=list(LifecycleState),
                )
            }

            result = await engine.organize(
                user_id=first, jobs=list(DEFAULT_JOBS), budget_ms=120000,
            )

            assert set(result.jobs_run) == set(DEFAULT_JOBS), result.jobs_skipped
            for job, job_result in result.per_job.items():
                if job in DEFAULT_JOBS:
                    assert job_result.errors == 0, (job, job_result)
            # snapshot_generation counts ephemeral snapshots as modified, so
            # only a job that retires or merges proves the owner changed.
            modified = sum(
                job.nodes_modified
                for name, job in result.per_job.items()
                if name in RETIRING_JOBS
            )
            assert modified > 0, "the scoped pass did not change its own owner"
            assert_unchanged(before, await graph_snapshot(engine, second), "organize", second)
            after_states = {
                str(node.id): node.lifecycle_state
                for node in await engine.query_nodes(
                    user_id=second, lifecycle_states=list(LifecycleState),
                )
            }
            assert after_states == before_states
        finally:
            await engine.close()
