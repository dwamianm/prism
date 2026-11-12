"""The hidden set runs the visible set's test code on its own data (world.py here)."""

from tests.contract.test_flood import (  # noqa: F401
    test_rare_event_ranks_in_top5,
    test_routine_lines_are_under_half_of_the_context,
)
