"""Three read-time repairs from the address-first work (memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md).

``packing.clock_window_filter`` reads a clock window a question states as its event-time filter
(``prme.retrieval.clock_window``); ``packing.collapse_identical_text`` shows records with identical text once
(``prme.retrieval.repeats``); both are part of memory_mode="address_first". ``packing.second_round`` searches once
more for what the best result points at (``prme.retrieval.second_round``); it is an option of its own, off, and not
part of the mode. These tests cover each rule with small in-memory fixtures, the scores and
receipts they write, and that each option off retrieves exactly as with the option absent. No model call.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.client import config_from_directory
from prme.models.relevance import RetrievalReceipt
from prme.retrieval.candidates import SEED_STOPWORDS
from prme.retrieval.clock_window import parse_clock_window
from prme.retrieval.repeats import collapse_identical_text
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.second_round import (
    SECOND_ROUND_AFTER_RANK,
    SECOND_ROUND_HITS,
    SECOND_ROUND_PATH,
    SECOND_ROUND_TERMS,
    second_round,
    second_round_terms,
)
from prme.types import LifecycleState, NodeType, Scope
from tests.test_rank_fusion import NOW as SCORED_AT
from tests.test_rank_fusion import RRF, candidate
from tests.test_relation_routing import isolated, receipt_bytes  # noqa: F401 (fixture)

UTC = timezone.utc
REFERENCE = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


# --- clock windows -------------------------------------------------------------------------------------------------


def at(day: tuple[int, int, int], hour: int, minute: int = 0, zone=UTC) -> datetime:
    return datetime(*day, hour, minute, tzinfo=zone)


@pytest.mark.parametrize("question, window", [
    ("What did the garage camera see between 15:00 and 16:00 on November 3?",
     (at((2026, 11, 3), 15), at((2026, 11, 3), 16))),
    ("What happened from 3 to 4 pm yesterday?", (at((2026, 11, 3), 15), at((2026, 11, 3), 16))),
    ("Who came by between 9:30 am and 11 am?", (at((2026, 11, 4), 9, 30), at((2026, 11, 4), 11))),
    ("Who came by between 3 and 4 pm today?", (at((2026, 11, 4), 15), at((2026, 11, 4), 16))),
    ("Who came by between 11 am and 1 pm?", (at((2026, 11, 4), 11), at((2026, 11, 4), 13))),
    ("Any noise between 12 am and 1 am?", (at((2026, 11, 4), 0), at((2026, 11, 4), 1))),
    ("What did I log between 10pm and 11pm on 2026-11-01?", (at((2026, 11, 1), 22), at((2026, 11, 1), 23))),
    ("What was seen between 15:00 and 16:00 on 3 November?", (at((2026, 11, 3), 15), at((2026, 11, 3), 16))),
    ("What was seen between 15:00 and 16:00 on the 3rd of Nov?", (at((2026, 11, 3), 15), at((2026, 11, 3), 16))),
    # A day without a year is the latest such day not after the reference day.
    ("What was seen between 15:00 and 16:00 on December 30?", (at((2025, 12, 30), 15), at((2025, 12, 30), 16))),
    ("What was seen between 15:00 and 16:00 on Nov 3, 2025?", (at((2025, 11, 3), 15), at((2025, 11, 3), 16))),
])
def test_a_clock_window_is_read_on_its_day(question, window):
    assert parse_clock_window(question, REFERENCE) == window


@pytest.mark.parametrize("question", [
    "Were there between 3 and 4 people at the door?",            # two bare numbers
    "Who came by between 9:30 am and noon?",                     # no second clock time
    "What happened on November 3?",                              # a day alone
    "What did the camera see?",
    "Who came by between 16:00 and 15:00?",                      # the end is not after the start
    "Who came by between 15:00 and 15:00?",
    "Who came by between 25:00 and 26:00?",                      # no such hour
    "Who came by between 13 pm and 2 pm?",                       # no such hour with a meridiem
    "Who came by between 10:61 and 11:00?",                      # no such minute
    "What was seen between 15:00 and 16:00 on February 30?",     # no such day
])
def test_anything_else_is_not_a_clock_window(question):
    assert parse_clock_window(question, REFERENCE) is None


def test_the_window_is_in_the_reference_times_zone():
    zone = timezone(timedelta(hours=2))
    window = parse_clock_window("between 15:00 and 16:00 today", REFERENCE.astimezone(zone))
    assert window == (at((2026, 11, 4), 15, zone=zone), at((2026, 11, 4), 16, zone=zone))


# --- identical text --------------------------------------------------------------------------------------------------


def said(number: int, text: str, *, days: float = 0, **changes):
    item = candidate(number, semantic=1 - number / 100, days=days, content=text)
    if changes:
        item = item.model_copy(update={"node": item.node.model_copy(update=changes)})
    return item


def ranked(*items):
    return sorted(items, key=lambda item: -item.semantic_score)


CHECK = "Hallway check: nothing moved since the last pass."


def test_identical_text_keeps_the_best_ranked_copy_with_a_count_and_a_span():
    pool = ranked(said(1, CHECK, days=0), said(2, "The red mug is on the kitchen counter."),
                  said(3, "hallway  CHECK: nothing moved since the last pass.", days=1.5),
                  said(4, "The front door was left open."), said(5, CHECK, days=3))
    kept, excluded = collapse_identical_text(pool)
    assert [item.node.id.int for item in kept] == [1, 2, 4]
    first = (SCORED_AT - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    last = SCORED_AT.strftime("%Y-%m-%d %H:%M")
    assert kept[0].repeat_note == f"said 3 times, {first} to {last}"
    assert [(item.node_id.int, item.reason) for item in excluded] == [
        (3, f"identical_text:{UUID(int=1)}"), (5, f"identical_text:{UUID(int=1)}")]
    assert [item.composite_score for item in excluded] == [pool[2].composite_score, pool[4].composite_score]
    assert kept[1].repeat_note is None


def test_copies_said_at_one_time_get_a_count_alone():
    kept, _ = collapse_identical_text(ranked(said(1, CHECK), said(2, CHECK)))
    assert [item.repeat_note for item in kept] == ["said 2 times"]


@pytest.mark.parametrize("changes", [
    {"node_type": NodeType.NOTE},
    {"scope": Scope.PERSONAL},
    {"metadata": {"prme_speaker_v1": "Dana"}},
    {"lifecycle_state": LifecycleState.SUPERSEDED},
])
def test_the_same_text_of_another_kind_of_record_is_not_collapsed(changes):
    kept, excluded = collapse_identical_text(ranked(said(1, CHECK), said(2, CHECK, **changes)))
    assert len(kept) == 2 and excluded == []


@pytest.mark.parametrize("path", ["BELIEF", "CURRENT_STATE", "OWNER_PROFILE"])
def test_a_record_another_rule_placed_is_left_alone(path):
    placed = said(1, CHECK)
    placed = placed.model_copy(update={"paths": [*placed.paths, path]})
    kept, excluded = collapse_identical_text(ranked(placed, said(2, CHECK)))
    assert len(kept) == 2 and excluded == []


@pytest.mark.parametrize("node_type", [NodeType.ENTITY, NodeType.SUMMARY, NodeType.INSTRUCTION])
def test_entities_summaries_and_instructions_are_left_alone(node_type):
    kept, excluded = collapse_identical_text(ranked(said(1, "Dana", node_type=node_type),
                                                    said(2, "Dana", node_type=node_type)))
    assert len(kept) == 2 and excluded == []


def test_no_copies_leave_the_results_as_they_were():
    pool = ranked(said(1, CHECK), said(2, "The red mug is on the kitchen counter."))
    kept, excluded = collapse_identical_text(pool)
    assert kept is pool and excluded == []


# --- second round ----------------------------------------------------------------------------------------------------


REQUEST = "Dana asked the robot to bring the red mug later."


def test_the_terms_are_the_top_results_own_words():
    pool = [said(1, REQUEST), said(2, "Ravi asked the robot to bring the laptop bag later."),
            said(3, "Mei asked the robot to bring the blue notebook later."),
            said(4, "Hana asked the robot to bring the cookbook later.")]
    # Not the question's words (thing, Dana), the look-alikes' (asked, robot, bring, later), short words or stopwords.
    assert second_round_terms("Where is the thing Dana asked for?", pool, SEED_STOPWORDS) == ["red", "mug"]


def test_terms_skip_numbers_and_short_words_and_are_capped():
    top = said(1, "Room 12 holds the violet ottoman, brass lamp, linen chest, oak desk, slate tray, wool rug.")
    terms = second_round_terms("What is in there?", [top], SEED_STOPWORDS)
    assert "12" not in terms and len(terms) == SECOND_ROUND_TERMS
    assert terms[:3] == ["room", "holds", "violet"]
    assert second_round_terms("anything", [], SEED_STOPWORDS) == []


def scored_pool(count=5):
    pool, _ = score_and_rank([candidate(number, semantic=1 - number / 20) for number in range(1, count + 1)],
                             RRF, now=SCORED_AT)
    return pool


def search_returning(*numbers):
    async def search(_text):
        return [{"node_id": str(UUID(int=number))} for number in numbers]
    return search


def loading(*numbers):
    async def load(node_ids):
        wanted = {UUID(node_id).int for node_id in node_ids}
        return [candidate(number) for number in numbers if number in wanted]
    return load


async def test_a_match_is_placed_just_below_the_third_result_and_its_score_replays():
    pool = scored_pool()
    results, placed = await second_round(pool, ["red", "mug"], search=search_returning(1, 9),
                                         load=loading(9))
    # The top result itself is not a match of its own words.
    assert placed == [str(UUID(int=9))]
    order = [item.node.id.int for item in results]
    assert order.index(9) == SECOND_ROUND_AFTER_RANK
    match = results[SECOND_ROUND_AFTER_RANK]
    assert pool[SECOND_ROUND_AFTER_RANK].composite_score < match.composite_score
    assert match.composite_score < pool[SECOND_ROUND_AFTER_RANK - 1].composite_score
    assert SECOND_ROUND_PATH in match.paths
    adjustment = match.score_provenance.adjustments[-1]
    assert (adjustment.kind, adjustment.source_node_id) == ("second_round", pool[0].node.id)
    assert match.score_provenance.replay_score() == match.composite_score


async def test_a_match_that_already_ranks_higher_keeps_its_place():
    pool = scored_pool()
    results, placed = await second_round(pool, ["red"], search=search_returning(2), load=loading())
    assert placed == [str(UUID(int=2))]
    assert [item.node.id.int for item in results] == [item.node.id.int for item in pool]
    assert results[1].composite_score == pool[1].composite_score and SECOND_ROUND_PATH in results[1].paths


async def test_at_most_the_cap_of_matches_is_placed_in_search_order():
    pool = scored_pool()
    results, placed = await second_round(pool, ["red"], search=search_returning(7, 8, 9), load=loading(7, 8, 9))
    assert placed == [str(UUID(int=7)), str(UUID(int=8))] and len(placed) == SECOND_ROUND_HITS
    order = [item.node.id.int for item in results]
    assert order.index(7) < order.index(8) < order.index(4)


async def test_nothing_to_search_or_nothing_found_leaves_the_results_as_they_were():
    pool = scored_pool()
    assert await second_round(pool, [], search=search_returning(9), load=loading(9)) == (pool, [])
    assert await second_round([], ["red"], search=search_returning(9), load=loading(9)) == ([], [])
    assert await second_round(pool, ["red"], search=search_returning(), load=loading()) == (pool, [])
    # A match the request's filters keep out cannot be loaded, so nothing is placed.
    assert await second_round(pool, ["red"], search=search_returning(9), load=loading()) == (pool, [])


# --- the options in retrieval ----------------------------------------------------------------------------------------


class WordEmbedding:
    """A bag of words as a vector: texts that share words are near, so a ranking can be reasoned about."""

    model_name, model_version, dimension = "test-words", "1", 384

    async def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for word in re.findall(r"\w+", text.casefold()):
                vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimension] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


@pytest.fixture
def word_embeddings(isolated, monkeypatch):  # noqa: F811 (the fixture imported above)
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: WordEmbedding())


USER = "robot"
DAY = datetime(2026, 11, 3, tzinfo=UTC)
ASKED = DAY + timedelta(days=1)
OPTIONS = ("clock_window_filter", "collapse_identical_text", "second_round")


def option_config(pack: Path, option: str | None, on: bool | None) -> PRMEConfig:
    """The pack's configuration with one option on, off, or not set (None)."""
    base = config_from_directory(str(pack))
    if option is None or on is None:
        return base
    return base.model_copy(update={"packing": base.packing.model_copy(update={option: on})})


async def build(pack: Path) -> None:
    async with MemoryEngine.open(option_config(pack, None, None)) as engine:
        await engine.store(REQUEST, user_id=USER, event_time=DAY + timedelta(hours=8))
        await engine.store("The red mug is on the kitchen counter.", user_id=USER,
                           event_time=DAY + timedelta(hours=8, minutes=7))
        for hour in range(9, 18):
            await engine.store(f"Garage camera saw a cyclist pass the driveway at {hour}:00.", user_id=USER,
                               event_time=DAY + timedelta(hours=hour))
        for minutes in (0, 20, 40):
            await engine.store(CHECK, user_id=USER, event_time=DAY + timedelta(hours=18, minutes=minutes))


async def ask(pack: Path, option: str | None, on: bool | None, question: str):
    async with MemoryEngine.open(option_config(pack, option, on)) as engine:
        response = await engine.retrieve(question, user_id=USER, reference_time=ASKED)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
    return response, receipt


QUESTIONS = {
    "clock_window_filter": "What did the garage camera see between 15:00 and 16:00 on November 3?",
    "collapse_identical_text": "Is the hallway clear?",
    "second_round": "Where is the thing Dana asked for?",
}


@pytest.mark.usefixtures("word_embeddings")
class TestInRetrieval:
    @pytest.mark.parametrize("option", OPTIONS)
    async def test_off_is_exactly_the_retrieval_without_the_option(self, tmp_path, option):
        await build(tmp_path / "pack")
        absent, absent_receipt = await ask(tmp_path / "pack", None, None, QUESTIONS[option])
        off, off_receipt = await ask(tmp_path / "pack", option, False, QUESTIONS[option])
        assert [result.model_dump(mode="json") for result in off.results] == [
            result.model_dump(mode="json") for result in absent.results]
        assert off.bundle.render() == absent.bundle.render()
        assert receipt_bytes(off_receipt) == receipt_bytes(absent_receipt)

    async def test_a_clock_window_filters_and_is_recorded(self, tmp_path):
        await build(tmp_path / "pack")
        on, receipt = await ask(tmp_path / "pack", "clock_window_filter", True, QUESTIONS["clock_window_filter"])
        assert {result.node.content for result in on.results} == {
            "Garage camera saw a cyclist pass the driveway at 15:00.",
            "Garage camera saw a cyclist pass the driveway at 16:00."}
        assert receipt.execution.features["clock_window"] == {
            "version": 1, "from": (DAY + timedelta(hours=15)).isoformat(),
            "to": (DAY + timedelta(hours=16)).isoformat()}
        assert RetrievalReceipt.model_validate_json(receipt.model_dump_json()).replay_ranking() == \
            receipt.replay_ranking()

    async def test_identical_text_is_shown_once_and_recorded(self, tmp_path):
        await build(tmp_path / "pack")
        on, receipt = await ask(tmp_path / "pack", "collapse_identical_text", True,
                                QUESTIONS["collapse_identical_text"])
        copies = [result for result in on.results if result.node.content == CHECK]
        assert len(copies) == 1 and copies[0].repeat_note.startswith("said 3 times")
        assert receipt.execution.features["collapse_identical_text"] == {"version": 1, "removed": 2}
        assert "said 3 times" in on.bundle.render()

    async def test_a_second_round_places_what_the_top_result_names_and_its_receipt_replays(self, tmp_path):
        await build(tmp_path / "pack")
        on, receipt = await ask(tmp_path / "pack", "second_round", True, QUESTIONS["second_round"])
        texts = [result.node.content for result in on.results]
        record = receipt.execution.features["second_round"]
        assert texts[0] == REQUEST and {"red", "mug"} <= set(record["terms"])
        placed = [result for result in on.results if SECOND_ROUND_PATH in result.paths]
        assert "The red mug is on the kitchen counter." in [result.node.content for result in placed]
        assert all(result.score_provenance.replay_score() == result.composite_score for result in placed)
        restored = RetrievalReceipt.model_validate_json(receipt.model_dump_json())
        assert restored.replay_ranking() == receipt.replay_ranking()
