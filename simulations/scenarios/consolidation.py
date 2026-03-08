"""Scenario: predictive forgetting / consolidation pipeline.

Tests that semantically similar episodic memories are clustered,
abstracted into summary nodes, and individual episodes are archived.
The summary should then retrieve correctly for relevant queries.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

_MESSAGES = [
    SimMessage(
        day=1, role="user",
        content="Python is widely used for machine learning projects.",
        tags=["python", "ml"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="Python is a popular language for ML development.",
        tags=["python", "ml"], node_type="fact",
    ),
    SimMessage(
        day=2, role="user",
        content="Python is commonly used in machine learning applications.",
        tags=["python", "ml"], node_type="fact",
    ),
    SimMessage(
        day=2, role="user",
        content="Python is the top choice for ML and data science.",
        tags=["python", "ml"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="Python dominates the machine learning ecosystem.",
        tags=["python", "ml"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="We deploy all services to Kubernetes clusters on AWS.",
        tags=["kubernetes", "aws"], node_type="fact",
    ),
    SimMessage(
        day=3, role="user",
        content="The team meets every Monday for sprint planning.",
        tags=["scrum", "meetings"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="Python has become the standard for machine learning work.",
        tags=["python", "ml"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="Most ML engineers prefer Python for their projects.",
        tags=["python", "ml"], node_type="fact",
    ),
    SimMessage(
        day=5, role="user",
        content="Python's ML libraries like scikit-learn and PyTorch are industry standard.",
        tags=["python", "ml"], node_type="fact",
    ),
]

_CHECKPOINTS = [
    SimCheckpoint(
        day=30,
        query="What language is used for machine learning?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description="After consolidation, Python+ML information should be retrievable",
    ),
    SimCheckpoint(
        day=30,
        query="What infrastructure does the team use?",
        expected_keywords=["Kubernetes"],
        excluded_keywords=[],
        description="Unrelated facts should not be affected by consolidation",
    ),
]

CONSOLIDATION_SCENARIO = SimScenario(
    name="consolidation",
    description=(
        "Tests predictive forgetting: many similar facts about Python+ML "
        "are stored, consolidation creates a summary, and old individual "
        "episodes are archived."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
    config_overrides={},
)
