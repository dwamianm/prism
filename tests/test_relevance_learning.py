"""Relevance learning must generalize across query groups or reject its proposal."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from prme.models.learning import LearningConfig, RankingMultipliers
from prme.models.nodes import MemoryNode
from prme.models.relevance import RelevanceRecord, RetrievalReceipt, make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.learning import evaluate_learning
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.retrieval.scoring import score_and_rank
from prme.types import NodeType, Scope

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def evidence(count=100):
    receipts, records = [], []
    for i in range(count):
        items = []
        for j, (semantic, lexical, graph) in enumerate(((.2, .9, 0), (.9, .1, 1))):
            items.append(RetrievalCandidate(node=MemoryNode(
                id=UUID(int=i * 10 + j + 1), user_id="owner", scope=Scope.PROJECT,
                node_type=NodeType.FACT, content=f"source {i} {j}", created_at=NOW,
                updated_at=NOW, last_reinforced_at=NOW, confidence_base=.8, salience_base=.5),
                semantic_score=semantic, lexical_score=lexical, graph_proximity=graph))
        items, _ = score_and_rank(items, now=NOW)
        receipt = make_receipt(request_id=UUID(int=10000 + i), user_id="owner", query=f"query {i}",
            reference_time=NOW, scopes=(Scope.PROJECT,), scoring=ScoringWeights(), packing=PackingConfig(),
            candidates=items, bundle=MemoryBundle())
        receipts.append(receipt)
        records.append(RelevanceRecord(feedback_id=UUID(int=20000 + i), request_id=receipt.request_id,
            labels={UUID(int=i * 10 + 1): True, UUID(int=i * 10 + 2): False}, user_id="owner",
            recorded_at=NOW, receipt_checksum=receipt.checksum, method="structured_evaluation"))
    return receipts, records


def evaluate(receipts, records, **kwargs):
    return evaluate_learning(receipts, records, user_id="owner", scopes=[Scope.PROJECT],
        config=LearningConfig(bootstrap_samples=100), **kwargs)


def test_fits_only_training_queries_and_replays_baseline_without_mutation():
    receipts, records = evidence()
    before = [r.model_dump_json() for r in receipts]
    report = evaluate(receipts, records)
    assert report.decision == "improved_on_observed_candidates"
    assert report.training_loss_after < report.training_loss_before
    assert report.validation_ndcg_gain > .3
    assert report.validation_gain_interval[0] > 0
    assert report.multipliers.lexical > report.multipliers.semantic
    assert [r.model_dump_json() for r in receipts] == before
    train = {rid for q in report.queries if q.split == "train" for rid in q.request_ids}
    validation = {rid for q in report.queries if q.split == "validation" for rid in q.request_ids}
    assert not train & validation
    assert train | validation == {r.request_id for r in receipts}
    # A failed holdout must not feed back into fitting or hyperparameter choice.
    reversed_holdout = [r.model_copy(update={"labels": {nid: not label for nid, label in r.labels.items()}})
                        if r.request_id in validation else r for r in records]
    negative_control = evaluate(receipts, reversed_holdout)
    assert negative_control.multipliers == report.multipliers
    assert negative_control.training_loss_after == report.training_loss_after
    assert negative_control.decision == "no_improvement"
    assert negative_control.validation_ndcg_gain < 0


def test_input_order_and_feedback_retries_do_not_change_the_report():
    receipts, records = evidence()
    first = evaluate(receipts, records)
    retried = evaluate(list(reversed(receipts)), list(reversed(records)) + records[:3])
    assert retried.model_dump_json() == first.model_dump_json()


def test_adding_feedback_never_moves_a_query_between_training_and_validation():
    receipts, records = evidence(120)
    first = evaluate(receipts[:100], records[:100])
    expanded = evaluate(receipts, records)
    assignments = {rid: q.split for q in expanded.queries for rid in q.request_ids}
    assert first.queries
    assert all(assignments[rid] == q.split for q in first.queries for rid in q.request_ids)


def test_repeated_queries_cannot_leak_across_train_and_validation():
    receipts, records = evidence(10)
    for i, receipt in enumerate(receipts):
        receipts[i] = receipt.model_copy(update={"query": "  THE SAME  Query " if i % 2 else "the same query"})
        records[i] = records[i].model_copy(update={"receipt_checksum": receipts[i].checksum})
    report = evaluate(receipts, records)
    assert report.decision == "insufficient_data"
    assert report.coverage["training_queries"] + report.coverage["validation_queries"] == 1
    assert report.multipliers == RankingMultipliers()
    with pytest.raises(ValueError, match="different query groups"):
        evaluate(receipts, records, query_groups={receipts[0].request_id: "one", receipts[1].request_id: "two"})


def test_explicit_paraphrase_groups_reduce_independent_query_count():
    receipts, records = evidence(10)
    report = evaluate(receipts, records, query_groups={r.request_id: "same intent" for r in receipts})
    assert report.coverage["training_queries"] + report.coverage["validation_queries"] == 1
    assert report.decision == "insufficient_data"


def test_missing_labels_are_never_negatives_and_conflicts_are_not_votes():
    receipts, records = evidence(3)
    records[0] = records[0].model_copy(update={"labels": {next(iter(records[0].labels)): True}})
    conflict = records[1].model_copy(update={"feedback_id": UUID(int=30000),
        "labels": {nid: not label for nid, label in records[1].labels.items()}})
    repeated = records[2].model_copy(update={"feedback_id": UUID(int=30001)})
    report = evaluate(receipts, [*records, conflict, repeated])
    assert report.coverage["explicit_pairs"] == 1
    assert report.coverage["requests_with_pairs"] == 1
    assert report.exclusions["conflicting_candidate_labels"] == 2
    assert report.exclusions["requests_without_explicit_pairs"] == 2


@pytest.mark.parametrize("kind", ["owner", "checksum", "missing", "unknown_label", "identity", "context"])
def test_invalid_learning_inputs_fail_before_fitting(kind):
    receipts, records = evidence(2)
    if kind == "owner":
        records[0] = records[0].model_copy(update={"user_id": "foreign"})
    elif kind == "checksum":
        records[0] = records[0].model_copy(update={"receipt_checksum": "0" * 64})
    elif kind == "missing":
        receipts.pop()
    elif kind == "unknown_label":
        records[0] = records[0].model_copy(update={"labels": {UUID(int=900): False}})
    elif kind == "identity":
        records.append(records[0].model_copy(update={"labels": {UUID(int=1): False}}))
    else:
        records[0] = records[0].model_copy(update={"surface": "context"})
    with pytest.raises(ValueError):
        evaluate(receipts, records)


def test_scope_and_surface_do_not_silently_mix():
    receipts, records = evidence(2)
    report = evaluate_learning(receipts, records, user_id="owner", scopes=[Scope.PERSONAL])
    assert report.exclusions["other_scope_records"] == 2
    assert report.coverage["explicit_pairs"] == 0
    report = evaluate_learning(receipts, records, user_id="owner", scopes=[Scope.PROJECT], surface="context")
    assert report.exclusions["other_surface_records"] == 2


def test_legacy_receipts_are_reported_as_unusable_for_learning():
    raw = (Path(__file__).parent / "fixtures/relevance/receipt-v1.json").read_text()
    receipt = RetrievalReceipt.model_validate_json(raw).model_copy(update={"user_id": "owner"})
    record = RelevanceRecord(request_id=receipt.request_id, labels={receipt.candidates[0].node_id: True},
                             user_id="owner", recorded_at=NOW, receipt_checksum=receipt.checksum)
    report = evaluate([receipt], [record])
    assert report.exclusions["legacy_receipt_records"] == 1
    assert report.decision == "insufficient_data"


def test_pair_budget_fails_explicitly_instead_of_sampling_labels():
    receipts, records = evidence(1)
    # The minimum budget permits this one pair, with no truncation.
    config = LearningConfig(max_pairs_per_request=1)
    report = evaluate_learning(receipts, records, user_id="owner", scopes=[Scope.PROJECT], config=config)
    assert report.coverage["explicit_pairs"] == 1
    extra = receipts[0].candidates[1].model_copy(update={"node_id": UUID(int=500)})
    provenance = receipts[0].score_provenance
    changed = receipts[0].model_copy(update={"candidates": (*receipts[0].candidates, extra),
        "score_provenance": {**provenance, extra.node_id: provenance[receipts[0].candidates[1].node_id]}})
    record = records[0].model_copy(update={"labels": {**records[0].labels, extra.node_id: True},
                                         "receipt_checksum": changed.checksum})
    with pytest.raises(ValueError, match="pair count exceeds"):
        evaluate_learning([changed], [record], user_id="owner", scopes=[Scope.PROJECT], config=config)


def test_ndcg_gain_cannot_hide_a_pairwise_ordering_regression(monkeypatch):
    weights = ScoringWeights(w_semantic=.9, w_lexical=.1, w_graph=0,
                             w_recency=0, w_salience=0, w_confidence=0, relevance_floor=0)
    nodes = [RetrievalCandidate(node=MemoryNode(id=UUID(int=j + 1), user_id="owner",
                 scope=Scope.PROJECT, node_type=NodeType.FACT, content=f"rank {j}",
                 created_at=NOW, updated_at=NOW, last_reinforced_at=NOW),
                 semantic_score=1 - .2 * j, lexical_score=lexical)
             for j, lexical in enumerate((.8, 1., 0., .6, .4))]
    ranked, _ = score_and_rank(nodes, weights=weights, now=NOW)
    receipts, records = evidence()
    for i, old in enumerate(receipts):
        receipts[i] = make_receipt(request_id=old.request_id, user_id="owner", query=old.query,
            reference_time=NOW, scopes=(Scope.PROJECT,), scoring=weights, packing=PackingConfig(),
            candidates=ranked, bundle=MemoryBundle())
        records[i] = records[i].model_copy(update={"labels": {n.node.id: j in (1, 2) for j, n in enumerate(nodes)},
            "receipt_checksum": receipts[i].checksum})
    proposed = RankingMultipliers(semantic=.25, lexical=4)
    monkeypatch.setattr("prme.retrieval.learning._fit", lambda observations, config: (proposed, .7, .6))
    report = evaluate_learning(receipts, records, user_id="owner", scopes=[Scope.PROJECT],
                               config=LearningConfig(ndcg_k=1, bootstrap_samples=100))
    assert report.validation_ndcg_gain == 1
    assert report.validation_pairwise_gain < 0
    assert report.decision == "no_improvement"
