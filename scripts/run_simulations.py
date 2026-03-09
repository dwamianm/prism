import asyncio
import sys

from simulations.harness import SimulationRunner
from simulations.scenarios import SCENARIOS


async def main():
    runner = SimulationRunner()
    results = {}
    for name, scenario in sorted(SCENARIOS.items()):
        report = await runner.run(scenario)
        passed = sum(1 for c in report.checkpoints if c.passed)
        total = len(report.checkpoints)
        results[name] = (passed, total)
        status = "PASS" if passed == total else "PARTIAL" if passed > 0 else "FAIL"
        print(f"  {name}: {status} ({passed}/{total})")

    total_passed = sum(p for p, _ in results.values())
    total_checks = sum(t for _, t in results.values())
    rate = total_passed / total_checks if total_checks else 0
    print(f"\nOverall: {total_passed}/{total_checks} ({rate:.0%})")
    sys.exit(0 if rate >= 0.80 else 1)


asyncio.run(main())
