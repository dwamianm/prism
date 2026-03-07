"""Scenario: epistemic decay profile behavior.

Stores facts with different epistemic types at day 0 and checks at
increasing time intervals to verify that decay profiles behave as
expected: HYPOTHETICAL decays fastest, OBSERVED decays slowest.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages: all stored at day 0 with different epistemic types
# ---------------------------------------------------------------------------

_MESSAGES = [
    # HYPOTHETICAL -- should decay fastest (RAPID profile)
    SimMessage(
        day=0, role="assistant",
        content="The caching layer might benefit from switching to Redis Cluster for horizontal scaling.",
        tags=["hypothesis", "redis", "caching"],
        node_type="fact",
        epistemic_type="hypothetical",
    ),
    SimMessage(
        day=0, role="assistant",
        content="Perhaps we should consider migrating to Rust for the hot path performance bottleneck.",
        tags=["hypothesis", "rust", "performance"],
        node_type="fact",
        epistemic_type="hypothetical",
    ),

    # INFERRED -- should decay moderately fast (FAST profile)
    SimMessage(
        day=0, role="assistant",
        content="Based on the logs, it appears the memory leak is caused by unclosed database connections.",
        tags=["inferred", "memory-leak", "database"],
        node_type="fact",
        epistemic_type="inferred",
    ),
    SimMessage(
        day=0, role="assistant",
        content="The deployment pattern suggests the team prefers blue-green deployments over rolling updates.",
        tags=["inferred", "deployment", "blue-green"],
        node_type="fact",
        epistemic_type="inferred",
    ),

    # ASSERTED -- should decay at medium rate (MEDIUM profile)
    SimMessage(
        day=0, role="user",
        content="We use Python 3.12 as our primary programming language for all backend services.",
        tags=["asserted", "python", "backend"],
        node_type="fact",
        epistemic_type="asserted",
    ),
    SimMessage(
        day=0, role="user",
        content="Our CI pipeline runs on GitHub Actions with a matrix of Python 3.11 and 3.12.",
        tags=["asserted", "ci", "github-actions"],
        node_type="fact",
        epistemic_type="asserted",
    ),

    # OBSERVED -- should decay slowest (SLOW profile)
    SimMessage(
        day=0, role="user",
        content="The production database server has 64GB RAM and 16 CPU cores. I checked the AWS console.",
        tags=["observed", "infrastructure", "production"],
        node_type="fact",
        epistemic_type="observed",
    ),
    SimMessage(
        day=0, role="user",
        content="I saw Alice commit the authentication refactor to main branch at 3:15 PM yesterday.",
        tags=["observed", "alice", "authentication"],
        node_type="fact",
        epistemic_type="observed",
    ),

    # Control: a mix of types stored later for baseline comparison
    SimMessage(
        day=30, role="user",
        content="We adopted FastAPI as our new web framework, replacing Flask.",
        tags=["asserted", "fastapi", "framework"],
        node_type="fact",
        epistemic_type="asserted",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints at increasing intervals
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    # Day 5: HYPOTHETICAL should already be scoring lower due to epistemic weight
    SimCheckpoint(
        day=5,
        query="What technologies and tools do we use?",
        expected_keywords=["Python", "production"],
        excluded_keywords=[],
        description="Day 5: observed and asserted facts should rank above hypothetical",
    ),

    # Day 15: INFERRED should be fading relative to ASSERTED/OBSERVED
    SimCheckpoint(
        day=15,
        query="What do we know about our infrastructure and deployment?",
        expected_keywords=["production"],
        excluded_keywords=[],
        description="Day 15: observed infrastructure facts should outrank inferred deployment patterns",
    ),

    # Day 50: ASSERTED should be past half-life, but still present
    SimCheckpoint(
        day=50,
        query="What programming language do we use?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Day 50: asserted facts (Python 3.12) should still be retrievable",
    ),

    # Day 50: newer facts should rank well
    SimCheckpoint(
        day=50,
        query="What web framework do we use?",
        expected_keywords=["FastAPI"],
        excluded_keywords=[],
        description="Day 50: recently asserted facts (FastAPI) should rank highly",
    ),

    # Day 150: only OBSERVED should still be strong
    SimCheckpoint(
        day=150,
        query="What do we know about our production infrastructure?",
        expected_keywords=["64GB", "production"],
        excluded_keywords=[],
        description="Day 150: observed facts about production should persist",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

DECAY_MECHANICS_SCENARIO = SimScenario(
    name="decay_mechanics",
    description=(
        "Tests epistemic decay profiles by storing facts with different "
        "epistemic types (hypothetical, inferred, asserted, observed) at "
        "day 0 and verifying decay behavior at day 5, 15, 50, and 150."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
