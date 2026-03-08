"""Scenario: oscillation detection in flip-flop patterns.

Tests that when a user oscillates between preferences (e.g. dark mode
-> light mode -> dark mode), the oscillating facts receive reduced
confidence in retrieval results, reflecting genuine uncertainty.

Requires enable_store_supersedence=True in config.
"""

from simulations.harness import SimCheckpoint, SimMessage, SimScenario

# ---------------------------------------------------------------------------
# Messages: dark mode / light mode flip-flop over ~30 days
# ---------------------------------------------------------------------------

_MESSAGES = [
    # Day 1: establish initial preference
    SimMessage(
        day=1, role="user",
        content="I use dark mode for all my development work. It reduces eye strain during long coding sessions.",
        tags=["preference", "dark-mode", "editor"], node_type="preference",
    ),

    # Day 10: switch to light mode
    SimMessage(
        day=10, role="user",
        content="I switched from dark mode to light mode for development. Better readability in daylight.",
        tags=["preference", "light-mode", "editor"], node_type="preference",
    ),

    # Day 20: flip back to dark mode
    SimMessage(
        day=20, role="user",
        content="I went back to dark mode from light mode. The eye strain was getting worse in light mode.",
        tags=["preference", "dark-mode", "editor"], node_type="preference",
    ),

    # Unrelated stable fact for control
    SimMessage(
        day=1, role="user",
        content="Our primary programming language is Python 3.12 for all backend services.",
        tags=["language", "python"], node_type="fact",
    ),
]

# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

_CHECKPOINTS = [
    SimCheckpoint(
        day=25,
        query="What display mode do I use for coding?",
        expected_keywords=["dark mode"],
        excluded_keywords=[],
        description=(
            "After oscillation (dark->light->dark), dark mode should appear "
            "but with reduced confidence reflecting the flip-flop uncertainty"
        ),
    ),
    SimCheckpoint(
        day=25,
        query="What programming language do we use?",
        expected_keywords=["Python"],
        excluded_keywords=[],
        description=(
            "Non-oscillating facts should maintain normal confidence"
        ),
    ),
]

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

OSCILLATION_SCENARIO = SimScenario(
    name="oscillation",
    description=(
        "Tests oscillation detection in flip-flop patterns. "
        "Verifies that repeatedly switching preferences results in "
        "reduced confidence on the oscillating topic, while unrelated "
        "stable facts maintain their confidence."
    ),
    messages=_MESSAGES,
    checkpoints=_CHECKPOINTS,
)
