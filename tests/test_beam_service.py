"""Official BEAM HTTP boundary: neutral inputs, isolation, and safe resume."""

import argparse
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from benchmarks.diagnostics.hybrid_lexical import raw_config
from benchmarks.integrations.beam_service import (
    UPSTREAM_COMMIT,
    _config as _service_config,
    _manifest,
    create_app,
)
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult


def _test_config(root: Path):
    (root / "lexical").mkdir(parents=True)
    return raw_config().model_copy(
        update={
            "db_path": str(root / "memory.duckdb"),
            "vector_path": str(root / "vectors.usearch"),
            "lexical_path": str(root / "lexical"),
            "duckdb_threads": 1,
        }
    )


async def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    )


async def test_beam_raw_adapter_is_idempotent_isolated_and_resumable(tmp_path):
    config = _test_config(tmp_path / "pack")
    request = {
        "messages": [
            {"role": "user", "content": "The deployment region is Virginia."},
            {"role": "assistant", "content": "I recorded Virginia as the region."},
        ],
        "user_id": "beam_case_alice",
        "timestamp": 1_735_689_600,
    }

    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with await _client(app) as client:
            health = (await client.get("/health")).json()
            assert health == {
                "status": "ok",
                "profile": "raw",
                "adapter_schema": 2,
                "upstream_commit": UPSTREAM_COMMIT,
            }
            first = await client.post("/memories", json=request)
            assert first.status_code == 200
            first_results = first.json()["results"]
            assert len(first_results) == 2
            assert {item["event"] for item in first_results} == {"ADD"}
            assert {item["created_at"] for item in first_results} == {
                "2025-01-01T00:00:00+00:00"
            }

            retried = await client.post("/memories", json=request)
            assert retried.status_code == 200
            assert [item["id"] for item in retried.json()["results"]] == [
                item["id"] for item in first_results
            ]
            assert await app.state.engine.count_nodes(user_id="beam_case_alice") == 2
            stored = await app.state.engine.query_nodes(
                user_id="beam_case_alice", limit=10
            )
            assert len({node.session_id for node in stored}) == 1

            later = await client.post(
                "/memories",
                json={
                    **request,
                    "messages": [
                        {"role": "user", "content": "The later session is separate."}
                    ],
                    "timestamp": request["timestamp"] + 86_400,
                },
            )
            assert later.status_code == 200
            stored = await app.state.engine.query_nodes(
                user_id="beam_case_alice", limit=10
            )
            assert len({node.session_id for node in stored}) == 2

            found = await client.post(
                "/search",
                json={
                    "query": "Which deployment region was recorded?",
                    "user_id": "beam_case_alice",
                    "limit": 10,
                },
            )
            assert found.status_code == 200
            assert any("Virginia" in item["memory"] for item in found.json()["results"])
            isolated = await client.post(
                "/search",
                json={
                    "query": "deployment region",
                    "user_id": "beam_case_bob",
                    "limit": 10,
                },
            )
            assert isolated.json() == {"results": []}

            contaminated = await client.post(
                "/memories", json={**request, "metadata": {"answer": "Virginia"}}
            )
            assert contaminated.status_code == 422

    reopened = create_app(config)
    async with reopened.router.lifespan_context(reopened):
        async with await _client(reopened) as client:
            retried = await client.post("/memories", json=request)
            assert retried.status_code == 200
            assert [item["id"] for item in retried.json()["results"]] == [
                item["id"] for item in first_results
            ]
            assert await reopened.state.engine.count_nodes(user_id="beam_case_alice") == 3


async def test_beam_adapter_rejects_unsupported_search_reranking(tmp_path):
    app = create_app(_test_config(tmp_path / "pack"))
    async with app.router.lifespan_context(app):
        async with await _client(app) as client:
            response = await client.post(
                "/search",
                json={"query": "anything", "user_id": "owner", "rerank": True},
            )
            assert response.status_code == 422


async def test_beam_raw_adapter_repairs_saved_source_before_acknowledging_retry(
    tmp_path, monkeypatch
):
    app = create_app(_test_config(tmp_path / "pack"))
    request = {
        "messages": [{"role": "user", "content": "Preserve the pending source."}],
        "user_id": "beam_pending",
        "timestamp": 1_735_689_600,
    }
    async with app.router.lifespan_context(app):
        async with await _client(app) as client:
            with monkeypatch.context() as outage:
                outage.setattr(
                    app.state.engine._vector_index,
                    "index",
                    AsyncMock(side_effect=OSError("offline")),
                )
                pending = await client.post("/memories", json=request)
            assert pending.status_code == 503
            assert await app.state.engine.count_nodes(user_id="beam_pending") == 1

            repaired = await client.post("/memories", json=request)
            assert repaired.status_code == 200
            assert len(repaired.json()["results"]) == 1
            assert await app.state.engine.count_nodes(user_id="beam_pending") == 1


async def test_beam_extracted_adapter_uses_product_ingestion_once_per_source(tmp_path):
    app = create_app(_test_config(tmp_path / "pack"), profile="extracted")
    source = "Alice uses Rust for deployment automation."
    extraction = AsyncMock(
        return_value=ExtractionResult(
            entities=[ExtractedEntity(name="Alice", entity_type="person")],
            facts=[
                ExtractedFact(
                    subject="Alice",
                    predicate="uses",
                    object="Rust",
                    evidence_quote=source,
                )
            ],
        )
    )
    request = {
        "messages": [{"role": "user", "content": source}],
        "user_id": "beam_extracted",
        "timestamp": 1_735_689_600,
    }

    async with app.router.lifespan_context(app):
        app.state.engine._pipeline._extraction_provider.extract = extraction
        async with await _client(app) as client:
            first = await client.post("/memories", json=request)
            assert first.status_code == 200
            assert any("Rust" in item["memory"] for item in first.json()["results"])
            retried = await client.post("/memories", json=request)
            assert retried.status_code == 200
            assert [item["id"] for item in retried.json()["results"]] == [
                item["id"] for item in first.json()["results"]
            ]
            extraction.assert_awaited_once()


def test_beam_manifest_fingerprints_extraction_without_persisting_key(tmp_path):
    args = argparse.Namespace(
        profile="extracted",
        directory=tmp_path,
        duckdb_threads=2,
        extraction_provider="ollama",
        extraction_model="named-local-model",
        extraction_base_url="http://127.0.0.1:11434/v1",
        extraction_reasoning_effort="none",
        extraction_timeout=123.0,
        extraction_lease_seconds=60.0,
    )
    manifest = _manifest(args, _service_config(args))
    serialized = json.dumps(manifest)
    assert "api_key" not in serialized
    assert manifest["extraction"] == {
        "provider": "ollama",
        "model": "named-local-model",
        "base_url": "http://127.0.0.1:11434/v1",
        "reasoning_effort": "none",
        "temperature": 0.0,
        "timeout": 123.0,
        "lease_seconds": 60.0,
    }
    assert manifest["duckdb_threads"] == 2
    assert manifest["adapter_source_sha256"]

    config = _service_config(args)
    unsafe = config.model_copy(
        update={
            "extraction": config.extraction.model_copy(
                update={"base_url": "https://token@example.test/v1?secret=value"}
            )
        }
    )
    with pytest.raises(ValueError, match="without credentials"):
        _manifest(args, unsafe)
