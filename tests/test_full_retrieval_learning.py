"""Full retrieval learning evaluates fresh candidate sets on a fixed holdout."""
from datetime import datetime, timezone
from uuid import UUID

import pytest

from prme import (
    FullRetrievalEvaluation,
    FullRetrievalEvaluationConfig,
    FullRetrievalTrial,
    RankingMultipliers,
    evaluate_full_retrieval,
)
from prme.models.nodes import MemoryNode
from prme.models.relevance import make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.retrieval.scoring import score_and_rank
from prme.types import NodeType, Scope

NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)
PROPOSAL = RankingMultipliers(lexical=2)
FEATURES = {
    "embedding": {"provider": "test", "model": "fixed-v1", "dimension": 3},
    "reranker": {"enabled": False, "provider": "builtins.NoneType", "model": None},
    "scorer": "test-v1",
}
CHECKSUM = "a" * 64
ARTIFACT = "b" * 64


def _receipt(
    *, request_id: int, query: str, node_id: int, multipliers=None,
    features=FEATURES, parameter="fixed",
):
    candidate = RetrievalCandidate(node=MemoryNode(
        id=UUID(int=node_id), user_id="owner", scope=Scope.PROJECT,
        node_type=NodeType.FACT, content=f"memory {node_id}", created_at=NOW,
        updated_at=NOW, last_reinforced_at=NOW,
    ), semantic_score=.8, lexical_score=.4)
    ranked, _ = score_and_rank(
        [candidate], weights=ScoringWeights(), now=NOW,
        ranking_multipliers=multipliers,
    )
    execution = RetrievalExecution(
        parameters={"ranking_multipliers": (
            multipliers.model_dump(mode="json") if multipliers is not None else None
        ), "fixed": parameter},
        features=features,
    )
    return make_receipt(
        request_id=UUID(int=request_id), user_id="owner", query=query,
        reference_time=NOW, scopes=(Scope.PROJECT,), scoring=ScoringWeights(),
        packing=PackingConfig(), candidates=ranked, bundle=MemoryBundle(),
        result_limit=1, execution=execution,
    )


def _evidence(count=40, *, improved=True):
    receipts = []
    trials = []
    for index in range(count):
        relevant = 10000 + index * 10 + 2
        baseline_node = 10000 + index * 10 + (1 if improved else 2)
        baseline = _receipt(
            request_id=1000 + index, query=f"query {index}", node_id=baseline_node,
        )
        candidate = _receipt(
            request_id=2000 + index, query=f"query {index}", node_id=relevant,
            multipliers=PROPOSAL,
        )
        receipts.extend((baseline, candidate))
        trials.append(FullRetrievalTrial(
            group_id=f"group {index}", baseline_request_id=baseline.request_id,
            candidate_request_id=candidate.request_id,
            relevant_node_ids=(UUID(int=relevant),),
        ))
    return receipts, trials


def _evaluate(receipts, trials, **kwargs):
    return evaluate_full_retrieval(
        receipts, trials, user_id="owner", scopes=[Scope.PROJECT],
        proposal_input_checksum=CHECKSUM, memory_artifact_sha256=ARTIFACT,
        candidate_multipliers=PROPOSAL,
        config=FullRetrievalEvaluationConfig(bootstrap_samples=100),
        **kwargs,
    )


def test_fresh_candidate_membership_passes_conservative_holdout_gate():
    receipts, trials = _evidence()
    before = [receipt.model_dump_json() for receipt in receipts]
    result = _evaluate(receipts, trials)
    assert result.decision == "improved_full_retrieval"
    assert result.coverage == {"trials": 40, "query_groups": 40, "relevant_nodes": 40}
    assert result.baseline_recall == result.baseline_ndcg == result.baseline_mrr == 0
    assert result.candidate_recall == result.candidate_ndcg == result.candidate_mrr == 1
    assert result.ndcg_gain_interval == (1, 1)
    assert result.regression_fraction == 0
    assert result.feature_identity == FEATURES
    assert len(result.receipt_checksums) == 80
    assert [receipt.model_dump_json() for receipt in receipts] == before


@pytest.mark.parametrize("field,value,message", [
    ("decision", "no_improvement", "decision does not match"),
    ("candidate_ndcg", .5, "aggregate metrics"),
    ("input_checksum", "f" * 64, "input checksum"),
])
def test_serialized_full_retrieval_evidence_rejects_tampering(field, value, message):
    receipts, trials = _evidence()
    result = _evaluate(receipts, trials)
    payload = result.model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValueError, match=message):
        FullRetrievalEvaluation.model_validate(payload)


def test_input_order_and_trial_retries_are_stable_but_duplicate_receipts_fail():
    receipts, trials = _evidence()
    first = _evaluate(receipts, trials)
    second = _evaluate(list(reversed(receipts)), list(reversed(trials)))
    assert first.model_dump_json() == second.model_dump_json()
    with pytest.raises(ValueError, match="cannot enter more than one"):
        _evaluate(receipts, [trials[0], trials[0].model_copy(update={"group_id": "again"})])


def test_insufficient_and_nonimproving_trials_never_authorize_activation():
    receipts, trials = _evidence(1)
    result = _evaluate(receipts, trials)
    assert result.decision == "insufficient_data"
    assert result.ndcg_gain_interval is None
    receipts, trials = _evidence(improved=False)
    result = _evaluate(receipts, trials)
    assert result.decision == "no_improvement"
    assert result.ndcg_gain == 0


@pytest.mark.parametrize("kind", [
    "owner", "scope", "query", "clock", "features", "parameters",
    "baseline_multiplier", "candidate_multiplier", "missing",
])
def test_pair_identity_and_fixed_pipeline_controls_fail_closed(kind):
    receipts, trials = _evidence(1)
    baseline, candidate = receipts
    if kind == "owner":
        baseline = baseline.model_copy(update={"user_id": "foreign"})
    elif kind == "scope":
        baseline = baseline.model_copy(update={"scopes": (Scope.PERSONAL,)})
    elif kind == "query":
        candidate = candidate.model_copy(update={"query": "another query"})
    elif kind == "clock":
        candidate = candidate.model_copy(update={
            "reference_time": datetime(2026, 9, 15, tzinfo=timezone.utc),
        })
    elif kind == "features":
        execution = candidate.execution.model_copy(update={"features": {"model": "changed"}})
        candidate = candidate.model_copy(update={"execution": execution})
    elif kind == "parameters":
        execution = candidate.execution.model_copy(update={"parameters": {
            **candidate.execution.parameters, "fixed": "changed",
        }})
        candidate = candidate.model_copy(update={"execution": execution})
    elif kind == "baseline_multiplier":
        execution = baseline.execution.model_copy(update={"parameters": {
            **baseline.execution.parameters,
            "ranking_multipliers": PROPOSAL.model_dump(mode="json"),
        }})
        baseline = baseline.model_copy(update={"execution": execution})
    elif kind == "candidate_multiplier":
        execution = candidate.execution.model_copy(update={"parameters": {
            **candidate.execution.parameters, "ranking_multipliers": None,
        }})
        candidate = candidate.model_copy(update={"execution": execution})
    elif kind == "missing":
        receipts = [baseline]
    if kind != "missing":
        receipts = [baseline, candidate]
    with pytest.raises(ValueError):
        _evaluate(receipts, trials)


def test_repeated_normalized_queries_cannot_claim_independent_groups():
    receipts, trials = _evidence(2)
    receipts[2] = receipts[2].model_copy(update={"query": "  QUERY 0 "})
    receipts[3] = receipts[3].model_copy(update={"query": "query 0"})
    with pytest.raises(ValueError, match="different groups"):
        _evaluate(receipts, trials)


def test_one_profile_holdout_requires_one_base_scoring_configuration():
    receipts, trials = _evidence(2)
    changed = ScoringWeights(
        w_semantic=.2, w_lexical=.25, w_graph=.2,
        w_recency=.15, w_salience=.1, w_confidence=.1,
    )
    receipts[2] = receipts[2].model_copy(update={"scoring": changed})
    receipts[3] = receipts[3].model_copy(update={"scoring": changed})
    with pytest.raises(ValueError, match="one base scoring"):
        _evaluate(receipts, trials)


@pytest.mark.parametrize("value", ["", "g" * 64, "z" * 64])
def test_artifact_identities_require_sha256(value):
    receipts, trials = _evidence(1)
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_full_retrieval(
            receipts, trials, user_id="owner", scopes=[Scope.PROJECT],
            proposal_input_checksum=value, memory_artifact_sha256=ARTIFACT,
            candidate_multipliers=PROPOSAL,
        )
