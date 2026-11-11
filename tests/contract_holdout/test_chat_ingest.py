"""The hidden set runs the visible set's test code on its own data (world.py here)."""

from tests.contract.test_chat_ingest import (  # noqa: F401
    test_a_corrected_claim_is_retired,
    test_answer_ranks_first,
    test_every_answer_ranks_in_top3,
    test_no_ended_claim_ranks_above_a_current_one,
    test_no_stale_answer_ranks_above_the_answer,
    test_no_true_claim_is_retired,
    test_unanswerable_question_packs_fewer_than_10_records,
)
