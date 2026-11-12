"""The chat through ``store()``: one owner's chat over four sessions, written turn by turn, then questioned.

Known answers, per question: every answer string ranks in the top 3; no stale string (an earlier value that a
later turn replaced) ranks above the worst-ranked answer; and a question nothing in the chat answers packs
fewer than 10 records.
"""

from tests.contract.harness import assert_answers_in_top3, assert_no_stale_above_answer, assert_packs_fewer_than_10


def test_every_answer_ranks_in_top3(chat_answer_case, chat):
    assert_answers_in_top3(chat, chat_answer_case.data[0])


def test_no_stale_answer_ranks_above_the_answer(chat_stale_case, chat):
    assert_no_stale_above_answer(chat, chat_stale_case.data[0])


def test_unanswerable_question_packs_fewer_than_10_records(chat_unanswerable_case, chat):
    assert_packs_fewer_than_10(chat, chat_unanswerable_case.data[0])
