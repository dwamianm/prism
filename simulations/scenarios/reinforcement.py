"""Scenario: reinforcement through repeated mentions.

Tests that repeatedly-mentioned topics rank higher in retrieval than
topics mentioned only once. Since the simulation harness only supports
store() messages (not direct reinforce() calls), this scenario stores
the same topics on multiple days to simulate reinforcement through
repeated evidence.

The checkpoints verify that heavily-repeated topics rank above
single-mention topics in retrieval results.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~30 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: Establish baseline facts
    SimMessage(
        day=1, role="user",
        content="We use Python as our primary programming language for all backend services.",
        tags=["python", "language"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="Our CI pipeline runs on GitHub Actions with automated testing.",
        tags=["ci", "github-actions"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="We use Redis for caching hot data in production.",
        tags=["redis", "caching"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="Our monitoring stack is based on Datadog for APM and logging.",
        tags=["monitoring", "datadog"], node_type="fact",
    ),

    # Day 5: Re-mention Python (reinforcement)
    SimMessage(
        day=5, role="user",
        content="Python is working great for our microservices architecture.",
        tags=["python", "microservices"], node_type="fact",
    ),

    # Day 8: Re-mention Python again
    SimMessage(
        day=8, role="user",
        content="We upgraded to Python 3.12 across all services for performance gains.",
        tags=["python", "upgrade"], node_type="fact",
    ),

    # Day 10: Re-mention Redis
    SimMessage(
        day=10, role="user",
        content="Redis cluster mode is now enabled for better scalability.",
        tags=["redis", "scaling"], node_type="fact",
    ),

    # Day 14: Re-mention Python yet again
    SimMessage(
        day=14, role="user",
        content="Python type hints have improved our code quality significantly.",
        tags=["python", "typing"], node_type="fact",
    ),

    # Day 18: Re-mention Python once more
    SimMessage(
        day=18, role="user",
        content="Our Python team adopted Ruff as the standard linter.",
        tags=["python", "linting", "ruff"], node_type="fact",
    ),

    # Day 22: Mention a new topic only once (control)
    SimMessage(
        day=22, role="user",
        content="We had a brief outage on our Kafka message broker last week.",
        tags=["kafka", "outage"], node_type="fact",
    ),

    # Day 25: Re-mention Python again
    SimMessage(
        day=25, role="user",
        content="Python asyncio is the foundation of our event-driven services.",
        tags=["python", "asyncio"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=15,
        query="What programming language do we use?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Python (mentioned 4 times) should rank strongly in results",
    ),
    SimCheckpoint(
        day=30,
        query="What technologies do we use for our backend?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Heavily-mentioned Python should rank above single-mention topics",
        ranking_assertions=[("Python", "Kafka")],
    ),
    SimCheckpoint(
        day=30,
        query="What caching technology do we use?",
        expected_keywords=["Redis"],
        excluded_keywords=[],
        description="Redis (mentioned 2 times) should appear in caching results",
    ),
    SimCheckpoint(
        day=30,
        query="What tools and technologies does the team use?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Python should dominate multi-topic query due to repeated mentions",
        ranking_assertions=[("Python", "Datadog")],
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

REINFORCEMENT_SCENARIO = SimScenario(
    name="reinforcement",
    description=(
        "Tests that repeatedly-mentioned topics rank higher in retrieval "
        "than single-mention topics. Simulates reinforcement through "
        "repeated store() calls for the same subject over ~30 days."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
