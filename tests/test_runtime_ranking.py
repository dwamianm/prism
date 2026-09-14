"""Request-time ranking trials must run downstream selection and packing afresh."""
import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from prme import MemoryClient, MemoryEngine, RankingMultipliers, RetrievalReceipt
from prme.retrieval import pipeline
from prme.retrieval.config import ScoringWeights
from prme.retrieval.learning import proposed_score
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.reranker import CrossEncoderReranker
from prme.types import RepresentationLevel, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user
NOW = datetime(2026, 9, 12, 18, tzinfo=timezone.utc)


def test_version_two_canonical_bytes_and_checksum_are_preserved():
    raw = (Path(__file__).parent / "fixtures/relevance/receipt-v2.json").read_text()
    expected = "8a3778c6c493cd17ad70ac978e6fb19769bee8723d649abe05d7ae7e2b1b21dd"
    assert hashlib.sha256(raw.encode()).hexdigest() == expected
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 2
    assert restored.model_dump_json() == raw and restored.checksum == expected
    assert "execution" not in restored.model_dump()
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)


async def test_weight_trial_changes_session_expansion_and_preserves_owner_scope(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        for session in ("semantic", "lexical"):
            await engine.store(session + " trigger", user_id=user, scope=Scope.PROJECT, session_id=session)
            await engine.store(session + " adjacent", user_id=user, scope=Scope.PROJECT, session_id=session)
            await engine.store(session + " foreign", user_id=user + "-other", session_id=session)
        nodes = await engine._graph_store.query_nodes(user_id=user, scopes=[Scope.PROJECT])
        triggers = [n for n in nodes if n.content.endswith("trigger")]

        async def generate(*args, **kwargs):
            return [RetrievalCandidate(node=node,
                semantic_score=.9 if node.session_id == "semantic" else .2,
                lexical_score=.1 if node.session_id == "semantic" else .9,
                graph_proximity=1 if node.session_id == "semantic" else 0) for node in triggers], {"VECTOR": 2}

        monkeypatch.setattr(pipeline, "generate_candidates", generate)
        engine._retrieval_pipeline._packing_config = config.packing.model_copy(update={"session_context_top_k": 1})
        original_weights = engine._config.scoring.model_dump_json()
        args = dict(user_id=user, scope=Scope.PROJECT, reference_time=NOW, include_cross_scope=False)
        baseline = await engine.retrieve("trigger", **args)
        adjustment = RankingMultipliers(semantic=.25, lexical=4, graph=.25)
        proposed = await engine.retrieve("trigger", ranking_multipliers=adjustment, **args)
        assert {c.node.content for c in baseline.results if "SESSION_CONTEXT" in c.paths} == {"semantic adjacent"}
        assert {c.node.content for c in proposed.results if "SESSION_CONTEXT" in c.paths} == {"lexical adjacent"}
        assert all(c.node.user_id == user and c.node.scope == Scope.PROJECT for c in proposed.results)
        assert "lexical adjacent" in proposed.bundle.render()
        assert "semantic adjacent" not in proposed.bundle.render()
        assert proposed.metadata.ranking_multipliers == adjustment
        assert engine._config.scoring.model_dump_json() == original_weights
        saved = await engine.get_retrieval_receipt(str(proposed.metadata.request_id), user_id=user)
        assert saved.schema_version == 6
        assert saved.packing.multipath_ordering == "balanced"
        assert saved.execution.parameters["ranking_multipliers"] == adjustment.model_dump(mode="json")
        assert saved.replay_ranking() == tuple(c.node.id for c in proposed.results)
    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_retrieval_receipt(str(saved.request_id), user_id=user)
        assert restored.checksum == saved.checksum


async def test_unity_trial_is_identical_and_concurrent_calls_keep_separate_adjustments(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue", user_id=user, scope=Scope.PROJECT)
        args = dict(user_id=user, scope=Scope.PROJECT, reference_time=NOW)
        baseline, unity, trial = await asyncio.gather(
            engine.retrieve("What telescope do I currently use?", **args),
            engine.retrieve("What telescope do I currently use?", ranking_multipliers=RankingMultipliers(), **args),
            engine.retrieve("What telescope do I currently use?", ranking_multipliers=RankingMultipliers(lexical=4), **args))
        assert baseline.bundle.render() == unity.bundle.render()
        assert [c.composite_score for c in baseline.results] == [c.composite_score for c in unity.results]
        assert baseline.metadata.ranking_multipliers is None
        baseline_by_id = {c.node.id: c for c in baseline.results}
        for candidate in trial.results:
            assert candidate.composite_score == proposed_score(baseline_by_id[candidate.node.id].score_provenance,
                                                                RankingMultipliers(lexical=4))


async def test_execution_records_filters_and_current_neural_model(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await engine.store("telescope", user_id=user, scope=Scope.PROJECT, event_time=NOW - timedelta(days=2))
        # Admission time is the real clock. A fixed historical knowledge cutoff
        # eventually excludes this newly stored source, correctly yielding none.
        known_at = datetime.now(timezone.utc)
        reranker = CrossEncoderReranker(model_name="controlled-neural-model")
        monkeypatch.setattr(reranker, "_predict_sync", Mock(return_value=[.8]))
        engine._retrieval_pipeline._reranker = reranker
        response = await engine.retrieve("telescope", user_id=user, scope=Scope.PROJECT, reference_time=NOW,
            knowledge_at=known_at, event_time_from=NOW - timedelta(days=3), event_time_to=NOW - timedelta(days=1),
            include_cross_scope=False)
        assert response.results
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        params, features = saved.execution.parameters, saved.execution.features
        assert params["knowledge_at"] == known_at.isoformat()
        assert params["event_time_from"] == (NOW - timedelta(days=3)).isoformat()
        assert params["event_time_to"] == (NOW - timedelta(days=1)).isoformat()
        assert params["include_cross_scope"] is False
        assert features["embedding"]["model"] == test_durable_ingestion.MockEmbeddingProvider.model_name
        assert features["reranker"]["enabled"] is True
        assert features["reranker"]["model"] == "controlled-neural-model"
        assert all(len(digest) == 64 for digest in features["source_files_sha256"].values())


def test_sync_client_exposes_full_ranking_and_temporal_controls(config, user):
    with MemoryClient(config=config) as client:
        client.store("telescope on Monday", user_id=user, event_time=NOW - timedelta(days=2))
        response = client.retrieve("telescope", user_id=user, reference_time=NOW,
            event_time_from=NOW - timedelta(days=3), event_time_to=NOW - timedelta(days=1),
            weights=ScoringWeights(), ranking_multipliers=RankingMultipliers(lexical=2),
            min_fidelity=RepresentationLevel.FULL, include_cross_scope=False)
        assert response.results
        saved = client.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.execution.parameters["ranking_multipliers"]["lexical"] == 2
        assert saved.packing.min_fidelity == RepresentationLevel.FULL


async def test_reported_pipeline_latency_includes_receipt_work(config, user, monkeypatch):
    from prme.models import relevance
    original = relevance.make_receipt

    def delayed_receipt(**kwargs):
        time.sleep(.02)
        return original(**kwargs)

    async with MemoryEngine.open(config) as engine:
        await engine.store("telescope", user_id=user)
        monkeypatch.setattr(relevance, "make_receipt", delayed_receipt)
        response = await engine.retrieve("telescope", user_id=user)
        assert response.metadata.receipt_persisted
        assert response.metadata.receipt_logging_ms >= 20
        assert response.metadata.timing_ms >= response.metadata.receipt_logging_ms
