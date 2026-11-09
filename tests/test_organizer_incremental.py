"""Incremental organizer runs, watermarks and resumable budget stops (#517).

Three properties are checked on the converted job:

* a second run over an unchanged scope changes no memory;
* a run stopped by the budget records where it stopped and the next run
  resumes there instead of starting over;
* an incremental run and a full run end with the same store on the same input.

The option is off by default, so a test also checks that a default
``organize()`` writes no watermark and behaves as before.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from prme.config import OrganizerConfig, PRMEConfig
from prme.organizer import jobs as jobs_module
from prme.organizer.jobs import run_job
from prme.organizer.watermarks import INCREMENTAL_JOBS, scope_key
from prme.storage.engine import MemoryEngine
from prme.types import LifecycleState, NodeType, Scope


USER = "incremental-user"
FIXED_VALID_FROM = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def engine():
    with tempfile.TemporaryDirectory(prefix="prme_incremental_") as d:
        tmp = Path(d)
        lexical_path = tmp / "lexical_index"
        lexical_path.mkdir()
        eng = await MemoryEngine.create(PRMEConfig(
            db_path=str(tmp / "memory.duckdb"),
            vector_path=str(tmp / "vectors.usearch"),
            lexical_path=str(lexical_path),
            organizer=OrganizerConfig(
                incremental_enabled=True,
                opportunistic_enabled=False,
            ),
        ))
        yield eng
        await eng.close()


@pytest_asyncio.fixture
async def default_engine():
    """An engine whose organizer keeps every job full-scope (the default)."""
    with tempfile.TemporaryDirectory(prefix="prme_incremental_default_") as d:
        tmp = Path(d)
        lexical_path = tmp / "lexical_index"
        lexical_path.mkdir()
        eng = await MemoryEngine.create(PRMEConfig(
            db_path=str(tmp / "memory.duckdb"),
            vector_path=str(tmp / "vectors.usearch"),
            lexical_path=str(lexical_path),
            organizer=OrganizerConfig(opportunistic_enabled=False),
        ))
        yield eng
        await eng.close()


async def _store_many(engine, contents, *, user_id=USER, valid_from=None):
    ids = []
    for content in contents:
        ids.append(await engine.store(
            content,
            user_id=user_id,
            node_type=NodeType.FACT,
            scope=Scope.PERSONAL,
            valid_from=valid_from,
        ))
    return ids


async def _all_states(engine, user_id: str = USER):
    nodes = await engine.query_nodes(
        user_id=user_id, lifecycle_states=list(LifecycleState), limit=1000,
    )
    return {str(node.id): node.lifecycle_state for node in nodes}


async def _watermark_rows(engine):
    return engine._conn.execute(
        "SELECT scope_key, job, completed_through, resume_updated_at, resume_id "
        "FROM organizer_watermarks ORDER BY scope_key, job"
    ).fetchall()


class TestWatermarkStore:
    @pytest.mark.asyncio
    async def test_round_trip_and_resume_cursor(self, engine):
        scoped = scope_key(USER)
        assert await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate") is None

        completed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=completed,
        )
        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row is not None
        assert row.completed_through == completed
        assert row.resume_cursor is None

        resume_at = datetime(2026, 1, 2, 3, 5, 0, tzinfo=timezone.utc)
        resume_id = "11111111-1111-1111-1111-111111111111"
        await engine.set_organizer_watermark(
            scope_key=scoped,
            job="deduplicate",
            completed_through=completed,
            resume_updated_at=resume_at,
            resume_id=resume_id,
        )
        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row.resume_cursor == (resume_at, resume_id)
        assert row.completed_through == completed

        # A later completed run clears the cursor.
        later = datetime(2026, 1, 3, tzinfo=timezone.utc)
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=later,
        )
        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row.completed_through == later
        assert row.resume_cursor is None

    @pytest.mark.asyncio
    async def test_scope_keys_keep_runs_apart(self, engine):
        await engine.set_organizer_watermark(
            scope_key=scope_key("alice"), job="deduplicate",
            completed_through=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        await engine.set_organizer_watermark(
            scope_key=scope_key("bob"), job="deduplicate",
            completed_through=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        await engine.set_organizer_watermark(
            scope_key=scope_key(None), job="deduplicate",
            completed_through=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )

        alice = await engine.get_organizer_watermark(scope_key=scope_key("alice"), job="deduplicate")
        bob = await engine.get_organizer_watermark(scope_key=scope_key("bob"), job="deduplicate")
        unscoped = await engine.get_organizer_watermark(scope_key=scope_key(None), job="deduplicate")
        assert alice.completed_through == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert bob.completed_through == datetime(2026, 1, 2, tzinfo=timezone.utc)
        assert unscoped.completed_through == datetime(2026, 1, 3, tzinfo=timezone.utc)
        assert len(await _watermark_rows(engine)) == 3

    def test_empty_user_id_does_not_collide_with_unscoped(self):
        assert scope_key(None) == ""
        assert scope_key("") != scope_key(None)
        assert scope_key("") == "u:"
        assert scope_key("alice") != scope_key("bob")


class TestScanChangedNodes:
    @pytest.mark.asyncio
    async def test_window_and_keyset_tie_break(self, engine):
        from prme.models.nodes import MemoryNode

        owner = "scan-user"
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
            ids.append(str(await engine._graph_store.create_node(node)))

        changed = await engine.scan_changed_nodes(
            user_id=owner,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            updated_after=base,
            updated_at_or_before=base + timedelta(minutes=2),
        )
        assert [str(node.id) for node in changed] == ids[1:3]

        page = await engine.scan_changed_nodes(
            user_id=owner,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            updated_after=base + timedelta(minutes=1),
            after_id=ids[1],
            updated_at_or_before=base + timedelta(minutes=5),
            limit=1,
        )
        assert [str(node.id) for node in page] == [ids[2]]

    @pytest.mark.asyncio
    async def test_keyset_pages_across_an_identical_timestamp(self, engine):
        from prme.models.nodes import MemoryNode

        owner = "tie-user"
        stamp = datetime(2025, 6, 1, tzinfo=timezone.utc)
        ids = []
        for index in range(3):
            node = MemoryNode(
                user_id=owner,
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
                content=f"tie {index}",
                created_at=stamp,
                updated_at=stamp,
            )
            ids.append(str(await engine._graph_store.create_node(node)))

        window = dict(
            user_id=owner,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            updated_after=stamp - timedelta(seconds=1),
            updated_at_or_before=stamp + timedelta(seconds=1),
        )
        ordered = await engine.scan_changed_nodes(**window, limit=10)
        assert sorted(str(node.id) for node in ordered) == sorted(ids)

        # The cursor sits on the tie, so only the id tie-break can advance.
        rest = await engine.scan_changed_nodes(
            user_id=owner,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            updated_after=stamp,
            after_id=str(ordered[0].id),
            updated_at_or_before=stamp + timedelta(seconds=1),
            limit=10,
        )
        assert [str(node.id) for node in rest] == [
            str(node.id) for node in ordered[1:]
        ]
        assert str(ordered[0].id) not in {str(node.id) for node in rest}

    @pytest.mark.asyncio
    async def test_no_lower_bound_returns_the_window(self, engine):
        """An absent watermark must mean "no lower bound", not "nothing"."""
        await _store_many(engine, ["alpha", "beta"])

        nodes = await engine.scan_changed_nodes(
            user_id=USER,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            updated_after=None,
            updated_at_or_before=datetime.now(timezone.utc),
        )
        assert len(nodes) == 2

    @pytest.mark.asyncio
    async def test_protected_view_honors_an_absent_lower_bound(self, engine):
        """The admission facade must agree with the SQL stores (#517 review)."""
        from prme.privacy.read_graph import AdmittedGraphView

        class _Context:
            user_id = USER

            async def project_node(self, node):
                return node

            async def disclose(self, value):
                return value

            async def check(self):
                return None

        await _store_many(engine, ["alpha", "beta"])
        view = AdmittedGraphView(engine._graph_store, _Context())
        nodes = await view.scan_changed_nodes(
            user_id=USER,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            updated_after=None,
            updated_at_or_before=datetime.now(timezone.utc),
        )
        assert len(nodes) == 2


class TestConvergence:
    @pytest.mark.asyncio
    async def test_second_deduplicate_run_changes_nothing(self, engine):
        await _store_many(engine, ["same", "same"], valid_from=FIXED_VALID_FROM)

        first = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert first.per_job["deduplicate"].nodes_modified == 1

        second = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert second.per_job["deduplicate"].nodes_modified == 0

        states = await _all_states(engine)
        assert sorted(state.value for state in states.values()) == ["superseded", "tentative"]

    @pytest.mark.asyncio
    async def test_multi_page_scan_advances_and_completes(self, engine, monkeypatch):
        monkeypatch.setattr(jobs_module, "_INCREMENTAL_PAGE_SIZE", 2)
        await _store_many(engine, [f"memory {i}" for i in range(5)])

        result = await engine.organize(user_id=USER, jobs=["deduplicate"])
        job = result.per_job["deduplicate"]
        assert job.status == "completed"
        assert job.nodes_processed == 5


class TestResume:
    @pytest.mark.asyncio
    async def test_deduplicate_budget_stop_does_not_advance_past_unsearched_nodes(
        self, engine, monkeypatch,
    ):
        contents = [f"memory {i}" for i in range(8)]
        await _store_many(engine, contents)
        monkeypatch.setattr(jobs_module, "_INCREMENTAL_PAGE_SIZE", 2)

        index = engine._vector_index
        original_search = index.search

        async def slow_search(*args, **kwargs):
            await asyncio.sleep(0.02)
            return await original_search(*args, **kwargs)

        monkeypatch.setattr(index, "search", slow_search)

        stopped = await engine.organize(user_id=USER, jobs=["deduplicate"], budget_ms=200)
        job = stopped.per_job["deduplicate"]
        assert job.status == "budget_exhausted"
        # Only the prefix whose neighbor search finished counts as examined.
        assert 0 < job.nodes_processed < len(contents)

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        )
        assert watermark.resume_cursor is not None

        resumed = await engine.organize(user_id=USER, jobs=["deduplicate"], budget_ms=5000)
        assert resumed.per_job["deduplicate"].status == "completed"
        assert resumed.per_job["deduplicate"].nodes_modified == 0

        states = await _all_states(engine)
        assert set(states.values()) == {LifecycleState.TENTATIVE}

    @pytest.mark.asyncio
    async def test_failed_page_does_not_move_the_cursor_past_it(self, engine, monkeypatch):
        """Page 1 succeeds, page 2's first search fails, page 3 must not skip it."""
        contents = [f"memory {i}" for i in range(6)]
        await _store_many(engine, contents)
        monkeypatch.setattr(jobs_module, "_INCREMENTAL_PAGE_SIZE", 2)

        nodes_by_content = {
            node.content: node
            for node in await engine.query_nodes(
                user_id=USER, lifecycle_states=list(LifecycleState), limit=1000,
            )
        }
        index = engine._vector_index
        original_search = index.search
        poison = nodes_by_content["memory 2"].content

        async def failing_search(content, *args, **kwargs):
            if content == poison:
                raise RuntimeError("embedding rejected")
            return await original_search(content, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(index, "search", failing_search)
            stopped = await engine.organize(user_id=USER, jobs=["deduplicate"], budget_ms=5000)

        job = stopped.per_job["deduplicate"]
        assert job.status == "error"
        assert job.errors >= 1

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        )
        assert watermark.resume_cursor is not None
        _, resume_id = watermark.resume_cursor
        # The cursor stops at the last node of the successful page, not past
        # the failed one.
        assert resume_id == str(nodes_by_content["memory 1"].id)

        retried = await engine.organize(user_id=USER, jobs=["deduplicate"], budget_ms=5000)
        assert retried.per_job["deduplicate"].status == "completed"


class TestFailure:
    @pytest.mark.asyncio
    async def test_failed_scan_does_not_advance_the_watermark(self, engine, monkeypatch):
        prior = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await engine.set_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate", completed_through=prior,
        )

        async def broken_scan(**kwargs):
            raise RuntimeError("scan failed")

        monkeypatch.setattr(engine, "scan_changed_nodes", broken_scan)

        result = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert result.per_job["deduplicate"].status == "error"

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        )
        assert watermark.completed_through == prior
        assert watermark.resume_cursor is None

    @pytest.mark.asyncio
    async def test_failed_neighbor_search_is_reported_and_retried(self, engine, monkeypatch):
        await _store_many(engine, ["alpha", "beta", "gamma"])
        index = engine._vector_index

        async def broken_search(*args, **kwargs):
            raise RuntimeError("embedding rejected")

        with monkeypatch.context() as patch:
            patch.setattr(index, "search", broken_search)
            result = await engine.organize(user_id=USER, jobs=["deduplicate"])

        job = result.per_job["deduplicate"]
        assert job.status == "error"
        assert job.errors >= 1
        assert job.nodes_processed == 0

        retried = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert retried.per_job["deduplicate"].status == "completed"
        assert retried.per_job["deduplicate"].nodes_processed == 3


class TestEquivalence:
    @pytest.mark.asyncio
    async def test_incremental_and_full_deduplicate_agree(self, tmp_path):
        full = await _fresh_engine(tmp_path / "full")
        try:
            await _store_many(full, ["alpha", "beta", "alpha", "beta"], valid_from=FIXED_VALID_FROM)
            await full.organize(user_id=USER, jobs=["deduplicate"])
            full_snapshot = await _content_states(full)
        finally:
            await full.close()

        incremental = await _fresh_engine(tmp_path / "incremental")
        try:
            await _store_many(incremental, ["alpha", "beta"], valid_from=FIXED_VALID_FROM)
            await incremental.organize(user_id=USER, jobs=["deduplicate"])
            await _store_many(incremental, ["alpha", "beta"], valid_from=FIXED_VALID_FROM)
            await incremental.organize(user_id=USER, jobs=["deduplicate"])
            incremental_snapshot = await _content_states(incremental)
        finally:
            await incremental.close()

        assert full_snapshot == incremental_snapshot
        assert [state for _content, state in full_snapshot].count(
            LifecycleState.TENTATIVE.value
        ) == 2


class TestTenancy:
    @pytest.mark.asyncio
    async def test_scoped_incremental_scan_excludes_another_owner(self, engine):
        await _store_many(engine, ["shared", "shared"], valid_from=FIXED_VALID_FROM)
        await _store_many(
            engine, ["shared", "shared"], user_id="other-user", valid_from=FIXED_VALID_FROM,
        )

        result = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert result.per_job["deduplicate"].nodes_modified == 1

        assert sorted((await _all_states(engine)).values()).count(LifecycleState.TENTATIVE) == 1
        other = await _all_states(engine, "other-user")
        assert sorted(other.values()) == [LifecycleState.TENTATIVE, LifecycleState.TENTATIVE]


class TestDefaultUnchanged:
    @pytest.mark.asyncio
    async def test_default_organize_writes_no_watermark(self, default_engine):
        await _store_many(default_engine, ["same", "same"], valid_from=FIXED_VALID_FROM)

        result = await default_engine.organize(user_id=USER, jobs=["deduplicate"])

        assert result.per_job["deduplicate"].nodes_modified == 1
        assert await _watermark_rows(default_engine) == []

    @pytest.mark.asyncio
    async def test_default_run_job_result_fields_are_additive(self, default_engine):
        """The new reporting fields keep their completed defaults."""
        await _store_many(default_engine, ["alpha"])
        result = await run_job(
            "deduplicate", default_engine, default_engine._config.organizer, 5000, USER,
        )
        assert result.status == "completed"
        assert result.stopped_on_budget is False


class TestProtectedReporting:
    def test_projected_result_keeps_the_typed_outcome(self):
        """A protected store must not read a stopped run as completed."""
        from prme.organizer.models import JobResult, OrganizeResult
        from prme.privacy.diagnostics import project_organize

        result = OrganizeResult(
            jobs_run=["deduplicate"],
            per_job={"deduplicate": JobResult(
                job="deduplicate", status="budget_exhausted", stopped_on_budget=True,
            )},
        )
        projected = project_organize(result)
        assert projected.per_job["deduplicate"].status == "budget_exhausted"
        assert projected.per_job["deduplicate"].stopped_on_budget is True


class TestNothingNewIsFast:
    @pytest.mark.asyncio
    async def test_incremental_organize_over_ten_thousand_nodes_under_one_second(self, engine):
        user = "ten-thousand"
        past = datetime.now(timezone.utc) - timedelta(days=1)
        # Bulk insert instead of 10,000 store() calls: this test measures the
        # organizer's scan, not the write path.
        engine._conn.execute(
            "INSERT INTO nodes (id, node_type, user_id, scope, content, "
            "lifecycle_state, created_at, updated_at, valid_from, last_reinforced_at) "
            "SELECT uuid(), 'fact', ?, 'personal', 'memory ' || i, 'stable', ?, ?, ?, ? "
            "FROM range(1, 10001) t(i)",
            [user, past, past, past, past],
        )

        # Simulate a completed run: it writes its ceiling less the 60-second
        # overlap, and the scope has had no writes since. Ten thousand nodes
        # written inside that overlap would legitimately be re-examined.
        for job in INCREMENTAL_JOBS:
            await engine.set_organizer_watermark(
                scope_key=scope_key(user), job=job,
                completed_through=datetime.now(timezone.utc) - timedelta(seconds=61),
            )

        started = time.monotonic()
        result = await engine.organize(
            user_id=user, jobs=sorted(INCREMENTAL_JOBS), budget_ms=5000,
        )
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, f"incremental organize took {elapsed:.3f}s"
        for job in INCREMENTAL_JOBS:
            assert result.per_job[job].nodes_processed == 0
            assert result.per_job[job].nodes_modified == 0
            assert result.per_job[job].status == "completed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fresh_engine(directory: Path) -> MemoryEngine:
    directory.mkdir(parents=True, exist_ok=True)
    lexical_path = directory / "lexical_index"
    lexical_path.mkdir()
    return await MemoryEngine.create(PRMEConfig(
        db_path=str(directory / "memory.duckdb"),
        vector_path=str(directory / "vectors.usearch"),
        lexical_path=str(lexical_path),
        organizer=OrganizerConfig(
            incremental_enabled=True,
            opportunistic_enabled=False,
        ),
    ))


async def _content_states(engine: MemoryEngine) -> list[tuple[str, str]]:
    nodes = await engine.query_nodes(
        user_id=USER, lifecycle_states=list(LifecycleState), limit=1000,
    )
    return sorted((node.content, node.lifecycle_state.value) for node in nodes)
