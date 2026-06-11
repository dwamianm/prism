"""Tests for opt-in LLM multi-query reformulation (issue #43).

Covers two layers:

1. The standalone ``prme.retrieval.reformulation.reformulate_query`` post-
   processing: count clamping, blank/echo/dedup filtering, empty-query short
   circuit, and the safe ``[]`` fallback when the LLM call fails.
2. The pipeline integration: with ``enable_query_reformulation=False`` (the
   default) retrieve() makes zero reformulation LLM calls; with it enabled and
   reformulation stubbed, an alternate query surfaces a node the original
   query misses and results are merged (deduplicated by node id).

The LLM call is always mocked -- no network/provider access in tests.
"""

from __future__ import annotations

import logging
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

import prme.retrieval.reformulation as reformulation
from prme.client import MemoryClient, config_from_directory
from prme.retrieval.reformulation import QueryReformulations, reformulate_query


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_reform_") as d:
        yield d


@pytest.fixture(autouse=True)
def suppress_structlog():
    """Suppress structlog output during tests."""
    import sys

    import structlog

    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _mock_client_returning(queries: list[str]) -> AsyncMock:
    """Build a mock instructor client whose create() yields ``queries``."""
    client = AsyncMock()
    client.create = AsyncMock(
        return_value=QueryReformulations(queries=queries)
    )
    return client


# ---------------------------------------------------------------------------
# reformulate_query — post-processing
# ---------------------------------------------------------------------------


class TestReformulateQuery:
    @pytest.mark.asyncio
    async def test_returns_alternatives(self):
        mock_client = _mock_client_returning(["alt one", "alt two"])
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            result = await reformulate_query("original question", count=2)
        assert result == ["alt one", "alt two"]

    @pytest.mark.asyncio
    async def test_empty_query_short_circuits(self):
        # No client call should happen for blank input.
        with patch.object(reformulation, "_get_client") as get_client:
            assert await reformulate_query("") == []
            assert await reformulate_query("   ") == []
            get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_drops_original_query_echo_case_insensitive(self):
        mock_client = _mock_client_returning(
            ["Original Question", "a genuine alternative"]
        )
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            result = await reformulate_query("original question", count=3)
        assert result == ["a genuine alternative"]

    @pytest.mark.asyncio
    async def test_drops_blanks_and_intra_list_duplicates(self):
        mock_client = _mock_client_returning(
            ["dup", "  ", "DUP", "unique alt"]
        )
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            result = await reformulate_query("q", count=5)
        assert result == ["dup", "unique alt"]

    @pytest.mark.asyncio
    async def test_caps_at_count(self):
        mock_client = _mock_client_returning(["a", "b", "c", "d"])
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            result = await reformulate_query("q", count=2)
        assert result == ["a", "b"]

    @pytest.mark.asyncio
    async def test_count_clamped_to_valid_range(self):
        # count=99 is clamped to 5; the mock supplies more than 5.
        mock_client = _mock_client_returning(
            ["a", "b", "c", "d", "e", "f", "g"]
        )
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            result = await reformulate_query("q", count=99)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_ignores_non_string_items(self):
        mock_client = AsyncMock()
        # Bypass pydantic validation by returning a raw object with .queries.
        mock_client.create = AsyncMock(
            return_value=type("R", (), {"queries": ["ok", 42, None, "fine"]})()
        )
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            result = await reformulate_query("q", count=5)
        assert result == ["ok", "fine"]

    @pytest.mark.asyncio
    async def test_failure_returns_empty_list(self):
        mock_client = AsyncMock()
        mock_client.create = AsyncMock(side_effect=RuntimeError("no api key"))
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            result = await reformulate_query("q", count=2)
        assert result == []


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_default_off_makes_no_reformulation_call(self, tmp_dir):
        """With the flag off (default), no reformulation LLM call happens."""
        with patch(
            "prme.retrieval.reformulation.reformulate_query",
            new_callable=AsyncMock,
        ) as mock_reform:
            with MemoryClient(tmp_dir) as client:
                client.store("Alice likes dark mode", user_id="alice")
                client.retrieve("preferences?", user_id="alice")
            mock_reform.assert_not_called()

    def test_enabled_merges_reformulated_candidates(self, tmp_dir):
        """A reformulated alternate query surfaces a node missed otherwise."""
        config = config_from_directory(tmp_dir)
        config.enable_query_reformulation = True

        # The alt query targets a distinctive keyword present only in the
        # tangential fact, so it surfaces that node when reformulation runs.
        async def fake_reformulate(query, **kwargs):
            return ["aurochs"]

        with patch(
            "prme.retrieval.reformulation.reformulate_query",
            side_effect=fake_reformulate,
        ) as mock_reform:
            with MemoryClient(config=config) as client:
                client.store(
                    "Last summer the team photographed an aurochs in the reserve",
                    user_id="u1",
                )
                client.store("The quarterly budget review went smoothly", user_id="u1")

                response = client.retrieve(
                    "tell me about the budget meeting", user_id="u1"
                )
            mock_reform.assert_called()

        contents = " ".join(r.node.content for r in response.results)
        assert "aurochs" in contents, (
            "reformulated query should surface the tangential node"
        )

    def test_enabled_dedupes_by_node_id(self, tmp_dir):
        """Reformulated results that duplicate the original are not double-added."""
        config = config_from_directory(tmp_dir)
        config.enable_query_reformulation = True

        async def fake_reformulate(query, **kwargs):
            # Return a query that surfaces the SAME node as the original.
            return ["dark mode preference"]

        with patch(
            "prme.retrieval.reformulation.reformulate_query",
            side_effect=fake_reformulate,
        ):
            with MemoryClient(config=config) as client:
                client.store("Alice likes dark mode", user_id="alice")
                response = client.retrieve("dark mode", user_id="alice")

        node_ids = [str(r.node.id) for r in response.results]
        assert len(node_ids) == len(set(node_ids)), "duplicate nodes in results"
