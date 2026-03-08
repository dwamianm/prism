"""Smoke tests for the PRME benchmark suite.

Verifies that each benchmark can run against a real MemoryEngine with
small synthetic datasets and produce valid BenchmarkResult objects.
Uses isolated temp directories per test for DuckDB safety.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine

from benchmarks.models import BenchmarkResult, QueryResult
from benchmarks.metrics import (
    category_scores,
    exclusion_score,
    keyword_match_score,
)
from benchmarks.report import generate_json_report, print_summary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def bench_engine(tmp_path):
    """Create an isolated MemoryEngine for benchmark tests."""
    lexical_dir = tmp_path / "lexical_index"
    lexical_dir.mkdir(parents=True, exist_ok=True)
    config = PRMEConfig(
        db_path=str(tmp_path / "bench.duckdb"),
        vector_path=str(tmp_path / "bench.usearch"),
        lexical_path=str(lexical_dir),
    )
    engine = await MemoryEngine.create(config)
    yield engine
    await engine.close()


# ---------------------------------------------------------------------------
# Metric unit tests
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_keyword_match_all_found(self):
        assert keyword_match_score(["hello", "world"], "hello world") == 1.0

    def test_keyword_match_none_found(self):
        assert keyword_match_score(["hello"], "goodbye") == 0.0

    def test_keyword_match_partial(self):
        assert keyword_match_score(["a", "b", "c"], "a and c") == pytest.approx(
            2 / 3
        )

    def test_keyword_match_empty(self):
        assert keyword_match_score([], "anything") == 1.0

    def test_keyword_match_case_insensitive(self):
        assert keyword_match_score(["HELLO"], "hello world") == 1.0

    def test_exclusion_score_none_found(self):
        assert exclusion_score(["bad"], "good text") == 1.0

    def test_exclusion_score_all_found(self):
        assert exclusion_score(["bad", "worse"], "bad and worse") == 0.0

    def test_exclusion_score_empty(self):
        assert exclusion_score([], "anything") == 1.0

    def test_category_scores_basic(self):
        data = [("qa", 0.8), ("qa", 0.6), ("temporal", 1.0)]
        result = category_scores(data)
        assert result["qa"] == pytest.approx(0.7)
        assert result["temporal"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_benchmark_result_to_dict(self):
        result = BenchmarkResult(
            benchmark_name="test",
            overall_score=0.85,
            category_scores={"qa": 0.9, "temporal": 0.8},
            total_queries=10,
            correct=8,
            incorrect=2,
            abstained=0,
            duration_ms=1234.5,
            details=[
                QueryResult(
                    query="test query",
                    category="qa",
                    expected="expected",
                    actual="actual",
                    correct=True,
                    score=0.9,
                )
            ],
        )
        d = result.to_dict()
        assert d["benchmark_name"] == "test"
        assert d["overall_score"] == 0.85
        assert len(d["details"]) == 1
        assert d["details"][0]["score"] == 0.9

    def test_query_result_fields(self):
        qr = QueryResult(
            query="q", category="c", expected="e", actual="a",
            correct=True, score=1.0,
        )
        assert qr.correct is True
        assert qr.score == 1.0


# ---------------------------------------------------------------------------
# LoCoMo benchmark smoke test
# ---------------------------------------------------------------------------


class TestLoCoMoBenchmark:
    async def test_locomo_runs_with_small_dataset(self, bench_engine):
        """LoCoMo benchmark completes with a minimal turn count."""
        from benchmarks.locomo import LoCoMoBenchmark

        bench = LoCoMoBenchmark(turns=20)
        result = await bench.run(bench_engine)

        assert isinstance(result, BenchmarkResult)
        assert result.benchmark_name == "locomo"
        assert result.total_queries > 0
        assert 0.0 <= result.overall_score <= 1.0
        assert result.correct + result.incorrect == result.total_queries
        assert result.duration_ms > 0

    async def test_locomo_category_scores(self, bench_engine):
        """LoCoMo produces scores for expected categories."""
        from benchmarks.locomo import LoCoMoBenchmark

        bench = LoCoMoBenchmark(turns=20)
        result = await bench.run(bench_engine)

        # Should have qa, summarization, temporal categories
        assert len(result.category_scores) > 0
        for score in result.category_scores.values():
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# LongMemEval benchmark smoke test
# ---------------------------------------------------------------------------


class TestLongMemEvalBenchmark:
    async def test_longmemeval_runs(self, bench_engine):
        """LongMemEval benchmark completes and produces results."""
        from benchmarks.longmemeval import LongMemEvalBenchmark

        bench = LongMemEvalBenchmark()
        result = await bench.run(bench_engine)

        assert isinstance(result, BenchmarkResult)
        assert result.benchmark_name == "longmemeval"
        assert result.total_queries > 0
        assert 0.0 <= result.overall_score <= 1.0
        assert result.duration_ms > 0

    async def test_longmemeval_five_abilities(self, bench_engine):
        """LongMemEval tests all 5 core abilities."""
        from benchmarks.longmemeval import LongMemEvalBenchmark

        bench = LongMemEvalBenchmark()
        result = await bench.run(bench_engine)

        # Should have all 5 ability categories
        expected_abilities = {
            "info_extraction",
            "multi_session",
            "temporal",
            "knowledge_update",
            "abstention",
        }
        assert expected_abilities == set(result.category_scores.keys())


# ---------------------------------------------------------------------------
# Epistemic benchmark smoke test
# ---------------------------------------------------------------------------


class TestEpistemicBenchmark:
    async def test_epistemic_runs(self, bench_engine):
        """Epistemic benchmark completes and produces results."""
        from benchmarks.epistemic import EpistemicBenchmark

        bench = EpistemicBenchmark()
        result = await bench.run(bench_engine)

        assert isinstance(result, BenchmarkResult)
        assert result.benchmark_name == "epistemic"
        assert result.total_queries > 0
        assert 0.0 <= result.overall_score <= 1.0
        assert result.duration_ms > 0

    async def test_epistemic_five_abilities(self, bench_engine):
        """Epistemic tests all 5 epistemic abilities."""
        from benchmarks.epistemic import EpistemicBenchmark

        bench = EpistemicBenchmark()
        result = await bench.run(bench_engine)

        expected_abilities = {
            "supersedence",
            "confidence",
            "contradiction",
            "belief_revision",
            "abstention",
        }
        assert expected_abilities == set(result.category_scores.keys())


# ---------------------------------------------------------------------------
# Runner smoke test
# ---------------------------------------------------------------------------


class TestBenchmarkRunner:
    async def test_runner_resolve_all(self):
        """Runner resolves 'all' to all registered benchmarks."""
        from benchmarks.runner import BenchmarkRunner

        runner = BenchmarkRunner()
        resolved = runner.resolve_names(["all"])
        assert "locomo" in resolved
        assert "longmemeval" in resolved
        assert "epistemic" in resolved

    async def test_runner_resolve_single(self):
        """Runner resolves a single benchmark name."""
        from benchmarks.runner import BenchmarkRunner

        runner = BenchmarkRunner()
        resolved = runner.resolve_names(["locomo"])
        assert resolved == ["locomo"]

    async def test_runner_invalid_name_raises(self):
        """Runner raises ValueError for unknown benchmark names."""
        from benchmarks.runner import BenchmarkRunner

        runner = BenchmarkRunner()
        with pytest.raises(ValueError, match="Unknown benchmark"):
            runner.resolve_names(["nonexistent"])

    async def test_runner_available_list(self):
        """Runner lists available benchmarks."""
        from benchmarks.runner import BenchmarkRunner

        runner = BenchmarkRunner()
        available = runner.available
        assert len(available) == 3
        assert "epistemic" in available


# ---------------------------------------------------------------------------
# Report smoke test
# ---------------------------------------------------------------------------


class TestReport:
    def test_json_report_generation(self, tmp_path):
        """JSON report generates valid output."""
        results = [
            BenchmarkResult(
                benchmark_name="test",
                overall_score=0.75,
                category_scores={"qa": 0.8},
                total_queries=5,
                correct=4,
                incorrect=1,
                abstained=0,
                duration_ms=100.0,
            )
        ]
        out_path = tmp_path / "report.json"
        json_str = generate_json_report(results, output_path=out_path)

        assert out_path.exists()
        import json

        data = json.loads(json_str)
        assert "benchmarks" in data
        assert "summary" in data
        assert data["benchmarks"][0]["benchmark_name"] == "test"

    def test_print_summary_runs(self, capsys):
        """print_summary runs without error."""
        results = [
            BenchmarkResult(
                benchmark_name="test",
                overall_score=0.5,
                category_scores={"qa": 0.5},
                total_queries=2,
                correct=1,
                incorrect=1,
                abstained=0,
                duration_ms=50.0,
                details=[
                    QueryResult(
                        query="q",
                        category="qa",
                        expected="e",
                        actual="a",
                        correct=False,
                        score=0.0,
                    )
                ],
            )
        ]
        print_summary(results)
        captured = capsys.readouterr()
        assert "PRME Benchmark Suite Results" in captured.out
        assert "TEST" in captured.out
