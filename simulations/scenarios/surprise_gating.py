"""Scenario: surprise-gated storage.

Tests that novel content receives boosted salience while redundant
(near-duplicate) content receives reduced salience. With surprise-gating
enabled, the first mention of a topic is novel and gets salience_base=0.65.
Subsequent near-duplicates are redundant and get salience_base=0.40.
Genuinely new topics introduced later also get boosted salience.

The checkpoints verify that original (novel) mentions rank above redundant
re-statements, and that new topics introduced later rank competitively.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~20 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: Establish diverse baseline topics (all novel — empty store)
    SimMessage(
        day=1, role="user",
        content="We use PostgreSQL as our primary relational database for all services.",
        tags=["postgresql", "database"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="The team follows Scrum methodology with two-week sprints.",
        tags=["scrum", "agile"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="All services are deployed to AWS us-east-1 region.",
        tags=["aws", "deployment"], node_type="fact",
    ),

    # Day 5: Store near-duplicates of existing topics (redundant)
    SimMessage(
        day=5, role="user",
        content="PostgreSQL is our main database system for production workloads.",
        tags=["postgresql", "database"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="The team uses Scrum framework for all development projects.",
        tags=["scrum", "agile"], node_type="fact",
    ),

    # Day 10: Genuinely new topics (novel — no similar content exists)
    SimMessage(
        day=10, role="user",
        content="We are evaluating GraphQL as a replacement for our REST API layer.",
        tags=["graphql", "api"], node_type="fact",
    ),
    SimMessage(
        day=10, role="user",
        content="The security team has implemented zero-trust networking across all clusters.",
        tags=["security", "zero-trust"], node_type="fact",
    ),

    # Day 15: Another redundant re-statement of PostgreSQL
    SimMessage(
        day=15, role="user",
        content="PostgreSQL database handles all of our application data storage needs.",
        tags=["postgresql", "database"], node_type="fact",
    ),

    # Day 18: Another new topic (novel)
    SimMessage(
        day=18, role="user",
        content="We adopted OpenTelemetry for distributed tracing across microservices.",
        tags=["opentelemetry", "observability"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=20,
        query="What database does the team use?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="Original PostgreSQL fact (novel, high salience) should rank above redundant re-statements",
        ranking_assertions=[
            # Original fact (salience 0.65) should outrank redundant copies (salience 0.40)
            ("primary relational database", "main database system"),
        ],
    ),
    SimCheckpoint(
        day=20,
        query="What API technology is being evaluated as a replacement?",
        expected_keywords=["GraphQL"],
        excluded_keywords=[],
        description="Novel GraphQL fact (high salience) should appear prominently",
    ),
    SimCheckpoint(
        day=20,
        query="What technologies and tools does the team use?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="Novel topics (GraphQL, zero-trust, OpenTelemetry) should rank competitively with original facts",
        ranking_assertions=[
            # Novel security fact should rank above redundant database re-statements
            ("zero-trust", "main database system"),
        ],
    ),
    SimCheckpoint(
        day=20,
        query="What observability tools are in use?",
        expected_keywords=["OpenTelemetry"],
        excluded_keywords=[],
        description="Late-arriving novel topic (OpenTelemetry) should appear in results with high salience",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

SURPRISE_GATING_SCENARIO = SimScenario(
    name="surprise_gating",
    description=(
        "Tests surprise-gated storage: novel content gets boosted salience "
        "(0.65) while redundant near-duplicates get penalized (0.40). "
        "Verifies that original facts rank above redundant re-statements "
        "and that new topics introduced later rank competitively."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
    config_overrides={
        "enable_surprise_gating": True,
        "novelty_high_threshold": 0.7,
        "novelty_low_threshold": 0.3,
        "novelty_salience_boost": 0.15,
        "novelty_salience_penalty": 0.10,
    },
)
