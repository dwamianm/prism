"""Evaluation metrics for retrieval quality measurement.

Provides standard IR metrics -- precision@k, recall@k, nDCG@k, MRR, F1@k,
and hit rate -- to evaluate how well the PRME retrieval pipeline surfaces
relevant content for a given query.

Ground truth is expressed as keyword lists:  each result is judged relevant
if it contains at least one of the ``relevant_keywords``, and irrelevant if
it contains any ``irrelevant_keywords``.  Graded relevance for nDCG is
specified via ``relevance_grades`` mapping keywords to integer grades (0-3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class GroundTruth:
    """Ground truth annotations for a single evaluation query.

    Attributes:
        query: The retrieval query text.
        relevant_keywords: Keywords whose presence in a result marks it as
            relevant.
        irrelevant_keywords: Keywords whose presence marks a result as
            explicitly irrelevant (used for sanity checks; not required for
            metric computation).
        relevance_grades: Mapping of keyword to integer grade (0-3) for
            graded relevance in nDCG.  Keywords not listed default to
            grade 1 if they appear in ``relevant_keywords``.
    """

    query: str
    relevant_keywords: list[str]
    irrelevant_keywords: list[str] = field(default_factory=list)
    relevance_grades: dict[str, int] = field(default_factory=dict)


@dataclass
class EvalMetrics:
    """Standard IR evaluation metrics aggregated across k values.

    All ``*_at_k`` dicts map k -> metric value.
    """

    precision_at_k: dict[int, float] = field(default_factory=dict)
    recall_at_k: dict[int, float] = field(default_factory=dict)
    ndcg_at_k: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    f1_at_k: dict[int, float] = field(default_factory=dict)
    hit_rate: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_relevant(content: str, ground_truth: GroundTruth) -> bool:
    """Return True if *content* contains any relevant keyword."""
    content_lower = content.lower()
    return any(kw.lower() in content_lower for kw in ground_truth.relevant_keywords)


def _relevance_grade(content: str, ground_truth: GroundTruth) -> int:
    """Return the maximum relevance grade for *content*.

    Scans ``relevance_grades`` first; falls back to 1 for any keyword in
    ``relevant_keywords`` that is found but not explicitly graded.
    """
    content_lower = content.lower()
    max_grade = 0

    # Check explicitly graded keywords first.
    for kw, grade in ground_truth.relevance_grades.items():
        if kw.lower() in content_lower:
            max_grade = max(max_grade, grade)

    # If no graded keyword matched, check relevant_keywords for a default
    # grade of 1.
    if max_grade == 0 and _is_relevant(content, ground_truth):
        max_grade = 1

    return max_grade


# ---------------------------------------------------------------------------
# Core metric functions
# ---------------------------------------------------------------------------


def compute_precision_at_k(
    results: list[dict],
    ground_truth: GroundTruth,
    k: int,
) -> float:
    """Compute precision@k: fraction of top-k results that are relevant.

    Args:
        results: Ranked list of result dicts (must contain ``"content"`` key).
        ground_truth: Ground truth for the query.
        k: Cutoff rank.

    Returns:
        Precision value in [0, 1].
    """
    if k <= 0:
        return 0.0
    top_k = results[:k]
    if not top_k:
        return 0.0
    relevant_count = sum(1 for r in top_k if _is_relevant(r["content"], ground_truth))
    return relevant_count / len(top_k)


def compute_recall_at_k(
    results: list[dict],
    ground_truth: GroundTruth,
    k: int,
) -> float:
    """Compute recall@k: fraction of relevant items found in top-k.

    The total number of relevant items is defined as
    ``len(ground_truth.relevant_keywords)``.

    Args:
        results: Ranked list of result dicts.
        ground_truth: Ground truth for the query.
        k: Cutoff rank.

    Returns:
        Recall value in [0, 1].
    """
    total_relevant = len(ground_truth.relevant_keywords)
    if total_relevant == 0 or k <= 0:
        return 0.0
    top_k = results[:k]
    # Count how many distinct relevant keywords appear in the top-k results.
    found_keywords: set[str] = set()
    for r in top_k:
        content_lower = r["content"].lower()
        for kw in ground_truth.relevant_keywords:
            if kw.lower() in content_lower:
                found_keywords.add(kw.lower())
    return len(found_keywords) / total_relevant


def compute_ndcg_at_k(
    results: list[dict],
    ground_truth: GroundTruth,
    k: int,
) -> float:
    """Compute normalised Discounted Cumulative Gain at k (nDCG@k).

    Uses graded relevance from ``ground_truth.relevance_grades``.
    Falls back to binary relevance (grade 1) when grades are unspecified.

    Args:
        results: Ranked list of result dicts.
        ground_truth: Ground truth for the query.
        k: Cutoff rank.

    Returns:
        nDCG value in [0, 1].
    """
    if k <= 0:
        return 0.0

    top_k = results[:k]
    if not top_k:
        return 0.0

    # Actual DCG.
    dcg = 0.0
    for i, r in enumerate(top_k):
        grade = _relevance_grade(r["content"], ground_truth)
        dcg += (2**grade - 1) / math.log2(i + 2)  # i+2 because rank is 1-based

    # Ideal DCG: sort all possible grades descending.
    ideal_grades: list[int] = []
    # Collect grades for each relevant keyword.
    for kw in ground_truth.relevant_keywords:
        grade = ground_truth.relevance_grades.get(kw, 1)
        ideal_grades.append(grade)
    ideal_grades.sort(reverse=True)
    ideal_grades = ideal_grades[:k]

    idcg = 0.0
    for i, grade in enumerate(ideal_grades):
        idcg += (2**grade - 1) / math.log2(i + 2)

    if idcg == 0.0:
        return 0.0

    return min(dcg / idcg, 1.0)


def compute_mrr(
    results: list[dict],
    ground_truth: GroundTruth,
) -> float:
    """Compute Mean Reciprocal Rank for a single query.

    Returns 1/rank of the first relevant result, or 0 if none found.
    """
    for i, r in enumerate(results):
        if _is_relevant(r["content"], ground_truth):
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Aggregate evaluation
# ---------------------------------------------------------------------------

_DEFAULT_K_VALUES = [1, 3, 5]


def evaluate_retrieval(
    results: list[dict],
    ground_truth: GroundTruth,
    k_values: list[int] | None = None,
) -> EvalMetrics:
    """Compute all evaluation metrics for one query's results.

    Args:
        results: Ranked retrieval results (each must have ``"content"``).
        ground_truth: Ground truth for this query.
        k_values: Cutoff values to compute *@k metrics for.
            Defaults to [1, 3, 5].

    Returns:
        Populated EvalMetrics.
    """
    if k_values is None:
        k_values = list(_DEFAULT_K_VALUES)

    precision: dict[int, float] = {}
    recall: dict[int, float] = {}
    ndcg: dict[int, float] = {}
    f1: dict[int, float] = {}

    for k in k_values:
        p = compute_precision_at_k(results, ground_truth, k)
        r = compute_recall_at_k(results, ground_truth, k)
        precision[k] = p
        recall[k] = r
        ndcg[k] = compute_ndcg_at_k(results, ground_truth, k)
        f1[k] = 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    mrr_val = compute_mrr(results, ground_truth)
    # Hit rate for a single query: 1 if at least one relevant result, else 0.
    has_hit = any(_is_relevant(r["content"], ground_truth) for r in results)

    return EvalMetrics(
        precision_at_k=precision,
        recall_at_k=recall,
        ndcg_at_k=ndcg,
        mrr=mrr_val,
        f1_at_k=f1,
        hit_rate=1.0 if has_hit else 0.0,
    )


def aggregate_metrics(metrics_list: list[EvalMetrics]) -> EvalMetrics:
    """Average EvalMetrics across multiple queries.

    Args:
        metrics_list: List of per-query EvalMetrics.

    Returns:
        Averaged EvalMetrics.  Returns zero-valued metrics if the input
        list is empty.
    """
    n = len(metrics_list)
    if n == 0:
        return EvalMetrics()

    # Collect all k values seen across all metrics.
    all_k: set[int] = set()
    for m in metrics_list:
        all_k.update(m.precision_at_k.keys())

    precision: dict[int, float] = {}
    recall: dict[int, float] = {}
    ndcg: dict[int, float] = {}
    f1: dict[int, float] = {}

    for k in sorted(all_k):
        precision[k] = sum(m.precision_at_k.get(k, 0.0) for m in metrics_list) / n
        recall[k] = sum(m.recall_at_k.get(k, 0.0) for m in metrics_list) / n
        ndcg[k] = sum(m.ndcg_at_k.get(k, 0.0) for m in metrics_list) / n
        f1[k] = sum(m.f1_at_k.get(k, 0.0) for m in metrics_list) / n

    mrr = sum(m.mrr for m in metrics_list) / n
    hit_rate = sum(m.hit_rate for m in metrics_list) / n

    return EvalMetrics(
        precision_at_k=precision,
        recall_at_k=recall,
        ndcg_at_k=ndcg,
        mrr=mrr,
        f1_at_k=f1,
        hit_rate=hit_rate,
    )
