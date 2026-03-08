"""Report generation for the PRME benchmark suite.

Produces both JSON output (for programmatic consumption) and
human-readable terminal summaries with scores and comparisons.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.models import BenchmarkResult


def generate_json_report(
    results: list[BenchmarkResult],
    output_path: Path | str | None = None,
) -> str:
    """Generate a JSON report from benchmark results.

    Args:
        results: List of BenchmarkResult from a runner execution.
        output_path: Optional file path to write the JSON report.
            If None, only returns the JSON string.

    Returns:
        JSON string of the full report.
    """
    report = {
        "benchmarks": [r.to_dict() for r in results],
        "summary": _build_summary(results),
    }
    json_str = json.dumps(report, indent=2)

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json_str, encoding="utf-8")

    return json_str


def print_summary(results: list[BenchmarkResult]) -> None:
    """Print a human-readable summary to stdout.

    Args:
        results: List of BenchmarkResult from a runner execution.
    """
    print()
    print("=" * 70)
    print("  PRME Benchmark Suite Results")
    print("=" * 70)
    print()

    for result in results:
        _print_benchmark_result(result)

    # Overall summary
    summary = _build_summary(results)
    print("-" * 70)
    print("  Overall Summary")
    print("-" * 70)
    print(f"  Benchmarks run:  {summary['benchmarks_run']}")
    print(f"  Total queries:   {summary['total_queries']}")
    print(f"  Overall score:   {summary['overall_score']:.4f}")
    print(f"  Total correct:   {summary['total_correct']}")
    print(f"  Total incorrect: {summary['total_incorrect']}")
    print(f"  Total abstained: {summary['total_abstained']}")
    print(f"  Total duration:  {summary['total_duration_ms']:.0f}ms")
    print("=" * 70)
    print()


def _print_benchmark_result(result: BenchmarkResult) -> None:
    """Print a single benchmark result section."""
    print(f"  [{result.benchmark_name.upper()}]")
    print(f"    Score:    {result.overall_score:.4f}")
    print(f"    Queries:  {result.total_queries}")
    print(
        f"    Correct:  {result.correct}  |  "
        f"Incorrect: {result.incorrect}  |  "
        f"Abstained: {result.abstained}"
    )
    print(f"    Duration: {result.duration_ms:.0f}ms")

    if result.category_scores:
        print("    Categories:")
        for cat, score in sorted(result.category_scores.items()):
            bar = _score_bar(score, width=20)
            print(f"      {cat:<25s} {score:.4f}  {bar}")

    # Show failed queries (max 5 per benchmark)
    failed = [d for d in result.details if not d.correct]
    if failed:
        show = failed[:5]
        print(f"    Failed queries ({len(failed)} total, showing {len(show)}):")
        for d in show:
            print(f"      [{d.category}] {d.query}")
            print(f"        Expected: {d.expected}")
            print(f"        Got:      {d.actual[:100]}")
    print()


def _score_bar(score: float, width: int = 20) -> str:
    """Render a simple ASCII progress bar for a score in [0, 1]."""
    filled = int(score * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _build_summary(results: list[BenchmarkResult]) -> dict:
    """Build an aggregate summary dictionary."""
    total_queries = sum(r.total_queries for r in results)
    total_correct = sum(r.correct for r in results)
    total_incorrect = sum(r.incorrect for r in results)
    total_abstained = sum(r.abstained for r in results)
    total_duration = sum(r.duration_ms for r in results)

    # Weighted average by query count
    if total_queries > 0:
        overall_score = sum(
            r.overall_score * r.total_queries for r in results
        ) / total_queries
    else:
        overall_score = 0.0

    return {
        "benchmarks_run": len(results),
        "total_queries": total_queries,
        "overall_score": round(overall_score, 4),
        "total_correct": total_correct,
        "total_incorrect": total_incorrect,
        "total_abstained": total_abstained,
        "total_duration_ms": round(total_duration, 1),
        "per_benchmark": {
            r.benchmark_name: round(r.overall_score, 4) for r in results
        },
    }
