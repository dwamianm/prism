"""Shared data models for the PRME benchmark suite.

Defines BenchmarkResult and QueryResult used by all benchmark adapters
and the runner infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class QueryResult:
    """Result of evaluating a single benchmark query.

    ``run_id`` tags which run produced this result so the mixed-run guardrail
    (``benchmarks.scoring``) can detect a report that splices together details
    from different runs. ``judge_error`` is the legacy name for an evaluation
    failure (including ingestion, retrieval, generation, or judging). These
    failures are reported separately from wrong answers and accuracy.
    """

    query: str
    category: str
    expected: str
    actual: str
    correct: bool
    score: float
    generated_answer: str = ""
    run_id: str | None = None
    judge_error: bool = False

    @classmethod
    def failed(cls, query: str, category: str, expected: str, error: Exception) -> QueryResult:
        """Keep an unmeasured question in reports and retry selection."""
        return cls(
            query=query, category=category, expected=expected,
            actual=f"Evaluation failed ({type(error).__name__})",
            correct=False, score=float("nan"), judge_error=True,
        )


@dataclass
class BenchmarkResult:
    """Aggregated result from a single benchmark run.

    Attributes:
        benchmark_name: Identifier for the benchmark (e.g., "locomo").
        overall_score: Weighted average score in [0, 1].
        category_scores: Per-category average scores.
        total_queries: Total number of queries evaluated.
        correct: Count of queries passing the correctness threshold.
        incorrect: Count of queries failing the correctness threshold.
        abstained: Count of queries where the engine correctly abstained.
        duration_ms: Benchmark wall-clock time in milliseconds.
        details: Per-query detailed results.
        timestamp: When the benchmark was run.
    """

    benchmark_name: str
    overall_score: float
    category_scores: dict[str, float]
    total_queries: int
    correct: int
    incorrect: int
    abstained: int
    duration_ms: float
    details: list[QueryResult] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    benchmark_error: str | None = None

    @property
    def error_count(self) -> int:
        return sum(d.judge_error for d in self.details)

    @property
    def scored_queries(self) -> int:
        return self.total_queries - self.error_count

    @property
    def coverage(self) -> float:
        return self.scored_queries / self.total_queries if self.total_queries else 0.0

    @property
    def complete(self) -> bool:
        return self.benchmark_error is None and self.error_count == 0

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "benchmark_name": self.benchmark_name,
            "overall_score": round(self.overall_score, 4),
            "category_scores": {
                k: round(v, 4) for k, v in self.category_scores.items()
            },
            "total_queries": self.total_queries,
            "scored_queries": self.scored_queries,
            "error_count": self.error_count,
            "coverage": round(self.coverage, 4),
            "complete": self.complete,
            **({"benchmark_error": self.benchmark_error} if self.benchmark_error else {}),
            "correct": self.correct,
            "incorrect": self.incorrect,
            "abstained": self.abstained,
            "duration_ms": round(self.duration_ms, 1),
            "timestamp": self.timestamp,
            "details": [
                {
                    "query": d.query,
                    "category": d.category,
                    "expected": d.expected,
                    "actual": d.actual,
                    "correct": d.correct,
                    # An infrastructure-error score is NaN, which is not valid
                    # JSON — emit null so reports stay portable to strict parsers.
                    "score": None if d.judge_error else round(d.score, 4),
                    **({"generated_answer": d.generated_answer} if d.generated_answer else {}),
                    **({"run_id": d.run_id} if d.run_id is not None else {}),
                    **({"judge_error": True} if d.judge_error else {}),
                }
                for d in self.details
            ],
        }
