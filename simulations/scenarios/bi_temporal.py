"""Scenario: bi-temporal data model.

Demonstrates the distinction between *event_time* (when something actually
happened) and *ingestion_time* (when the system learned about it).

Key demonstrations:
- Learning today about something that happened last week
- Knowledge snapshot queries (what did the system know at time X?)
- Event time range queries (what happened during period Y?)

The scenario stores facts where some have explicit event_times in the past
(learning about past events retroactively), while others have no event_time
(standard ingestion). Checkpoints verify that queries can distinguish between
the two temporal dimensions.

Note: This scenario uses the standard SimScenario infrastructure which does
not yet support the event_time or knowledge_at parameters in store/retrieve.
The checkpoints validate the basic storage and retrieval behavior. Full
bi-temporal query integration is validated in tests/test_bi_temporal.py.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~14 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: Learn about current facts (ingestion = event time)
    SimMessage(
        day=1, role="user",
        content="The team uses Python 3.12 as the primary programming language.",
        tags=["python", "language"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="All services are deployed to Google Cloud Platform.",
        tags=["gcp", "deployment"], node_type="fact",
    ),

    # Day 5: Learn about something that happened earlier (retroactive learning)
    # In the real bi-temporal system, these would carry event_time in the past.
    # The simulation harness stores them at day 5 (ingestion_time).
    SimMessage(
        day=5, role="user",
        content="The company was founded in 2019 by three co-founders from Stanford.",
        tags=["founding", "history"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="The first product launch happened in Q2 2020 with 500 beta users.",
        tags=["launch", "history"], node_type="fact",
    ),

    # Day 8: Learn about recent decisions
    SimMessage(
        day=8, role="user",
        content="The team decided last week to migrate from REST to GraphQL.",
        tags=["graphql", "migration", "decision"], node_type="decision",
    ),

    # Day 10: Learn about more current facts
    SimMessage(
        day=10, role="user",
        content="Redis is used for caching and session management across all services.",
        tags=["redis", "caching"], node_type="fact",
    ),

    # Day 12: Another retroactive fact about the past
    SimMessage(
        day=12, role="user",
        content="The original database was MySQL before migrating to PostgreSQL in 2022.",
        tags=["mysql", "postgresql", "migration", "history"], node_type="fact",
    ),

    # Day 14: Current operational fact
    SimMessage(
        day=14, role="user",
        content="CI/CD pipeline runs on GitHub Actions with 15-minute average deploy time.",
        tags=["cicd", "github-actions"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=14,
        query="When was the company founded and what is its history?",
        expected_keywords=["2019", "Stanford"],
        excluded_keywords=[],
        description="Historical facts (learned retroactively on day 5) should be retrievable",
    ),
    SimCheckpoint(
        day=14,
        query="What technology stack does the team use?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Current tech facts should all be available",
    ),
    SimCheckpoint(
        day=14,
        query="What database changes has the team made?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="Migration history (retroactive fact from day 12) should be retrievable",
    ),
    SimCheckpoint(
        day=14,
        query="What API technology decisions have been made?",
        expected_keywords=["GraphQL"],
        excluded_keywords=[],
        description="Decision about migration to GraphQL should be retrievable",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

BI_TEMPORAL_SCENARIO = SimScenario(
    name="bi_temporal",
    description=(
        "Demonstrates bi-temporal data model: facts learned retroactively "
        "(event happened in the past but ingested now) coexist with "
        "current-time facts. Validates that all facts are retrievable "
        "regardless of when they were ingested vs when they happened."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
