"""A month named without a year resolves against the turn's own event time (#498)."""

from datetime import datetime, timezone

import pytest

from prme import MemoryEngine
from prme.ingestion.pipeline import IngestionPipeline
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.ingestion.temporal import classify_tense, names_month_without_year
from prme.models import Event
from prme.types import NodeType
from tests.test_durable_ingestion import config, user  # noqa: F401

# The member-v1 trip turn and the session time it was stored at.
TRIP_TURN = "I am planning a trip for us in December to Jamaica. Do we need vaccines?"
TRIP_TIME = datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc)


def resolve(reference, source_text, *, reference_time=TRIP_TIME):
    return IngestionPipeline._resolve_temporal_reference(
        reference, reference_time=reference_time, source_text=source_text
    )


def test_a_planned_month_takes_the_next_occurrence():
    resolution = resolve("December", TRIP_TURN)
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2026, 12)


def test_a_future_month_before_the_turn_takes_next_year():
    resolution = resolve("March", "We are planning a trip in March.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2027, 3)


def test_a_completed_month_takes_the_most_recent_past_occurrence():
    resolution = resolve("December", "We went to Jamaica in December.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2025, 12)


def test_a_month_with_no_tense_takes_the_nearest_occurrence_and_is_uncertain():
    resolution = resolve("March", "My birthday is in March.")
    assert resolution.uncertain is True
    assert datetime.fromisoformat(resolution.value) == datetime(2027, 3, 2, tzinfo=timezone.utc)


def test_the_nearest_occurrence_can_look_backwards():
    resolution = resolve(
        "March", "My birthday is in March.",
        reference_time=datetime(2026, 4, 2, 12, tzinfo=timezone.utc),
    )
    assert resolution.uncertain is True
    assert datetime.fromisoformat(resolution.value) == datetime(2026, 3, 2, tzinfo=timezone.utc)


def test_a_year_in_the_reference_is_not_overridden_by_tense():
    resolution = resolve("March 2024", "We are planning a trip in March 2024.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2024, 3)


def test_a_day_without_a_year_follows_the_same_rule():
    resolution = resolve("December 9", "I am planning a trip on December 9.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month, moment.day) == (2026, 12, 9)


def test_only_the_sentence_holding_the_reference_decides():
    text = "I visited Jamaica in March. I am planning another trip in December."
    resolution = resolve("December", text)
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2026, 12)


def test_a_relative_reference_keeps_the_past_preference():
    resolution = resolve("yesterday", "I went yesterday.")
    assert resolution.uncertain is False
    assert datetime.fromisoformat(resolution.value) == datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("sentence", [
    "I finished a book in December.",
    "I read a book in December.",
    "I visited a nature reserve in December.",
    "I checked the schedule in December.",
])
def test_past_statements_with_a_future_shaped_word_stay_past(sentence):
    resolution = resolve("December", sentence)
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2025, 12)


@pytest.mark.parametrize("sentence", [
    "I hoped to visit in December.",
    "We planned the trip in December.",
])
def test_a_past_shaped_plan_is_not_confidently_future(sentence):
    resolution = resolve("December", sentence)
    assert resolution.uncertain is True
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2026, 12)


def test_a_contraction_is_future():
    resolution = resolve("June", "I'll go to Jamaica in June.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2027, 6)


def test_a_present_tense_plan_verb_is_future():
    resolution = resolve("June", "I start a new job in June.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2027, 6)


def test_a_plan_in_the_event_month_stays_in_that_month():
    resolution = resolve("November", "I am planning to launch in November.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2026, 11)


def test_a_month_mentioned_twice_is_ambiguous():
    text = "We are planning a trip in December. Last December we went to Jamaica."
    assert resolve("December", text).uncertain is True


def test_a_two_digit_year_is_not_treated_as_missing():
    assert names_month_without_year("March '24") is False
    resolution = resolve("March '24", "We went in March '24.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2026, 3)


def test_the_nearer_cue_wins():
    resolution = resolve("December", "I was hoping to go to Jamaica in December.")
    assert resolution.uncertain is False
    moment = datetime.fromisoformat(resolution.value)
    assert (moment.year, moment.month) == (2026, 12)


def test_a_tie_between_cues_is_no_signal():
    text = "was" + " " * 9 + "December" + " " * 4 + "will"
    assert classify_tense(text, "December") is None


async def test_ingestion_stores_the_planned_month_at_the_turn_time(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        event = Event(
            user_id=user, role="user", content=TRIP_TURN,
            timestamp=TRIP_TIME, created_at=TRIP_TIME,
        )
        await engine._event_store.append(event)
        extracted = ExtractionResult(
            entities=[ExtractedEntity(name="Jamaica", entity_type="place")],
            facts=[ExtractedFact(
                subject="I", predicate="planned_trip_to", object="Jamaica",
                temporal_ref="December", evidence_quote=TRIP_TURN,
            )],
            relationships=[],
        )
        await engine._pipeline._materialize(extracted, event, str(event.id))
        fact = (await engine.query_nodes(user_id=user, node_type=NodeType.FACT))[0]
        assert (fact.event_time.year, fact.event_time.month) == (2026, 12)
        assert fact.metadata["resolved_date"].startswith("2026-12")
        assert "resolved_date_uncertain" not in fact.metadata


async def test_ingestion_records_an_unguessable_month_as_uncertain(config, user):  # noqa: F811
    quote = "My birthday is in March."
    async with MemoryEngine.open(config) as engine:
        event = Event(
            user_id=user, role="user", content=quote,
            timestamp=TRIP_TIME, created_at=TRIP_TIME,
        )
        await engine._event_store.append(event)
        extracted = ExtractionResult(
            entities=[],
            facts=[ExtractedFact(
                subject="I", predicate="birthday_month", object="March",
                temporal_ref="March", evidence_quote=quote,
            )],
            relationships=[],
        )
        await engine._pipeline._materialize(extracted, event, str(event.id))
        fact = (await engine.query_nodes(user_id=user, node_type=NodeType.FACT))[0]
        assert (fact.event_time.year, fact.event_time.month) == (2027, 3)
        assert fact.metadata["resolved_date_uncertain"] is True
