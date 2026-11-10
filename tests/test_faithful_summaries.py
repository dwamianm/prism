"""Hierarchical excerpts retain qualifications, namespaces, and episode time."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.config import OrganizerConfig
from prme.models import MemoryNode
from prme.organizer.summarization import (
    _create_summary_node, _group_nodes_by_day, _group_scoped,
    generate_daily_summaries, roll_up_weekly, roll_up_monthly, SummarizationLevel,
)
from prme.index_policy import is_unindexed_summary
from prme.types import EpistemicType, NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


def test_index_policy_distinguishes_summary_kinds():
    assert is_unindexed_summary(
        "summary", {"summary_publication_kind": "hierarchical_source_excerpts_v2"},
    )
    assert is_unindexed_summary(
        "summary",
        {"summarization_level": "weekly", "summary_format": "source-excerpts-v1",
         "period_key": "2024-W02"},
    )
    # A consolidation summary retires the sources it represents, so it stays
    # indexed. So does a knowledge profile, and a summary with no metadata.
    assert not is_unindexed_summary(
        "summary", {"consolidation_key": "k", "consolidation_format_version": 2},
    )
    assert not is_unindexed_summary("summary", {"entity_profile": True})
    assert not is_unindexed_summary("summary", None)
    assert not is_unindexed_summary(
        "fact", {"summary_publication_kind": "hierarchical_source_excerpts_v2"},
    )


def test_unscoped_groups_are_separate_for_each_user_and_scope():
    nodes = [MemoryNode(user_id=owner, scope=scope, node_type=NodeType.FACT, content="source")
             for owner in ("alice", "bob") for scope in (Scope.PERSONAL, Scope.PROJECT)]
    groups = _group_scoped(nodes, _group_nodes_by_day)
    assert len(groups) == 4
    assert all(len(group) == 1 for group in groups.values())


async def test_summary_preserves_scope_full_qualifiers_and_historical_rollup_time(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        source = "Use the proposed workflow. " * 20 + "Only if approved; otherwise keep the current workflow."
        for scope in (Scope.PERSONAL, Scope.PROJECT):
            for day in (8, 9):
                await engine._graph_store.create_node(MemoryNode(
                    user_id=user, scope=scope, node_type=NodeType.FACT,
                    content=f"{scope.value}: {source}", epistemic_type=EpistemicType.HYPOTHETICAL,
                    event_time=datetime(2024, 1, day, tzinfo=timezone.utc),
                ))
        cfg = OrganizerConfig(summarization_daily_min_events=1,
                              summarization_weekly_min_summaries=2,
                              summarization_monthly_min_summaries=1)
        daily = await generate_daily_summaries(engine, cfg, 10000, user_id=user)
        assert daily.nodes_modified == 4
        assert (await generate_daily_summaries(engine, cfg, 10000, user_id=user)).nodes_modified == 0
        assert (await roll_up_weekly(engine, cfg, 10000, user_id=user)).nodes_modified == 2
        assert (await roll_up_monthly(engine, cfg, 10000, user_id=user)).nodes_modified == 2
        summaries = await engine.query_nodes(user_id=user, node_type=NodeType.SUMMARY)
        assert len(summaries) == 8
        # The summarize job's summaries stay out of the default indexes
        # (dwamianm/prism#534); an explicit node-type request reads them from
        # the graph instead.
        response = await engine.retrieve(
            "workflow", user_id=user, scope=Scope.PERSONAL,
            include_node_types=[NodeType.SUMMARY], limit=100,
        )
        assert sum(result.node.node_type == NodeType.SUMMARY for result in response.results) == 4
        assert not any(
            is_unindexed_summary(result.node.node_type.value, result.node.metadata)
            for result in (await engine.retrieve("workflow", user_id=user, scope=Scope.PERSONAL, limit=100)).results
        )
        for node in summaries:
            assert node.epistemic_type == EpistemicType.INFERRED
            assert node.scope in (Scope.PERSONAL, Scope.PROJECT)
            assert "Only if approved; otherwise keep the current workflow." in node.content
            assert node.event_time.year == 2024 and node.event_time.month == 1
            source_ids = node.metadata["source_node_ids"]
            originals = await engine._graph_store.get_nodes(source_ids)
            assert all(n.user_id == user and n.scope == node.scope for n in originals)
            if node.metadata["summarization_level"] == "weekly":
                assert node.metadata["period_key"] == "2024-W02"
            if node.metadata["summarization_level"] == "monthly":
                assert node.metadata["period_key"] == "2024-01"


async def test_summarize_summaries_stay_out_of_the_default_indexes(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        day = datetime(2024, 1, 9, tzinfo=timezone.utc)
        for index in range(2):
            await engine._graph_store.create_node(MemoryNode(
                user_id=user, scope=Scope.PERSONAL, node_type=NodeType.FACT,
                content=f"The workflow detail {index}", event_time=day,
            ))
        result = await generate_daily_summaries(
            engine, OrganizerConfig(summarization_daily_min_events=1), 10000, user_id=user,
        )
        assert result.nodes_modified == 1
        summaries = await engine.query_nodes(user_id=user, node_type=NodeType.SUMMARY)
        summary_ids = {str(node.id) for node in summaries}
        assert summary_ids

        # The lexical index has no document, and the native vector index has
        # no entry, but the vector itself is still durable for recovery.
        assert await engine._lexical_index.search(
            "workflow", user, node_type="summary", limit=100,
        ) == []
        vector_hits = await engine._vector_index.search("workflow", user, k=100)
        assert not summary_ids & {hit["node_id"] for hit in vector_hits}
        if engine._conn is not None:
            keys = [
                row[0]
                for row in engine._conn.execute(
                    "SELECT vector_key FROM vector_metadata WHERE node_id IN "
                    f"({', '.join('?' for _ in summary_ids)})",
                    list(summary_ids),
                ).fetchall()
            ]
            assert keys
            assert not any(engine._vector_index._index.contains(key) for key in keys)

        if engine._conn is not None:
            await engine.rebuild_indexes()
            assert await engine._lexical_index.search(
                "workflow", user, node_type="summary", limit=100,
            ) == []
            vector_hits = await engine._vector_index.search("workflow", user, k=100)
            assert not summary_ids & {hit["node_id"] for hit in vector_hits}


async def test_summary_creation_rejects_mixed_scope_before_any_write(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        sources = [MemoryNode(user_id=user, scope=scope, node_type=NodeType.FACT, content="source")
                   for scope in (Scope.PERSONAL, Scope.PROJECT)]
        with pytest.raises(ValueError, match="user and scope"):
            await _create_summary_node(engine, SummarizationLevel.DAILY, "2024-01-01", sources, user)
        assert await engine.query_nodes(user_id=user, node_type=NodeType.SUMMARY) == []


async def test_failed_summary_publication_leaves_original_sources_and_no_partial_summary(config, user, monkeypatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        source = MemoryNode(user_id=user, node_type=NodeType.FACT, content="A complete original source")
        await engine._graph_store.create_node(source)
        monkeypatch.setattr(
            engine._graph_store,
            "publish_consolidation",
            AsyncMock(side_effect=RuntimeError("injected")),
        )
        result = await generate_daily_summaries(engine, OrganizerConfig(summarization_daily_min_events=1),
                                                10000, user_id=user)
        assert result.errors == 1 and result.nodes_modified == 0
        assert await engine.query_nodes(user_id=user, node_type=NodeType.SUMMARY) == []
        assert (await engine._graph_store.get_node(str(source.id))).content == source.content
        assert await engine._graph_store.get_edges(target_id=str(source.id)) == []
