"""Scenario: information accumulation at scale.

Generates a large number of messages across multiple topics over many
days, testing how retrieval performs as the memory store grows.
"""

from __future__ import annotations

import random

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Topic templates
# ---------------------------------------------------------------------------

_TOPICS: dict[str, list[str]] = {
    "work_projects": [
        "Working on project Alpha today, focusing on the authentication module.",
        "Project Alpha's deadline is next Friday, need to finish the user dashboard.",
        "Had a review meeting for project Alpha, stakeholders want more analytics.",
        "Starting project Beta, a new internal tool for data visualization.",
        "Project Beta will use React and D3.js for the frontend charting.",
        "Project Beta requires a real-time data pipeline from our PostgreSQL warehouse.",
        "Project Gamma kicked off as a mobile companion app for Alpha.",
        "Project Gamma will use React Native for cross-platform development.",
    ],
    "tools": [
        "Started using Docker Compose for local development environments.",
        "Switched to GitHub Actions for CI/CD from our old Jenkins setup.",
        "Adopted Terraform for infrastructure-as-code management.",
        "Trying out Copilot for code completion, seems promising for boilerplate.",
        "Set up Sentry for error tracking across all our microservices.",
        "Migrated our monitoring from Datadog to Grafana with Prometheus.",
        "Started using Linear for project management instead of Jira.",
        "Adopted pre-commit hooks with ruff and mypy for code quality.",
    ],
    "people": [
        "Alice is leading the frontend redesign effort this quarter.",
        "Bob completed the database migration and is now on project Beta.",
        "Charlie set up the new Kubernetes cluster for production.",
        "Diana joined as a data engineer specializing in Apache Spark.",
        "Eve from security did an audit of our authentication flow.",
        "Frank is the new product manager for project Alpha.",
        "Grace is our UX designer working on the mobile app mockups.",
        "Henry is mentoring the junior developers on backend patterns.",
    ],
    "preferences": [
        "I prefer writing Python code with strict type hints everywhere.",
        "Dark mode is essential for all my development tools.",
        "I like to do code reviews first thing in the morning.",
        "Prefer async/await patterns over threading for I/O-bound work.",
        "I always use virtual environments for Python projects.",
        "Prefer PostgreSQL over MySQL for relational database work.",
        "I like pair programming for complex architectural decisions.",
        "I prefer trunk-based development over long-lived feature branches.",
    ],
    "decisions": [
        "We decided to use microservices architecture for the new platform.",
        "Team agreed on a two-week sprint cycle with Friday demos.",
        "Decision: all new APIs will use GraphQL instead of REST.",
        "We chose to adopt a monorepo structure for better code sharing.",
        "Decided to implement feature flags using LaunchDarkly.",
        "Team voted to use Conventional Commits for all repositories.",
        "We agreed to target 90% code coverage for critical paths.",
        "Decision: use event sourcing for the order management domain.",
    ],
    "goals": [
        "Goal: reduce API response time to under 200ms by end of quarter.",
        "Aiming to get project Alpha to 1000 daily active users.",
        "Want to implement automated canary deployments by month end.",
        "Goal: establish a shared component library for all frontend projects.",
        "Planning to set up disaster recovery with multi-region failover.",
        "Target: reduce on-call incidents by 50% through better monitoring.",
        "Goal: complete SOC 2 compliance audit by Q3.",
        "Aiming to hire two more backend engineers this quarter.",
    ],
    "meetings": [
        "Sprint planning went well, team committed to 8 story points.",
        "Retrospective highlighted need for better documentation practices.",
        "Architecture review for the new payment integration module.",
        "One-on-one with Alice about her career growth and tech lead path.",
        "All-hands meeting covered Q2 roadmap and company direction.",
        "Design review for the new dashboard layout with stakeholders.",
        "Incident post-mortem: database connection pool exhaustion last Tuesday.",
        "Knowledge sharing session on Kubernetes best practices.",
    ],
    "learnings": [
        "Learned about structured concurrency patterns in Python with anyio.",
        "Discovered that PostgreSQL JSONB indexes can speed up queries 10x.",
        "TIL: DuckDB can query Parquet files directly without loading them.",
        "Learned about the circuit breaker pattern for resilient microservices.",
        "Discovered that HNSW indexes give much better recall than IVF for our use case.",
        "Realized we need connection pooling with pgBouncer for our PostgreSQL setup.",
        "Learned about semantic versioning best practices for internal libraries.",
        "TIL: Python 3.12 has significant performance improvements for comprehensions.",
    ],
}

_NODE_TYPE_MAP = {
    "work_projects": "fact",
    "tools": "fact",
    "people": "fact",
    "preferences": "preference",
    "decisions": "decision",
    "goals": "task",
    "meetings": "event",
    "learnings": "fact",
}


def generate_accumulation_scenario(
    num_messages: int = 200,
    num_days: int = 180,
    seed: int = 42,
) -> SimScenario:
    """Generate a large-scale accumulation scenario.

    Args:
        num_messages: Total messages to generate.
        num_days: Span of simulated days.
        seed: Random seed for reproducibility.

    Returns:
        SimScenario with generated messages and checkpoints.
    """
    rng = random.Random(seed)
    topics = list(_TOPICS.keys())

    # Weight early topics heavier (simulate topic drift)
    messages: list[SimMessage] = []

    for i in range(num_messages):
        # Progress through the timeline
        day = int((i / num_messages) * num_days) + 1

        # Topic selection with drift: early = work_projects/tools,
        # later = goals/learnings
        progress = i / num_messages
        if progress < 0.3:
            weights = [3, 2, 2, 1, 1, 0.5, 1, 0.5]
        elif progress < 0.6:
            weights = [1, 1, 1, 2, 2, 2, 1, 1]
        else:
            weights = [0.5, 1, 0.5, 1, 1, 2, 1, 3]

        topic = rng.choices(topics, weights=weights, k=1)[0]
        template = rng.choice(_TOPICS[topic])

        messages.append(SimMessage(
            day=day,
            role=rng.choice(["user", "assistant"]),
            content=f"[Day {day}] {template}",
            tags=[topic],
            node_type=_NODE_TYPE_MAP[topic],
        ))

    # Checkpoints
    checkpoints = [
        SimCheckpoint(
            day=30,
            query="What projects are we working on?",
            expected_keywords=["Alpha"],
            excluded_keywords=[],
            description="Early projects should be retrievable at day 30",
        ),
        SimCheckpoint(
            day=90,
            query="What tools and technologies do we use?",
            expected_keywords=["microservices"],
            excluded_keywords=[],
            description="Technology decisions should be retrievable mid-timeline",
        ),
        SimCheckpoint(
            day=120,
            query="Who works on the team and what do they do?",
            expected_keywords=["sprint"],
            excluded_keywords=[],
            description="Team activity should persist as core knowledge",
        ),
        SimCheckpoint(
            day=180,
            query="What have we learned about software engineering?",
            expected_keywords=["learned"],
            excluded_keywords=[],
            description="Recent learnings should dominate at end of timeline",
        ),
        SimCheckpoint(
            day=180,
            query="What are our targets and plans for the team this quarter?",
            expected_keywords=["plan"],
            excluded_keywords=[],
            description="Goals and plans should be retrievable across the timeline",
        ),
    ]

    return SimScenario(
        name="information_accumulation",
        description=(
            f"Tests retrieval accuracy across {num_messages} messages over "
            f"{num_days} days with topic drift across 8 topics."
        ),
        messages=messages,
        checkpoints=checkpoints,
        config_overrides={
            # Extend task TTL so goals (stored as node_type=task) survive the
            # full 180-day timeline without being archived by TTL enforcement.
            "organizer": {
                "default_ttl_days": {
                    "entity": None,
                    "fact": None,
                    "event": 365,
                    "decision": 365,
                    "preference": None,
                    "task": 365,
                    "summary": 365,
                    "note": 90,
                },
            },
        },
    )
