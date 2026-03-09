"""Scenario: TTL-based archival (issue #12, RFC-0007 S9).

Tests that nodes with TTL settings are properly archived when their
retention period expires, while nodes without TTL or with longer TTL
are preserved.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

_MESSAGES = [
    SimMessage(
        day=1, role="user",
        content="Python is our primary programming language for all backend services.",
        tags=["python", "backend"], node_type="entity",
    ),
    SimMessage(
        day=1, role="user",
        content="We need to finish the API migration by end of quarter.",
        tags=["api", "migration"], node_type="task",
    ),
    SimMessage(
        day=1, role="user",
        content="The database schema was updated to version 3.2 today.",
        tags=["database", "schema"], node_type="event",
    ),
    SimMessage(
        day=1, role="user",
        content="All production services must use TLS 1.3 or higher.",
        tags=["security", "tls"], node_type="fact",
    ),
    SimMessage(
        day=2, role="user",
        content="Review the pull request for the authentication module.",
        tags=["review", "auth"], node_type="note",
    ),
    SimMessage(
        day=3, role="user",
        content="We decided to adopt microservices architecture for the new platform.",
        tags=["architecture", "microservices"], node_type="decision",
    ),
]

_CHECKPOINTS = [
    SimCheckpoint(
        day=100,
        query="What programming language do we use?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Entity nodes (no TTL) remain retrievable after 100 days",
    ),
    SimCheckpoint(
        day=100,
        query="What must all production services use for TLS?",
        expected_keywords=["TLS"],
        excluded_keywords=[],
        description="Fact nodes (no TTL) remain retrievable after 100 days",
    ),
    SimCheckpoint(
        day=100,
        query="What happened with the database?",
        expected_keywords=["schema", "database"],
        excluded_keywords=[],
        description="Event nodes (365d TTL) survive at 100 days",
    ),
    SimCheckpoint(
        day=200,
        query="What architecture decisions were made?",
        expected_keywords=[],
        excluded_keywords=["microservices"],
        description="Decision nodes (180d TTL) should be archived by day 200",
    ),
]

TTL_ARCHIVAL_SCENARIO = SimScenario(
    name="ttl_archival",
    description=(
        "Tests TTL-based archival: nodes with TTL settings are properly "
        "archived when their retention period expires. Entity and Fact nodes "
        "(no TTL) survive indefinitely. Task and Note nodes (90d) expire first, "
        "followed by Decision nodes (180d) and Event nodes (365d)."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
    config_overrides={},
)
