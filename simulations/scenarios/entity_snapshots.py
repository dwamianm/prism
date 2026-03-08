"""Scenario: entity snapshot generation.

Tests that entity snapshots correctly bundle related facts, preferences,
and other memory objects. Stores entities with related facts and preferences,
then verifies retrieval surfaces the entity correctly.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

_MESSAGES = [
    SimMessage(
        day=1, role="user",
        content="Alice is the lead developer on the backend team.",
        tags=["alice", "entity"], node_type="entity",
    ),
    SimMessage(
        day=1, role="user",
        content="Bob manages the frontend team and oversees UI/UX.",
        tags=["bob", "entity"], node_type="entity",
    ),
    SimMessage(
        day=2, role="user",
        content="Alice prefers using Python for all backend services.",
        tags=["alice", "python"], node_type="fact",
    ),
    SimMessage(
        day=2, role="user",
        content="Alice decided to migrate the API to FastAPI last week.",
        tags=["alice", "fastapi"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="Bob prefers React over Vue for frontend development.",
        tags=["bob", "react"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="Bob is responsible for the design system components.",
        tags=["bob", "design"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="Alice and Bob collaborate on the API integration layer.",
        tags=["alice", "bob", "api"], node_type="fact",
    ),
    SimMessage(
        day=7, role="user",
        content="Alice needs to review the database schema changes by Friday.",
        tags=["alice", "task"], node_type="fact",
    ),
]

_CHECKPOINTS = [
    SimCheckpoint(
        day=10,
        query="What do we know about Alice?",
        expected_keywords=["Alice"],
        excluded_keywords=[],
        description="Alice entity and related facts should appear in results",
    ),
    SimCheckpoint(
        day=10,
        query="What does Bob work on?",
        expected_keywords=["Bob"],
        excluded_keywords=[],
        description="Bob entity and related facts should appear in results",
    ),
    SimCheckpoint(
        day=10,
        query="Who is involved in API work?",
        expected_keywords=["Alice"],
        excluded_keywords=[],
        description="Entity snapshots should surface collaboration facts",
    ),
    SimCheckpoint(
        day=10,
        query="What technology preferences exist on the team?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="Technology preferences associated with entities should be retrievable",
    ),
]

ENTITY_SNAPSHOTS_SCENARIO = SimScenario(
    name="entity_snapshots",
    description=(
        "Tests entity snapshot generation: stores entities with related facts "
        "and preferences, then verifies that retrieval surfaces entities with "
        "their associated knowledge."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
