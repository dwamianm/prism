"""Deterministic, owner-scoped offline learning from explicit relevance pairs.

No provider calls, graph reads, shared weight mutations or profile activation.
The fitted scorer is evaluated using disjoint query groups and saved exposure.
"""
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import random
import unicodedata
from typing import Literal
from uuid import UUID

from prme.models.learning import LearningConfig, LearningEvaluation, QueryLearningResult, RankingMultipliers
from prme.models.relevance import RelevanceRecord, RetrievalReceipt
from prme.retrieval.config import ScoringWeights
from prme.retrieval.models import ScoreProvenance
from prme.types import Scope

_FEATURES = ("semantic", "lexical", "graph", "recency", "salience", "confidence")


def adjusted_weights(weights: ScoringWeights, multipliers: RankingMultipliers) -> ScoringWeights:
    """Apply bounded multipliers after query-specific weight redistribution."""
    if any(not math.isfinite(getattr(weights, "w_" + name)) or getattr(weights, "w_" + name) < 0
           for name in _FEATURES):
        raise ValueError("Learning requires finite nonnegative additive weights")
    if all(getattr(multipliers, name) == 1 for name in _FEATURES):
        return weights
    values = {"w_" + name: getattr(weights, "w_" + name) * getattr(multipliers, name) for name in _FEATURES}
    total = sum(values.values())
    return ScoringWeights(**{**weights.model_dump(), **{key: value / total for key, value in values.items()}})


def proposed_score(provenance: ScoreProvenance, multipliers: RankingMultipliers) -> float:
    """Rescore frozen features, preserving caps, rounding and score operations."""
    # This copy is an experimental score computation, not a newly validated
    # historical trace: its saved composite_score still describes the baseline.
    proposed = provenance.model_copy(update={"weights": adjusted_weights(provenance.weights, multipliers)})
    score = proposed.replay_score()
    if not math.isfinite(score):
        raise ValueError("Proposed score must be finite")
    return score


@dataclass(frozen=True)
class _Observation:
    receipt: RetrievalReceipt
    group: str
    labels: dict[UUID, bool]


def _normalized_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())


def _scope_key(scopes) -> tuple[Scope, ...] | None:
    return tuple(sorted({Scope(scope) for scope in scopes}, key=lambda scope: scope.value)) if scopes is not None else None


def _scores(observation: _Observation, multipliers: RankingMultipliers) -> dict[UUID, float]:
    provenance = observation.receipt.score_provenance
    assert provenance is not None
    return {nid: proposed_score(provenance[nid], multipliers) for nid in observation.labels}


def _order(observation: _Observation, scores: dict[UUID, float]) -> list[UUID]:
    receipt = observation.receipt
    def key(candidate):
        score = scores[candidate.node_id]
        if receipt.ranking_policy == "score_id":
            return (0, -score, 0., str(candidate.node_id))
        if receipt.ranking_policy == "reranked_prefix" and candidate.reranker_score is not None:
            return (0, -score, 0., str(candidate.node_id))
        group = 1 if receipt.ranking_policy == "reranked_prefix" else 0
        return (group, -score, -candidate.trace.path_score if candidate.trace else 0., str(candidate.node_id))
    return [c.node_id for c in sorted((c for c in receipt.candidates if c.node_id in scores), key=key)]


def _metrics(observation: _Observation, multipliers: RankingMultipliers, k: int) -> tuple[float, float]:
    order = _order(observation, _scores(observation, multipliers))
    labels = observation.labels
    positions = {nid: index for index, nid in enumerate(order)}
    positives = [nid for nid, label in labels.items() if label]
    negatives = [nid for nid, label in labels.items() if not label]
    accuracy = sum(positions[p] < positions[n] for p in positives for n in negatives) / (len(positives) * len(negatives))
    dcg = sum(1 / math.log2(i + 2) for i, nid in enumerate(order[:k]) if labels[nid])
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(positives))))
    return accuracy, dcg / ideal


def _loss(observations: list[_Observation], multipliers: RankingMultipliers, config: LearningConfig) -> float:
    grouped = defaultdict(list)
    for observation in observations:
        scores = _scores(observation, multipliers)
        pos = [score for nid, score in scores.items() if observation.labels[nid]]
        neg = [score for nid, score in scores.items() if not observation.labels[nid]]
        loss = 0.
        for p in pos:
            for n in neg:
                z = config.margin_scale * (n - p)
                loss += max(z, 0) + math.log1p(math.exp(-abs(z)))
        grouped[observation.group].append(loss / (len(pos) * len(neg)))
    # Each query group has equal mass regardless of its number of requests/pairs.
    mean = sum(sum(values) / len(values) for values in grouped.values()) / len(grouped)
    return mean + config.regularization * sum(math.log(getattr(multipliers, name)) ** 2 for name in _FEATURES)


def _fit(observations: list[_Observation], config: LearningConfig) -> tuple[RankingMultipliers, float, float]:
    current = RankingMultipliers()
    before = best = _loss(observations, current, config)
    # Fixed coordinate order and step schedule are part of algorithm version 1.
    # No validation labels enter the objective or select hyperparameters.
    for step in (.5, .25, .125, .0625):
        for _ in range(config.passes_per_step):
            changed = False
            for name in _FEATURES:
                center = math.log(getattr(current, name))
                proposals = [current]
                for direction in (-1, 1):
                    value = math.exp(max(math.log(.25), min(math.log(4), center + direction * step)))
                    proposals.append(RankingMultipliers(**{**current.model_dump(), name: value}))
                selected = current
                for proposed in proposals[1:]:
                    value = _loss(observations, proposed, config)
                    if value < best - 1e-12:
                        best, selected = value, proposed
                if selected != current:
                    current, changed = selected, True
            if not changed:
                break
    return current, before, best


def evaluate_learning(receipts: Sequence[RetrievalReceipt], records: Sequence[RelevanceRecord], *,
                      user_id: str, scopes: Sequence[Scope] | None = None,
                      surface: Literal["results", "context"] = "results",
                      config: LearningConfig | None = None,
                      query_groups: dict[UUID, str] | None = None) -> LearningEvaluation:
    """Fit and evaluate a proposal; return inputs, coverage, metrics and rejection.

    Callers may supply group identities for paraphrases. Repeated normalized
    queries always share a group, even if conflicting explicit groups were given
    (such conflicting assignments fail). Unlabelled candidates are never negative.
    """
    if not user_id.strip() or surface not in {"results", "context"}:
        raise ValueError("Learning requires an owner and a valid relevance surface")
    config = config or LearningConfig()
    scope = _scope_key(scopes)
    # Detach caller-owned mutable dictionaries before fitting or hashing.
    receipts = [RetrievalReceipt.model_validate_json(r.model_dump_json()) for r in receipts]
    records = [RelevanceRecord.model_validate_json(r.model_dump_json()) for r in records]
    if any(r.user_id != user_id for r in receipts) or any(r.user_id != user_id for r in records):
        raise ValueError("Learning inputs must belong to the requested owner")
    by_request: dict[UUID, RetrievalReceipt] = {}
    for receipt in receipts:
        if receipt.request_id in by_request and by_request[receipt.request_id] != receipt:
            raise ValueError("Conflicting receipt identity")
        by_request[receipt.request_id] = receipt
    distinct: dict[UUID, RelevanceRecord] = {}
    for record in records:
        if record.feedback_id in distinct and distinct[record.feedback_id] != record:
            raise ValueError("Conflicting feedback identity")
        distinct[record.feedback_id] = record
    records = sorted(distinct.values(), key=lambda r: str(r.feedback_id))
    explicit = query_groups or {}
    if set(explicit) - set(by_request):
        raise ValueError("Query groups refer to unknown receipts")
    named: dict[str, str] = {}
    for request, group in explicit.items():
        if not group.strip():
            raise ValueError("Query group identities must be nonempty")
        normalized = _normalized_query(by_request[request].query)
        if normalized in named and named[normalized] != group:
            raise ValueError("Repeated queries cannot use different query groups")
        named[normalized] = group
    labels: defaultdict[UUID, defaultdict[UUID, set[bool]]] = defaultdict(lambda: defaultdict(set))
    excluded: Counter[str] = Counter()
    for record in records:
        linked = by_request.get(record.request_id)
        if linked is None:
            raise ValueError("Every relevance record requires its saved receipt")
        receipt = linked
        if record.receipt_checksum != receipt.checksum:
            raise ValueError("Relevance receipt checksum mismatch")
        candidates = {c.node_id: c for c in receipt.candidates}
        for nid, label in record.labels.items():
            candidate = candidates.get(nid)
            if candidate is None:
                raise ValueError("Relevance label refers to an unobserved candidate")
            if record.surface == "context" and (not candidate.in_context or (label and not candidate.has_content)):
                raise ValueError("Invalid context evidence label")
        if record.surface != surface:
            excluded["other_surface_records"] += 1
        elif _scope_key(receipt.scopes) != scope:
            excluded["other_scope_records"] += 1
        elif receipt.schema_version != 2:
            excluded["legacy_receipt_records"] += 1
        else:
            for nid, label in record.labels.items():
                labels[record.request_id][nid].add(label)
    observations = []
    for request, judgments in sorted(labels.items(), key=lambda item: str(item[0])):
        receipt = by_request[request]
        normalized = _normalized_query(receipt.query)
        identity = ["explicit", named[normalized]] if normalized in named else ["query", normalized]
        group = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        unambiguous = {nid: next(iter(votes)) for nid, votes in judgments.items() if len(votes) == 1}
        excluded["conflicting_candidate_labels"] += sum(len(votes) > 1 for votes in judgments.values())
        positives = sum(unambiguous.values())
        pairs = positives * (len(unambiguous) - positives)
        if not pairs:
            excluded["requests_without_explicit_pairs"] += 1
            continue
        if pairs > config.max_pairs_per_request:
            raise ValueError("Explicit pair count exceeds max_pairs_per_request")
        # Verify unity exactly reproduces the saved baseline before optimization.
        observation = _Observation(receipt, group, unambiguous)
        baseline = _scores(observation, RankingMultipliers())
        if any(baseline[c.node_id] != c.score for c in receipt.candidates if c.node_id in baseline):
            raise ValueError("Baseline score replay mismatch")
        observations.append(observation)
    groups = sorted({o.group for o in observations})
    # Assign by a fixed hash threshold, not a fraction-sized slice. Adding
    # feedback must not move yesterday's validation query into training.
    validation_groups = {group for group in groups
        if int(hashlib.sha256(f"{config.seed}:{group}".encode()).hexdigest(), 16) / (1 << 256)
        < config.validation_fraction}
    validation_count = len(validation_groups)
    train = [o for o in observations if o.group not in validation_groups]
    checksum_input = {"receipts": [r.model_dump(mode="json") for r in sorted(by_request.values(), key=lambda r: str(r.request_id))],
                      "records": [r.model_dump(mode="json") for r in records],
                      "query_groups": {str(k): v for k, v in sorted(explicit.items(), key=lambda item: str(item[0]))}}
    common = LearningEvaluation(user_id=user_id, scopes=scope, surface=surface, config=config,
                  multipliers=RankingMultipliers(), decision="insufficient_data",
                  input_checksum=hashlib.sha256(json.dumps(checksum_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                  feedback_ids=tuple(r.feedback_id for r in records),
                  receipt_checksums={r.request_id: r.checksum for r in sorted(by_request.values(), key=lambda r: str(r.request_id))},
                  exclusions=dict(excluded), coverage={"feedback_records": len(records), "requests_with_pairs": len(observations),
                      "training_queries": len(groups) - validation_count, "validation_queries": validation_count,
                      "explicit_pairs": sum(sum(o.labels.values()) * (len(o.labels) - sum(o.labels.values())) for o in observations)})
    if len(groups) - validation_count < config.min_training_queries or validation_count < config.min_validation_queries:
        return common
    multipliers, before, after = _fit(train, config)
    query_results = []
    for group in sorted(groups):
        group_observations = [o for o in observations if o.group == group]
        base = [_metrics(o, RankingMultipliers(), config.ndcg_k) for o in group_observations]
        proposed = [_metrics(o, multipliers, config.ndcg_k) for o in group_observations]
        query_results.append(QueryLearningResult(group_id=group,
            split="validation" if group in validation_groups else "train",
            request_ids=tuple(o.receipt.request_id for o in group_observations),
            baseline_pairwise_accuracy=sum(v[0] for v in base) / len(base),
            candidate_pairwise_accuracy=sum(v[0] for v in proposed) / len(proposed),
            baseline_judged_ndcg=sum(v[1] for v in base) / len(base),
            candidate_judged_ndcg=sum(v[1] for v in proposed) / len(proposed)))
    diffs = [q.candidate_judged_ndcg - q.baseline_judged_ndcg for q in query_results if q.split == "validation"]
    randomizer = random.Random(config.seed)
    means = sorted(sum(randomizer.choice(diffs) for _ in diffs) / len(diffs) for _ in range(config.bootstrap_samples))
    interval = (means[int(.025 * len(means))], means[min(len(means) - 1, int(.975 * len(means)))])
    gain = sum(diffs) / len(diffs)
    pairwise_diffs = [q.candidate_pairwise_accuracy - q.baseline_pairwise_accuracy
                     for q in query_results if q.split == "validation"]
    pairwise_gain = sum(pairwise_diffs) / len(pairwise_diffs)
    improved = gain >= config.min_ndcg_gain and interval[0] > 0 and pairwise_gain >= -1e-12
    return LearningEvaluation.model_validate({**common.model_dump(), "multipliers": multipliers,
        "training_loss_before": before, "training_loss_after": after,
        "decision": "improved_on_observed_candidates" if improved else "no_improvement",
        "validation_ndcg_gain": gain, "validation_pairwise_gain": pairwise_gain,
        "validation_gain_interval": interval, "queries": tuple(query_results)})
