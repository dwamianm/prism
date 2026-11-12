"""The hidden set runs the visible set's test code on its own data (world.py here)."""

from tests.contract.test_state_churn import (  # noqa: F401
    test_newest_place_ranks_first,
    test_newest_stated_place_ranks_first,
)
