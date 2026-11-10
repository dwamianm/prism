"""Incremental organizer runs, watermarks and resumable budget stops (#517, #523).

Three properties are checked on each converted job, deduplicate and
alias_resolve:

* a second run over an unchanged scope changes no memory;
* a run stopped by the budget records where it stopped and the next run
  resumes there instead of starting over;
* an incremental run and a full run end with the same store on the same input
  for a pair whose two endpoints are both in the full job's newest-100 page.

The option is off by default, so a test also checks that a default
``organize()`` writes no watermark and behaves as before. Later sections cover
the four cases that could leave memories unexamined for good
(dwamianm/prism#545) and the watermark store's migration for an older pack.
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
from prme.models.nodes import MemoryNode
from prme.organizer import jobs as jobs_module
from prme.organizer.jobs import run_job
from prme.organizer.watermarks import INCREMENTAL_JOBS, scope_key
from prme.storage.engine import MemoryEngine
from prme.types import EdgeType, LifecycleState, NodeType, Scope


USER = "incremental-user"
FIXED_VALID_FROM = datetime(2025, 1, 1, tzinfo=timezone.utc)

# Four memories the default embedding provider clusters at the similarity
# threshold the engine fixture pins for the consolidation tests.
CONSOLIDATION_CONTENTS = [
    "Python is widely used for machine learning",
    "Python is a popular language for ML projects",
    "Python is commonly used in machine learning applications",
    "Python is the top choice for ML development",
]


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
                # Pin the summarization thresholds the summarize test below
                # builds its periods with, so a later default flip does not
                # change what that test proves.
                summarization_daily_min_events=5,
                summarization_max_items_per_summary=10,
                # Pin the consolidation thresholds the consolidate tests
                # cluster with, for the same reason.
                consolidation_min_cluster_size=3,
                consolidation_similarity_threshold=0.70,
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


async def _store_entities(engine, names, *, user_id=USER):
    ids = []
    for name in names:
        ids.append(await engine.store(
            name,
            user_id=user_id,
            node_type=NodeType.ENTITY,
            scope=Scope.PERSONAL,
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
    def test_an_older_pack_gains_the_stall_columns_on_open(self, tmp_path):
        """A pack written before the stall counter keeps its rows and gains them."""
        import duckdb

        from prme.storage.schema import create_schema

        path = tmp_path / "older.duckdb"
        conn = duckdb.connect(str(path))
        try:
            conn.execute("""
                CREATE TABLE organizer_watermarks (
                    scope_key VARCHAR NOT NULL,
                    job VARCHAR NOT NULL,
                    completed_through TIMESTAMPTZ,
                    resume_updated_at TIMESTAMPTZ,
                    resume_id UUID,
                    updated_at TIMESTAMPTZ DEFAULT current_timestamp,
                    PRIMARY KEY (scope_key, job)
                )
            """)
            conn.execute(
                "INSERT INTO organizer_watermarks (scope_key, job, completed_through) "
                "VALUES ('u:alice', 'deduplicate', current_timestamp)"
            )

            create_schema(conn)

            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info('organizer_watermarks')").fetchall()
            }
            assert {"stalled_id", "stalled_attempts"} <= columns
            row = conn.execute(
                "SELECT stalled_id, stalled_attempts FROM organizer_watermarks"
            ).fetchone()
            assert row == (None, 0)
        finally:
            conn.close()

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

    def test_scope_keys_round_trip_and_keep_the_exact_owner_text(self):
        from prme.organizer.watermarks import owner_scope_keys, scope_owner

        assert scope_owner(scope_key(None)) is None
        assert scope_owner(scope_key("")) == ""
        assert scope_owner(scope_key("alice")) == "alice"
        upper = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        # Ownership matches the exact string, so the key keeps the exact text:
        # two spellings of one UUID are two owners with two watermarks.
        assert scope_key(upper) != scope_key(upper.lower())
        assert scope_owner(scope_key(upper)) == upper
        # The purge still finds every spelling, because an erasure names the
        # owner as one UUID and the rows may be keyed under any text form.
        assert set(owner_scope_keys(
            [scope_key(upper), scope_key(upper.lower()), scope_key("alice"), scope_key(None)],
            upper.lower(),
        )) == {scope_key(upper), scope_key(upper.lower())}
        assert owner_scope_keys([scope_key("alice"), ""], upper.lower()) == []
        # A key that is neither the operator scope nor a scoped key is a bug.
        with pytest.raises(ValueError):
            scope_owner("alice")

    @pytest.mark.asyncio
    async def test_completed_through_never_moves_backwards(self, engine):
        """A run that finishes last must not move the scope behind an earlier one."""
        scoped = scope_key(USER)
        later = datetime(2026, 2, 1, tzinfo=timezone.utc)
        earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=later,
        )
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=earlier,
        )

        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row.completed_through == later

    @pytest.mark.asyncio
    async def test_newer_resume_cursor_survives_an_older_stop(self, engine):
        """Two stops keep the later (updated_at, id); a completed run clears it."""
        scoped = scope_key(USER)
        completed = datetime(2026, 1, 2, tzinfo=timezone.utc)
        newer = datetime(2026, 1, 5, tzinfo=timezone.utc)
        older = datetime(2026, 1, 3, tzinfo=timezone.utc)
        newer_id = "22222222-2222-2222-2222-222222222222"
        older_id = "11111111-1111-1111-1111-111111111111"
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=completed,
            resume_updated_at=newer, resume_id=newer_id,
        )
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=completed,
            resume_updated_at=older, resume_id=older_id,
        )
        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row.resume_cursor == (newer, newer_id)

        # A completed run behind the cursor keeps it: that position was not
        # covered by the run.
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=older,
        )
        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row.resume_cursor == (newer, newer_id)

        # A completed run past the cursor clears it.
        await engine.set_organizer_watermark(
            scope_key=scoped, job="deduplicate", completed_through=datetime(2026, 1, 6, tzinfo=timezone.utc),
        )
        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row.resume_cursor is None

    @pytest.mark.asyncio
    async def test_concurrent_upserts_converge_on_the_furthest_position(self, engine):
        scoped = scope_key(USER)
        first = datetime(2026, 3, 1, tzinfo=timezone.utc)
        second = datetime(2026, 3, 2, tzinfo=timezone.utc)
        await asyncio.gather(
            engine.set_organizer_watermark(
                scope_key=scoped, job="deduplicate", completed_through=second,
            ),
            engine.set_organizer_watermark(
                scope_key=scoped, job="deduplicate", completed_through=first,
            ),
        )

        row = await engine.get_organizer_watermark(scope_key=scoped, job="deduplicate")
        assert row.completed_through == second

    @pytest.mark.asyncio
    async def test_watermark_is_not_written_for_an_erased_owner(self, engine, monkeypatch):
        """The engine refuses the scope and leaves the unscoped run alone."""
        async def erased(owner_id):
            return owner_id == USER

        monkeypatch.setattr(
            engine._privacy.erasure.repository, "owner_wide_erasure_requested", erased,
        )
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        assert await engine.set_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate", completed_through=now,
        ) is False
        assert await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        ) is None
        assert await engine.set_organizer_watermark(
            scope_key=scope_key(None), job="deduplicate", completed_through=now,
        ) is True
        assert await engine.get_organizer_watermark(
            scope_key=scope_key(None), job="deduplicate",
        ) is not None

        # The job reports why no watermark was recorded.
        result = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert result.per_job["deduplicate"].details["incremental"]["watermark"] == (
            "not_written_erased_scope"
        )
        assert await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        ) is None

    @pytest.mark.asyncio
    async def test_erasure_recorded_after_the_guard_removes_the_written_row(
        self, engine, monkeypatch,
    ):
        """A row written in the check-to-write window is deleted again."""
        calls = []

        async def erased(owner_id):
            calls.append(owner_id)
            return len(calls) > 1

        monkeypatch.setattr(
            engine._privacy.erasure.repository, "owner_wide_erasure_requested", erased,
        )

        assert await engine.set_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
            completed_through=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ) is False
        assert calls == [USER, USER]
        assert await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        ) is None


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
    async def test_query_nodes_matches_whole_content_case_insensitively(self, engine):
        await _store_entities(engine, ["JavaScript", " javascript ", "JS"])

        matches = await engine.query_nodes(
            user_id=USER,
            node_type=NodeType.ENTITY,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            content_equals_any=["javascript"],
        )
        assert sorted(node.content for node in matches) == [" javascript ", "JavaScript"]

        missing = await engine.query_nodes(
            user_id=USER,
            content_equals_any=["vaScript"],
        )
        assert missing == []

        # An empty list applies no filter, matching content_contains_any().
        unfiltered = await engine.query_nodes(user_id=USER, content_equals_any=[])
        assert sorted(node.content for node in unfiltered) == [
            " javascript ", "JS", "JavaScript",
        ]

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

    @pytest.mark.asyncio
    async def test_protected_view_matches_whole_content(self, engine):
        """The name lookup must behave the same through the admitted view."""
        from prme.privacy.read_graph import AdmittedGraphView

        class _Context:
            user_id = USER

            async def project_node(self, node):
                return node

            async def disclose(self, value):
                return value

            async def check(self):
                return None

        await _store_entities(engine, ["JavaScript", " javascript ", "JS"])
        view = AdmittedGraphView(engine._graph_store, _Context())
        nodes = await view.query_nodes(
            user_id=USER,
            node_type=NodeType.ENTITY,
            content_equals_any=["JAVASCRIPT"],
        )
        assert sorted(node.content for node in nodes) == [" javascript ", "JavaScript"]

        unfiltered = await view.query_nodes(
            user_id=USER, node_type=NodeType.ENTITY, content_equals_any=[],
        )
        assert sorted(node.content for node in unfiltered) == [
            " javascript ", "JS", "JavaScript",
        ]


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
    async def test_second_alias_resolve_run_changes_nothing(self, engine):
        await _store_entities(engine, ["JavaScript", "js", "PostgreSQL", "postgresql"])

        first = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert first.per_job["alias_resolve"].nodes_modified == 2

        second = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert second.per_job["alias_resolve"].nodes_modified == 0

        states = await _all_states(engine)
        assert sorted(states.values()).count(LifecycleState.TENTATIVE) == 2
        assert sorted(states.values()).count(LifecycleState.SUPERSEDED) == 2

    @pytest.mark.asyncio
    async def test_alias_resolve_pages_changed_entities(self, engine, monkeypatch):
        monkeypatch.setattr(jobs_module, "_INCREMENTAL_PAGE_SIZE", 2)
        names = ["JavaScript", "js", "PostgreSQL", "postgresql", "TypeScript", "ts"]
        await _store_entities(engine, names)

        result = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        job = result.per_job["alias_resolve"]
        assert job.status == "completed"
        assert job.nodes_processed == len(names)
        assert job.nodes_modified == 3

    @pytest.mark.asyncio
    async def test_alias_resolve_finds_a_twin_outside_the_window(self, engine, monkeypatch):
        """The name lookup, not the page, is what supplies the older twin."""
        monkeypatch.setattr(jobs_module, "_WATERMARK_OVERLAP", timedelta(0))

        await _store_entities(engine, ["JavaScript", "alpha"])
        first = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert first.per_job["alias_resolve"].nodes_modified == 0

        # The first run's ceiling is now the lower bound, so "JavaScript" is
        # outside the second window and only "js" is examined.
        await _store_entities(engine, ["js"])
        second = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert second.per_job["alias_resolve"].nodes_processed == 1
        assert second.per_job["alias_resolve"].nodes_modified == 1

        states = await _all_states(engine)
        assert sorted(states.values()).count(LifecycleState.SUPERSEDED) == 1

    @pytest.mark.asyncio
    async def test_alias_resolve_links_a_semantic_twin_outside_the_window(
        self, engine, monkeypatch,
    ):
        monkeypatch.setattr(jobs_module, "_WATERMARK_OVERLAP", timedelta(0))

        await _store_entities(engine, ["Beta Name"])
        await engine.organize(user_id=USER, jobs=["alias_resolve"])
        await _store_entities(engine, ["Alpha Name"])

        by_name = {
            node.content: str(node.id)
            for node in await engine.query_nodes(
                user_id=USER, lifecycle_states=list(LifecycleState), limit=100,
            )
        }
        index = engine._vector_index
        original_search = index.search

        async def semantic_search(content, *args, **kwargs):
            if content == "Alpha Name":
                return [{"node_id": by_name["Beta Name"], "score": 0.93}]
            return await original_search(content, *args, **kwargs)

        monkeypatch.setattr(index, "search", semantic_search)

        second = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert second.per_job["alias_resolve"].nodes_modified == 1

        endpoints = [by_name["Alpha Name"], by_name["Beta Name"]]
        edges = await engine._graph_store.get_edges(
            node_ids=endpoints, edge_type=EdgeType.RELATES_TO,
        )
        assert len(edges) == 1

        # A re-proposal of the same identity does not add a second link.
        monkeypatch.setattr(jobs_module, "_WATERMARK_OVERLAP", timedelta(seconds=60))
        third = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert third.per_job["alias_resolve"].nodes_modified == 0
        edges = await engine._graph_store.get_edges(
            node_ids=endpoints, edge_type=EdgeType.RELATES_TO,
        )
        assert len(edges) == 1

    @pytest.mark.asyncio
    async def test_alias_resolve_finds_a_twin_on_a_later_page(self, engine, monkeypatch):
        monkeypatch.setattr(jobs_module, "_INCREMENTAL_PAGE_SIZE", 2)
        await _store_entities(engine, ["JavaScript", "alpha", "js"])

        result = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        job = result.per_job["alias_resolve"]
        assert job.status == "completed"
        assert job.nodes_modified == 1

        states = await _all_states(engine)
        assert sorted(states.values()).count(LifecycleState.SUPERSEDED) == 1

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
    async def test_a_budget_skipped_job_still_reports_a_result(self, engine):
        """A requested job that the budget never reached still has an entry, so a reader can tell why."""
        await _store_entities(engine, ["Alpha", "Beta", "Gamma"])

        result = await engine.organize(
            user_id=USER, jobs=["alias_resolve", "deduplicate"], budget_ms=0,
        )

        assert result.jobs_skipped == ["alias_resolve", "deduplicate"]
        assert set(result.per_job) == {"alias_resolve", "deduplicate"}
        for job in result.per_job.values():
            assert job.status == "not_started"
            assert job.nodes_processed == 0

    @pytest.mark.asyncio
    async def test_alias_resolve_makes_progress_with_a_tiny_budget(self, engine, monkeypatch):
        """A budget smaller than the whole scan still leaves a cursor."""
        await _store_entities(engine, ["Alpha", "Beta", "Gamma"])
        index = engine._vector_index
        original_search = index.search

        async def slow_search(*args, **kwargs):
            await asyncio.sleep(0.02)
            return await original_search(*args, **kwargs)

        monkeypatch.setattr(index, "search", slow_search)

        result = await engine.organize(
            user_id=USER, jobs=["alias_resolve"], budget_ms=40,
        )
        job = result.per_job["alias_resolve"]
        assert job.status == "budget_exhausted"
        assert 0 < job.nodes_processed < 3

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="alias_resolve",
        )
        assert watermark.resume_cursor is not None

        resumed = await engine.organize(
            user_id=USER, jobs=["alias_resolve"], budget_ms=5000,
        )
        assert resumed.per_job["alias_resolve"].status == "completed"

    @pytest.mark.asyncio
    async def test_alias_resolve_budget_stop_resumes(self, engine, monkeypatch):
        names = [
            "JavaScript", "js", "PostgreSQL", "postgresql",
            "TypeScript", "ts", "Python", "py",
        ]
        await _store_entities(engine, names)
        monkeypatch.setattr(jobs_module, "_INCREMENTAL_PAGE_SIZE", 2)

        index = engine._vector_index
        original_search = index.search

        async def slow_search(*args, **kwargs):
            await asyncio.sleep(0.02)
            return await original_search(*args, **kwargs)

        monkeypatch.setattr(index, "search", slow_search)

        stopped = await engine.organize(user_id=USER, jobs=["alias_resolve"], budget_ms=120)
        job = stopped.per_job["alias_resolve"]
        assert job.status == "budget_exhausted"
        assert 0 < job.nodes_processed < len(names)

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="alias_resolve",
        )
        assert watermark.resume_cursor is not None

        resumed = await engine.organize(user_id=USER, jobs=["alias_resolve"], budget_ms=5000)
        assert resumed.per_job["alias_resolve"].status == "completed"

        converged = await engine.organize(user_id=USER, jobs=["alias_resolve"], budget_ms=5000)
        assert converged.per_job["alias_resolve"].nodes_modified == 0

        states = await _all_states(engine)
        assert sorted(states.values()).count(LifecycleState.TENTATIVE) == 4
        assert sorted(states.values()).count(LifecycleState.SUPERSEDED) == 4

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
    async def test_alias_name_lookup_failure_is_reported_and_retried(self, engine, monkeypatch):
        await _store_entities(engine, ["JavaScript", "js"])
        original_query_nodes = engine.query_nodes

        async def failing_lookup(*args, **kwargs):
            if kwargs.get("content_equals_any"):
                raise RuntimeError("name lookup failed")
            return await original_query_nodes(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(engine, "query_nodes", failing_lookup)
            result = await engine.organize(user_id=USER, jobs=["alias_resolve"])

        job = result.per_job["alias_resolve"]
        assert job.status == "error"
        assert job.errors >= 1

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="alias_resolve",
        )
        assert watermark is None or watermark.completed_through is None
        assert watermark is None or watermark.resume_cursor is None

        retried = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert retried.per_job["alias_resolve"].status == "completed"
        assert retried.per_job["alias_resolve"].nodes_modified == 1

    @pytest.mark.asyncio
    async def test_alias_resolve_failure_does_not_advance_the_watermark(
        self, engine, monkeypatch,
    ):
        await _store_entities(engine, ["JavaScript", "js"])

        async def failing_merge(*args, **kwargs):
            raise RuntimeError("merge failed")

        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "merge_nodes", failing_merge)
            result = await engine.organize(user_id=USER, jobs=["alias_resolve"])

        job = result.per_job["alias_resolve"]
        assert job.status == "error"
        assert job.errors >= 1
        assert job.nodes_modified == 0

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="alias_resolve",
        )
        assert watermark is None or watermark.completed_through is None
        assert watermark is None or watermark.resume_cursor is None

        retried = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert retried.per_job["alias_resolve"].status == "completed"
        assert retried.per_job["alias_resolve"].nodes_modified == 1

    @pytest.mark.asyncio
    async def test_alias_failure_after_a_completed_page_keeps_its_cursor(
        self, engine, monkeypatch,
    ):
        """Page 2 fails; the resume cursor stays at the end of page 1."""
        monkeypatch.setattr(jobs_module, "_INCREMENTAL_PAGE_SIZE", 2)
        await _store_entities(
            engine, ["JavaScript", "js", "PostgreSQL", "postgresql"],
        )
        by_content = {
            node.content: str(node.id)
            for node in await engine.query_nodes(
                user_id=USER, lifecycle_states=list(LifecycleState), limit=100,
            )
        }

        original_merge = engine._graph_store.merge_nodes
        calls = [0]

        async def failing_second_merge(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("merge failed on page 2")
            return await original_merge(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "merge_nodes", failing_second_merge)
            result = await engine.organize(user_id=USER, jobs=["alias_resolve"])

        job = result.per_job["alias_resolve"]
        assert job.status == "error"
        assert job.errors >= 1
        assert job.nodes_modified == 1

        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="alias_resolve",
        )
        assert watermark.resume_cursor is not None
        _, resume_id = watermark.resume_cursor
        assert resume_id == by_content["js"]

        retried = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert retried.per_job["alias_resolve"].status == "completed"
        assert retried.per_job["alias_resolve"].nodes_modified == 1

        states = await _all_states(engine)
        assert sorted(states.values()).count(LifecycleState.SUPERSEDED) == 2

    @pytest.mark.asyncio
    async def test_alias_neighbor_search_failure_is_reported_and_retried(
        self, engine, monkeypatch,
    ):
        await _store_entities(engine, ["Alpha", "Beta"])
        index = engine._vector_index

        async def broken_search(*args, **kwargs):
            raise RuntimeError("embedding rejected")

        with monkeypatch.context() as patch:
            patch.setattr(index, "search", broken_search)
            result = await engine.organize(user_id=USER, jobs=["alias_resolve"])

        job = result.per_job["alias_resolve"]
        assert job.status == "error"
        assert job.errors >= 1
        assert job.nodes_processed == 0

        retried = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert retried.per_job["alias_resolve"].status == "completed"
        assert retried.per_job["alias_resolve"].nodes_processed == 2

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

    @pytest.mark.asyncio
    async def test_incremental_and_full_alias_resolve_agree(self, tmp_path):
        full = await _fresh_engine(tmp_path / "full-alias", incremental=False)
        try:
            await _store_entities(full, ["JavaScript", "js", "alpha"])
            await _store_entities(full, ["PostgreSQL", "postgresql"])
            await full.organize(user_id=USER, jobs=["alias_resolve"])
            full_snapshot = await _content_states(full)
        finally:
            await full.close()

        incremental = await _fresh_engine(tmp_path / "incremental-alias")
        try:
            await _store_entities(incremental, ["JavaScript", "alpha"])
            await incremental.organize(user_id=USER, jobs=["alias_resolve"])
            await _store_entities(incremental, ["js", "PostgreSQL", "postgresql"])
            await incremental.organize(user_id=USER, jobs=["alias_resolve"])
            incremental_snapshot = await _content_states(incremental)
        finally:
            await incremental.close()

        assert full_snapshot == incremental_snapshot
        assert [state for _content, state in full_snapshot].count(
            LifecycleState.TENTATIVE.value
        ) == 3
        assert [state for _content, state in full_snapshot].count(
            LifecycleState.SUPERSEDED.value
        ) == 2


class TestSummarizeStaysFullScope:
    """summarize stays full-scope, and the docstring records why (#523).

    Its unit of work is a time period with a top-N selection over that period's
    active membership, and a node-change window cannot name a period a node has
    left: ``update_node`` can change ``event_time``, and a hard delete leaves no
    node row. The summary that recorded the sources still names the old period,
    which is a different read from the node window.
    """

    @pytest.mark.asyncio
    async def test_summarize_is_not_an_incremental_job(self, engine):
        assert "summarize" not in INCREMENTAL_JOBS
        await _store_many(engine, [f"summarize input {i}" for i in range(5)])

        result = await engine.organize(user_id=USER, jobs=["summarize"], budget_ms=60000)

        job = result.per_job["summarize"]
        assert job.status == "completed"
        assert "incremental" not in job.details
        assert await _watermark_rows(engine) == []

    @pytest.mark.asyncio
    async def test_event_time_move_changes_both_periods(self, engine):
        """A source that leaves a day changes that day and its new day."""
        day_a = datetime(2026, 3, 2, 12, tzinfo=timezone.utc)
        day_b = datetime(2026, 3, 3, 12, tzinfo=timezone.utc)
        contents_a = [f"day-a-{i}" for i in range(6)]
        contents_b = [f"day-b-{i}" for i in range(5)]

        ids_a = [
            (await engine.store_with_receipt(
                content, user_id=USER, node_type=NodeType.FACT,
                scope=Scope.PERSONAL, event_time=day_a + timedelta(minutes=i),
            )).node_id
            for i, content in enumerate(contents_a)
        ]
        for i, content in enumerate(contents_b):
            await engine.store_with_receipt(
                content, user_id=USER, node_type=NodeType.FACT,
                scope=Scope.PERSONAL, event_time=day_b + timedelta(minutes=i),
            )

        await engine.organize(user_id=USER, jobs=["summarize"], budget_ms=60000)

        before_move = await engine._graph_store.get_node(str(ids_a[-1]))
        old_a = await _active_daily_summary(engine, "2026-03-02")
        assert str(ids_a[-1]) in old_a.metadata["source_node_ids"]

        # The move writes the new event time and a new updated_at. The node
        # window finds the node, but its row names only the new day.
        await engine._graph_store.update_node(
            str(ids_a[-1]), event_time=day_b + timedelta(hours=1),
        )
        moved = await engine._graph_store.get_node(str(ids_a[-1]))
        assert moved.updated_at > before_move.updated_at

        # Until the next run, the old day's summary is the only record that
        # names the vacated period.
        still_old_a = await _active_daily_summary(engine, "2026-03-02")
        assert str(ids_a[-1]) in still_old_a.metadata["source_node_ids"]

        await engine.organize(user_id=USER, jobs=["summarize"], budget_ms=60000)

        after_a = await _active_daily_summary(engine, "2026-03-02")
        after_b = await _active_daily_summary(engine, "2026-03-03")
        assert str(ids_a[-1]) not in after_a.metadata["source_node_ids"]
        assert "day-a-5" not in after_a.content
        assert "day-a-5" in after_b.content


class TestConsolidateStaysFullScope:
    """consolidate stays full-scope, and the docstring records why (#523).

    Its unit of work is a cluster, an emergent property of the active node page
    the job reads. Clustering seeds at the earliest unassigned node under the
    stable source key, so a changed-node scan can produce a different member set
    and therefore a different consolidation lineage; a cluster can also become
    eligible when a different node's retirement frees its neighbors. The
    publication path already makes a second full run a no-op when the first
    run retired nothing.
    """

    @pytest.mark.asyncio
    async def test_consolidate_is_not_an_incremental_job(self, engine):
        assert "consolidate" not in INCREMENTAL_JOBS
        await _store_many(engine, CONSOLIDATION_CONTENTS)

        result = await engine.organize(
            user_id=USER, jobs=["consolidate"], budget_ms=60000,
        )

        job = result.per_job["consolidate"]
        assert job.status == "completed"
        assert job.details["summaries_created"] == 1
        assert "incremental" not in job.details
        assert await _watermark_rows(engine) == []

    @pytest.mark.asyncio
    async def test_second_consolidate_run_creates_no_new_summary(self, engine):
        await _store_many(engine, CONSOLIDATION_CONTENTS)

        first = await engine.organize(
            user_id=USER, jobs=["consolidate"], budget_ms=60000,
        )
        first_job = first.per_job["consolidate"]
        assert first_job.details["summaries_created"] == 1
        # No source retired, so the second run re-partitions the same node set
        # and reaches the publication path's idempotent match.
        assert first_job.details["nodes_archived"] == 0

        second = await engine.organize(
            user_id=USER, jobs=["consolidate"], budget_ms=60000,
        )
        job = second.per_job["consolidate"]
        assert job.details["clusters_found"] == 1
        assert job.details["summaries_created"] == 0
        assert job.details["nodes_archived"] == 0
        assert job.nodes_modified == 0

        summaries = await engine.query_nodes(
            user_id=USER,
            node_type=NodeType.SUMMARY,
            lifecycle_states=list(LifecycleState),
            limit=100,
        )
        assert len(summaries) == 1

    @pytest.mark.asyncio
    async def test_a_cluster_can_seed_at_an_unchanged_node(self, engine, monkeypatch):
        """The seed need not be the node a change window would contain.

        Seed is similar to both other nodes; Bridge is similar only to Seed.
        The full greedy partition forms one three-node cluster from Seed, which
        a changed-node scan seeded at Bridge cannot reproduce: Bridge's own
        neighborhood is a single node.
        """
        from prme.organizer.consolidation import cluster_similar_memories

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        contents = ["Seed memory", "Bridge memory", "Leaf memory"]
        for index, content in enumerate(contents):
            await engine.store(
                content,
                user_id=USER,
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
                event_time=base + timedelta(minutes=index),
            )
        ids = {
            node.content: str(node.id)
            for node in await engine.query_nodes(
                user_id=USER, lifecycle_states=list(LifecycleState), limit=100,
            )
        }
        neighbours = {
            "Seed memory": ["Bridge memory", "Leaf memory"],
            "Bridge memory": ["Seed memory"],
            "Leaf memory": ["Seed memory"],
        }

        async def planted_search(content, *args, **kwargs):
            return [
                {"node_id": ids[name], "score": 0.95}
                for name in neighbours.get(content, [])
            ]

        with monkeypatch.context() as patch:
            patch.setattr(engine._vector_index, "search", planted_search)
            clusters = await cluster_similar_memories(
                engine,
                user_id=USER,
                min_cluster_size=3,
                similarity_threshold=0.70,
            )

        # Bridge's planted neighborhood is Seed only, so a cluster seeded at
        # Bridge has two members and is below the minimum.
        assert len(clusters) == 1
        assert clusters[0].centroid_id == ids["Seed memory"]
        assert sorted(ids[name] for name in contents) == sorted(
            clusters[0].member_ids
        )

    @pytest.mark.asyncio
    async def test_truncated_run_reports_completed_and_writes_no_cursor(self, engine):
        """A budget-truncated full run is not a resumable incremental stop."""
        await _store_many(engine, CONSOLIDATION_CONTENTS)

        # Run the job directly so the assertion does not depend on organize()'s
        # remaining-budget gate, which marks a job not_started when it has none.
        truncated = await run_job(
            "consolidate",
            engine,
            engine._config.organizer,
            0,
            USER,
        )
        assert truncated.status == "completed"
        assert truncated.stopped_on_budget is False
        assert truncated.details["clusters_found"] == 1
        assert truncated.details["summaries_created"] == 0
        assert await _watermark_rows(engine) == []

        # The next run starts again at the first cluster and finishes it.
        completed = await engine.organize(
            user_id=USER, jobs=["consolidate"], budget_ms=60000,
        )
        assert (
            completed.per_job["consolidate"].details["summaries_created"] == 1
        )


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

    @pytest.mark.asyncio
    async def test_scoped_incremental_alias_scan_excludes_another_owner(self, engine):
        await _store_entities(engine, ["JavaScript", "js"])
        await _store_entities(engine, ["JavaScript", "js"], user_id="other-user")

        result = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        assert result.per_job["alias_resolve"].nodes_modified == 1

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
# Missed memories (dwamianm/prism#545)
# ---------------------------------------------------------------------------


class TestMaterializedNoteEntersNextWindow:
    """A note made from a queued fast-ingest item must enter the next window.

    The note's ``created_at`` stays the event's save time, and its
    ``updated_at`` is the time it is made, so a note materialized after a
    completed run's watermark still ranks after it.
    """

    @pytest.mark.asyncio
    async def test_a_queued_note_made_after_a_completed_run_is_examined(self, engine):
        event_id = await engine.ingest_fast("queued note", user_id=USER)
        # A completed run whose window starts after the event was saved.
        await engine.set_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
            completed_through=datetime.now(timezone.utc),
        )
        processed = await engine.process_pending(user_id=USER, budget_ms=5000)
        assert processed.processed == 1

        [node] = await engine.query_nodes(
            user_id=USER, lifecycle_states=list(LifecycleState),
        )
        event = await engine.get_event(event_id)
        assert node.created_at == event.created_at
        assert node.updated_at > event.created_at

        result = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert result.per_job["deduplicate"].nodes_processed == 1


class TestRepeatedFailureIsBounded:
    """A node that keeps failing must not pin the scope's cursor for good."""

    @pytest.mark.asyncio
    async def test_a_node_whose_search_always_fails_is_skipped_after_bounded_attempts(
        self, engine, monkeypatch,
    ):
        await _store_many(
            engine, ["poison memory", "alpha memory", "beta memory"],
            valid_from=FIXED_VALID_FROM,
        )
        poison_id = next(
            node.id for node in await engine.query_nodes(
                user_id=USER, lifecycle_states=list(LifecycleState),
            )
            if node.content == "poison memory"
        )

        index = engine._vector_index
        original_search = index.search

        async def failing_search(content, *args, **kwargs):
            if content == "poison memory":
                raise RuntimeError("embedding rejected")
            return await original_search(content, *args, **kwargs)

        monkeypatch.setattr(index, "search", failing_search)

        max_attempts = jobs_module._MAX_STALLED_ATTEMPTS
        for attempt in range(1, max_attempts):
            result = await engine.organize(user_id=USER, jobs=["deduplicate"])
            job = result.per_job["deduplicate"]
            assert job.status == "error"
            assert job.nodes_processed == 0
            watermark = await engine.get_organizer_watermark(
                scope_key=scope_key(USER), job="deduplicate",
            )
            assert watermark.stalled_id == str(poison_id)
            assert watermark.stalled_attempts == attempt

        bounded = await engine.organize(user_id=USER, jobs=["deduplicate"])
        job = bounded.per_job["deduplicate"]
        assert job.status == "completed"
        assert job.details["skipped_nodes"] == [
            {"node_id": str(poison_id), "attempts": max_attempts},
        ]
        # The nodes after the skipped one are examined in the same run.
        assert job.nodes_processed == 2
        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        )
        assert watermark.stalled_id is None

        # Nothing the owner stores afterwards is stuck behind the bad node.
        await _store_many(engine, ["new twin", "new twin"], valid_from=FIXED_VALID_FROM)
        later = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert later.per_job["deduplicate"].nodes_modified == 1

    @pytest.mark.asyncio
    async def test_alias_resolve_bounds_a_repeated_search_failure_too(self, engine, monkeypatch):
        """The same bound covers alias_resolve's incremental path."""
        await _store_entities(engine, ["poison entity", "alpha", "beta"])
        poison_id = next(
            node.id for node in await engine.query_nodes(
                user_id=USER, node_type=NodeType.ENTITY, lifecycle_states=list(LifecycleState),
            )
            if node.content == "poison entity"
        )

        index = engine._vector_index
        original_search = index.search

        async def failing_search(content, *args, **kwargs):
            if content == "poison entity":
                raise RuntimeError("embedding rejected")
            return await original_search(content, *args, **kwargs)

        monkeypatch.setattr(index, "search", failing_search)

        max_attempts = jobs_module._MAX_STALLED_ATTEMPTS
        for _ in range(max_attempts - 1):
            result = await engine.organize(user_id=USER, jobs=["alias_resolve"])
            assert result.per_job["alias_resolve"].status == "error"

        bounded = await engine.organize(user_id=USER, jobs=["alias_resolve"])
        job = bounded.per_job["alias_resolve"]
        assert job.status == "completed"
        assert job.details["skipped_nodes"] == [
            {"node_id": str(poison_id), "attempts": max_attempts},
        ]
        assert job.nodes_processed == 2


class TestFailedMergeKeepsThePageResumable:
    """A pair whose merge failed is tried again instead of being skipped."""

    @pytest.mark.asyncio
    async def test_a_failed_merge_is_retried_on_the_next_run(self, engine, monkeypatch):
        await _store_many(engine, ["same thing", "same thing"], valid_from=FIXED_VALID_FROM)

        original_merge = engine._graph_store.merge_nodes

        async def failing_merge(*args, **kwargs):
            raise RuntimeError("merge failed")

        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "merge_nodes", failing_merge)
            failed = await engine.organize(user_id=USER, jobs=["deduplicate"])

        job = failed.per_job["deduplicate"]
        assert job.status == "error"
        assert job.nodes_modified == 0
        watermark = await engine.get_organizer_watermark(
            scope_key=scope_key(USER), job="deduplicate",
        )
        assert watermark.completed_through is None
        assert watermark.resume_cursor is None

        monkeypatch.setattr(engine._graph_store, "merge_nodes", original_merge)
        retried = await engine.organize(user_id=USER, jobs=["deduplicate"])
        assert retried.per_job["deduplicate"].nodes_modified == 1


class TestOwnerKeyMatchesOwnership:
    """Two spellings of one UUID owner id are two owners and two watermarks."""

    @pytest.mark.asyncio
    async def test_another_spelling_does_not_inherit_the_first_watermark(self, engine):
        canonical = "550e8400-e29b-41d4-a716-446655440000"
        upper = canonical.upper()
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        for index in range(2):
            await engine._graph_store.create_node(MemoryNode(
                user_id=canonical,
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
                content=f"older memory {index}",
                created_at=old,
                updated_at=old,
                valid_from=old,
            ))
        await engine.set_organizer_watermark(
            scope_key=scope_key(upper), job="deduplicate",
            completed_through=datetime.now(timezone.utc),
        )

        result = await engine.organize(user_id=canonical, jobs=["deduplicate"])

        assert result.per_job["deduplicate"].nodes_processed == 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fresh_engine(
    directory: Path, *, incremental: bool = True,
) -> MemoryEngine:
    directory.mkdir(parents=True, exist_ok=True)
    lexical_path = directory / "lexical_index"
    lexical_path.mkdir()
    return await MemoryEngine.create(PRMEConfig(
        db_path=str(directory / "memory.duckdb"),
        vector_path=str(directory / "vectors.usearch"),
        lexical_path=str(lexical_path),
        organizer=OrganizerConfig(
            incremental_enabled=incremental,
            opportunistic_enabled=False,
        ),
    ))


async def _content_states(engine: MemoryEngine) -> list[tuple[str, str]]:
    nodes = await engine.query_nodes(
        user_id=USER, lifecycle_states=list(LifecycleState), limit=1000,
    )
    return sorted((node.content, node.lifecycle_state.value) for node in nodes)


async def _active_daily_summary(engine: MemoryEngine, period_key: str) -> MemoryNode:
    nodes = await engine.query_nodes(
        user_id=USER,
        node_type=NodeType.SUMMARY,
        lifecycle_states=[LifecycleState.STABLE],
        limit=200,
    )
    matches = [
        node for node in nodes
        if (node.metadata or {}).get("summarization_level") == "daily"
        and (node.metadata or {}).get("period_key") == period_key
    ]
    assert len(matches) == 1, [
        (str(node.id), node.metadata) for node in matches
    ]
    return matches[0]
