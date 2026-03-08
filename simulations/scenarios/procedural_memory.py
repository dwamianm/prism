"""Scenario: procedural memory (INSTRUCTION nodes).

Tests that INSTRUCTION nodes (learned behavioral rules) are stored,
retrieved, and surfaced as system-level instructions separate from
factual content. Verifies that instructions rank appropriately and
appear in the system_instructions bundle section.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~20 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: Establish behavioral instructions
    SimMessage(
        day=1, role="user",
        content="Always respond with concise bullet points instead of long paragraphs.",
        tags=["format", "concise", "instructions"], node_type="instruction",
    ),
    SimMessage(
        day=1, role="user",
        content="Always use Python 3.12+ for all new project code.",
        tags=["python", "version", "instructions"], node_type="instruction",
    ),

    # Day 2: Store some factual content
    SimMessage(
        day=2, role="user",
        content="Our main product is a data analytics platform built with Django.",
        tags=["product", "django"], node_type="fact",
    ),
    SimMessage(
        day=2, role="user",
        content="The team uses PostgreSQL as the primary database.",
        tags=["database", "postgresql"], node_type="fact",
    ),

    # Day 5: More instructions
    SimMessage(
        day=5, role="user",
        content="Always check the CI pipeline status before merging pull requests.",
        tags=["ci", "merge", "instructions"], node_type="instruction",
    ),

    # Day 8: Content that validates an existing instruction
    SimMessage(
        day=8, role="user",
        content="I used Python 3.12 for the new microservice as per our standards.",
        tags=["python", "microservice"], node_type="fact",
    ),

    # Day 12: Another instruction
    SimMessage(
        day=12, role="user",
        content="Never deploy to production on Fridays.",
        tags=["deploy", "friday", "instructions"], node_type="instruction",
    ),

    # Day 15: More factual content
    SimMessage(
        day=15, role="user",
        content="We migrated from MySQL to PostgreSQL last quarter.",
        tags=["database", "migration"], node_type="fact",
    ),

    # Day 18: Reinforce the concise-answers instruction
    SimMessage(
        day=18, role="user",
        content="Remember to keep all responses short and to the point with bullet points.",
        tags=["format", "concise"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=10,
        query="What rules should I follow when writing code?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Python version instruction should appear in code-related queries",
    ),
    SimCheckpoint(
        day=20,
        query="What are the team's behavioral rules and guidelines?",
        expected_keywords=["concise", "Python"],
        excluded_keywords=[],
        description="Instructions should dominate behavioral/guidelines queries",
    ),
    SimCheckpoint(
        day=20,
        query="What database does the team use?",
        expected_keywords=["PostgreSQL"],
        excluded_keywords=[],
        description="Factual queries should still return facts correctly",
    ),
    SimCheckpoint(
        day=20,
        query="What should I remember about deployments?",
        expected_keywords=["Friday"],
        excluded_keywords=[],
        description="Deployment instruction should appear for deployment queries",
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

PROCEDURAL_MEMORY_SCENARIO = SimScenario(
    name="procedural_memory",
    description=(
        "Tests that INSTRUCTION nodes (procedural memory) are stored with "
        "correct defaults, surfaced as system-level context in retrieval, "
        "and ranked appropriately alongside factual content. Verifies that "
        "instructions benefit from reinforcement when related content "
        "validates them."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
