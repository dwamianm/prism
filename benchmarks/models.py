"""Shared data models for the PRME benchmark suite.

Defines BenchmarkResult and QueryResult used by all benchmark adapters
and the runner infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class QueryResult:
    """Result of evaluating a single benchmark query."""

    query: str
    category: str
    expected: str
    actual: str
    correct: bool
    score: float
    generated_answer: str = ""


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

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "benchmark_name": self.benchmark_name,
            "overall_score": round(self.overall_score, 4),
            "category_scores": {
                k: round(v, 4) for k, v in self.category_scores.items()
            },
            "total_queries": self.total_queries,
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
                    "score": round(d.score, 4),
                    **({"generated_answer": d.generated_answer} if d.generated_answer else {}),
                }
                for d in self.details
            ],
        }
