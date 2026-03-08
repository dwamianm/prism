"""Core simulation engine for PRME memory validation.

Runs scenarios through MemoryEngine with simulated time progression,
evaluating retrieval behavior at defined checkpoints. No LLM required --
uses engine.store() directly.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.types import EpistemicType, LifecycleState, NodeType, Scope

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------


@dataclass
class SimMessage:
    """A single message to store at a simulated day."""

    day: int  # simulated day number
    role: str  # "user" or "assistant"
    content: str  # message text
    tags: list[str]  # ground truth topic tags
    node_type: str = "fact"  # NodeType value
    epistemic_type: str | None = None  # optional override


@dataclass
class SimCheckpoint:
    """An evaluation point in the simulation timeline."""

    day: int  # when to evaluate
    query: str  # what to ask
    expected_keywords: list[str]  # must appear in top results
    excluded_keywords: list[str]  # must NOT appear in top results
    description: str  # human-readable description
    lifecycle_assertions: dict[str, int] = field(default_factory=dict)
    # Maps lifecycle state name to minimum expected count.
    # e.g., {"stable": 3} means "at least 3 nodes should be in stable state"


@dataclass
class SimScenario:
    """Complete simulation scenario with messages and checkpoints."""

    name: str
    description: str
    messages: list[SimMessage]
    checkpoints: list[SimCheckpoint]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class CheckpointResult:
    """Result of evaluating a single checkpoint."""

    checkpoint: SimCheckpoint
    passed: bool
    top_results: list[dict]  # [{content, score, node_type, lifecycle_state}]
    expected_found: list[str]  # expected keywords that were found
    expected_missing: list[str]  # expected keywords that were NOT found
    excluded_found: list[str]  # excluded keywords that appeared (bad)
    lifecycle_failures: list[str] = field(default_factory=list)
    # Diagnostic messages for failed lifecycle assertions
    lifecycle_counts: dict[str, int] = field(default_factory=dict)
    # Actual counts of each lifecycle state


@dataclass
class SimulationReport:
    """Full report from a simulation run."""

    scenario_name: str
    config_summary: dict
    checkpoints: list[CheckpointResult]
    overall_pass_rate: float
    total_nodes: int
    duration_ms: float

    def print_report(self) -> None:
        """Print a human-readable report to stdout."""
        print()
        print("=" * 70)
        print(f"  Simulation: {self.scenario_name}")
        print("=" * 70)
        print()

        passed = sum(1 for c in self.checkpoints if c.passed)
        total = len(self.checkpoints)

        for i, cr in enumerate(self.checkpoints, 1):
            status = "[PASS]" if cr.passed else "[FAIL]"
            print(f"  Checkpoint {i}: {status} - {cr.checkpoint.description}")
            print(f"    Query: \"{cr.checkpoint.query}\"")
            print(f"    Day: {cr.checkpoint.day}")

            if cr.expected_found:
                print(f"    Found expected: {', '.join(cr.expected_found)}")
            if cr.expected_missing:
                print(f"    Missing expected: {', '.join(cr.expected_missing)}")
            if cr.excluded_found:
                print(f"    Unwanted found: {', '.join(cr.excluded_found)}")

            if cr.lifecycle_counts:
                counts_str = ", ".join(
                    f"{k}={v}" for k, v in sorted(cr.lifecycle_counts.items()) if v > 0
                )
                print(f"    Lifecycle states: {counts_str}")
            if cr.lifecycle_failures:
                print(f"    Lifecycle failures: {'; '.join(cr.lifecycle_failures)}")

            if cr.top_results:
                print("    Top results:")
                for j, r in enumerate(cr.top_results[:5], 1):
                    content_preview = r["content"][:80]
                    print(
                        f"      {j}. [{r['score']:.3f}] "
                        f"({r['node_type']}/{r['lifecycle_state']}) "
                        f"{content_preview}"
                    )
            print()

        print("-" * 70)
        print(f"  Summary: {passed}/{total} checkpoints passed")
        print(f"  Total nodes: {self.total_nodes}")
        print(f"  Duration: {self.duration_ms:.0f}ms")
        print("=" * 70)
        print()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class SimulationRunner:
    """Runs a scenario through MemoryEngine with simulated time."""

    USER_ID = "sim-user"

    async def run(
        self,
        scenario: SimScenario,
        config: PRMEConfig | None = None,
        organize_at_checkpoints: bool = True,
    ) -> SimulationReport:
        """Execute a simulation scenario and return the report.

        Args:
            scenario: The scenario to run.
            config: Optional PRMEConfig override.
            organize_at_checkpoints: If True, run engine.organize() before
                each checkpoint evaluation to apply maintenance jobs.

        Returns:
            SimulationReport with all checkpoint results.
        """
        start_time = time.monotonic()

        # Create temp directory for engine storage
        tmp = tempfile.mkdtemp(prefix="prme_sim_")
        lexical_dir = Path(tmp) / "lexical_index"
        lexical_dir.mkdir(parents=True, exist_ok=True)
        if config is None:
            config = PRMEConfig(
                db_path=str(Path(tmp) / "memory.duckdb"),
                vector_path=str(Path(tmp) / "vectors.usearch"),
                lexical_path=str(lexical_dir),
            )

        engine = await MemoryEngine.create(config)

        try:
            # Phase 1: Store all messages
            node_ids = await self._store_messages(engine, scenario.messages)

            # Phase 2: Evaluate checkpoints
            checkpoint_results = []
            for checkpoint in sorted(scenario.checkpoints, key=lambda c: c.day):
                result = await self._evaluate_checkpoint(
                    engine, checkpoint, scenario.messages, node_ids,
                    organize=organize_at_checkpoints,
                )
                checkpoint_results.append(result)

            # Compute stats
            total_nodes = len(node_ids)
            passed = sum(1 for cr in checkpoint_results if cr.passed)
            pass_rate = passed / len(checkpoint_results) if checkpoint_results else 0.0
            duration_ms = (time.monotonic() - start_time) * 1000

            return SimulationReport(
                scenario_name=scenario.name,
                config_summary={
                    "db_path": config.db_path,
                    "scoring_version": config.scoring.version_id,
                },
                checkpoints=checkpoint_results,
                overall_pass_rate=pass_rate,
                total_nodes=total_nodes,
                duration_ms=duration_ms,
            )
        finally:
            await engine.close()

    async def _store_messages(
        self,
        engine: MemoryEngine,
        messages: list[SimMessage],
    ) -> list[str]:
        """Store all scenario messages and return their event IDs."""
        event_ids: list[str] = []
        for msg in messages:
            kwargs: dict = {
                "user_id": self.USER_ID,
                "role": msg.role,
                "node_type": NodeType(msg.node_type),
                "scope": Scope.PERSONAL,
            }
            if msg.epistemic_type is not None:
                kwargs["epistemic_type"] = EpistemicType(msg.epistemic_type)

            eid = await engine.store(msg.content, **kwargs)
            event_ids.append(eid)
        return event_ids

    async def _evaluate_checkpoint(
        self,
        engine: MemoryEngine,
        checkpoint: SimCheckpoint,
        messages: list[SimMessage],
        node_ids: list[str],
        organize: bool = False,
    ) -> CheckpointResult:
        """Evaluate a single checkpoint by adjusting timestamps and retrieving.

        For each checkpoint at day N:
        1. Compute now = datetime.now(UTC)
        2. For each stored node, compute simulated age: age_days = N - message.day
        3. Set node timestamps to now - timedelta(days=age_days)
        4. Call engine.retrieve() -- it uses datetime.now() internally
        """
        now = datetime.now(timezone.utc)

        # Adjust timestamps in DuckDB for time simulation
        conn = engine._conn
        for i, msg in enumerate(messages):
            if i >= len(node_ids):
                break
            age_days = max(checkpoint.day - msg.day, 0)
            simulated_ts = now - timedelta(days=age_days)
            ts_str = simulated_ts.strftime("%Y-%m-%d %H:%M:%S.%f+00")

            # Update both nodes and events tables (including last_reinforced_at
            # so virtual decay computes correct elapsed time)
            conn.execute(
                "UPDATE nodes SET created_at = ?::TIMESTAMPTZ, "
                "updated_at = ?::TIMESTAMPTZ, "
                "valid_from = ?::TIMESTAMPTZ, "
                "last_reinforced_at = ?::TIMESTAMPTZ "
                "WHERE content = ? AND user_id = ?",
                [ts_str, ts_str, ts_str, ts_str, msg.content, self.USER_ID],
            )
            conn.execute(
                "UPDATE events SET created_at = ?::TIMESTAMPTZ, "
                "timestamp = ?::TIMESTAMPTZ "
                "WHERE content = ? AND user_id = ?",
                [ts_str, ts_str, msg.content, self.USER_ID],
            )

        # Run organize to apply maintenance (promotion, archival, etc.)
        if organize:
            await engine.organize()

        # Run retrieval
        response = await engine.retrieve(
            checkpoint.query,
            user_id=self.USER_ID,
        )

        # Extract top results
        top_results = []
        for r in response.results[:10]:
            top_results.append({
                "content": r.node.content,
                "score": r.composite_score,
                "node_type": r.node.node_type.value,
                "lifecycle_state": r.node.lifecycle_state.value,
            })

        # Check keywords against top result content
        top_content = " ".join(
            r["content"].lower() for r in top_results[:5]
        )

        expected_found = [
            kw for kw in checkpoint.expected_keywords
            if kw.lower() in top_content
        ]
        expected_missing = [
            kw for kw in checkpoint.expected_keywords
            if kw.lower() not in top_content
        ]
        excluded_found = [
            kw for kw in checkpoint.excluded_keywords
            if kw.lower() in top_content
        ]

        # Lifecycle assertions: count nodes by lifecycle state
        lifecycle_counts: dict[str, int] = {}
        lifecycle_failures: list[str] = []
        if checkpoint.lifecycle_assertions:
            all_nodes = await engine.query_nodes(
                limit=1000,
                lifecycle_states=[
                    LifecycleState.TENTATIVE,
                    LifecycleState.STABLE,
                    LifecycleState.SUPERSEDED,
                    LifecycleState.ARCHIVED,
                    LifecycleState.DEPRECATED,
                    LifecycleState.CONTESTED,
                ],
            )
            for node in all_nodes:
                state_name = node.lifecycle_state.value
                lifecycle_counts[state_name] = lifecycle_counts.get(state_name, 0) + 1

            for state, min_count in checkpoint.lifecycle_assertions.items():
                actual = lifecycle_counts.get(state, 0)
                if actual < min_count:
                    lifecycle_failures.append(
                        f"Expected >= {min_count} nodes in '{state}', got {actual}"
                    )

        # Pass if all expected found, no excluded found, and no lifecycle failures
        passed = (
            len(expected_missing) == 0
            and len(excluded_found) == 0
            and len(lifecycle_failures) == 0
        )

        return CheckpointResult(
            checkpoint=checkpoint,
            passed=passed,
            top_results=top_results,
            expected_found=expected_found,
            expected_missing=expected_missing,
            excluded_found=excluded_found,
            lifecycle_failures=lifecycle_failures,
            lifecycle_counts=lifecycle_counts,
        )
