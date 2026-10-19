"""enable_owner_name_carry on the batched path hosted projects run (prism-monorepo issue 258).

The hosted dev service ingested the four turns of the issue's repro with the structure options on and
extraction batched at 2,048 tokens, and stored 7 claims of which only one names "Alex Rivera" in its text.
``fixtures/extraction/trip_dev_a3.json`` holds what the service saved for those turns (read back with
``GET /v1/events/{id}/extraction``) and what it then stored for the claims. These tests replay that
extraction without a model call and show where the rest of the claims went: the text of a claim keeps the
user's own words by design, and what a carried name changes is the entity the claim is attached to.

Every test runs on DuckDB and, with PRME_TEST_DATABASE_URL set, on PostgreSQL.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prme import MemoryEngine
from prme.ingestion.schema import ExtractionResult
from prme.types import NodeType
from tests import test_durable_ingestion
from tests.test_batched_extraction import drain
from tests.test_ingest_names_and_claims import claims
from tests.test_owner_name_carry import (
    CASEY,
    INTRODUCTION,
    PREFERENCE,
    RESULTS,
    WEEKEND,
    entity_names,
    on,
    result,
    subjects,
)

config = test_durable_ingestion.config
user = test_durable_ingestion.user

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "extraction" / "trip_dev_a3.json").read_text())
T0 = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
SUNDAY = "On Sundays I bake bread."
RESULTS = {
    **RESULTS,
    SUNDAY: result([{"name": "bread", "entity_type": "concept"}], "I", "bakes", "bread", SUNDAY, "concept"),
}


def hosted(base, **updates):
    """The ingestion settings the dev service ran: batched at 2,048 tokens, the structure options on.

    ``tests/conftest.py`` starts every test with the options off, so each is named here. The two packing
    options change retrieval, not what ingest stores, and are left out.
    """
    return on(base, extraction_batch_target_tokens=2048, enable_mention_links=True,
              enable_speaker_references=True, enable_claim_sentence_text=True, **updates)


def source_times():
    """The recorded turns' source times, a few hundred milliseconds apart as the dev run's were."""
    stamps = [datetime.fromisoformat(turn["timestamp"].replace("Z", "+00:00")) for turn in RECORDED["turns"]]
    return [T0 + (stamp - stamps[0]) for stamp in stamps]


async def replay_dev_run(engine, user, *, flush_seconds=3600.0):
    """Four ingest() calls that do not wait, then the flush the 30 second timer arms; the model is the record.

    Returns the event IDs and the batched requests the engine sent, each a list of (turn, role, text).
    """
    recorded = {turn["content"]: entry for turn, entry in zip(RECORDED["turns"], RECORDED["records"])}
    requests = []

    async def extract_batch(turns):
        requests.append([(turn.index, turn.role, turn.content) for turn in turns])
        return {"turns": [{**recorded[turn.content], "turn": turn.index} for turn in turns]}

    pipeline = engine._pipeline
    pipeline._extraction_provider.extract_batch = extract_batch
    pipeline._batch_flush_seconds = flush_seconds
    event_ids = []
    for turn, source_time in zip(RECORDED["turns"], source_times()):
        event_ids.append(await engine.ingest(turn["content"], user_id=user, role=turn["role"],
                                             session_id="trip-1", event_time=source_time))
    return event_ids, requests


async def stored_claims(engine, user, event_ids):
    """Every claim of the run with the entity it is attached to, as (turn number, claim node, subject entity)."""
    return [(number, node, subject) for number, event_id in enumerate(event_ids, start=1)
            for node, subject in await claims(engine, user, event_id)]


def as_observed(stored):
    """What the service stored for each claim, in the fixture's terms, in a fixed order."""
    return sorted(
        (number, node.node_type.value, node.content, node.metadata["subject"],
         (node.metadata.get("speaker_reference") or {}).get("basis"))
        for number, node, _ in stored
    )


def recorded_observed():
    return sorted((o["event"], o["node_type"], o["content"], o["subject"],
                   (o["speaker_reference"] or {}).get("basis")) for o in RECORDED["observed"])


class TestRecordedDevRun:
    async def test_the_replay_stores_what_the_dev_service_stored(self, config, user):
        async with MemoryEngine.open(hosted(config)) as engine:
            event_ids, requests = await replay_dev_run(engine, user)
            assert (await engine.process_extractions(user_id=user)).processed == 4
            stored = await stored_claims(engine, user, event_ids)
        # One request carried the four turns in event order, as the dev service sent them.
        assert requests == [[(number, turn["role"], turn["content"])
                             for number, turn in enumerate(RECORDED["turns"], start=1)]]
        assert as_observed(stored) == recorded_observed()

    async def test_the_timer_flush_stores_the_same_claims(self, config, user):
        async with MemoryEngine.open(hosted(config)) as engine:
            event_ids, _ = await replay_dev_run(engine, user, flush_seconds=0.2)
            await drain(engine._pipeline)
            stored = await stored_claims(engine, user, event_ids)
        assert as_observed(stored) == recorded_observed()

    async def test_most_claims_are_attached_to_the_owner_though_their_text_keeps_the_users_words(self, config, user):
        async with MemoryEngine.open(hosted(config)) as engine:
            event_ids, _ = await replay_dev_run(engine, user)
            await engine.process_extractions(user_id=user)
            stored = await stored_claims(engine, user, event_ids)
            marked = [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
                      if "owner_name" in (node.metadata or {})]
        attached = [node for _, node, subject in stored if subject.content == "Alex Rivera"]
        group = [node for _, node, subject in stored if subject.content == "We"]
        in_text = [node for _, node, _ in stored if "Alex Rivera" in node.content]
        # Five of the seven claims belong to the owner's entity, against the two "We" claims of the cities
        # turn, which the engine keeps on the speaker's group. Only one claim's text names the owner.
        assert (len(attached), len(group), len(in_text)) == (5, 2, 1)
        assert len(attached) > len(group)
        # Turn 4 names nobody, so the carry attached it, from the name turn 1 gave; turn 3 has no I to bind.
        bases = {number: {(node.metadata.get("speaker_reference") or {}).get("basis")
                          for n, node, _ in stored if n == number} for number in (1, 3, 4)}
        assert bases == {1: {"self_introduction"}, 3: {None}, 4: {"owner_name"}}
        assert [node.content for node in marked] == ["Alex Rivera"]
        assert marked[0].metadata["owner_name"]["basis"] == "self_introduction"

    async def test_without_the_carry_the_later_turn_keeps_its_own_i(self, config, user):
        async with MemoryEngine.open(hosted(config, enable_owner_name_carry=False)) as engine:
            event_ids, _ = await replay_dev_run(engine, user)
            await engine.process_extractions(user_id=user)
            stored = await stored_claims(engine, user, event_ids)
        attached = [node for _, node, subject in stored if subject.content == "Alex Rivera"]
        on_i = [node for number, node, subject in stored if number == 4 and subject.content == "I"]
        # Only the first turn's two claims are the owner's: 2 against 5 with the carry on.
        assert (len(attached), len(on_i)) == (2, 3)


class TestBatchedScope:
    """The scope rules hold between the turns of one batched request, as they do between separate requests."""

    async def say_all(self, engine, user, messages):
        start = T0 + timedelta(hours=1)
        prepared = [{"event_time": start + timedelta(minutes=index), **message}
                    for index, message in enumerate(messages)]
        requests = []

        async def extract_batch(turns):
            requests.append([turn.content for turn in turns])
            return {"turns": [{**RESULTS[turn.content].model_dump(mode="json"), "turn": turn.index}
                              for turn in turns]}

        engine._pipeline._extraction_provider.extract_batch = extract_batch
        event_ids = await engine.ingest_batch(prepared, user_id=user, session_id="s1", wait_for_extraction=True)
        return event_ids, requests

    async def test_only_the_owners_user_turns_take_the_name(self, config, user):
        messages = [
            {"content": INTRODUCTION, "role": "user"},
            {"content": PREFERENCE, "role": "participant"},
            {"content": WEEKEND, "role": "assistant"},
            {"content": SUNDAY, "role": "user"},
        ]
        async with MemoryEngine.open(hosted(config)) as engine:
            event_ids, requests = await self.say_all(engine, user, messages)
            found = [subjects(await claims(engine, user, event_id)) for event_id in event_ids]
            names = await entity_names(engine, user)
        assert requests == [[message["content"] for message in messages]]
        assert [[name for name, _, _ in turn] for turn in found] == [["Alex Rivera"], ["I"], ["I"], ["Alex Rivera"]]
        assert found[3][0][2] == found[0][0][2]
        assert names.count("Alex Rivera") == 1

    async def test_a_later_name_replaces_the_earlier_one_without_rewriting_a_claim(self, config, user):
        messages = [{"content": text, "role": "user"} for text in (INTRODUCTION, PREFERENCE, CASEY, WEEKEND)]
        async with MemoryEngine.open(hosted(config)) as engine:
            event_ids, requests = await self.say_all(engine, user, messages)
            found = [subjects(await claims(engine, user, event_id)) for event_id in event_ids]
        assert len(requests) == 1
        assert [[name for name, _, _ in turn] for turn in found] == [
            ["Alex Rivera"], ["Alex Rivera"], ["Casey Lee"], ["Casey Lee"]]
        assert found[1][0][2] == found[0][0][2] != found[2][0][2]

    async def test_a_callers_speaker_wins_on_its_turn_and_names_the_owner_after_it(self, config, user):
        messages = [
            {"content": INTRODUCTION, "role": "user"},
            {"content": PREFERENCE, "role": "user", "speaker": "Dana"},
            {"content": WEEKEND, "role": "user"},
        ]
        async with MemoryEngine.open(hosted(config, enable_ingest_claim_fixes=True)) as engine:
            event_ids, _ = await self.say_all(engine, user, messages)
            found = [await claims(engine, user, event_id) for event_id in event_ids]
        assert [[subject.content for _, subject in turn] for turn in found] == [["Alex Rivera"], ["Dana"], ["Dana"]]
        assert "basis" not in found[1][0][0].metadata["speaker_reference"]
        assert found[2][0][0].metadata["speaker_reference"]["basis"] == "owner_name"

    async def test_a_name_no_turn_gave_is_not_invented_in_a_batch(self, config, user):
        messages = [{"content": text, "role": "user"} for text in (PREFERENCE, WEEKEND, SUNDAY)]
        async with MemoryEngine.open(hosted(config)) as engine:
            event_ids, _ = await self.say_all(engine, user, messages)
            found = [subjects(await claims(engine, user, event_id)) for event_id in event_ids]
        assert [[name for name, _, _ in turn] for turn in found] == [["I"], ["I"], ["I"]]


def test_the_fixture_is_the_four_turns_of_the_repro():
    assert [turn["role"] for turn in RECORDED["turns"]] == ["user", "assistant", "user", "user"]
    assert all(ExtractionResult.model_validate(record) for record in RECORDED["records"])
    assert RECORDED["records"][0]["entities"][0]["name"] == "Alex Rivera"
