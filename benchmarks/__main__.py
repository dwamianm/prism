"""CLI entry point for the PRME benchmark suite.

Usage::

    python -m benchmarks [locomo|longmemeval|epistemic|all]

Examples::

    python -m benchmarks all
    python -m benchmarks locomo epistemic
    python -m benchmarks longmemeval --json report.json
    python -m benchmarks all --no-parallel
"""

from __future__ import annotations

import argparse
import asyncio
import sys

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
            "Benchmarks to run: locomo, longmemeval, epistemic, or all. "
            "Defaults to all."
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
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    runner = BenchmarkRunner()
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
