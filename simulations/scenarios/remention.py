"""Scenario: re-mention reinforcement.

Tests that when topics are re-mentioned in different words, the existing
nodes about those topics get reinforced and rank higher than topics that
were only mentioned once.

This scenario requires reinforce_similarity_threshold to be set in the
engine config so that store() automatically reinforces similar existing
nodes when new content arrives.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages spanning ~30 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: Establish baseline facts about various topics
    SimMessage(
        day=1, role="user",
        content="We use Docker containers for all our service deployments.",
        tags=["docker", "containers", "deployment"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="Our machine learning models are trained using TensorFlow.",
        tags=["ml", "tensorflow"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="The company uses Slack for all internal communication.",
        tags=["slack", "communication"], node_type="fact",
    ),
    SimMessage(
        day=1, role="user",
        content="Our data warehouse runs on Snowflake for analytics queries.",
        tags=["snowflake", "data-warehouse"], node_type="fact",
    ),

    # Day 5: Re-mention Docker in different words (reinforcement)
    SimMessage(
        day=5, role="user",
        content="Docker containerization has been essential for our microservices.",
        tags=["docker", "containers"], node_type="fact",
    ),

    # Day 8: Re-mention Docker again
    SimMessage(
        day=8, role="user",
        content="We run all production services inside Docker containers managed by Kubernetes.",
        tags=["docker", "kubernetes"], node_type="fact",
    ),

    # Day 12: Re-mention TensorFlow
    SimMessage(
        day=12, role="user",
        content="TensorFlow 2.x has made our model training pipeline much simpler.",
        tags=["tensorflow", "ml"], node_type="fact",
    ),

    # Day 15: Re-mention Docker once more
    SimMessage(
        day=15, role="user",
        content="Docker Compose is used for local development environment setup.",
        tags=["docker", "development"], node_type="fact",
    ),

    # Day 20: Mention a new topic only once (control)
    SimMessage(
        day=20, role="user",
        content="We had a brief incident with our Elasticsearch cluster last week.",
        tags=["elasticsearch", "incident"], node_type="fact",
    ),

    # Day 25: Re-mention Docker yet again
    SimMessage(
        day=25, role="user",
        content="Docker image builds are cached in our CI pipeline for faster deployments.",
        tags=["docker", "ci"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=10,
        query="What container technology do we use for deployment?",
        expected_keywords=["Docker"],
        excluded_keywords=[],
        description="Docker (re-mentioned twice by day 10) should rank strongly",
    ),
    SimCheckpoint(
        day=30,
        query="What technologies are most important to our infrastructure?",
        expected_keywords=["Docker"],
        excluded_keywords=[],
        description="Heavily re-mentioned Docker should rank above single-mention topics",
        ranking_assertions=[("Docker", "Elasticsearch")],
    ),
    SimCheckpoint(
        day=30,
        query="What do we use for machine learning?",
        expected_keywords=["TensorFlow"],
        excluded_keywords=[],
        description="TensorFlow (re-mentioned once) should appear in ML results",
    ),
    SimCheckpoint(
        day=30,
        query="What tools does the team rely on?",
        expected_keywords=["Docker"],
        excluded_keywords=[],
        description="Docker should dominate broad queries due to repeated re-mentions",
        ranking_assertions=[("Docker", "Snowflake")],
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

REMENTION_SCENARIO = SimScenario(
    name="remention",
    description=(
        "Tests that re-mentioned topics get reinforced and rank higher in "
        "retrieval than topics mentioned only once. Docker is re-mentioned "
        "across 5 different days while other topics are mentioned 1-2 times."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
