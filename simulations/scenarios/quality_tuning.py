"""Scenario: memory quality self-assessment and auto-tuning.

Stores facts, retrieves them, simulates feedback signals (USED, IGNORED,
CORRECTED), runs the feedback_apply organizer job, and verifies that
scoring weights are adjusted accordingly. After tuning, retrieval quality
should reflect the feedback patterns.

Because the simulation harness operates on messages and checkpoints
(not direct feedback injection), this scenario uses a custom post-store
hook approach: the checkpoints verify that the quality subsystem is wired
correctly by asserting on the engine's quality metrics and weight state
after feedback is applied programmatically in the scenario runner.

However, since the SimScenario model does not support arbitrary code
execution, this module exports both:
    1. QUALITY_TUNING_SCENARIO - a SimScenario for harness compatibility
    2. run_quality_tuning_scenario() - a standalone async function that
       exercises the full feedback/tuning loop
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from prme.config import PRMEConfig
from prme.quality.feedback import FeedbackSignal, FeedbackSignalType
from prme.storage.engine import MemoryEngine
from prme.types import NodeType, Scope
from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

_MESSAGES = [
    SimMessage(
        day=1, role="user",
        content="The team uses Python 3.11 as the primary development language.",
        tags=["python", "language"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="All production services are containerized with Docker.",
        tags=["docker", "containers"], node_type="fact",
    ),
    SimMessage(
        day=2, role="user",
        content="We use Redis for caching and session management.",
        tags=["redis", "caching"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="The CI/CD pipeline is built on GitHub Actions.",
        tags=["cicd", "github-actions"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="Our monitoring stack includes Prometheus and Grafana.",
        tags=["monitoring", "prometheus"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints (basic retrieval validation before tuning)
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=7,
        query="What programming language does the team use?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Python fact should be retrievable",
    ),
    SimCheckpoint(
        day=7,
        query="How are services deployed?",
        expected_keywords=["Docker"],
        excluded_keywords=[],
        description="Docker fact should be retrievable",
    ),
]

# ---------------------------------------------------------------------------
# SimScenario for harness compatibility
# ---------------------------------------------------------------------------

QUALITY_TUNING_SCENARIO = SimScenario(
    name="quality_tuning",
    description=(
        "Tests memory quality self-assessment and auto-tuning. "
        "Stores facts, simulates feedback signals, runs feedback_apply, "
        "and verifies that scoring weights adjust in response to "
        "USED, IGNORED, and CORRECTED signals."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
    config_overrides={},
)


# ---------------------------------------------------------------------------
# Standalone scenario runner (exercises full feedback loop)
# ---------------------------------------------------------------------------


async def run_quality_tuning_scenario() -> dict:
    """Run the full quality tuning scenario with feedback injection.

    Returns a dict summarising results:
        - store_count: number of facts stored
        - feedback_count: number of feedback signals injected
        - weights_changed: whether weights changed after feedback_apply
        - old_weight_version: version before tuning
        - new_weight_version: version after tuning
        - quality_before: quality score before feedback
        - quality_after: quality score after feedback but before apply
        - final_weights: dict of final additive weight values
        - passed: True if all assertions hold
    """
    tmp = tempfile.mkdtemp(prefix="prme_quality_sim_")
    lexical_dir = Path(tmp) / "lexical_index"
    lexical_dir.mkdir(parents=True, exist_ok=True)

    config = PRMEConfig(
        db_path=str(Path(tmp) / "memory.duckdb"),
        vector_path=str(Path(tmp) / "vectors.usearch"),
        lexical_path=str(lexical_dir),
    )

    engine = await MemoryEngine.create(config)
    results: dict = {"passed": True, "errors": []}

    try:
        # Phase 1: Store facts
        for msg in _MESSAGES:
            await engine.store(
                msg.content,
                user_id="sim-user",
                role=msg.role,
                node_type=NodeType(msg.node_type),
                scope=Scope.PERSONAL,
            )
        results["store_count"] = len(_MESSAGES)

        # Phase 2: Check quality before feedback
        quality_before = engine.quality_metrics
        results["quality_before"] = quality_before.retrieval_quality
        assert quality_before.retrieval_quality == 1.0, (
            "Quality should be 1.0 with no feedback signals"
        )

        # Phase 3: Inject feedback signals
        old_version = engine._config.scoring.version_id
        results["old_weight_version"] = old_version

        # Simulate: 3 USED, 2 IGNORED, 3 CORRECTED
        used_signals = [
            FeedbackSignal(
                query="What language?",
                surfaced_node_ids=["node-1"],
                signal_type=FeedbackSignalType.USED,
            )
            for _ in range(3)
        ]
        ignored_signals = [
            FeedbackSignal(
                query="Deployment info?",
                surfaced_node_ids=["node-2"],
                signal_type=FeedbackSignalType.IGNORED,
            )
            for _ in range(2)
        ]
        corrected_signals = [
            FeedbackSignal(
                query="What cache do we use?",
                surfaced_node_ids=["node-3"],
                signal_type=FeedbackSignalType.CORRECTED,
                correction_content="We actually use Memcached, not Redis",
            )
            for _ in range(3)
        ]

        all_signals = used_signals + ignored_signals + corrected_signals
        for sig in all_signals:
            await engine.feedback(sig)
        results["feedback_count"] = len(all_signals)

        # Phase 4: Check quality after feedback (before apply)
        quality_after = engine.quality_metrics
        results["quality_after"] = quality_after.retrieval_quality
        # Quality should be < 1.0 now (we have corrections and ignores)
        assert quality_after.retrieval_quality < 1.0, (
            "Quality should decrease with corrections"
        )

        # Phase 5: Run feedback_apply
        organize_result = await engine.organize(
            user_id="sim-user",
            jobs=["feedback_apply"],
            budget_ms=5000,
        )

        assert "feedback_apply" in organize_result.jobs_run
        job_result = organize_result.per_job["feedback_apply"]
        assert job_result.details["signals_processed"] == len(all_signals)

        new_version = engine._config.scoring.version_id
        results["new_weight_version"] = new_version
        results["weights_changed"] = old_version != new_version

        # Weights should have changed
        assert old_version != new_version, "Weights should change after feedback"

        # Phase 6: Verify weight adjustments make sense
        new_weights = engine._config.scoring
        results["final_weights"] = {
            "w_semantic": new_weights.w_semantic,
            "w_lexical": new_weights.w_lexical,
            "w_graph": new_weights.w_graph,
            "w_recency": new_weights.w_recency,
            "w_salience": new_weights.w_salience,
            "w_confidence": new_weights.w_confidence,
            "w_epistemic": new_weights.w_epistemic,
        }

        # Additive weights must still sum to 1.0
        additive_sum = (
            new_weights.w_semantic + new_weights.w_lexical + new_weights.w_graph
            + new_weights.w_recency + new_weights.w_salience + new_weights.w_confidence
        )
        assert abs(additive_sum - 1.0) < 1e-6, (
            f"Additive weights must sum to 1.0, got {additive_sum}"
        )

        # With 3 CORRECTED signals, w_confidence should have increased
        # relative to w_semantic (correction rule: -lr to w_semantic,
        # +lr to w_confidence)
        default_weights = PRMEConfig().scoring
        old_ratio = default_weights.w_confidence / (
            default_weights.w_semantic + default_weights.w_confidence
        )
        new_ratio = new_weights.w_confidence / (
            new_weights.w_semantic + new_weights.w_confidence
        )
        assert new_ratio > old_ratio, (
            f"Confidence ratio should increase after corrections: "
            f"{old_ratio:.4f} -> {new_ratio:.4f}"
        )

        # Phase 7: Verify retrieval still works after weight change
        response = await engine.retrieve(
            "What programming language does the team use?",
            user_id="sim-user",
        )
        assert len(response.results) > 0, "Retrieval should return results"

        # Tracker should be cleared
        assert len(engine._feedback_tracker) == 0

    except AssertionError as e:
        results["passed"] = False
        results["errors"].append(str(e))
    except Exception as e:
        results["passed"] = False
        results["errors"].append(f"Unexpected error: {e}")
    finally:
        await engine.close()

    return results
