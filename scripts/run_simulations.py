"""Run causal simulation checks; every selected checkpoint must pass."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from simulations.harness import SimulationRunner
from simulations.scenarios import SCENARIOS


async def run_scenarios(names, *, output: Path | None = None) -> int:
    runner = SimulationRunner()
    reports, errors = {}, {}
    for name in names:
        try:
            report = await runner.run(SCENARIOS[name])
        except Exception as exc:
            errors[name] = type(exc).__name__
            print(f"  {name}: ERROR ({type(exc).__name__})")
            continue
        reports[name] = asdict(report)
        passed = sum(c.passed for c in report.checkpoints)
        total = len(report.checkpoints)
        status = "PASS" if total and passed == total else "FAIL"
        print(f"  {name}: {status} ({passed}/{total})")

    checks = [c for report in reports.values() for c in report["checkpoints"]]
    passed = sum(c["passed"] for c in checks)
    complete = len(reports) == len(names) and all(r["checkpoints"] for r in reports.values())
    successful = complete and bool(checks) and passed == len(checks) and not errors
    payload = {"schema_version": 1, "selected_scenarios": list(names), "passed": passed,
               "total": len(checks), "complete": complete, "successful": successful,
               "errors": errors, "scenarios": reports}
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nOverall: {passed}/{len(checks)}; {len(errors)} errors; "
          f"{'PASS' if successful else 'FAIL'}")
    return 0 if successful else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS),
                        help="Select a scenario (repeatable); defaults to all")
    parser.add_argument("--output", type=Path, help="Retain all checkpoint results and failures as JSON")
    args = parser.parse_args()
    names = sorted(set(args.scenario or SCENARIOS))
    return asyncio.run(run_scenarios(names, output=args.output))


if __name__ == "__main__":
    raise SystemExit(main())
