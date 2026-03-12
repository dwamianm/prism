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

from benchmarks.llm_judge import LLMJudgeConfig
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
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Build LLM config from CLI args + env vars
    import os
    llm_config = LLMJudgeConfig(
        provider=args.llm_provider or os.environ.get("PRME_EXTRACTION__PROVIDER", "openai"),
        model=args.llm_model or os.environ.get("PRME_EXTRACTION__MODEL", "gpt-4o-mini"),
        enabled=args.llm,
    )
    if llm_config.enabled:
        print(f"  LLM judge: {llm_config.provider_string}")

    runner = BenchmarkRunner(llm_config=llm_config)
    try:
        results = await runner.run(
            args.benchmarks,
            parallel=not args.no_parallel,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print_summary(results)

    if args.json:
        generate_json_report(results, output_path=args.json)
        if not args.quiet:
            print(f"  JSON report written to: {args.json}")

    # Exit code: 0 if all benchmarks scored > 0, 1 otherwise
    all_ok = all(r.overall_score > 0.0 or r.total_queries == 0 for r in results)
    return 0 if all_ok else 1


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
