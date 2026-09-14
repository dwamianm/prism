"""Atomicity and recovery guarantees for hierarchical summary publication."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.config import OrganizerConfig
from prme.models import MemoryNode
from prme.organizer.summarization import generate_daily_summaries
from prme.types import LifecycleState, NodeType
from tests.test_durable_ingestion import config, user  # noqa: F401


async def _daily_sources(engine, owner, *, count=3, salience=0.4):
    day = datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
    nodes = []
    for index in range(count):
        node_salience = min(salience + index * 0.0001, 1.0)
        node = MemoryNode(
            user_id=owner,
            node_type=NodeType.FACT,
            content=f"Daily source {index}",
            salience=node_salience,
            salience_base=node_salience,
            event_time=day + timedelta(seconds=index),
        )
        await engine._graph_store.create_node(node)
        nodes.append(node)
    return nodes


async def _active_daily(engine, owner):
    nodes = await engine.query_nodes(
        user_id=owner,
        node_type=NodeType.SUMMARY,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=100,
    )
    return [
        node
        for node in nodes
        if (node.metadata or {}).get("summarization_level") == "daily"
    ]


def _config():
    return OrganizerConfig(
        summarization_daily_min_events=2,
        summarization_max_items_per_summary=2,
    )


async def test_concurrent_identical_daily_runs_publish_one_identity(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await _daily_sources(engine, user)
        await asyncio.gather(
            generate_daily_summaries(engine, _config(), 10_000, user_id=user),
            generate_daily_summaries(engine, _config(), 10_000, user_id=user),
        )

        summaries = await _active_daily(engine, user)
        assert len(summaries) == 1
        assert summaries[0].metadata["summary_publication_kind"] == (
            "hierarchical_source_excerpts_v2"
        )


async def test_independent_engines_converge_on_one_daily_summary(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as first, MemoryEngine.open(config) as second:
        await _daily_sources(first, user)
        await asyncio.gather(
            generate_daily_summaries(first, _config(), 10_000, user_id=user),
            generate_daily_summaries(second, _config(), 10_000, user_id=user),
        )

        summaries = await _active_daily(first, user)
        assert len(summaries) == 1


async def test_unscoped_daily_run_pages_all_tenants(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="requires user_id"):
            await engine._graph_store.scan_nodes(user_id=None)
        other = user + "-other"
        await _daily_sources(engine, user, count=251)
        await _daily_sources(engine, other, count=251)

        result = await generate_daily_summaries(
            engine,
            OrganizerConfig(
                summarization_daily_min_events=250,
                summarization_max_items_per_summary=2,
            ),
            10_000,
        )

        assert result.nodes_processed == 2 and result.nodes_modified == 2
        assert len(await _active_daily(engine, user)) == 1
        assert len(await _active_daily(engine, other)) == 1


async def test_late_high_salience_source_atomically_replaces_daily_summary(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await _daily_sources(engine, user)
        first_result = await generate_daily_summaries(
            engine, _config(), 10_000, user_id=user
        )
        first = (await _active_daily(engine, user))[0]
        assert first_result.nodes_modified == 1

        late = MemoryNode(
            user_id=user,
            node_type=NodeType.DECISION,
            content="Late arrival that must enter the daily excerpt",
            salience=1.0,
            salience_base=1.0,
            event_time=datetime(2026, 7, 14, 23, tzinfo=timezone.utc),
        )
        await engine._graph_store.create_node(late)
        second_result = await generate_daily_summaries(
            engine, _config(), 10_000, user_id=user
        )

        active = await _active_daily(engine, user)
        assert second_result.nodes_modified == 1
        assert len(active) == 1 and active[0].id != first.id
        assert str(late.id) in active[0].metadata["source_node_ids"]
        retired = await engine.get_node(str(first.id), include_superseded=True)
        assert retired is not None
        assert retired.lifecycle_state == LifecycleState.ARCHIVED


async def test_unchanged_daily_run_reports_no_modification(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await _daily_sources(engine, user)
        await generate_daily_summaries(engine, _config(), 10_000, user_id=user)
        repeated = await generate_daily_summaries(
            engine, _config(), 10_000, user_id=user
        )
        assert repeated.nodes_processed == 1
        assert repeated.nodes_modified == 0
        assert repeated.errors == 0


async def test_interrupted_daily_publication_resumes_without_reembedding(
    config,  # noqa: F811
    user,  # noqa: F811
    monkeypatch,
):
    async with MemoryEngine.open(config) as engine:
        await _daily_sources(engine, user)
        with monkeypatch.context() as failure:
            failure.setattr(
                engine._graph_store,
                "publish_consolidation",
                AsyncMock(side_effect=RuntimeError("injected interruption")),
            )
            result = await generate_daily_summaries(
                engine, _config(), 10_000, user_id=user
            )
        assert result.errors == 1
        assert await _active_daily(engine, user) == []

    async def unexpected_embedding(*_args, **_kwargs):
        raise AssertionError("summary recovery repeated embedding work")

    monkeypatch.setattr(
        "prme.organizer.summarization.encode_texts", unexpected_embedding
    )
    async with MemoryEngine.open(config) as engine:
        result = await generate_daily_summaries(
            engine, _config(), 10_000, user_id=user
        )
        assert result.errors == 0 and result.nodes_modified == 1
        assert len(await _active_daily(engine, user)) == 1


async def test_first_managed_run_atomically_retires_legacy_daily_summary(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        sources = await _daily_sources(engine, user)
        legacy = MemoryNode(
            user_id=user,
            node_type=NodeType.SUMMARY,
            content="legacy daily excerpt",
            lifecycle_state=LifecycleState.STABLE,
            event_time=sources[0].event_time,
            metadata={
                "summarization_level": "daily",
                "summary_format": "source-excerpts-v1",
                "period_key": "2026-07-14",
                "source_count": 2,
                "source_node_ids": [str(node.id) for node in sources[:2]],
            },
        )
        await engine._graph_store.create_node(legacy)

        result = await generate_daily_summaries(
            engine, _config(), 10_000, user_id=user
        )

        active = await _active_daily(engine, user)
        assert result.nodes_modified == 1
        assert len(active) == 1 and active[0].id != legacy.id
        retired = await engine.get_node(str(legacy.id), include_superseded=True)
        assert retired is not None
        assert retired.lifecycle_state == LifecycleState.ARCHIVED
