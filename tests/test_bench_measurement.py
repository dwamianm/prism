"""Unit tests for benchmark measurement-rigor infrastructure (issue #44).

Covers the pure, deterministic pieces added to make movement toward the
accuracy target measurable:

  - Pinned judge model resolution on LLMJudgeConfig.
  - The JUDGE_ERROR sentinel and is_judge_error predicate.
  - VerdictCache: round-trip, error-never-cached, key sensitivity, buffered
    flush, and corrupt-file recovery.
  - Multi-run aggregation (aggregate_runs / summarize_multi_run).
  - The mixed-run report guardrail (detect_mixed_run / load_report).
  - QueryResult.run_id / judge_error serialization (NaN -> null).

These are dependency-free (no MemoryEngine, no API calls), so they live in
tests/ where CI collects them.
"""

from __future__ import annotations

import json
import math

import pytest

from benchmarks.llm_judge import (
    JUDGE_ERROR,
    LLMJudgeConfig,
    VerdictCache,
    is_judge_error,
)
from benchmarks.models import BenchmarkResult, QueryResult
from benchmarks.report import generate_json_report, load_report
from benchmarks.scoring import (
    CORRECT_THRESHOLD,
    MixedRunReportError,
    aggregate_runs,
    assert_single_run_provenance,
    detect_mixed_run,
    summarize_multi_run,
)


# ---------------------------------------------------------------------------
# Pinned judge config
# ---------------------------------------------------------------------------


class TestPinnedJudgeConfig:
    def test_judge_falls_back_to_answerer_when_unset(self):
        cfg = LLMJudgeConfig(provider="openai", model="gpt-5-mini")
        assert cfg.provider_string == "openai/gpt-5-mini"
        assert cfg.judge_provider_string == "openai/gpt-5-mini"

    def test_pinned_judge_independent_of_answerer(self):
        cfg = LLMJudgeConfig(
            provider="openai",
            model="gpt-5-mini",
            judge_provider="anthropic",
            judge_model="claude-sonnet-4",
        )
        assert cfg.provider_string == "openai/gpt-5-mini"
        assert cfg.judge_provider_string == "anthropic/claude-sonnet-4"

    def test_partial_pin_uses_answerer_for_missing_half(self):
        cfg = LLMJudgeConfig(provider="openai", model="gpt-5-mini", judge_model="o3")
        # judge_provider unset -> falls back to answering provider
        assert cfg.judge_provider_string == "openai/o3"


# ---------------------------------------------------------------------------
# Error sentinel
# ---------------------------------------------------------------------------


class TestJudgeErrorSentinel:
    def test_sentinel_is_nan(self):
        assert math.isnan(JUDGE_ERROR)

    def test_is_judge_error_true_for_sentinel(self):
        assert is_judge_error(JUDGE_ERROR) is True

    def test_is_judge_error_false_for_real_scores(self):
        assert is_judge_error(0.0) is False
        assert is_judge_error(1.0) is False
        assert is_judge_error(0.5) is False


# ---------------------------------------------------------------------------
# VerdictCache
# ---------------------------------------------------------------------------


class TestVerdictCache:
    def test_round_trip_in_memory(self):
        cache = VerdictCache()
        key = VerdictCache.make_key("openai/gpt", "q", "expected", "generated")
        assert cache.get(key) is None
        cache.put(key, 0.8)
        assert cache.get(key) == 0.8

    def test_cached_zero_distinguished_from_miss(self):
        cache = VerdictCache()
        key = VerdictCache.make_key("openai/gpt", "q", "e", "g")
        cache.put(key, 0.0)
        assert cache.get(key) == 0.0  # a real cached 0.0, not a miss
        assert cache.get("other") is None

    def test_error_sentinel_never_cached(self):
        cache = VerdictCache()
        key = VerdictCache.make_key("openai/gpt", "q", "e", "g")
        cache.put(key, JUDGE_ERROR)
        assert cache.get(key) is None
        assert len(cache) == 0

    def test_key_depends_on_judge_model(self):
        k1 = VerdictCache.make_key("openai/gpt-5", "q", "e", "g")
        k2 = VerdictCache.make_key("anthropic/claude", "q", "e", "g")
        assert k1 != k2

    def test_key_field_separation_avoids_collision(self):
        # ("ab","c") must not collide with ("a","bc")
        k1 = VerdictCache.make_key("m", "ab", "c", "g")
        k2 = VerdictCache.make_key("m", "a", "bc", "g")
        assert k1 != k2

    def test_persists_and_reloads(self, tmp_path):
        path = tmp_path / "verdicts.json"
        cache = VerdictCache(path)
        key = VerdictCache.make_key("openai/gpt", "q", "e", "g")
        cache.put(key, 0.9)
        cache.flush()
        assert path.exists()

        reloaded = VerdictCache(path)
        assert reloaded.get(key) == 0.9

    def test_buffered_flush_threshold(self, tmp_path):
        path = tmp_path / "verdicts.json"
        cache = VerdictCache(path)
        cache._FLUSH_EVERY = 3
        cache.put(VerdictCache.make_key("m", "q1", "e", "g"), 0.1)
        cache.put(VerdictCache.make_key("m", "q2", "e", "g"), 0.2)
        # Below threshold: nothing written yet.
        assert not path.exists()
        cache.put(VerdictCache.make_key("m", "q3", "e", "g"), 0.3)
        # Threshold reached: auto-flushed.
        assert path.exists()
        assert len(json.loads(path.read_text())) == 3

    def test_corrupt_file_recovers_to_empty(self, tmp_path):
        path = tmp_path / "verdicts.json"
        path.write_text("{ this is not valid json")
        cache = VerdictCache(path)  # must not raise
        assert len(cache) == 0

    def test_flush_noop_without_path(self):
        cache = VerdictCache()  # no path
        cache.put(VerdictCache.make_key("m", "q", "e", "g"), 0.5)
        cache.flush()  # must not raise


# ---------------------------------------------------------------------------
# Multi-run aggregation
# ---------------------------------------------------------------------------


class TestAggregateRuns:
    def test_majority_correct(self):
        agg = aggregate_runs([1.0, 1.0, 0.0])
        assert agg.runs == 3
        assert agg.majority_correct is True
        assert agg.mean == pytest.approx(2 / 3)
        assert agg.spread == pytest.approx(1.0)
        assert agg.error_runs == 0

    def test_majority_tie_is_not_correct(self):
        # 2 of 4 pass -> pass_fraction 0.5, strict > 0.5 means NOT majority.
        agg = aggregate_runs([1.0, 1.0, 0.0, 0.0])
        assert agg.pass_fraction == 0.5
        assert agg.majority_correct is False

    def test_all_errors_returns_zero_aggregate(self):
        agg = aggregate_runs([JUDGE_ERROR, JUDGE_ERROR])
        assert agg.runs == 0
        assert agg.error_runs == 2
        assert agg.majority_correct is False
        assert agg.mean == 0.0
        assert agg.spread == 0.0

    def test_errors_excluded_from_mean_and_majority(self):
        agg = aggregate_runs([1.0, JUDGE_ERROR])
        assert agg.runs == 1
        assert agg.error_runs == 1
        assert agg.mean == 1.0
        assert agg.majority_correct is True

    def test_threshold_boundary(self):
        agg = aggregate_runs([CORRECT_THRESHOLD], threshold=CORRECT_THRESHOLD)
        # score == threshold counts as a pass
        assert agg.majority_correct is True


class TestSummarizeMultiRun:
    def test_empty_input(self):
        summary = summarize_multi_run({})
        assert summary.runs == 0
        assert summary.total_questions == 0
        assert summary.accuracy_spread == 0.0

    def test_majority_and_spread(self):
        # q1 passes 2/3, q2 fails all 3
        summary = summarize_multi_run(
            {"q1": [1.0, 1.0, 0.0], "q2": [0.0, 0.0, 0.0]}
        )
        assert summary.runs == 3
        assert summary.total_questions == 2
        assert summary.majority_correct == 1
        # per-run accuracy: run1 1/2, run2 1/2, run3 0/2
        assert summary.per_run_accuracy == pytest.approx([0.5, 0.5, 0.0])
        assert summary.accuracy_spread == pytest.approx(0.5)

    def test_unstable_questions_counted(self):
        summary = summarize_multi_run({"q1": [1.0, 0.0], "q2": [1.0, 1.0]})
        # q1 flips (unstable), q2 stable
        assert summary.unstable_questions == 1

    def test_error_runs_excluded_from_accuracy(self):
        # q2 errors in run 3 -> excluded from that run's denominator, surfaced
        summary = summarize_multi_run(
            {"q1": [1.0, 1.0, 0.0], "q2": [0.0, 0.0, JUDGE_ERROR]}
        )
        assert summary.error_questions == 1
        # run 3: q1=0.0 graded fail, q2 excluded -> 0/1
        assert summary.per_run_accuracy[2] == pytest.approx(0.0)

    def test_ragged_run_counts_tolerated(self):
        # q2 only ran twice; must not IndexError
        summary = summarize_multi_run({"q1": [1.0, 1.0, 1.0], "q2": [0.0, 0.0]})
        assert summary.runs == 3
        assert len(summary.per_run_accuracy) == 3


# ---------------------------------------------------------------------------
# Mixed-run guardrail
# ---------------------------------------------------------------------------


class TestMixedRunGuardrail:
    def test_clean_single_run_passes(self):
        report = {
            "benchmarks": [
                {"details": [{"run_id": "run-1"}, {"run_id": "run-1"}]}
            ]
        }
        assert detect_mixed_run(report) is None
        assert_single_run_provenance(report)  # must not raise

    def test_untagged_report_passes(self):
        # Old reports with no provenance are not flagged.
        report = {"benchmarks": [{"details": [{}, {}]}]}
        assert detect_mixed_run(report) is None

    def test_multiple_run_ids_flagged(self):
        report = {
            "benchmarks": [
                {"details": [{"run_id": "run-1"}, {"run_id": "run-2"}]}
            ]
        }
        assert detect_mixed_run(report) is not None
        with pytest.raises(MixedRunReportError):
            assert_single_run_provenance(report)

    def test_tagged_and_untagged_mix_flagged(self):
        report = {
            "benchmarks": [{"details": [{"run_id": "run-1"}, {}]}]
        }
        assert detect_mixed_run(report) is not None
        with pytest.raises(MixedRunReportError):
            assert_single_run_provenance(report)

    def test_load_report_rejects_mixed(self, tmp_path):
        path = tmp_path / "mixed.json"
        path.write_text(
            json.dumps(
                {
                    "benchmarks": [
                        {"details": [{"run_id": "run-1"}, {"run_id": "run-2"}]}
                    ]
                }
            )
        )
        with pytest.raises(MixedRunReportError):
            load_report(path)

    def test_load_report_accepts_clean(self, tmp_path):
        path = tmp_path / "clean.json"
        path.write_text(
            json.dumps(
                {"benchmarks": [{"details": [{"run_id": "run-1"}]}]}
            )
        )
        report = load_report(path)
        assert report["benchmarks"][0]["details"][0]["run_id"] == "run-1"


# ---------------------------------------------------------------------------
# QueryResult provenance + error serialization
# ---------------------------------------------------------------------------


class TestQueryResultSerialization:
    def test_run_id_and_judge_error_serialized(self):
        qr = QueryResult(
            query="q", category="c", expected="e", actual="a",
            correct=True, score=0.9, run_id="run-2",
        )
        result = BenchmarkResult(
            benchmark_name="b", overall_score=0.9, category_scores={},
            total_queries=1, correct=1, incorrect=0, abstained=0,
            duration_ms=1.0, details=[qr],
        )
        d = result.to_dict()["details"][0]
        assert d["run_id"] == "run-2"
        assert "judge_error" not in d  # only emitted when True

    def test_error_score_serialized_as_null_and_is_valid_json(self):
        qr = QueryResult(
            query="q", category="c", expected="e", actual="a",
            correct=False, score=JUDGE_ERROR, judge_error=True,
        )
        result = BenchmarkResult(
            benchmark_name="b", overall_score=0.0, category_scores={},
            total_queries=1, correct=0, incorrect=0, abstained=0,
            duration_ms=1.0, details=[qr],
        )
        d = result.to_dict()["details"][0]
        assert d["score"] is None
        assert d["judge_error"] is True
        # NaN would make this unparseable by a strict JSON reader; null is fine.
        serialized = generate_json_report([result])
        json.loads(serialized)  # must not raise

    def test_round_trip_through_report_preserves_run_id(self, tmp_path):
        qr = QueryResult(
            query="q", category="c", expected="e", actual="a",
            correct=False, score=0.0, run_id="run-1",
        )
        result = BenchmarkResult(
            benchmark_name="b", overall_score=0.0, category_scores={},
            total_queries=1, correct=0, incorrect=1, abstained=0,
            duration_ms=1.0, details=[qr],
        )
        path = tmp_path / "report.json"
        generate_json_report([result], output_path=path)
        loaded = load_report(path)  # single run -> guardrail passes
        assert loaded["benchmarks"][0]["details"][0]["run_id"] == "run-1"
