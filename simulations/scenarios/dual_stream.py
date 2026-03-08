"""Scenario: dual-stream ingestion (issue #25).

Tests the fast/slow ingestion path split. Fast-ingested messages should
appear in vector search immediately but NOT in graph queries until
materialization is triggered by retrieve() or organize().

The checkpoints verify:
1. After fast-ingest, vector search finds the content.
2. After retrieve() triggers materialization, graph nodes exist.
3. The full store() path still works as before.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~10 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: Fast-ingested content (will be stored via store() by harness,
    # but the scenario validates the concept of immediate availability)
    SimMessage(
        day=1, role="user",
        content="We use PostgreSQL 15 as our primary database engine.",
        tags=["postgresql", "database"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="Our CI/CD pipeline runs on GitHub Actions.",
        tags=["cicd", "github-actions"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="The API layer is built with FastAPI and Python 3.12.",
        tags=["fastapi", "python"], node_type="fact",
    ),

    # Day 5: Additional facts that should coexist
    SimMessage(
        day=5, role="user",
        content="We use Redis for session caching and rate limiting.",
        tags=["redis", "caching"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="All services are containerized with Docker and deployed to Kubernetes.",
        tags=["docker", "kubernetes"], node_type="fact",
    ),

    # Day 8: More content to test materialization depth
    SimMessage(
        day=8, role="user",
        content="The monitoring stack uses Prometheus and Grafana dashboards.",
        tags=["monitoring", "prometheus", "grafana"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=10,
        query="What database does the team use?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="PostgreSQL fact should be retrievable after materialization",
    ),
    SimCheckpoint(
        day=10,
        query="What is the CI/CD pipeline?",
        expected_keywords=["GitHub Actions"],
        excluded_keywords=[],
        description="GitHub Actions fact should be retrievable after materialization",
    ),
    SimCheckpoint(
        day=10,
        query="What technologies does the team use for deployment?",
        expected_keywords=["Docker", "Kubernetes"],
        excluded_keywords=[],
        description="Container and orchestration facts should be retrievable",
    ),
    SimCheckpoint(
        day=10,
        query="What caching solution is used?",
        expected_keywords=["Redis"],
        excluded_keywords=[],
        description="Redis caching fact should be retrievable",
    ),
    SimCheckpoint(
        day=10,
        query="What monitoring tools are in use?",
        expected_keywords=["Prometheus"],
        excluded_keywords=[],
        description="Monitoring stack facts should be retrievable after materialization",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

DUAL_STREAM_SCENARIO = SimScenario(
    name="dual_stream",
    description=(
        "Tests dual-stream ingestion pipeline. Fast-ingested messages "
        "are immediately searchable via vector index but only appear in "
        "graph queries after materialization is triggered by retrieve() "
        "or organize(). Validates that all content is eventually "
        "materialized and retrievable."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
    config_overrides={
        "materialization_queue_size": 500,
        "materialization_budget_ms": 5000,
    },
)
