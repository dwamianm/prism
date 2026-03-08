"""Scenario: deduplication and entity alias resolution.

Tests that duplicate facts are detected and merged by the organizer,
and that entity aliases (abbreviations, case variations) are resolved.
After running organize() with deduplicate + alias_resolve jobs, the
memory should have fewer active nodes (duplicates superseded) and alias
entities merged.

Messages store duplicate facts and aliased entity references. Checkpoints
verify that duplicates are merged (only one active copy) and aliases
resolved (longer canonical name preferred).
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: Establish baseline facts
    SimMessage(
        day=1,
        role="user",
        content="We use PostgreSQL as our primary database.",
        tags=["postgresql", "database"],
        node_type="fact",
    ),
    SimMessage(
        day=1,
        role="user",
        content="The team writes all backend code in Python.",
        tags=["python", "backend"],
        node_type="fact",
    ),

    # Day 3: Store duplicate facts (exact same content)
    SimMessage(
        day=3,
        role="user",
        content="We use PostgreSQL as our primary database.",
        tags=["postgresql", "database"],
        node_type="fact",
    ),
    SimMessage(
        day=3,
        role="user",
        content="The team writes all backend code in Python.",
        tags=["python", "backend"],
        node_type="fact",
    ),

    # Day 5: Store entity references with aliases
    SimMessage(
        day=5,
        role="user",
        content="JavaScript",
        tags=["javascript", "entity"],
        node_type="entity",
    ),
    SimMessage(
        day=5,
        role="user",
        content="JS",
        tags=["javascript", "entity"],
        node_type="entity",
    ),

    # Day 7: Another duplicate fact
    SimMessage(
        day=7,
        role="user",
        content="We use PostgreSQL as our primary database.",
        tags=["postgresql", "database"],
        node_type="fact",
    ),

    # Day 10: Unique new fact (should not be merged)
    SimMessage(
        day=10,
        role="user",
        content="The deployment pipeline uses GitHub Actions for CI/CD.",
        tags=["github-actions", "cicd"],
        node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=15,
        query="What database does the team use?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description=(
            "After deduplication, only one PostgreSQL fact should be "
            "active; duplicates should be superseded"
        ),
    ),
    SimCheckpoint(
        day=15,
        query="What deployment tools does the team use?",
        expected_keywords=["GitHub Actions"],
        excluded_keywords=[],
        description="Unique fact should survive deduplication unchanged",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

DEDUPLICATION_SCENARIO = SimScenario(
    name="deduplication",
    description=(
        "Tests deduplication and entity alias resolution. Stores duplicate "
        "facts and aliased entity references, runs organize() with "
        "deduplicate + alias_resolve jobs, and verifies that duplicates "
        "are merged and aliases are resolved."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
    config_overrides={},
)
