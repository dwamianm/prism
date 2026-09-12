"""Fixed synthetic probes for claim kind, epistemic type and source preservation.

These development probes are not a held-out benchmark or an accuracy estimate.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from benchmarks.diagnostics.entity_references import run
from benchmarks.diagnostics._process import checked_report


def case(name, source, kinds, *, uncertain=False):
    return name, source, {
        "expected_kinds": kinds,
        "allowed_epistemic": ["hypothetical", "conditional"] if uncertain else ["asserted", "observed"],
    }


CASES = [
    case("usage", "Maya uses Redis.", {"Redis": "fact"}),
    case("possible_usage", "Maya might use Redis after evaluation.", {"Redis": "fact"}, uncertain=True),
    case("conditional_usage", "If latency improves, Noah will use SQLite.", {"SQLite": "fact"}, uncertain=True),
    case("explicit_preference", "Elena prefers PostgreSQL.", {"PostgreSQL": "preference"}),
    case("dislike", "Noah does not like Redis.", {"Redis": "preference"}),
    case("choice", "Maya chose SQLite.", {"SQLite": "decision"}),
    case("rejected_choice", "Elena decided against Redis.", {"Redis": "decision"}),
    case("mixed", "Maya uses Redis but prefers SQLite.", {"Redis": "fact", "SQLite": "preference"}),
    case("past_choice", "Last month, Noah selected PostgreSQL.", {"PostgreSQL": "decision"}),
    case("service", "The Cedar service uses Redis.", {"Redis": "fact"}),
    ("namesake", "Jordan, the engineer, lives in Jordan, the country.", {
        "allowed_epistemic": ["asserted", "observed"],
        "expected_entity_types": {"subject": "person", "object": "location"},
    }),
    case("conditional_preference", "If latency is equal, Elena prefers SQLite.", {"SQLite": "preference"}, uncertain=True),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        report = asyncio.run(run(args, cases=CASES))
        report["limits"] = "Twelve authored development probes; not held-out accuracy or competitive evidence."
    else:
        report = checked_report([
            sys.executable, "-m", "benchmarks.diagnostics.claim_classification", "--worker",
            "--model", args.model, "--base-url", args.base_url, "--timeout", str(args.timeout),
        ], timeout=args.timeout * len(CASES) + 60)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not args.worker:
        print(json.dumps({k: v for k, v in report.items() if k != "cases"}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
