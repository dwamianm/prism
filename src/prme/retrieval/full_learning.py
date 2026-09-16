"""Final holdout evaluation over separately executed retrieval pipelines."""
from collections import defaultdict
from collections.abc import Sequence
import hashlib
import json
import math
import random
import unicodedata
from uuid import UUID

from prme.models.learning import (
    FullRetrievalEvaluation,
    FullRetrievalEvaluationConfig,
    FullRetrievalQueryResult,
    FullRetrievalTrial,
    RankingMultipliers,
)
from prme.models.relevance import RetrievalReceipt
from prme.types import Scope


def _canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _normalized_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())


def _scope_key(scopes) -> tuple[Scope, ...] | None:
    if scopes is None:
        return None
    return tuple(sorted({Scope(scope) for scope in scopes}, key=lambda scope: scope.value))


def _multiplier_parameter(receipt: RetrievalReceipt) -> RankingMultipliers:
    if receipt.execution is None:
        raise ValueError("Full retrieval evaluation requires execution receipts")
    raw = receipt.execution.parameters.get("ranking_multipliers")
    return RankingMultipliers() if raw is None else RankingMultipliers.model_validate(raw)


def _fixed_parameters(receipt: RetrievalReceipt) -> dict:
    assert receipt.execution is not None
    return {
        key: value for key, value in receipt.execution.parameters.items()
        if key not in {"ranking_multipliers", "ranking_profile"}
    }


def _metrics(receipt: RetrievalReceipt, relevant: set[UUID], k: int) -> tuple[float, float, float]:
    order = [candidate.node_id for candidate in receipt.candidates[:k]]
    hits = [index for index, node_id in enumerate(order) if node_id in relevant]
    recall = len(hits) / len(relevant)
    dcg = sum(1 / math.log2(index + 2) for index in hits)
    ideal = sum(1 / math.log2(index + 2) for index in range(min(k, len(relevant))))
    mrr = 1 / (hits[0] + 1) if hits else 0.
    return recall, dcg / ideal, mrr


def _validate_pair(
    baseline: RetrievalReceipt,
    candidate: RetrievalReceipt,
    *,
    user_id: str,
    scopes: tuple[Scope, ...] | None,
    baseline_multipliers: RankingMultipliers,
    candidate_multipliers: RankingMultipliers,
) -> None:
    if baseline.user_id != user_id or candidate.user_id != user_id:
        raise ValueError("Full retrieval receipts must belong to the requested owner")
    if _scope_key(baseline.scopes) != scopes or _scope_key(candidate.scopes) != scopes:
        raise ValueError("Full retrieval receipts must match the requested scopes")
    if baseline.schema_version < 3 or candidate.schema_version < 3:
        raise ValueError("Full retrieval evaluation requires execution receipts")
    if _normalized_query(baseline.query) != _normalized_query(candidate.query):
        raise ValueError("Paired retrievals must use the same normalized query")
    fixed = (
        "reference_time", "scoring", "packing", "min_score", "result_limit",
        "retrieval_mode", "time_from", "time_to",
    )
    if any(getattr(baseline, name) != getattr(candidate, name) for name in fixed):
        raise ValueError("Paired retrievals must use the same clock, filters, limits, scoring, and packing")
    assert baseline.execution is not None and candidate.execution is not None
    if baseline.execution.features != candidate.execution.features:
        raise ValueError("Paired retrieval feature identities differ")
    if _fixed_parameters(baseline) != _fixed_parameters(candidate):
        raise ValueError("Paired retrieval execution parameters differ")
    if _multiplier_parameter(baseline) != baseline_multipliers:
        raise ValueError("Baseline receipt does not use the declared multipliers")
    if _multiplier_parameter(candidate) != candidate_multipliers:
        raise ValueError("Candidate receipt does not use the declared multipliers")


def evaluate_full_retrieval(
    receipts: Sequence[RetrievalReceipt],
    trials: Sequence[FullRetrievalTrial],
    *,
    user_id: str,
    scopes: Sequence[Scope] | None,
    proposal_input_checksum: str,
    memory_artifact_sha256: str,
    candidate_multipliers: RankingMultipliers,
    baseline_multipliers: RankingMultipliers | None = None,
    config: FullRetrievalEvaluationConfig | None = None,
) -> FullRetrievalEvaluation:
    """Evaluate complete candidate generation on a fixed final holdout.

    Each trial supplies the full relevant-node set, including relevant nodes
    omitted from either response. This avoids converting unexposed or unlabelled
    memories into negatives. Both arms must be fresh execution receipts with
    identical request and feature identity outside the declared multipliers.
    """
    if not user_id.strip():
        raise ValueError("Full retrieval evaluation requires an owner")
    if len(proposal_input_checksum) != 64 or len(memory_artifact_sha256) != 64:
        raise ValueError("Evaluation and memory artifact checksums must be SHA-256 hex digests")
    try:
        bytes.fromhex(proposal_input_checksum)
        bytes.fromhex(memory_artifact_sha256)
    except ValueError as exc:
        raise ValueError("Evaluation and memory artifact checksums must be SHA-256 hex digests") from exc
    config = config or FullRetrievalEvaluationConfig()
    baseline_multipliers = baseline_multipliers or RankingMultipliers()
    candidate_multipliers = RankingMultipliers.model_validate_json(
        candidate_multipliers.model_dump_json()
    )
    scope_key = _scope_key(scopes)
    detached = [RetrievalReceipt.model_validate_json(item.model_dump_json()) for item in receipts]
    trials = [FullRetrievalTrial.model_validate_json(item.model_dump_json()) for item in trials]
    by_request: dict[UUID, RetrievalReceipt] = {}
    for receipt in detached:
        if receipt.request_id in by_request and by_request[receipt.request_id] != receipt:
            raise ValueError("Conflicting full retrieval receipt identity")
        by_request[receipt.request_id] = receipt
    if not trials:
        raise ValueError("Full retrieval evaluation requires at least one paired trial")
    used: set[UUID] = set()
    normalized_groups: dict[str, str] = {}
    features = None
    base_scoring = None
    grouped = defaultdict(list)
    receipt_checksums = {}
    for trial in sorted(trials, key=lambda item: (item.group_id, str(item.baseline_request_id))):
        baseline = by_request.get(trial.baseline_request_id)
        candidate = by_request.get(trial.candidate_request_id)
        if baseline is None or candidate is None:
            raise ValueError("Every full retrieval trial requires both saved receipts")
        pair_ids = {baseline.request_id, candidate.request_id}
        if used & pair_ids:
            raise ValueError("A retrieval receipt cannot enter more than one full retrieval trial")
        used |= pair_ids
        _validate_pair(
            baseline, candidate, user_id=user_id, scopes=scope_key,
            baseline_multipliers=baseline_multipliers,
            candidate_multipliers=candidate_multipliers,
        )
        normalized = _normalized_query(baseline.query)
        previous = normalized_groups.setdefault(normalized, trial.group_id)
        if previous != trial.group_id:
            raise ValueError("Repeated normalized queries cannot use different groups")
        assert baseline.execution is not None
        if features is None:
            features = baseline.execution.features
        elif features != baseline.execution.features:
            raise ValueError("All holdout trials must use one feature identity")
        if base_scoring is None:
            base_scoring = baseline.scoring
        elif base_scoring != baseline.scoring:
            raise ValueError("All holdout trials must use one base scoring configuration")
        relevant = set(trial.relevant_node_ids)
        grouped[trial.group_id].append((
            baseline.request_id,
            candidate.request_id,
            _metrics(baseline, relevant, config.k),
            _metrics(candidate, relevant, config.k),
        ))
        receipt_checksums[baseline.request_id] = baseline.checksum
        receipt_checksums[candidate.request_id] = candidate.checksum
    assert features is not None and base_scoring is not None
    query_results = []
    for group_id, observations in sorted(grouped.items()):
        count = len(observations)
        base = tuple(sum(item[2][index] for item in observations) / count for index in range(3))
        proposed = tuple(sum(item[3][index] for item in observations) / count for index in range(3))
        query_results.append(FullRetrievalQueryResult(
            group_id=group_id,
            baseline_request_ids=tuple(item[0] for item in observations),
            candidate_request_ids=tuple(item[1] for item in observations),
            baseline_recall=base[0], candidate_recall=proposed[0],
            baseline_ndcg=base[1], candidate_ndcg=proposed[1],
            baseline_mrr=base[2], candidate_mrr=proposed[2],
        ))
    count = len(query_results)
    baseline_recall = sum(item.baseline_recall for item in query_results) / count
    candidate_recall = sum(item.candidate_recall for item in query_results) / count
    baseline_ndcg = sum(item.baseline_ndcg for item in query_results) / count
    candidate_ndcg = sum(item.candidate_ndcg for item in query_results) / count
    baseline_mrr = sum(item.baseline_mrr for item in query_results) / count
    candidate_mrr = sum(item.candidate_mrr for item in query_results) / count
    diffs = [item.candidate_ndcg - item.baseline_ndcg for item in query_results]
    regressions = sum(diff < -1e-12 for diff in diffs)
    interval = None
    decision = "insufficient_data"
    if count >= config.min_query_groups:
        randomizer = random.Random(config.seed)
        means = sorted(
            sum(randomizer.choice(diffs) for _ in diffs) / len(diffs)
            for _ in range(config.bootstrap_samples)
        )
        interval = (
            means[int(.025 * len(means))],
            means[min(len(means) - 1, int(.975 * len(means)))],
        )
        gain = candidate_ndcg - baseline_ndcg
        improved = (
            gain >= config.min_ndcg_gain
            and interval[0] > 0
            and candidate_recall >= baseline_recall - 1e-12
            and regressions / count <= config.max_regression_fraction
        )
        decision = "improved_full_retrieval" if improved else "no_improvement"
    feature_hash = hashlib.sha256(_canonical(features)).hexdigest()
    input_value = {
        "user_id": user_id,
        "scopes": [scope.value for scope in scope_key] if scope_key is not None else None,
        "proposal_input_checksum": proposal_input_checksum,
        "memory_artifact_sha256": memory_artifact_sha256,
        "baseline_multipliers": baseline_multipliers.model_dump(mode="json"),
        "candidate_multipliers": candidate_multipliers.model_dump(mode="json"),
        "base_scoring": base_scoring.model_dump(mode="json"),
        "config": config.model_dump(mode="json"),
        "trials": [trial.model_dump(mode="json") for trial in sorted(
            trials, key=lambda item: (item.group_id, str(item.baseline_request_id))
        )],
        "receipt_checksums": {str(key): value for key, value in sorted(
            receipt_checksums.items(), key=lambda item: str(item[0])
        )},
    }
    return FullRetrievalEvaluation(
        user_id=user_id,
        scopes=scope_key,
        config=config,
        proposal_input_checksum=proposal_input_checksum,
        memory_artifact_sha256=memory_artifact_sha256,
        input_checksum=hashlib.sha256(_canonical(input_value)).hexdigest(),
        receipt_checksums=receipt_checksums,
        feature_identity=features,
        feature_identity_sha256=feature_hash,
        base_scoring=base_scoring,
        baseline_multipliers=baseline_multipliers,
        candidate_multipliers=candidate_multipliers,
        decision=decision,
        baseline_recall=baseline_recall,
        candidate_recall=candidate_recall,
        recall_gain=candidate_recall - baseline_recall,
        baseline_ndcg=baseline_ndcg,
        candidate_ndcg=candidate_ndcg,
        ndcg_gain=candidate_ndcg - baseline_ndcg,
        ndcg_gain_interval=interval,
        baseline_mrr=baseline_mrr,
        candidate_mrr=candidate_mrr,
        mrr_gain=candidate_mrr - baseline_mrr,
        regression_fraction=regressions / count,
        coverage={"trials": len(trials), "query_groups": count, "relevant_nodes": sum(
            len(trial.relevant_node_ids) for trial in trials
        )},
        trials=tuple(sorted(
            trials, key=lambda item: (item.group_id, str(item.baseline_request_id))
        )),
        queries=tuple(query_results),
    )
