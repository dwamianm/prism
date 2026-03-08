"""Evaluation runner for aggregate retrieval quality assessment.

Runs all (or selected) scenarios that carry ground truth checkpoints,
collects per-scenario and aggregate IR metrics, and prints a summary
table.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from simulations.evaluation import EvalMetrics, aggregate_metrics
from simulations.harness import SimulationReport, SimulationRunner, SimScenario


@dataclass
class EvalReport:
    """Aggregate evaluation report across multiple scenarios."""

    scenario_metrics: dict[str, EvalMetrics] = field(default_factory=dict)
    scenario_reports: dict[str, SimulationReport] = field(default_factory=dict)
    aggregate: EvalMetrics = field(default_factory=EvalMetrics)

    def print_report(self) -> None:
        """Print a human-readable evaluation summary to stdout."""
        print()
        print("=" * 70)
        print("  Evaluation Report")
        print("=" * 70)

        if not self.scenario_metrics:
            print("  No scenarios with ground truth found.")
            print("=" * 70)
            print()
            return

        # Per-scenario metrics table header.
        print()
        print(f"  {'Scenario':<30} {'MRR':>6} {'Hit':>6} {'P@5':>6} "
              f"{'R@5':>6} {'F1@5':>6} {'nDCG@5':>7}")
        print("  " + "-" * 67)

        for name, metrics in self.scenario_metrics.items():
            print(
                f"  {name:<30} "
                f"{metrics.mrr:>6.3f} "
                f"{metrics.hit_rate:>6.3f} "
                f"{metrics.precision_at_k.get(5, 0.0):>6.3f} "
                f"{metrics.recall_at_k.get(5, 0.0):>6.3f} "
                f"{metrics.f1_at_k.get(5, 0.0):>6.3f} "
                f"{metrics.ndcg_at_k.get(5, 0.0):>7.3f}"
            )

        # Aggregate row.
        print("  " + "-" * 67)
        agg = self.aggregate
        print(
            f"  {'AGGREGATE':<30} "
            f"{agg.mrr:>6.3f} "
            f"{agg.hit_rate:>6.3f} "
            f"{agg.precision_at_k.get(5, 0.0):>6.3f} "
            f"{agg.recall_at_k.get(5, 0.0):>6.3f} "
            f"{agg.f1_at_k.get(5, 0.0):>6.3f} "
            f"{agg.ndcg_at_k.get(5, 0.0):>7.3f}"
        )

        # Detail table for all k values in the aggregate.
        print()
        print("  Aggregate @k breakdown:")
        for k in sorted(agg.precision_at_k):
            print(
                f"    @{k}  P={agg.precision_at_k[k]:.3f}  "
                f"R={agg.recall_at_k[k]:.3f}  "
                f"F1={agg.f1_at_k[k]:.3f}  "
                f"nDCG={agg.ndcg_at_k[k]:.3f}"
            )

        print()
        print("=" * 70)
        print()


class EvalRunner:
    """Runs scenarios and produces an aggregate evaluation report."""

    def __init__(self, runner: SimulationRunner | None = None) -> None:
        self._runner = runner or SimulationRunner()

    async def run_evaluation(
        self,
        scenarios: dict[str, SimScenario],
    ) -> EvalReport:
        """Run all scenarios and collect evaluation metrics.

        Only checkpoints with ``ground_truth`` set contribute to metrics.
        Scenarios whose checkpoints have no ground truth are still run
        (their SimulationReport is stored) but do not contribute metrics.

        Args:
            scenarios: Mapping of name -> SimScenario.

        Returns:
            EvalReport with per-scenario and aggregate metrics.
        """
        report = EvalReport()

        all_per_query_metrics: list[EvalMetrics] = []

        for name, scenario in scenarios.items():
            sim_report = await self._runner.run(scenario)
            report.scenario_reports[name] = sim_report

            # Collect eval metrics from checkpoints that have them.
            checkpoint_metrics: list[EvalMetrics] = []
            for cr in sim_report.checkpoints:
                if cr.eval_metrics is not None:
                    checkpoint_metrics.append(cr.eval_metrics)
                    all_per_query_metrics.append(cr.eval_metrics)

            if checkpoint_metrics:
                report.scenario_metrics[name] = aggregate_metrics(checkpoint_metrics)

        report.aggregate = aggregate_metrics(all_per_query_metrics)
        return report
