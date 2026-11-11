"""The hidden set runs the visible set's test code on its own data (world.py here)."""

from tests.contract.test_chat_store import (  # noqa: F401
    test_every_answer_ranks_in_top3,
    test_no_stale_answer_ranks_above_the_answer,
    test_unanswerable_question_packs_fewer_than_10_records,
)
