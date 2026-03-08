"""Tests for simulations.evaluation -- IR metric computation.

Covers precision@k, recall@k, nDCG@k, MRR, F1@k, hit rate, and
aggregate metrics with various edge cases including perfect results,
no relevant results, and empty inputs.
"""

from __future__ import annotations

import math

import pytest

from simulations.evaluation import (
    EvalMetrics,
    GroundTruth,
    aggregate_metrics,
    compute_mrr,
    compute_ndcg_at_k,
    compute_precision_at_k,
    compute_recall_at_k,
    evaluate_retrieval,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_results(contents: list[str]) -> list[dict]:
    """Create minimal result dicts from content strings."""
    return [{"content": c, "score": 1.0 - i * 0.1} for i, c in enumerate(contents)]


def _make_gt(
    query: str = "test query",
    relevant: list[str] | None = None,
    irrelevant: list[str] | None = None,
    grades: dict[str, int] | None = None,
) -> GroundTruth:
    return GroundTruth(
        query=query,
        relevant_keywords=relevant or [],
        irrelevant_keywords=irrelevant or [],
        relevance_grades=grades or {},
    )


# ======================================================================
# Precision@k
# ======================================================================


class TestPrecisionAtK:
    def test_all_relevant(self):
        """All top-k results contain a relevant keyword."""
        results = _make_results(["Python is great", "Python rocks", "Python rules"])
        gt = _make_gt(relevant=["Python"])
        assert compute_precision_at_k(results, gt, 3) == pytest.approx(1.0)

    def test_none_relevant(self):
        """No top-k results are relevant."""
        results = _make_results(["Java is great", "Java rocks", "Java rules"])
        gt = _make_gt(relevant=["Python"])
        assert compute_precision_at_k(results, gt, 3) == pytest.approx(0.0)

    def test_partial_relevant(self):
        """Some top-k results are relevant."""
        results = _make_results(["Python is great", "Java rocks", "Python rules"])
        gt = _make_gt(relevant=["Python"])
        assert compute_precision_at_k(results, gt, 3) == pytest.approx(2.0 / 3.0)

    def test_k_one(self):
        """Precision@1 with a relevant first result."""
        results = _make_results(["Python is here"])
        gt = _make_gt(relevant=["Python"])
        assert compute_precision_at_k(results, gt, 1) == pytest.approx(1.0)

    def test_k_larger_than_results(self):
        """k exceeds available results -- use actual count."""
        results = _make_results(["Python rules"])
        gt = _make_gt(relevant=["Python"])
        assert compute_precision_at_k(results, gt, 5) == pytest.approx(1.0)

    def test_k_zero(self):
        """k=0 returns 0.0."""
        results = _make_results(["Python"])
        gt = _make_gt(relevant=["Python"])
        assert compute_precision_at_k(results, gt, 0) == pytest.approx(0.0)

    def test_empty_results(self):
        """Empty result list returns 0.0."""
        gt = _make_gt(relevant=["Python"])
        assert compute_precision_at_k([], gt, 3) == pytest.approx(0.0)

    def test_case_insensitive(self):
        """Keyword matching is case-insensitive."""
        results = _make_results(["PYTHON is here"])
        gt = _make_gt(relevant=["python"])
        assert compute_precision_at_k(results, gt, 1) == pytest.approx(1.0)


# ======================================================================
# Recall@k
# ======================================================================


class TestRecallAtK:
    def test_all_keywords_found(self):
        """All relevant keywords appear in top-k."""
        results = _make_results([
            "Python is great",
            "TypeScript rocks",
            "Rust rules",
        ])
        gt = _make_gt(relevant=["Python", "TypeScript", "Rust"])
        assert compute_recall_at_k(results, gt, 3) == pytest.approx(1.0)

    def test_some_keywords_found(self):
        """Only some relevant keywords appear in top-k."""
        results = _make_results([
            "Python is great",
            "Java rocks",
            "Rust rules",
        ])
        gt = _make_gt(relevant=["Python", "TypeScript", "Rust"])
        assert compute_recall_at_k(results, gt, 3) == pytest.approx(2.0 / 3.0)

    def test_no_keywords_found(self):
        """None of the relevant keywords appear."""
        results = _make_results(["Java", "C++", "Ruby"])
        gt = _make_gt(relevant=["Python", "Rust"])
        assert compute_recall_at_k(results, gt, 3) == pytest.approx(0.0)

    def test_k_limits_search(self):
        """Recall only considers top-k results."""
        results = _make_results([
            "Java is here",  # irrelevant
            "C++ is here",   # irrelevant
            "Python finally",  # relevant but at position 3
        ])
        gt = _make_gt(relevant=["Python"])
        assert compute_recall_at_k(results, gt, 2) == pytest.approx(0.0)
        assert compute_recall_at_k(results, gt, 3) == pytest.approx(1.0)

    def test_multiple_keywords_in_one_result(self):
        """One result can match multiple relevant keywords."""
        results = _make_results(["Python and Rust together"])
        gt = _make_gt(relevant=["Python", "Rust"])
        assert compute_recall_at_k(results, gt, 1) == pytest.approx(1.0)

    def test_no_relevant_keywords_defined(self):
        """Zero relevant keywords returns 0.0."""
        results = _make_results(["Python"])
        gt = _make_gt(relevant=[])
        assert compute_recall_at_k(results, gt, 1) == pytest.approx(0.0)

    def test_empty_results(self):
        gt = _make_gt(relevant=["Python"])
        assert compute_recall_at_k([], gt, 3) == pytest.approx(0.0)


# ======================================================================
# nDCG@k
# ======================================================================


class TestNDCGAtK:
    def test_perfect_ranking(self):
        """Ideal ordering should give nDCG = 1.0."""
        results = _make_results([
            "highly relevant Python",
            "moderately relevant Python too",
        ])
        gt = _make_gt(
            relevant=["Python"],
            grades={"Python": 3},
        )
        # Both results match "Python" with grade 3.
        # Since both are equally graded, any order is ideal -> nDCG = 1.0
        assert compute_ndcg_at_k(results, gt, 2) == pytest.approx(1.0)

    def test_graded_relevance(self):
        """Results with different grades affect nDCG."""
        results = _make_results([
            "contains low_grade",   # grade 1
            "contains high_grade",  # grade 3
        ])
        gt = _make_gt(
            relevant=["low_grade", "high_grade"],
            grades={"low_grade": 1, "high_grade": 3},
        )
        # DCG: (2^1 -1)/log2(2) + (2^3 -1)/log2(3) = 1/1 + 7/1.585 = 1 + 4.416 = 5.416
        # Ideal: high_grade first -> (2^3 -1)/log2(2) + (2^1 -1)/log2(3) = 7/1 + 1/1.585 = 7.631
        # nDCG = 5.416 / 7.631 ≈ 0.710
        ndcg = compute_ndcg_at_k(results, gt, 2)
        assert 0.5 < ndcg < 1.0  # Sub-optimal ranking

    def test_no_relevant_results(self):
        """No relevant results should give nDCG = 0.0."""
        results = _make_results(["Java", "C++"])
        gt = _make_gt(relevant=["Python"], grades={"Python": 3})
        assert compute_ndcg_at_k(results, gt, 2) == pytest.approx(0.0)

    def test_k_zero(self):
        results = _make_results(["Python"])
        gt = _make_gt(relevant=["Python"])
        assert compute_ndcg_at_k(results, gt, 0) == pytest.approx(0.0)

    def test_empty_results(self):
        gt = _make_gt(relevant=["Python"])
        assert compute_ndcg_at_k([], gt, 3) == pytest.approx(0.0)

    def test_default_grade_for_ungraded_keyword(self):
        """Keywords in relevant_keywords but not in relevance_grades get grade 1."""
        results = _make_results(["Python result"])
        gt = _make_gt(relevant=["Python"])  # no explicit grades
        ndcg = compute_ndcg_at_k(results, gt, 1)
        # DCG = (2^1 - 1)/log2(2) = 1.0
        # IDCG = 1.0  (one keyword with grade 1)
        assert ndcg == pytest.approx(1.0)


# ======================================================================
# MRR
# ======================================================================


class TestMRR:
    def test_first_result_relevant(self):
        """Relevant result at rank 1 gives MRR = 1.0."""
        results = _make_results(["Python", "Java"])
        gt = _make_gt(relevant=["Python"])
        assert compute_mrr(results, gt) == pytest.approx(1.0)

    def test_second_result_relevant(self):
        """Relevant result at rank 2 gives MRR = 0.5."""
        results = _make_results(["Java", "Python"])
        gt = _make_gt(relevant=["Python"])
        assert compute_mrr(results, gt) == pytest.approx(0.5)

    def test_third_result_relevant(self):
        """Relevant result at rank 3 gives MRR = 1/3."""
        results = _make_results(["Java", "C++", "Python"])
        gt = _make_gt(relevant=["Python"])
        assert compute_mrr(results, gt) == pytest.approx(1.0 / 3.0)

    def test_no_relevant_results(self):
        """No relevant results gives MRR = 0.0."""
        results = _make_results(["Java", "C++"])
        gt = _make_gt(relevant=["Python"])
        assert compute_mrr(results, gt) == pytest.approx(0.0)

    def test_empty_results(self):
        gt = _make_gt(relevant=["Python"])
        assert compute_mrr([], gt) == pytest.approx(0.0)

    def test_multiple_relevant_first_counts(self):
        """MRR uses the FIRST relevant result only."""
        results = _make_results(["Java", "Python", "Python again"])
        gt = _make_gt(relevant=["Python"])
        assert compute_mrr(results, gt) == pytest.approx(0.5)


# ======================================================================
# evaluate_retrieval (integration)
# ======================================================================


class TestEvaluateRetrieval:
    def test_perfect_results(self):
        """All results are relevant -- high metrics across the board."""
        results = _make_results([
            "Python rocks", "Python rules", "Python is best",
            "Python forever", "Python always",
        ])
        gt = _make_gt(relevant=["Python"])
        metrics = evaluate_retrieval(results, gt)

        assert metrics.precision_at_k[1] == pytest.approx(1.0)
        assert metrics.precision_at_k[3] == pytest.approx(1.0)
        assert metrics.precision_at_k[5] == pytest.approx(1.0)
        assert metrics.recall_at_k[1] == pytest.approx(1.0)
        assert metrics.mrr == pytest.approx(1.0)
        assert metrics.hit_rate == pytest.approx(1.0)
        assert metrics.f1_at_k[1] == pytest.approx(1.0)
        assert metrics.ndcg_at_k[1] == pytest.approx(1.0)

    def test_no_relevant_results(self):
        """No results are relevant -- all metrics should be 0."""
        results = _make_results(["Java", "C++", "Ruby", "Go", "Rust"])
        gt = _make_gt(relevant=["Python"])
        metrics = evaluate_retrieval(results, gt)

        assert metrics.precision_at_k[1] == pytest.approx(0.0)
        assert metrics.precision_at_k[5] == pytest.approx(0.0)
        assert metrics.recall_at_k[5] == pytest.approx(0.0)
        assert metrics.mrr == pytest.approx(0.0)
        assert metrics.hit_rate == pytest.approx(0.0)
        assert metrics.f1_at_k[5] == pytest.approx(0.0)
        assert metrics.ndcg_at_k[5] == pytest.approx(0.0)

    def test_custom_k_values(self):
        """Custom k_values are respected."""
        results = _make_results(["Python", "Java"])
        gt = _make_gt(relevant=["Python"])
        metrics = evaluate_retrieval(results, gt, k_values=[1, 2, 10])

        assert set(metrics.precision_at_k.keys()) == {1, 2, 10}
        assert set(metrics.recall_at_k.keys()) == {1, 2, 10}
        assert set(metrics.ndcg_at_k.keys()) == {1, 2, 10}
        assert set(metrics.f1_at_k.keys()) == {1, 2, 10}

    def test_f1_computation(self):
        """F1 is the harmonic mean of precision and recall."""
        results = _make_results(["Python", "Java", "Python again"])
        gt = _make_gt(relevant=["Python", "Rust"])
        metrics = evaluate_retrieval(results, gt, k_values=[3])

        p = metrics.precision_at_k[3]
        r = metrics.recall_at_k[3]
        expected_f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        assert metrics.f1_at_k[3] == pytest.approx(expected_f1)

    def test_empty_results(self):
        """Empty results should produce zero metrics."""
        gt = _make_gt(relevant=["Python"])
        metrics = evaluate_retrieval([], gt)

        assert metrics.mrr == pytest.approx(0.0)
        assert metrics.hit_rate == pytest.approx(0.0)
        for k in [1, 3, 5]:
            assert metrics.precision_at_k[k] == pytest.approx(0.0)
            assert metrics.recall_at_k[k] == pytest.approx(0.0)


# ======================================================================
# aggregate_metrics
# ======================================================================


class TestAggregateMetrics:
    def test_average_of_two(self):
        """Averaging two metric sets should produce correct means."""
        m1 = EvalMetrics(
            precision_at_k={1: 1.0, 3: 0.8},
            recall_at_k={1: 0.5, 3: 0.8},
            ndcg_at_k={1: 1.0, 3: 0.9},
            mrr=1.0,
            f1_at_k={1: 0.667, 3: 0.8},
            hit_rate=1.0,
        )
        m2 = EvalMetrics(
            precision_at_k={1: 0.0, 3: 0.4},
            recall_at_k={1: 0.0, 3: 0.4},
            ndcg_at_k={1: 0.0, 3: 0.5},
            mrr=0.5,
            f1_at_k={1: 0.0, 3: 0.4},
            hit_rate=1.0,
        )
        agg = aggregate_metrics([m1, m2])

        assert agg.precision_at_k[1] == pytest.approx(0.5)
        assert agg.precision_at_k[3] == pytest.approx(0.6)
        assert agg.mrr == pytest.approx(0.75)
        assert agg.hit_rate == pytest.approx(1.0)

    def test_empty_list(self):
        """Aggregating no metrics returns zero-valued EvalMetrics."""
        agg = aggregate_metrics([])
        assert agg.mrr == pytest.approx(0.0)
        assert agg.hit_rate == pytest.approx(0.0)
        assert agg.precision_at_k == {}

    def test_single_metric(self):
        """Aggregating one metric set returns it unchanged."""
        m = EvalMetrics(
            precision_at_k={1: 0.8},
            recall_at_k={1: 0.6},
            ndcg_at_k={1: 0.9},
            mrr=0.5,
            f1_at_k={1: 0.686},
            hit_rate=1.0,
        )
        agg = aggregate_metrics([m])
        assert agg.precision_at_k[1] == pytest.approx(0.8)
        assert agg.mrr == pytest.approx(0.5)

    def test_different_k_values_union(self):
        """Metrics with different k-value sets should union the keys."""
        m1 = EvalMetrics(
            precision_at_k={1: 1.0, 5: 0.5},
            recall_at_k={1: 1.0, 5: 0.5},
            ndcg_at_k={1: 1.0, 5: 0.5},
            mrr=1.0,
            f1_at_k={1: 1.0, 5: 0.5},
            hit_rate=1.0,
        )
        m2 = EvalMetrics(
            precision_at_k={1: 0.5, 3: 0.6},
            recall_at_k={1: 0.5, 3: 0.6},
            ndcg_at_k={1: 0.5, 3: 0.6},
            mrr=0.5,
            f1_at_k={1: 0.5, 3: 0.6},
            hit_rate=1.0,
        )
        agg = aggregate_metrics([m1, m2])
        # k=1 present in both, k=3 only in m2 (m1 contributes 0), k=5 only in m1
        assert set(agg.precision_at_k.keys()) == {1, 3, 5}
        assert agg.precision_at_k[1] == pytest.approx(0.75)
        assert agg.precision_at_k[3] == pytest.approx(0.3)  # (0 + 0.6) / 2
        assert agg.precision_at_k[5] == pytest.approx(0.25)  # (0.5 + 0) / 2

    def test_all_zeros(self):
        """Aggregating zero-valued metrics returns zeros."""
        m1 = EvalMetrics()
        m2 = EvalMetrics()
        agg = aggregate_metrics([m1, m2])
        assert agg.mrr == pytest.approx(0.0)
        assert agg.hit_rate == pytest.approx(0.0)


# ======================================================================
# Edge cases
# ======================================================================


class TestEdgeCases:
    def test_evaluation_with_perfect_recall_imperfect_precision(self):
        """All keywords found but extra irrelevant results."""
        results = _make_results([
            "Python rules",
            "Java rocks",  # irrelevant
            "Rust rules",
            "C++ is nice",  # irrelevant
            "Go is fast",   # irrelevant
        ])
        gt = _make_gt(relevant=["Python", "Rust"])
        metrics = evaluate_retrieval(results, gt, k_values=[5])

        assert metrics.recall_at_k[5] == pytest.approx(1.0)
        assert metrics.precision_at_k[5] == pytest.approx(2.0 / 5.0)

    def test_evaluation_with_perfect_precision_imperfect_recall(self):
        """All top-k are relevant but not all keywords covered."""
        results = _make_results([
            "Python is great",
        ])
        gt = _make_gt(relevant=["Python", "Rust", "Go"])
        metrics = evaluate_retrieval(results, gt, k_values=[1])

        assert metrics.precision_at_k[1] == pytest.approx(1.0)
        assert metrics.recall_at_k[1] == pytest.approx(1.0 / 3.0)

    def test_hit_rate_zero(self):
        """No relevant results gives hit_rate = 0."""
        results = _make_results(["Java", "C++"])
        gt = _make_gt(relevant=["Python"])
        metrics = evaluate_retrieval(results, gt)
        assert metrics.hit_rate == pytest.approx(0.0)

    def test_hit_rate_one_with_late_relevant(self):
        """Even a relevant result deep in the list gives hit_rate = 1."""
        results = _make_results(["Java", "C++", "Ruby", "Go", "Python finally"])
        gt = _make_gt(relevant=["Python"])
        metrics = evaluate_retrieval(results, gt)
        assert metrics.hit_rate == pytest.approx(1.0)
