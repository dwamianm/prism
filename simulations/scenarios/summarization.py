"""Scenario: hierarchical summarization pipeline.

Tests that the summarize organize() job creates daily, weekly, and monthly
summary nodes from accumulated events. Verifies that summaries are retrievable
and contain references to source content.

The scenario stores events across multiple simulated days within a single
month, then runs organize() with the summarize job. Checkpoints verify that
summary nodes appear in retrieval results and that source content is
aggregated correctly.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~25 days across 4 weeks
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Week 1, Day 1-3: Project setup discussions
    SimMessage(
        day=1, role="user",
        content="We decided to use Python 3.12 as the primary language for the backend.",
        tags=["python", "backend"], node_type="decision",
    ),
    SimMessage(
        day=1, role="user",
        content="FastAPI will be our web framework for all HTTP services.",
        tags=["fastapi", "framework"], node_type="decision",
    ),
    SimMessage(
        day=2, role="user",
        content="The team agreed on PostgreSQL for persistent storage.",
        tags=["postgresql", "database"], node_type="decision",
    ),
    SimMessage(
        day=2, role="user",
        content="Redis is being used for caching and session management.",
        tags=["redis", "caching"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="Docker Compose handles local development orchestration.",
        tags=["docker", "devops"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="CI/CD pipeline runs on GitHub Actions with automated tests.",
        tags=["ci-cd", "github-actions"], node_type="fact",
    ),

    # Week 2, Day 8-10: Architecture decisions
    SimMessage(
        day=8, role="user",
        content="We adopted a microservices architecture with gRPC for internal communication.",
        tags=["microservices", "grpc"], node_type="decision",
    ),
    SimMessage(
        day=8, role="user",
        content="Event sourcing pattern is used for the order processing domain.",
        tags=["event-sourcing", "architecture"], node_type="fact",
    ),
    SimMessage(
        day=9, role="user",
        content="API gateway handles authentication, rate limiting, and routing.",
        tags=["api-gateway", "auth"], node_type="fact",
    ),
    SimMessage(
        day=9, role="user",
        content="Prometheus and Grafana are used for monitoring and alerting.",
        tags=["monitoring", "prometheus"], node_type="fact",
    ),
    SimMessage(
        day=10, role="user",
        content="The search service uses Elasticsearch for full-text search.",
        tags=["elasticsearch", "search"], node_type="fact",
    ),
    SimMessage(
        day=10, role="user",
        content="All services log to a centralized ELK stack for debugging.",
        tags=["logging", "elk"], node_type="fact",
    ),

    # Week 3, Day 15-17: Team and process
    SimMessage(
        day=15, role="user",
        content="Sprint planning happens every Monday morning at 10am.",
        tags=["sprint", "planning"], node_type="fact",
    ),
    SimMessage(
        day=15, role="user",
        content="Code reviews require at least two approvals before merging.",
        tags=["code-review", "process"], node_type="fact",
    ),
    SimMessage(
        day=16, role="user",
        content="The team uses Notion for documentation and knowledge management.",
        tags=["notion", "docs"], node_type="fact",
    ),
    SimMessage(
        day=16, role="user",
        content="Slack is the primary communication channel for the engineering team.",
        tags=["slack", "communication"], node_type="fact",
    ),
    SimMessage(
        day=17, role="user",
        content="Performance testing is done with k6 before each release.",
        tags=["k6", "performance"], node_type="fact",
    ),
    SimMessage(
        day=17, role="user",
        content="Feature flags are managed through LaunchDarkly for gradual rollouts.",
        tags=["feature-flags", "launchdarkly"], node_type="fact",
    ),

    # Week 4, Day 22-24: Security and compliance
    SimMessage(
        day=22, role="user",
        content="All API endpoints require JWT authentication tokens.",
        tags=["jwt", "security"], node_type="fact",
    ),
    SimMessage(
        day=22, role="user",
        content="PII data is encrypted at rest using AES-256.",
        tags=["encryption", "pii"], node_type="fact",
    ),
    SimMessage(
        day=23, role="user",
        content="SOC 2 Type II compliance audit is scheduled for Q2.",
        tags=["soc2", "compliance"], node_type="fact",
    ),
    SimMessage(
        day=23, role="user",
        content="Dependabot checks for vulnerable dependencies weekly.",
        tags=["dependabot", "security"], node_type="fact",
    ),
    SimMessage(
        day=24, role="user",
        content="Data retention policy requires deletion of user data after 90 days of inactivity.",
        tags=["retention", "policy"], node_type="fact",
    ),
    SimMessage(
        day=24, role="user",
        content="GDPR compliance module handles data export and deletion requests.",
        tags=["gdpr", "compliance"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=30,
        query="What database does the team use for storage?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="After organize, retrieval should find database technology facts",
    ),
    SimCheckpoint(
        day=30,
        query="What architecture decisions were made about microservices?",
        expected_keywords=["microservices"],
        excluded_keywords=[],
        description="Architecture decisions should be retrievable after summarization",
    ),
    SimCheckpoint(
        day=30,
        query="What monitoring and alerting tools are used?",
        expected_keywords=["Prometheus"],
        excluded_keywords=[],
        description="Monitoring facts should be retrievable",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

SUMMARIZATION_SCENARIO = SimScenario(
    name="summarization",
    description=(
        "Tests the hierarchical summarization pipeline: stores events "
        "across 4 weeks, runs organize() with summarize job, and verifies "
        "that summary nodes are created and source content remains retrievable. "
        "Uses low thresholds to ensure summaries trigger with the available data."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
    config_overrides={
        "organizer": {
            "summarization_daily_min_events": 2,
            "summarization_weekly_min_summaries": 2,
            "summarization_monthly_min_summaries": 2,
            "summarization_max_items_per_summary": 10,
        },
    },
)
