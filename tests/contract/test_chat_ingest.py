"""The chat through ``ingest()``, its extraction replayed from a recording: no model and no network.

The chat's four sessions go through ``ingest_batch()``, one call per session, with the product defaults. The
extraction provider's answers come from ``fixtures/chat_extraction.json``, recorded once against the local Ollama
server with ``memory_mode="address_first"`` (``fixtures/record_chat_extraction.py`` has the command), so the typed
fields are in it. In the classic mode the same answers are replayed and the typed fields are not asked for.

Known answers: those of ``test_chat_store.py``, under the same case ids, and:

- first: the one answer string is in ``results[0]`` (the new job; the sister's new city);
- ledger: no result the ledger marks ``ended`` ranks above one it marks ``current`` (``belief_state`` is set only
  under ``enable_belief_state``, so in the classic mode there is nothing to compare and the check holds);
- retired: each claim the chat later corrects (``ChatWorld.retire``, the key) has ended retired, every copy;
- no true claim retired: no claim outside the key ended retired, other than a copy merged into a repeat of itself.

The recording was made with corrections on (address_first turns them on), so in the classic mode, where they are
off, the labels it holds are not asked for and retire nothing.
"""

from tests.contract.harness import (
    assert_answers_in_top3,
    assert_no_stale_above_answer,
    assert_packs_fewer_than_10,
    first_rank,
    texts,
)


def test_every_answer_ranks_in_top3(ingest_answer_case, ingest_chat):
    assert_answers_in_top3(ingest_chat, ingest_answer_case.data[0])


def test_no_stale_answer_ranks_above_the_answer(ingest_stale_case, ingest_chat):
    assert_no_stale_above_answer(ingest_chat, ingest_stale_case.data[0])


def test_unanswerable_question_packs_fewer_than_10_records(ingest_unanswerable_case, ingest_chat):
    assert_packs_fewer_than_10(ingest_chat, ingest_unanswerable_case.data[0])


def test_answer_ranks_first(ingest_first_case, ingest_chat):
    (question,) = ingest_first_case.data
    ranked = texts(ingest_chat.ask(question.question))
    ranks = {needle: first_rank(ranked, needle) for needle in question.answers}
    assert all(rank == 1 for rank in ranks.values()), f"ranks: {ranks}; top 3: {ranked[:3]}"


def test_no_ended_claim_ranks_above_a_current_one(ingest_ledger_case, ingest_chat):
    (question,) = ingest_ledger_case.data
    results = ingest_chat.ask(question.question).results
    # getattr: a tree from before enable_belief_state has no such field, and nothing to compare.
    states = [(rank, getattr(candidate, "belief_state", None), candidate.node.content)
              for rank, candidate in enumerate(results, start=1)
              if getattr(candidate, "belief_state", None) in ("current", "ended")]
    last_current = max((rank for rank, state, _ in states if state == "current"), default=0)
    above = [(rank, text) for rank, state, text in states if state == "ended" and rank < last_current]
    assert not above, f"ended above a current claim (last current at {last_current}): {above}"


def test_a_corrected_claim_is_retired(ingest_retire_case, ingest_chat, chat_world):
    (item,) = ingest_retire_case.data
    found = ingest_chat.retirements(chat_world).matches[item.tag]
    assert found and all(retired for _line, retired in found), f"claims of the key item: {found}"


def test_no_true_claim_is_retired(ingest_true_claim_case, ingest_chat, chat_world):
    wrong = ingest_chat.retirements(chat_world).wrong
    assert not wrong, f"retired outside the key: {wrong}"
