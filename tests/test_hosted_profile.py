"""Tests for the hosted request profile (benchmarks.diagnostics.hosted_profile)."""

from __future__ import annotations

import asyncio
import os

import pytest

from benchmarks.diagnostics import hosted_profile

PG_URL = os.environ.get("PRME_TEST_DATABASE_URL")
pg_required = pytest.mark.skipif(not PG_URL, reason="PRME_TEST_DATABASE_URL not set")


def test_hosted_config_mirrors_the_service():
    config = hosted_profile.hosted_config("postgresql://user:pass@127.0.0.1:5432/db")

    assert config.backend == "postgres"
    assert config.embedding.provider == "fastembed"
    assert config.embedding.threads == hosted_profile.HOSTED_EMBEDDING_THREADS
    assert config.enable_relation_routing is True
    assert config.enable_speaker_grounding is True
    assert config.enable_speaker_references is True
    assert config.scoring.rrf_graph_seed_count is True
    assert config.scoring.rrf_graph_weight == hosted_profile.GRAPH_SEED_COUNT_WEIGHT
    assert config.packing.graph_seed_words is True
    assert config.packing.folded_records_keep_rank is True
    assert config.packing.session_context_skip_entities is True
    # The service leaves the organizer as shipped: opportunistic maintenance on.
    assert config.organizer.opportunistic_enabled is True


def test_hosted_config_ignores_the_environment(monkeypatch):
    monkeypatch.setenv("PRME_ENABLE_RERANKER", "true")
    monkeypatch.setenv("PRME_ENABLE_RELATION_ROUTING", "false")

    config = hosted_profile.hosted_config("postgresql://user:pass@127.0.0.1:5432/db")

    assert config.enable_reranker is False
    assert config.enable_relation_routing is True


def test_stage_recorder_reports_per_request_values():
    recorder = hosted_profile.StageRecorder(("packing", "unused"))
    recorder.next_request()
    recorder.record("packing", 2.0, 1.0)
    recorder.record("packing", 3.0, 2.0)
    recorder.next_request()
    recorder.record("packing", 4.0, 1.0)

    report = recorder.report()

    assert set(report) == {"packing"}
    assert report["packing"]["wall_ms"]["p50"] == 4.5
    assert report["packing"]["cpu_ms"]["p50"] == 2.0
    assert report["packing"]["calls_per_request"] == 1.5


@pytest.mark.asyncio
async def test_instrumented_wraps_and_restores_attributes():
    class Target:
        async def async_stage(self, value):
            await asyncio.sleep(0)
            return value * 2

        def sync_stage(self, value):
            return value + 1

    recorder = hosted_profile.StageRecorder(("async", "sync"))
    target = Target()
    original_async, original_sync = target.async_stage, target.sync_stage
    recorder.next_request()

    with hosted_profile.instrumented(target, (("async", "async_stage"), ("sync", "sync_stage")), recorder):
        assert await target.async_stage(3) == 6
        assert target.sync_stage(3) == 4

    report = recorder.report()
    assert set(report) == {"async", "sync"}
    assert report["async"]["calls_per_request"] == 1.0
    assert report["sync"]["calls_per_request"] == 1.0
    assert target.async_stage.__func__ is original_async.__func__
    assert target.sync_stage.__func__ is original_sync.__func__


def test_instrumented_refuses_a_missing_attribute():
    recorder = hosted_profile.StageRecorder(("x",))
    with pytest.raises(AttributeError):
        with hosted_profile.instrumented(object(), (("x", "missing"),), recorder):
            pass


def test_forget_embeddings_drops_both_keys_of_a_real_cached_provider():
    from prme.storage.embedding import CachedEmbeddingProvider

    class Provider:
        model_name = "fake-model"
        model_version = "fake-1"
        dimension = 3

        async def embed(self, texts):
            return [[float(len(text)), 0.0, 0.0] for text in texts]

    class Index:
        _provider = CachedEmbeddingProvider(Provider())

    class Engine:
        _vector_index = Index()

    engine = Engine()
    cache = engine._vector_index._provider._cache
    asyncio.run(engine._vector_index._provider.embed(["a question", "a statement"]))
    import hashlib

    digest = hashlib.sha256(b"a question").hexdigest()
    cache["query:" + digest] = (99.0, 0.0, 0.0)  # the key an encoder-backed provider writes
    assert len(cache) == 3

    hosted_profile.forget_embeddings(engine, ["a question"])

    remaining = list(cache.values())
    assert len(remaining) == 1
    assert remaining[0] == (11.0, 0.0, 0.0)


def test_settings_digest_records_the_pinned_switches():
    config = hosted_profile.hosted_config("postgresql://user:pass@127.0.0.1:5432/db")
    digest = hosted_profile.settings_digest(config)

    assert digest["backend"] == "postgres"
    assert digest["scoring"]["rrf_graph_weight"] == hosted_profile.GRAPH_SEED_COUNT_WEIGHT
    assert digest["embedding"]["threads"] == hosted_profile.HOSTED_EMBEDDING_THREADS
    assert digest["switches"]["enable_relation_routing"] is True
    assert digest["switches"]["enable_ingest_claim_fixes"] is True


def test_required_texts_covers_the_build_and_both_write_shapes():
    assert hosted_profile.required_texts(10_000, 20, 20) == 10_401
    # The unwritten tail holds one warm-up turn and the requested batches.
    assert hosted_profile.required_texts(10_000, 20, 20) - 10_000 == 1 + 20 * 20
    assert hosted_profile.required_texts(10_000, 50, 10) == 10_501


def test_corpus_uses_the_graph_oracle_pad(monkeypatch):
    from benchmarks.graph_oracle import scale

    monkeypatch.setattr(scale, "conversation_pad", lambda count: [f"turn {i}" for i in range(count)])

    assert hosted_profile.corpus(3) == ["turn 0", "turn 1", "turn 2"]


@pytest.mark.asyncio
async def test_drain_materialization_refuses_leftover_work():
    from prme.models.processing import ProcessingResult

    class Engine:
        async def process_pending(self, *, user_id, budget_ms):
            assert user_id == "owner"
            assert budget_ms == hosted_profile.MATERIALIZATION_BUDGET_MS
            return ProcessingResult(processed=0, pending=1, failed=0)

    with pytest.raises(ValueError, match="pending"):
        await hosted_profile.drain_materialization(Engine(), "owner")


@pytest.mark.asyncio
async def test_measure_requests_refuses_a_corpus_that_is_too_small():
    with pytest.raises(ValueError, match="corpus is smaller"):
        await hosted_profile.measure_requests(None, "owner", requests=1, store=True, texts=["only"])


def test_stage_recorder_ignores_warm_up_samples():
    recorder = hosted_profile.StageRecorder(("packing",))
    recorder.record("packing", 99.0, 99.0)  # before the first next_request: warm-up
    recorder.next_request()
    recorder.record("packing", 5.0, 2.0)

    report = recorder.report()

    assert report["packing"]["wall_ms"]["p50"] == 5.0
    assert report["packing"]["calls_per_request"] == 1.0


def test_instrumented_restores_earlier_bindings_when_a_later_one_is_missing():
    class Target:
        def good(self):
            return 1

    target = Target()
    original = target.good
    recorder = hosted_profile.StageRecorder(("a", "b"))

    with pytest.raises(AttributeError):
        with hosted_profile.instrumented(target, (("a", "good"), ("b", "missing")), recorder):
            pass

    assert target.good.__func__ is original.__func__


@pg_required
@pytest.mark.asyncio
async def test_profile_runs_end_to_end(tmp_path, monkeypatch):
    # The benchmark data is not in the repository or in CI, so the test supplies its own turns.
    monkeypatch.setattr(hosted_profile, "corpus", lambda count: [f"profile turn {i}" for i in range(count)])

    report = await hosted_profile.run(
        database_url=PG_URL, nodes=30, requests=1, batch_size=2, out=tmp_path,
    )

    assert report["kind"] == hosted_profile.KIND
    assert report["backend"] == "postgres"
    assert report["nodes"] == 30
    assert set(report["shapes"]) == {"retrieve", "ingest_fast", "store"}
    retrieve = report["shapes"]["retrieve"]
    assert retrieve["requests"] == 1
    assert set(hosted_profile.RETRIEVE_STAGES) <= set(retrieve["stages"])
    store = report["shapes"]["store"]
    assert set(hosted_profile.WRITE_STAGES) <= set(store["stages"])
    ingest = report["shapes"]["ingest_fast"]
    assert set(hosted_profile.WRITE_STAGES) <= set(ingest["stages"])
    assert "materialization_wall_ms" in ingest
    assert ingest["items_per_request"] == 2
    assert report["storage_identity"]
    assert report["embedding_identity"]["model_name"]
    assert (tmp_path / "hosted-profile.json").exists()
    assert report["profile_top"]
    assert all(row["function"] and row["calls"] >= 1 for row in report["profile_top"])


def test_markdown_names_every_shape():
    report = {
        "engine_version": "0.17.0a2", "backend": "postgres", "nodes": 10, "requests_per_shape": 1,
        "machine": {"platform": "test", "cpu_count": 1},
        "shapes": {
            "retrieve": {"wall_ms": {"p50": 1, "p95": 2}, "cpu_ms": {"p50": 1, "p95": 2},
                         "stages": {"packing": {"wall_ms": {"p50": 1, "p95": 2},
                                                "cpu_ms": {"p50": 1, "p95": 2}, "calls_per_request": 1}}},
            "store": {"wall_ms": {"p50": 1, "p95": 2}, "cpu_ms": {"p50": 1, "p95": 2}, "stages": {}},
            "ingest_fast": {"wall_ms": {"p50": 1, "p95": 2}, "cpu_ms": {"p50": 1, "p95": 2}, "stages": {}},
        },
    }

    text = hosted_profile.markdown(report)

    for name in ("retrieve", "store", "ingest_fast", "packing"):
        assert name in text
