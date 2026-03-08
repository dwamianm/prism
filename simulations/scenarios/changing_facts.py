"""Scenario: changing facts over time.

Tests that fact supersedence and temporal decay work correctly when
a user's tech stack, preferences, and team evolve over ~60 days.

NOTE: With PRMEConfig(enable_store_supersedence=True), the store() path
will automatically detect migration/replacement language in messages
(e.g. "migrated from MySQL to PostgreSQL") and mark matching older nodes
as superseded via keyword-based ContentContradictionDetector. This is
disabled by default for backward compatibility. To enable, pass
enable_store_supersedence=True in the config when creating the engine.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~60 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Week 1 (days 1-7): establish baseline
    SimMessage(
        day=1, role="user",
        content="Our backend database is MySQL 8.0 and it handles all our data storage needs.",
        tags=["database", "mysql"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="I use VS Code as my primary editor with the Python extension.",
        tags=["editor", "vscode"], node_type="preference",
    ),
    SimMessage(
        day=2, role="user",
        content="Our API layer is built on REST with Flask as the web framework.",
        tags=["api", "rest", "flask"], node_type="fact",
    ),
    SimMessage(
        day=2, role="user",
        content="The team consists of Alice (frontend lead), Bob (backend), and Charlie (devops).",
        tags=["team", "people"], node_type="fact",
        epistemic_type="observed",
    ),
    SimMessage(
        day=3, role="user",
        content="We deploy our services on AWS EC2 instances managed by Ansible.",
        tags=["infrastructure", "ec2", "aws"], node_type="fact",
    ),
    SimMessage(
        day=4, role="user",
        content="Our project Alpha is the main revenue-generating product.",
        tags=["project", "alpha"], node_type="fact",
        epistemic_type="observed",
    ),
    SimMessage(
        day=5, role="user",
        content="We use pytest for all our Python testing with 85% code coverage.",
        tags=["testing", "pytest"], node_type="fact",
    ),

    # Day 12: database migration
    SimMessage(
        day=12, role="user",
        content="We have completed the migration from MySQL to PostgreSQL for better JSON support and performance.",
        tags=["database", "postgresql", "migration"], node_type="fact",
    ),
    SimMessage(
        day=12, role="user",
        content="MySQL is no longer used in our stack. All data has been migrated to PostgreSQL.",
        tags=["database", "mysql", "deprecated"], node_type="fact",
    ),

    # Day 14: editor switch
    SimMessage(
        day=14, role="user",
        content="I switched from VS Code to Neovim for all my development work. The modal editing is much faster.",
        tags=["editor", "neovim"], node_type="preference",
    ),

    # Day 20: team update
    SimMessage(
        day=20, role="user",
        content="Diana joined our team as a data engineer. She specializes in ETL pipelines.",
        tags=["team", "people", "diana"], node_type="fact",
        epistemic_type="observed",
    ),

    # Day 32: API migration
    SimMessage(
        day=32, role="user",
        content="We migrated our API from REST to GraphQL using Strawberry. The developer experience is much better.",
        tags=["api", "graphql", "strawberry"], node_type="fact",
    ),
    SimMessage(
        day=32, role="user",
        content="Our REST API is deprecated and will be removed next quarter.",
        tags=["api", "rest", "deprecated"], node_type="fact",
    ),

    # Day 35: infrastructure migration
    SimMessage(
        day=35, role="user",
        content="We moved our infrastructure from EC2 to Kubernetes on EKS for better scaling and deployment.",
        tags=["infrastructure", "kubernetes", "eks"], node_type="fact",
    ),

    # Day 45: new project
    SimMessage(
        day=45, role="user",
        content="We started project Beta, a new analytics platform built on the PostgreSQL data warehouse.",
        tags=["project", "beta", "analytics"], node_type="fact",
    ),

    # Day 55: editor switch back
    SimMessage(
        day=55, role="user",
        content="I went back to VS Code from Neovim. The integrated debugging and extensions are too valuable to give up.",
        tags=["editor", "vscode"], node_type="preference",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=15,
        query="What database does the project use?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="After MySQL->PostgreSQL migration, PostgreSQL should dominate",
        ranking_assertions=[("PostgreSQL", "MySQL")],
    ),
    SimCheckpoint(
        day=15,
        query="What editor do I use for development?",
        expected_keywords=["Neovim"],
        excluded_keywords=[],
        description="After editor switch, Neovim should appear in results",
    ),
    SimCheckpoint(
        day=25,
        query="Who is on the team?",
        expected_keywords=["Alice", "Bob", "Charlie"],
        excluded_keywords=[],
        description="Long-lived observed facts (team members) should persist",
        lifecycle_assertions={"stable": 1},  # At least 1 node promoted after 7+ days
    ),
    SimCheckpoint(
        day=40,
        query="What API technology do we use?",
        expected_keywords=["GraphQL"],
        excluded_keywords=[],
        description="After API migration, GraphQL should dominate retrieval",
        ranking_assertions=[("GraphQL", "REST")],
    ),
    SimCheckpoint(
        day=40,
        query="How do we deploy our services?",
        expected_keywords=["Kubernetes"],
        excluded_keywords=[],
        description="After infra migration, Kubernetes should dominate",
    ),
    SimCheckpoint(
        day=60,
        query="What editor do I use?",
        expected_keywords=["VS Code"],
        excluded_keywords=[],
        description="After switching back, VS Code should be the current editor",
        ranking_assertions=[("VS Code", "Neovim")],
    ),
    SimCheckpoint(
        day=90,
        query="Who are the team members?",
        expected_keywords=["Alice", "Bob"],
        excluded_keywords=[],
        description="At 90 days, stable observed facts should still be retrievable",
        lifecycle_assertions={"stable": 1},  # Stable nodes should persist at 90 days
    ),
    SimCheckpoint(
        day=90,
        query="What database does the project use currently?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="At 90 days, stable facts (PostgreSQL) should persist",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

CHANGING_FACTS_SCENARIO = SimScenario(
    name="changing_facts",
    description=(
        "Tests fact supersedence and temporal decay over ~60 days. "
        "Verifies that migrated technologies supersede old ones, "
        "long-lived observed facts persist, and re-adopted preferences "
        "correctly reflect the latest state."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
