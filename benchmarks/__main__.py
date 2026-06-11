"""CLI entry point for the PRME benchmark suite.

Usage::

    python -m benchmarks [locomo|longmemeval|epistemic|locomo-real|longmemeval-real|all|all-real|all-both]

Examples::

    python -m benchmarks all                     # synthetic only (fast)
    python -m benchmarks all-real                 # real datasets only
    python -m benchmarks all-both                 # everything
    python -m benchmarks locomo-real --json r.json
    python -m benchmarks all --no-parallel

    # LLM generation + judge scoring (requires API key):
    python -m benchmarks locomo-real --llm
    python -m benchmarks all-real --llm --llm-provider anthropic --llm-model claude-sonnet-4-20250514
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from benchmarks.llm_judge import LLMJudgeConfig, VerdictCache
from benchmarks.runner import BenchmarkRunner
from benchmarks.report import generate_json_report, print_summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks",
        description="Run PRME benchmark suite",
    )
    parser.add_argument(
        "benchmarks",
        nargs="*",
        default=["all"],
        help=(
            "Benchmarks to run: locomo, longmemeval, epistemic, "
            "locomo-real, longmemeval-real, all (synthetic), "
            "all-real, or all-both. Defaults to all."
        ),
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        default=None,
        help="Write JSON report to PATH",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        default=False,
        help="Run benchmarks sequentially instead of in parallel",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress human-readable output (only write JSON)",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        default=False,
        help="Enable LLM generation + judge scoring (requires API key)",
    )
    parser.add_argument(
        "--llm-provider",
        default=None,
        help="LLM provider (default: openai). Overrides PRME_EXTRACTION__PROVIDER.",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="LLM model (default: gpt-4o-mini). Overrides PRME_EXTRACTION__MODEL.",
    )
    parser.add_argument(
        "--judge-provider",
        default=None,
        help=(
            "Pin a separate judge provider, independent of the answering model. "
            "Defaults to the answering provider."
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help=(
            "Pin a separate judge model, independent of the answering model. "
            "Avoids same-family leniency bias. Defaults to the answering model."
        ),
    )
    parser.add_argument(
        "--judge-cache",
        metavar="PATH",
        default=None,
        help=(
            "Cache judge verdicts to PATH (keyed by judge model + Q/A pair). "
            "Makes re-runs deterministic and cuts API cost."
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help=(
            "Number of full benchmark runs. With >1, reports majority "
            "correctness and the per-run accuracy spread so a score delta can "
            "be told apart from run-to-run noise."
        ),
    )
    parser.add_argument(
        "--retry-failed",
        metavar="PATH",
        default=None,
        help="Path to a previous JSON report. Only reruns questions that failed.",
    )
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Build LLM config from CLI args + env vars
    import os
    llm_config = LLMJudgeConfig(
        provider=args.llm_provider or os.environ.get("PRME_EXTRACTION__PROVIDER", "openai"),
        model=args.llm_model or os.environ.get("PRME_EXTRACTION__MODEL", "gpt-4o-mini"),
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
        enabled=args.llm,
    )
    if llm_config.enabled:
        print(f"  LLM answerer: {llm_config.provider_string}")
        print(f"  LLM judge:    {llm_config.judge_provider_string}")
        if args.judge_cache:
            print(f"  Judge cache:  {args.judge_cache}")

    if args.runs < 1:
        print("Error: --runs must be at least 1", file=sys.stderr)
        return 1

    # Load failed question IDs from previous report if --retry-failed.
    # load_report rejects hand-merged mixed-run reports before we trust them.
    only_questions: set[str] | None = None
    if args.retry_failed:
        from benchmarks.report import load_report
        from benchmarks.scoring import MixedRunReportError

        try:
            prev_report = load_report(args.retry_failed)
        except MixedRunReportError as exc:
            print(f"Error: {args.retry_failed} is not a clean single run: {exc}", file=sys.stderr)
            return 1
        only_questions = set()
        for bench in prev_report.get("benchmarks", []):
            for detail in bench.get("details", []):
                if not detail.get("correct", True):
                    only_questions.add(detail["query"])
        print(f"  Retrying {len(only_questions)} failed questions from {args.retry_failed}")

    verdict_cache = VerdictCache(args.judge_cache) if args.judge_cache else None
    runner = BenchmarkRunner(llm_config=llm_config, verdict_cache=verdict_cache)

    # Execute one or more full runs. A run_id is stamped on every result so a
    # later report cannot silently splice runs together.
    all_runs: list[list] = []
    for run_index in range(args.runs):
        run_id = f"run-{run_index + 1}"
        if args.runs > 1 and not args.quiet:
            print(f"\n=== Run {run_index + 1}/{args.runs} ({run_id}) ===")
        try:
            results = await runner.run(
                args.benchmarks,
                parallel=not args.no_parallel,
                only_questions=only_questions,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        for r in results:
            for d in r.details:
                d.run_id = run_id
        all_runs.append(results)

        if not args.quiet:
            print_summary(results)

        if args.json:
            out_path = args.json
            if args.runs > 1:
                stem = Path(args.json)
                out_path = str(stem.with_name(f"{stem.stem}.{run_id}{stem.suffix}"))
            generate_json_report(results, output_path=out_path)
            if not args.quiet:
                print(f"  JSON report written to: {out_path}")

    if verdict_cache is not None:
        verdict_cache.flush()

    if args.runs > 1 and not args.quiet:
        _print_multi_run_summary(all_runs)

    # Exit code: 0 if every benchmark in the last run scored > 0, 1 otherwise
    last = all_runs[-1]
    all_ok = all(r.overall_score > 0.0 or r.total_queries == 0 for r in last)
    return 0 if all_ok else 1


def _print_multi_run_summary(all_runs: list[list]) -> None:
    """Print the cross-run majority verdict and accuracy spread per benchmark."""
    from collections import defaultdict

    from benchmarks.scoring import summarize_multi_run

    print()
    print("=" * 70)
    print(f"  Multi-run summary ({len(all_runs)} runs)")
    print("=" * 70)

    # Group results by benchmark name across runs.
    by_bench: dict[str, list] = defaultdict(list)
    for run in all_runs:
        for result in run:
            by_bench[result.benchmark_name].append(result)

    for name, runs in by_bench.items():
        per_question: dict[str, list[float]] = defaultdict(list)
        for result in runs:
            for d in result.details:
                per_question[d.query].append(d.score)
        summary = summarize_multi_run(per_question)
        if summary.total_questions == 0:
            continue
        majority_pct = 100.0 * summary.majority_correct / summary.total_questions
        print(f"\n  [{name}]")
        print(f"    Majority correct: {summary.majority_correct}/{summary.total_questions}"
              f" ({majority_pct:.1f}%)")
        print(f"    Mean accuracy:    {summary.mean_accuracy:.4f}")
        print(f"    Accuracy spread:  ±{summary.accuracy_spread / 2:.4f}"
              f" (range {summary.accuracy_spread:.4f})")
        print("    Per-run accuracy: "
              + ", ".join(f"{a:.4f}" for a in summary.per_run_accuracy))
        print(f"    Unstable (flipped) questions: {summary.unstable_questions}")
        if summary.error_questions:
            print(f"    Judge/generation errors (excluded): {summary.error_questions}")
    print("=" * 70)
    print()


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
