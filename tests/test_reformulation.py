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

import asyncio
import logging
import os
import tempfile
import time
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

import prme.retrieval.reformulation as reformulation
from prme.client import MemoryClient, config_from_directory
from prme.config import ExtractionConfig
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


# ---------------------------------------------------------------------------
# Connection settings (issue #101)
# ---------------------------------------------------------------------------


@pytest.fixture
def provider_env(tmp_path, monkeypatch):
    """No provider settings in the environment or .env, and an empty cache."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("PRME_", "OPENAI_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    reformulation._client_cache.clear()
    yield tmp_path / ".env"
    reformulation._client_cache.clear()


def _recording_builder(built: list[tuple]):
    def build(provider_string, *, api_key=None, base_url=None):
        built.append((provider_string, api_key, base_url))
        return _mock_client_returning(["alt"])

    return build


@pytest.mark.usefixtures("provider_env")
class TestReformulationConnection:
    @pytest.mark.asyncio
    async def test_configured_endpoint_and_credential_build_the_client(self):
        built: list[tuple] = []
        with patch.object(
            reformulation, "create_instructor_client", side_effect=_recording_builder(built)
        ):
            result = await reformulate_query(
                "original question",
                provider="openai",
                model="example",
                api_key=SecretStr("configured-key"),
                base_url="https://gateway.invalid/v1",
            )

        assert result == ["alt"]
        assert built == [
            ("openai/example", SecretStr("configured-key"), "https://gateway.invalid/v1")
        ]

    @pytest.mark.asyncio
    async def test_a_blank_key_uses_the_providers_own_like_extraction(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
        built: list[tuple] = []
        with patch.object(
            reformulation, "create_instructor_client", side_effect=_recording_builder(built)
        ):
            await reformulate_query("q", model="m", api_key=SecretStr(""))

        assert built == [("openai/m", SecretStr("environment-key"), None)]

    @pytest.mark.asyncio
    async def test_clients_are_not_shared_across_connections(self, monkeypatch):
        built: list[tuple] = []
        with patch.object(
            reformulation, "create_instructor_client", side_effect=_recording_builder(built)
        ):
            await reformulate_query("q", model="m", base_url="https://a.invalid/v1")
            await reformulate_query("q", model="m", base_url="https://a.invalid/v1")
            await reformulate_query("q", model="m", base_url="https://b.invalid/v1")
            await reformulate_query(
                "q", model="m", base_url="https://a.invalid/v1", api_key=SecretStr("other")
            )
            await reformulate_query("q", model="other-model", base_url="https://a.invalid/v1")
            # A credential read from the environment is part of the connection too.
            monkeypatch.setenv("OPENAI_API_KEY", "rotated-key")
            await reformulate_query("q", model="m", base_url="https://a.invalid/v1")

        assert [(entry[0], entry[2]) for entry in built] == [
            ("openai/m", "https://a.invalid/v1"),
            ("openai/m", "https://b.invalid/v1"),
            ("openai/m", "https://a.invalid/v1"),
            ("openai/other-model", "https://a.invalid/v1"),
            ("openai/m", "https://a.invalid/v1"),
        ]
        assert built[2][1] == SecretStr("other")
        assert built[4][1] == SecretStr("rotated-key")
        assert "rotated-key" not in repr(reformulation._client_cache)

    @pytest.mark.asyncio
    async def test_a_callers_cache_keeps_its_clients_out_of_the_process_cache(self):
        mine: dict = {}
        with patch.object(
            reformulation, "create_instructor_client", side_effect=_recording_builder([])
        ):
            await reformulate_query("q", model="m", client_cache=mine)

        assert len(mine) == 1
        assert reformulation._client_cache == {}

    @pytest.mark.asyncio
    async def test_the_call_names_its_model_and_turns_off_ollama_reasoning(self):
        mock_client = _mock_client_returning(["alt"])
        with patch.object(reformulation, "_get_client", return_value=mock_client):
            await reformulate_query("q", provider="openai", model="example")
            await reformulate_query("q", provider="ollama", model="thinker")

        openai_call, ollama_call = mock_client.create.call_args_list
        assert openai_call.kwargs["model"] == "example"
        assert "reasoning_effort" not in openai_call.kwargs
        assert "temperature" not in openai_call.kwargs
        assert ollama_call.kwargs["model"] == "thinker"
        assert ollama_call.kwargs["reasoning_effort"] == "none"

    @pytest.mark.asyncio
    async def test_timeout_bounds_the_whole_call_and_falls_back(self, caplog):
        async def stall(**kwargs):
            await asyncio.sleep(30)

        client = AsyncMock()
        client.create = AsyncMock(side_effect=stall)
        started = time.perf_counter()
        with patch.object(reformulation, "_get_client", return_value=client):
            with caplog.at_level(logging.WARNING, logger=reformulation.__name__):
                result = await reformulate_query("q", timeout=0.05)

        assert result == []
        assert time.perf_counter() - started < 5
        assert "timed out after 0.05s" in caplog.text

    @pytest.mark.asyncio
    async def test_without_a_timeout_the_call_is_not_limited_here(self):
        async def slow(**kwargs):
            await asyncio.sleep(0.1)
            return QueryReformulations(queries=["alt"])

        client = AsyncMock()
        client.create = AsyncMock(side_effect=slow)
        with patch.object(reformulation, "_get_client", return_value=client):
            assert await reformulate_query("q") == ["alt"]

    @pytest.mark.asyncio
    async def test_cancellation_still_propagates(self):
        started = asyncio.Event()

        async def stall(**kwargs):
            started.set()
            await asyncio.sleep(30)

        client = AsyncMock()
        client.create = AsyncMock(side_effect=stall)
        with patch.object(reformulation, "_get_client", return_value=client):
            task = asyncio.create_task(reformulate_query("q", timeout=10))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


class TestPipelineConnection:
    def test_engines_reach_the_extraction_endpoint_with_their_own_clients(
        self, tmp_dir, provider_env
    ):
        """Each engine builds its reformulation client from config.extraction."""
        built: list[tuple] = []

        def build(provider_string, *, api_key=None, base_url=None):
            built.append((provider_string, api_key, base_url))
            return _mock_client_returning(["aurochs"])

        def config_for(directory, base_url):
            config = config_from_directory(directory)
            config.enable_query_reformulation = True
            config.extraction = ExtractionConfig(
                provider="openai",
                model="example-model",
                api_key=SecretStr("configured-key"),
                base_url=base_url,
                timeout=7.5,
            )
            return config

        with patch.object(reformulation, "create_instructor_client", side_effect=build):
            for index, base_url in enumerate(
                ["https://gateway.invalid/v1", "https://gateway.invalid/v1"]
            ):
                config = config_for(os.path.join(tmp_dir, str(index)), base_url)
                with MemoryClient(config=config) as client:
                    client.store(
                        "Last summer the team photographed an aurochs in the reserve",
                        user_id="u1",
                    )
                    response = client.retrieve("tell me about wildlife", user_id="u1")
                    pipeline = client._engine._retrieval_pipeline
                    assert pipeline._query_reformulation_timeout == 7.5
                    assert len(pipeline._query_reformulation_clients) == 1
                assert response.results

        # Two engines with the same settings still build two clients, one on
        # each engine's event loop, and none lands in the process-wide cache.
        assert built == [
            ("openai/example-model", SecretStr("configured-key"), "https://gateway.invalid/v1")
        ] * 2
        assert reformulation._client_cache == {}
