"""Opt-in reciprocal rank fusion (score formula version 2) and its receipts."""

import hashlib
import json
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.models.learning import RankingMultipliers
from prme.models.nodes import MemoryNode
from prme.models.relevance import RelevanceRecord, RetrievalReceipt, make_receipt
from prme.quality.feedback import FeedbackSignal, FeedbackSignalType
from prme.quality.tuner import WeightTuner
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.learning import evaluate_learning, proposed_score
from prme.retrieval.models import (
    MemoryBundle,
    QueryAnalysis,
    RankFusion,
    RetrievalCandidate,
    ScoreAdjustment,
    ScoreProvenance,
)
from prme.retrieval.scoring import compute_composite_score, score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import EpistemicType, NodeType, QueryIntent, Scope
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_ranking_profiles import _evidence

config = test_durable_ingestion.config
user = test_durable_ingestion.user

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
RRF = ScoringWeights(fusion="rrf")
EXECUTION = RetrievalExecution(features={"test": True}, parameters={})


def candidate(number, *, semantic=None, lexical=None, days=0, content="The telescope is blue.",
              node_type=NodeType.FACT, epistemic=EpistemicType.ASSERTED, session=None,
              salience=.5, confidence=.8, graph=0.0):
    ts = NOW - timedelta(days=days)
    paths = [name for name, value in (("VECTOR", semantic), ("LEXICAL", lexical)) if value is not None]
    return RetrievalCandidate(node=MemoryNode(
        id=UUID(int=number), user_id="owner", scope=Scope.PROJECT, node_type=node_type,
        content=content, created_at=ts, updated_at=ts, last_reinforced_at=ts, event_time=ts,
        session_id=session, confidence_base=confidence, salience_base=salience,
        epistemic_type=epistemic,
    ), semantic_score=semantic or 0.0, lexical_score=lexical or 0.0, graph_proximity=graph,
        paths=paths, path_count=len(paths))


def fused(ranks, k=60):
    return round(sum(1 / (k + rank) for rank in ranks) * (k + 1) / 2, 10)


def receipt(items, *, weights=RRF, policy="score_path_id", execution=EXECUTION):
    return make_receipt(request_id=UUID(int=100), user_id="owner", query="telescope",
                        reference_time=NOW, scopes=(Scope.PROJECT,), scoring=weights,
                        packing=PackingConfig(), candidates=items, bundle=MemoryBundle(),
                        ranking_policy=policy, execution=execution)


# --- Configuration -----------------------------------------------------------


def test_weighted_default_keeps_its_bytes_and_version():
    # Recorded on main before rank fusion existed.
    assert DEFAULT_SCORING_WEIGHTS.version_id == "95f3ec502933"
    assert hashlib.sha256(DEFAULT_SCORING_WEIGHTS.model_dump_json().encode()).hexdigest() == (
        "6e65ba7cf8359c462c2509e61d6a8d852847da322c6551de8e0eaaefd248a2ec"
    )
    assert not {"fusion", "rrf_k"} & DEFAULT_SCORING_WEIGHTS.model_dump(mode="json").keys()
    assert ScoringWeights.model_validate(DEFAULT_SCORING_WEIGHTS.model_dump()) == DEFAULT_SCORING_WEIGHTS


def test_rank_fusion_settings_are_serialized_versioned_and_validated():
    assert RRF.model_dump(mode="json")["fusion"] == "rrf"
    assert RRF.model_dump(mode="json")["rrf_k"] == 60
    assert DEFAULT_SCORING_WEIGHTS.rrf_k is None
    assert ScoringWeights.model_validate_json(RRF.model_dump_json()) == RRF
    assert len({DEFAULT_SCORING_WEIGHTS.version_id, RRF.version_id,
                ScoringWeights(fusion="rrf", rrf_k=30).version_id}) == 3
    for bad in (0, 10_001):
        with pytest.raises(ValidationError):
            ScoringWeights(fusion="rrf", rrf_k=bad)
    with pytest.raises(ValidationError):
        ScoringWeights(fusion="max")


def test_a_stray_rank_constant_is_ignored_with_a_warning(monkeypatch):
    # Turning rank fusion off by removing only the fusion setting must not
    # stop the engine from starting.
    with pytest.warns(UserWarning, match="rrf_k applies only"):
        weights = ScoringWeights(rrf_k=30)
    assert weights == DEFAULT_SCORING_WEIGHTS
    monkeypatch.setenv("PRME_SCORING__RRF_K", "30")
    with pytest.warns(UserWarning, match="rrf_k applies only"):
        assert PRMEConfig(_env_file=None).scoring == DEFAULT_SCORING_WEIGHTS


def test_rank_fusion_is_selected_from_the_environment(monkeypatch):
    monkeypatch.setenv("PRME_SCORING__FUSION", "rrf")
    monkeypatch.setenv("PRME_SCORING__RRF_K", "30")
    scoring = PRMEConfig(_env_file=None).scoring
    assert (scoring.fusion, scoring.rrf_k) == ("rrf", 30)


# --- Scoring ------------------------------------------------------------------


def test_scores_are_reciprocal_ranks_scaled_so_first_on_both_is_one():
    items = [candidate(1, semantic=.9, lexical=1.0), candidate(2, semantic=.8, lexical=.2),
             candidate(3, semantic=.7), candidate(4, lexical=.6)]
    ranked, traces = score_and_rank(items, RRF, now=NOW)
    scores = {c.node.id.int: c.composite_score for c in ranked}
    assert scores == {1: 1.0, 2: fused([2, 3]), 3: fused([3]), 4: fused([2])}
    assert [c.node.id.int for c in ranked] == [1, 2, 4, 3]
    assert [t.composite_score for t in traces] == [c.composite_score for c in ranked]
    fusion = ranked[1].score_provenance.rank_fusion
    assert (fusion.semantic_rank, fusion.lexical_rank) == (2, 3)
    assert ranked[1].score_provenance.formula_version == 2


def test_rank_constant_is_applied_and_replayed():
    weights = ScoringWeights(fusion="rrf", rrf_k=5)
    items = [candidate(1, semantic=.9, lexical=.1), candidate(2, semantic=.2, lexical=.9),
             candidate(3, semantic=.5)]
    ranked, _ = score_and_rank(items, weights, now=NOW)
    scores = {c.node.id.int: c.composite_score for c in ranked}
    assert scores == {1: fused([1, 2], k=5), 2: fused([3, 1], k=5), 3: fused([2], k=5)}
    saved = receipt(ranked, weights=weights)
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert {nid: p.replay_score() for nid, p in restored.score_provenance.items()} == {
        c.node.id: c.composite_score for c in ranked
    }


def test_ties_share_a_rank_and_a_zero_lexical_hit_is_still_on_the_channel():
    # Min-max normalization gives the weakest lexical hit 0.0.
    items = [candidate(1, semantic=.5, lexical=1.0), candidate(2, semantic=.5, lexical=0.0),
             candidate(3, semantic=.4)]
    ranked, _ = score_and_rank(items, RRF, now=NOW)
    ranks = {c.node.id.int: (c.score_provenance.rank_fusion.semantic_rank,
                             c.score_provenance.rank_fusion.lexical_rank) for c in ranked}
    assert ranks == {1: (1, 1), 2: (1, 2), 3: (3, None)}


def test_candidates_on_neither_channel_score_zero_and_do_not_set_the_factors():
    # An observed fact reached only through the graph would otherwise lower
    # every ranked candidate's epistemic and node-type factors.
    graph_only = candidate(1, graph=1.0, epistemic=EpistemicType.OBSERVED)
    event = candidate(2, semantic=.1, node_type=NodeType.EVENT)
    ranked, _ = score_and_rank([graph_only, event], RRF, now=NOW)
    assert [c.node.id.int for c in ranked] == [2, 1]
    assert ranked[0].composite_score == 0.5
    fusion = ranked[0].score_provenance.rank_fusion
    assert (fusion.epistemic_factor, fusion.node_type_factor) == (1.0, 1.0)
    assert ranked[1].composite_score == 0.0
    assert ranked[1].score_trace.graph_proximity == 1.0
    assert ranked[1].score_provenance.rank_fusion.node_type_factor == 1.0  # capped


def test_constant_and_query_independent_signals_do_not_move_scores():
    items = [candidate(1, semantic=.9, lexical=.3), candidate(2, semantic=.4, lexical=.8)]
    baseline = {c.node.id: c.composite_score for c in score_and_rank(items, RRF, now=NOW)[0]}
    changed = [candidate(1, semantic=.9, lexical=.3, days=400, salience=.01, confidence=.1),
               candidate(2, semantic=.4, lexical=.8, salience=1.0, confidence=1.0, graph=.7)]
    ranked, traces = score_and_rank(changed, RRF, now=NOW)
    assert {c.node.id: c.composite_score for c in ranked} == baseline
    assert all((t.recency_factor, t.salience, t.confidence) == (0.0, 0.0, 0.0) for t in traces)


def test_ranking_is_independent_of_input_order_and_repeatable():
    rng = random.Random(7)
    items = [candidate(i, semantic=round(rng.random(), 2) if i % 3 else None,
                       lexical=round(rng.random(), 1) if i % 4 else None) for i in range(1, 40)]
    first, _ = score_and_rank([c.model_copy(deep=True) for c in items], RRF, now=NOW)
    shuffled = [c.model_copy(deep=True) for c in items]
    rng.shuffle(shuffled)
    second, _ = score_and_rank(shuffled, RRF, now=NOW)
    assert [(c.node.id, c.composite_score) for c in first] == [(c.node.id, c.composite_score) for c in second]
    assert [c.score_provenance for c in first] == [c.score_provenance for c in second]


def test_adjustments_are_relative_to_the_pool_and_neutral_when_shared():
    shared = [candidate(1, semantic=.9), candidate(2, semantic=.8)]
    ranked, _ = score_and_rank(shared, RRF, now=NOW)
    for item in ranked:
        fusion = item.score_provenance.rank_fusion
        assert (fusion.epistemic_factor, fusion.node_type_factor, fusion.temporal_factor) == (1, 1, 1)
        assert item.score_trace.epistemic_weight == .9 and item.score_trace.node_type_boost == 1.15

    mixed = [candidate(1, semantic=.9, node_type=NodeType.EVENT, epistemic=EpistemicType.HYPOTHETICAL),
             candidate(2, semantic=.8)]
    ranked, _ = score_and_rank(mixed, RRF, now=NOW)
    event = next(c for c in ranked if c.node.id.int == 1).score_provenance.rank_fusion
    assert event.node_type_factor == pytest.approx(1 / 1.15)
    assert event.epistemic_factor == pytest.approx(.3 / .9)
    assert [c.node.id.int for c in ranked] == [2, 1]


def test_temporal_affinity_applies_only_to_temporal_questions():
    dated = candidate(1, semantic=.8, content="We met on 2026-09-10.", days=2)
    plain = candidate(2, semantic=.9, content="We met at the cafe.", days=200)
    window = dict(time_from=NOW - timedelta(days=3), time_to=NOW)
    temporal = QueryAnalysis(query="When did we meet?", intent=QueryIntent.TEMPORAL, **window)
    ranked, _ = score_and_rank([dated.model_copy(deep=True), plain.model_copy(deep=True)],
                               RRF, now=NOW, query_analysis=temporal)
    factors = {c.node.id.int: c.score_provenance.rank_fusion.temporal_factor for c in ranked}
    assert factors[1] == 1.0 and factors[2] < 1.0
    factual = QueryAnalysis(query="Where did we meet?", intent=QueryIntent.FACTUAL, **window)
    ranked, _ = score_and_rank([dated, plain], RRF, now=NOW, query_analysis=factual)
    assert all(c.score_provenance.rank_fusion.temporal_factor == 1.0 for c in ranked)
    assert all(c.score_trace.temporal_affinity == 0.0 for c in ranked)


def test_query_specific_weight_shifts_do_not_apply():
    query = QueryAnalysis(query="What telescope do I currently use?", intent=QueryIntent.FACTUAL)
    ranked, _ = score_and_rank([candidate(1, semantic=.9), candidate(2, semantic=.5, days=100)],
                               RRF, now=NOW, query_analysis=query)
    assert all(c.score_provenance.weights == RRF for c in ranked)
    assert [c.node.id.int for c in ranked] == [1, 2]


def test_current_update_multiplier_applies_after_fusion_above_the_relevance_floor():
    old = candidate(1, semantic=.85, lexical=1.0, days=1, content="The budget is OLD dollars.")
    update = candidate(2, semantic=.80, lexical=.05, content="The updated budget is NEW dollars.")
    query = QueryAnalysis(query="What is the current budget?", intent=QueryIntent.FACTUAL)
    ranked, _ = score_and_rank([old, update], RRF, now=NOW, query_analysis=query)
    boosted = next(c for c in ranked if c.node.id.int == 2)
    assert [op.kind for op in boosted.score_provenance.adjustments] == ["current_update"]
    assert boosted.composite_score == pytest.approx(boosted.score_trace.composite_score * 1.3)

    weak = candidate(2, semantic=.1, lexical=.05, content="The updated budget is NEW dollars.")
    ranked, _ = score_and_rank([old.model_copy(deep=True), weak], RRF, now=NOW, query_analysis=query)
    weak = next(c for c in ranked if c.node.id.int == 2)
    assert weak.score_provenance.adjustments == ()
    assert weak.composite_score == weak.score_trace.composite_score


def test_model_copy_without_a_rank_constant_is_rejected():
    # Trusted model_copy skips validation; scoring must not guess the constant.
    unset = DEFAULT_SCORING_WEIGHTS.model_copy(update={"fusion": "rrf"})
    with pytest.raises(ValueError, match="requires rrf_k"):
        score_and_rank([candidate(1, semantic=.9)], unset, now=NOW)


def test_learned_multipliers_are_rejected_unless_neutral():
    items = [candidate(1, semantic=.9)]
    with pytest.raises(ValueError, match="do not apply to fusion='rrf'"):
        score_and_rank(items, RRF, now=NOW, ranking_multipliers=RankingMultipliers(semantic=2))
    ranked, _ = score_and_rank(items, RRF, now=NOW, ranking_multipliers=RankingMultipliers())
    assert ranked[0].composite_score == 0.5  # first on its only channel
    with pytest.raises(ValueError, match="use score_and_rank"):
        compute_composite_score(items[0], RRF, now=NOW)


def test_non_finite_channel_scores_are_rejected():
    with pytest.raises(ValueError, match="finite"):
        score_and_rank([candidate(1, semantic=float("nan"))], RRF, now=NOW)


def test_negative_adjustments_are_rejected_with_a_clear_error():
    weights = ScoringWeights(fusion="rrf", node_type_boost={"event": -1.0})
    with pytest.raises(ValueError, match="nonnegative"):
        score_and_rank([candidate(1, semantic=.5, node_type=NodeType.EVENT)], weights, now=NOW)
    with pytest.raises(ValueError, match="nonnegative"):
        score_and_rank([candidate(1, semantic=.5)], RRF, now=NOW, epistemic_weights={"asserted": -1.0})


# --- Provenance and receipts --------------------------------------------------


def test_provenance_replays_formula_two_from_saved_ranks_alone():
    ranked, _ = score_and_rank([candidate(1, semantic=.9, lexical=.1), candidate(2, semantic=.2, lexical=.9),
                                candidate(3, semantic=.5)], RRF, now=NOW)
    for item in ranked:
        provenance = item.score_provenance
        assert provenance.replay_score() == item.composite_score
        item.node.content = "changed"
        item.node.epistemic_type = EpistemicType.DEPRECATED
        restored = ScoreProvenance.model_validate_json(provenance.model_dump_json())
        assert restored == provenance and restored.replay_score() == item.composite_score


def test_formula_one_provenance_keeps_its_bytes():
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], now=NOW)
    provenance = ranked[0].score_provenance
    assert provenance.formula_version == 1 and provenance.rank_fusion is None
    assert "rank_fusion" not in json.loads(provenance.model_dump_json())
    assert not {"fusion", "rrf_k"} & json.loads(provenance.model_dump_json())["weights"].keys()


@pytest.mark.parametrize("change", ["version_one", "weighted", "missing", "factor", "score"])
def test_inconsistent_formula_two_provenance_is_rejected(change):
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], RRF, now=NOW)
    data = json.loads(ranked[0].score_provenance.model_dump_json())
    if change == "version_one":
        data["formula_version"] = 1
    elif change == "weighted":
        data["weights"].pop("fusion"), data["weights"].pop("rrf_k")
    elif change == "missing":
        data.pop("rank_fusion")
    elif change == "factor":
        data["rank_fusion"]["epistemic_factor"] = 1.5
    else:
        data["rank_fusion"]["semantic_rank"] = 2
    with pytest.raises(ValidationError):
        ScoreProvenance.model_validate(data)


def test_rank_fusion_inputs_are_rejected_on_formula_one():
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], now=NOW)
    data = json.loads(ranked[0].score_provenance.model_dump_json())
    data["rank_fusion"] = RankFusion(semantic_rank=1, epistemic_factor=1, node_type_factor=1,
                                     temporal_factor=1).model_dump(mode="json")
    with pytest.raises(ValidationError, match="version 2"):
        ScoreProvenance.model_validate(data)


def test_rank_fusion_receipt_is_version_15_and_replays_its_ranking():
    ranked, _ = score_and_rank([candidate(i, semantic=i / 10, lexical=1 - i / 10) for i in range(1, 6)],
                               RRF, now=NOW)
    saved = receipt(ranked)
    assert saved.schema_version == 15
    assert saved.replay_ranking() == tuple(c.node.id for c in ranked)
    data = json.loads(saved.model_dump_json())
    assert data["scoring"]["fusion"] == "rrf"
    assert all(item["formula_version"] == 2 and item["rank_fusion"]
               for item in data["score_provenance"].values())
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.model_dump_json() == saved.model_dump_json()
    assert restored.checksum == saved.checksum
    with pytest.raises(ValueError, match="execution descriptor"):
        receipt(ranked, execution=None)


def test_weighted_receipts_keep_their_version_and_bytes():
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], now=NOW)
    saved = receipt(ranked, weights=DEFAULT_SCORING_WEIGHTS)
    assert saved.schema_version == 12
    data = json.loads(saved.model_dump_json())
    assert "fusion" not in data["scoring"]
    assert "rank_fusion" not in next(iter(data["score_provenance"].values()))


@pytest.mark.parametrize("version", [9, 12, 13, 14])
def test_earlier_receipt_versions_cannot_claim_rank_fusion(version):
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], RRF, now=NOW)
    data = json.loads(receipt(ranked).model_dump_json())
    data["schema_version"] = version
    with pytest.raises(ValidationError, match="version 15"):
        RetrievalReceipt.model_validate(data)


@pytest.mark.parametrize("where", ["scoring", "provenance"])
def test_version_15_requires_an_explicit_rank_constant(where):
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], RRF, now=NOW)
    data = json.loads(receipt(ranked).model_dump_json())
    target = data["scoring"] if where == "scoring" else next(iter(data["score_provenance"].values()))["weights"]
    target.pop("rrf_k")
    with pytest.raises(ValidationError, match="explicit rank fusion constant"):
        RetrievalReceipt.model_validate(data)


def test_version_15_is_only_for_rank_fusion():
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], now=NOW)
    data = json.loads(receipt(ranked, weights=DEFAULT_SCORING_WEIGHTS).model_dump_json())
    data["schema_version"] = 15
    data["packing"]["context_citations"] = False
    with pytest.raises(ValidationError, match="rank fusion scoring only"):
        RetrievalReceipt.model_validate(data)


def test_rank_fusion_receipts_keep_reader_and_rank_assignment_features():
    ranked, _ = score_and_rank([candidate(1, semantic=.9), candidate(2, semantic=.4)], RRF, now=NOW)
    first = ranked[0]
    provenance = first.score_provenance.model_copy(update={"adjustments": (
        ScoreAdjustment(kind="neural_rank_assignment", coefficient=.9, source_node_id=first.node.id),
    )})
    ranked[0] = first.model_copy(update={"score_provenance": provenance,
                                         "composite_score": provenance.replay_score()})
    packing = PackingConfig(context_format="reader", context_citations=True)
    saved = make_receipt(request_id=UUID(int=101), user_id="owner", query="telescope",
                         reference_time=NOW, scopes=(Scope.PROJECT,), scoring=RRF, packing=packing,
                         candidates=ranked, bundle=MemoryBundle(), ranking_policy="score_id",
                         execution=EXECUTION)
    assert saved.schema_version == 15
    assert json.loads(saved.model_dump_json())["packing"]["context_citations"] is True
    assert saved.replay_ranking() == tuple(c.node.id for c in ranked)
    assert RetrievalReceipt.model_validate_json(saved.model_dump_json()).checksum == saved.checksum


async def test_session_neighbors_inherit_replayable_fused_scores():
    trigger = candidate(1, semantic=.9, lexical=.9, days=1, session="conversation")
    other = candidate(3, semantic=.5, lexical=.5, days=3)
    neighbor = candidate(2, session="conversation")
    ranked, _ = score_and_rank([trigger, other], RRF, now=NOW)
    graph = Mock(query_nodes=AsyncMock(return_value=[trigger.node, neighbor.node]))
    expanded = await expand_session_context(ranked, graph, "owner", PackingConfig(), [Scope.PROJECT])
    added = next(c for c in expanded if c.node.id == neighbor.node.id)
    assert added.composite_score == pytest.approx(.85)
    assert added.score_provenance.formula_version == 2
    saved = receipt(expanded, policy="score_id")
    assert saved.schema_version == 15
    assert saved.replay_ranking() == tuple(c.node.id for c in expanded)


# --- Sibling paths ------------------------------------------------------------


def test_learning_excludes_rank_fused_receipts_and_rejects_their_provenance():
    ranked, _ = score_and_rank([candidate(1, semantic=.9)], RRF, now=NOW)
    with pytest.raises(ValueError, match="weighted-formula receipts"):
        proposed_score(ranked[0].score_provenance, RankingMultipliers(semantic=2))

    def labelled(number, weights):
        items, _ = score_and_rank([candidate(1, semantic=.9, lexical=.5), candidate(2, semantic=.3)],
                                  weights, now=NOW)
        saved = make_receipt(request_id=UUID(int=number), user_id="owner", query=f"telescope {number}",
                             reference_time=NOW, scopes=(Scope.PROJECT,), scoring=weights,
                             packing=PackingConfig(), candidates=items, bundle=MemoryBundle(),
                             execution=EXECUTION)
        record = RelevanceRecord(request_id=saved.request_id, labels={items[0].node.id: True,
                                 items[1].node.id: False}, user_id="owner", recorded_at=NOW,
                                 receipt_checksum=saved.checksum)
        return saved, record

    weighted, weighted_label = labelled(200, DEFAULT_SCORING_WEIGHTS)
    fused_receipt, fused_label = labelled(201, RRF)
    report = evaluate_learning([weighted, fused_receipt], [weighted_label, fused_label],
                               user_id="owner", scopes=[Scope.PROJECT])
    assert report.exclusions["rank_fusion_receipt_records"] == 1


def test_feedback_tuning_keeps_rank_fusion():
    original = ScoringWeights(fusion="rrf", rrf_k=30)
    tuned = WeightTuner(original).update([FeedbackSignal(
        query="telescope", surfaced_node_ids=["n1"], signal_type=FeedbackSignalType.USED,
    )])
    assert tuned.version_id != original.version_id
    assert (tuned.fusion, tuned.rrf_k) == ("rrf", 30)


async def test_engine_retrieval_with_rank_fusion_persists_a_replayable_receipt(config, user):
    config = config.model_copy(update={"scoring": RRF})
    async with MemoryEngine.open(config) as engine:
        for text in ("The telescope is blue.", "The telescope lens is cracked.", "We bought bread."):
            await engine.store(text, user_id=user, scope=Scope.PROJECT, session_id="s1")
        response = await engine.retrieve("telescope", user_id=user, scope=Scope.PROJECT,
                                         min_score=0, include_cross_scope=False)
        assert response.results
        assert response.metadata.scoring_config_version == RRF.version_id
        # First on both channels is 1.0; only the current-update multiplier can exceed it.
        assert all(0 <= item.composite_score <= 1 for item in response.results)
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.schema_version == 15
        assert saved.scoring == RRF
        assert saved.replay_ranking() == tuple(item.node.id for item in response.results)
        assert {p.formula_version for p in saved.score_provenance.values()} == {2}


async def test_request_multipliers_are_rejected_before_retrieval_on_every_interface(config, user):
    config = config.model_copy(update={"scoring": RRF, "mcp": MCPConfig(user_id=user)})
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
        pipeline = engine._retrieval_pipeline
        engine._retrieval_pipeline = Mock(retrieve=AsyncMock(side_effect=AssertionError("ran")))
        with pytest.raises(ValueError, match="do not apply to fusion='rrf'"):
            await engine.retrieve("telescope", user_id=user,
                                  ranking_multipliers=RankingMultipliers(lexical=2))
        engine._retrieval_pipeline = pipeline

        async with client_for(app_for(config, engine, user)) as client:
            response = await client.post("/v1/retrieve", json={
                "query": "telescope", "ranking_multipliers": {"lexical": 2},
            })
            assert response.status_code == 422, response.text
            assert "fusion='rrf'" in response.json()["detail"]

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(
            server._mcp_server, raise_exceptions=True,
        ) as session:
            await session.initialize()
            reply = await session.call_tool("memory_retrieve", {
                "query": "telescope", "ranking_multipliers": {"lexical": 2},
            })
            assert "fusion='rrf'" in json.loads(reply.content[0].text)["error"]


async def test_weighted_ranking_profiles_are_not_applied_to_rank_fusion(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
        proposal, holdout = _evidence(engine, RankingMultipliers(lexical=2), owner=user)
        profile = await engine.create_ranking_profile(proposal, holdout, user_id=user)
        await engine.activate_ranking_profile(str(profile.profile_id), user_id=user)
        response = await engine.retrieve("telescope", user_id=user, scope=Scope.PROJECT, weights=RRF)
        assert response.metadata.ranking_profile_status == "inapplicable"
        assert response.metadata.ranking_profile_reason == "rank_fusion_scoring"
        assert response.metadata.ranking_multipliers is None
        assert response.results


async def test_feedback_tuning_is_not_applied_under_rank_fusion(config, user):
    config = config.model_copy(update={"scoring": RRF})
    async with MemoryEngine.open(config) as engine:
        await engine.feedback(FeedbackSignal(query="telescope", surfaced_node_ids=["n1"],
                                             signal_type=FeedbackSignalType.USED))
        result = await engine.organize(jobs=["feedback_apply"], budget_ms=5000)
        assert result.per_job["feedback_apply"].details["status"] == "not_applicable"
        assert engine._config.scoring == RRF
        assert len(engine._feedback_tracker) == 1
