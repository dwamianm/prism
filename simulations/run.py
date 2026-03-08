"""Run PRME memory simulations.

Usage:
    python -m simulations.run                    # run all scenarios
    python -m simulations.run changing_facts     # run specific scenario
    python -m simulations.run --list             # list available scenarios
    python -m simulations.run --compare          # run changing_facts with/without organizing
    python -m simulations.run --deterministic changing_facts  # verify deterministic scores
"""

from __future__ import annotations

import asyncio
import sys

from simulations.harness import SimulationRunner


def main() -> None:
    """CLI entry point for simulation runner."""
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--list" in args:
        _list_scenarios()
        return

    if "--deterministic" in args:
        scenario_name = None
        for a in args:
            if a not in ("--deterministic",):
                scenario_name = a
                break
        if not scenario_name:
            print("Error: --deterministic requires a scenario name")
            print("Usage: python -m simulations --deterministic <scenario>")
            sys.exit(1)
        asyncio.run(_run_deterministic(scenario_name))
        return

    if "--compare" in args:
        scenario_name = None
        for a in args:
            if a != "--compare":
                scenario_name = a
                break
        asyncio.run(_run_comparison(scenario_name or "changing_facts"))
        return

    # Run specific scenario(s) or all
    scenario_names = [a for a in args if not a.startswith("-")]
    asyncio.run(_run_scenarios(scenario_names))


def _list_scenarios() -> None:
    """List all available scenarios."""
    from simulations.scenarios import SCENARIOS

    print()
    print("Available simulation scenarios:")
    print("-" * 50)
    for name, scenario in SCENARIOS.items():
        msg_count = len(scenario.messages)
        cp_count = len(scenario.checkpoints)
        print(f"  {name}")
        print(f"    {scenario.description[:80]}")
        print(f"    Messages: {msg_count}, Checkpoints: {cp_count}")
        print()


async def _run_scenarios(names: list[str]) -> None:
    """Run one or more scenarios."""
    from simulations.scenarios import SCENARIOS

    if not names:
        names = list(SCENARIOS.keys())

    runner = SimulationRunner()
    all_passed = True

    for name in names:
        if name not in SCENARIOS:
            print(f"Unknown scenario: {name}")
            print(f"Available: {', '.join(SCENARIOS.keys())}")
            sys.exit(1)

        scenario = SCENARIOS[name]
        report = await runner.run(scenario)
        report.print_report()

        if report.overall_pass_rate < 1.0:
            all_passed = False

    if not all_passed:
        sys.exit(1)


async def _run_deterministic(scenario_name: str) -> None:
    """Run a scenario twice and verify deterministic score reproducibility."""
    from simulations.scenarios import SCENARIOS

    if scenario_name not in SCENARIOS:
        print(f"Unknown scenario: {scenario_name}")
        print(f"Available: {', '.join(SCENARIOS.keys())}")
        sys.exit(1)

    scenario = SCENARIOS[scenario_name]
    runner = SimulationRunner()

    print()
    print(f"  Running deterministic check for: {scenario_name}")
    print(f"  (executing scenario twice with independent state)")
    print()

    result = await runner.run_deterministic_check(scenario)
    result.print_report()

    if not result.passed:
        sys.exit(1)


async def _run_comparison(scenario_name: str) -> None:
    """Run a scenario with and without organizing, then compare."""
    from simulations.scenarios import SCENARIOS

    if scenario_name not in SCENARIOS:
        print(f"Unknown scenario: {scenario_name}")
        print(f"Available: {', '.join(SCENARIOS.keys())}")
        sys.exit(1)

    scenario = SCENARIOS[scenario_name]
    runner = SimulationRunner()

    print()
    print("=" * 70)
    print(f"  Comparison run: {scenario_name}")
    print("=" * 70)

    # Run without organizing
    print()
    print("  [1/2] Running WITHOUT organizing...")
    report_no_org = await runner.run(
        scenario,
        organize_at_checkpoints=False,
    )

    # Run with organizing
    print("  [2/2] Running WITH organizing...")
    report_with_org = await runner.run(
        scenario,
        organize_at_checkpoints=True,
    )

    # Print comparison
    print()
    print("-" * 70)
    print(f"  {'Checkpoint':<40} {'No Org':>10} {'With Org':>10}")
    print("-" * 70)

    for cr_no, cr_with in zip(
        report_no_org.checkpoints, report_with_org.checkpoints
    ):
        desc = cr_no.checkpoint.description[:38]
        no_status = "PASS" if cr_no.passed else "FAIL"
        with_status = "PASS" if cr_with.passed else "FAIL"
        print(f"  {desc:<40} {no_status:>10} {with_status:>10}")

    print("-" * 70)

    no_pass = sum(1 for c in report_no_org.checkpoints if c.passed)
    with_pass = sum(1 for c in report_with_org.checkpoints if c.passed)
    total = len(report_no_org.checkpoints)

    print(
        f"  {'Pass rate':<40} "
        f"{no_pass}/{total!s:>9} "
        f"{with_pass}/{total!s:>9}"
    )
    print(
        f"  {'Duration (ms)':<40} "
        f"{report_no_org.duration_ms:>10.0f} "
        f"{report_with_org.duration_ms:>10.0f}"
    )
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
