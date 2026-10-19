"""The repro of prism-monorepo issue 258: a speaker who introduces themselves.

The batched response in ``fixtures/extraction/trip_batch_haiku45.json`` is what
Claude Haiku 4.5 returned for the four turns of the issue's repro, recorded once.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from prme import MemoryEngine
from prme.ingestion.schema import ExtractionResult
from prme.models.speaker import introduced_speaker
from prme.types import EdgeType, NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "extraction" / "trip_batch_haiku45.json").read_text())
T0 = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
CITIES = "We chose Tokyo, Kyoto, Kanazawa, and Osaka."
DROPPED = "We dropped Hiroshima to save time."
INTRODUCTION = "Hi, I am Alex Rivera."


def batched(base, **updates):
    """The tests' per-turn configuration, set back to the batched extraction a hosted project runs."""
    return base.model_copy(update={"extraction_batch_target_tokens": 2048, **updates})


async def ingest_trip(engine, user):
    """The issue's four turns, extracted in one batched call that answers with the recorded response."""
    by_text = {turn["content"]: entry for turn, entry in zip(RECORDED["turns"], RECORDED["response"]["turns"])}

    async def extract_batch(turns):
        return {"turns": [{**by_text[turn.content], "turn": turn.index} for turn in turns]}

    engine._pipeline._extraction_provider.extract_batch = extract_batch
    messages = [{"content": turn["content"], "role": turn["role"], "event_time": T0 + timedelta(minutes=i)}
                for i, turn in enumerate(RECORDED["turns"])]
    return await engine.ingest_batch(messages, user_id=user, session_id="trip-1", wait_for_extraction=True)


async def claims(engine, user, event_id):
    """The claims of one event, each with the entity its HAS_FACT edge comes from."""
    found = []
    for node in await engine.query_nodes(user_id=user):
        if node.node_type == NodeType.ENTITY or UUID(event_id) not in node.evidence_refs:
            continue
        [edge] = await engine._graph_store.get_edges(target_id=str(node.id), edge_type=EdgeType.HAS_FACT)
        found.append((node, await engine.get_node(str(edge.source_id), include_superseded=True, user_id=user)))
    return found


class TestIntroducedSpeaker:
    PEOPLE = ["Alex Rivera", "Jordan"]

    @pytest.mark.parametrize(("text", "expected"), [
        ("Hi, I am Alex Rivera. My partner Jordan and I are planning a trip.", "Alex Rivera"),
        ("I'm Alex Rivera from Denver.", "Alex Rivera"),
        ("Hello! My name is Alex Rivera and I like tea.", "Alex Rivera"),
        ("Hi I’m Alex Rivera!", "Alex Rivera"),
        ("I am Jordan's partner.", None),
        ("I am not Alex Rivera.", None),
        ("I am tired of trains.", None),
        ("I am Alex Rivera and later in this letter my name is Jordan.", None),
        ("My partner Jordan and I are planning a trip.", None),
    ])
    def test_a_listed_person_the_turn_says_it_is(self, text, expected):
        assert introduced_speaker(text, self.PEOPLE) == expected

    def test_an_unlisted_name_is_not_a_speaker(self):
        assert introduced_speaker("I am Alex Rivera.", []) is None
        assert introduced_speaker("I am Canadian.", ["Jordan"]) is None

    def test_the_longest_listed_name_wins(self):
        assert introduced_speaker("I am Alex Rivera Jr.", ["Alex", "Alex Rivera"]) == "Alex Rivera"
        assert introduced_speaker("I am Alex Rivera Jr.", ["Alex"]) == "Alex"


class TestWhoseIsI:
    """The introduction binds I only where no caller or declaration already says whose it is."""

    def extractor(self):
        fact = {"subject": "I", "predicate": "works_at", "object": "Northwind", "object_entity_type": "organization",
                "polarity": "positive", "evidence_quote": "I work at Northwind."}
        return AsyncMock(return_value=ExtractionResult.model_validate({
            "entities": [{"name": "Alex Rivera", "entity_type": "person"},
                         {"name": "Northwind", "entity_type": "organization"}], "facts": [fact]}))

    async def subject_of_claim(self, base, user, *, role="user", **kwargs):
        content = f"{INTRODUCTION} I work at Northwind."
        async with MemoryEngine.open(base) as engine:
            engine._pipeline._extraction_provider.extract = self.extractor()
            event_id = await engine.ingest(content, user_id=user, session_id="s1", role=role,
                                           wait_for_extraction=True, **kwargs)
            [(node, subject)] = await claims(engine, user, event_id)
        return node, subject

    async def test_the_name_in_the_turn_binds_its_i(self, config, user):
        node, subject = await self.subject_of_claim(config, user)
        assert subject.content == "Alex Rivera" and node.metadata["speaker_reference"]["basis"] == "self_introduction"

    async def test_a_name_the_caller_gives_wins(self, config, user):
        node, subject = await self.subject_of_claim(config, user, speaker="Dana")
        assert subject.content == "Dana" and "basis" not in node.metadata["speaker_reference"]

    async def test_a_declared_owner_wins(self, config, user):
        node, subject = await self.subject_of_claim(config, user, first_person_owner=True)
        assert subject.content == "I" and "speaker_reference" not in node.metadata and "owner_reference" in node.metadata

    async def test_an_assistant_turn_names_no_user(self, config, user):
        node, subject = await self.subject_of_claim(config, user, role="assistant")
        assert subject.content == "I" and "speaker_reference" not in node.metadata

    async def test_it_is_off_with_speaker_references(self, config, user):
        node, subject = await self.subject_of_claim(config.model_copy(update={"enable_speaker_references": False}), user)
        assert subject.content == "I" and "speaker_reference" not in node.metadata


class TestTheRepro:
    """The issue's four turns, with the model's recorded answer."""

    async def test_the_claims_name_the_speaker_who_introduces_themselves(self, config, user):
        async with MemoryEngine.open(batched(config)) as engine:
            event_ids = await ingest_trip(engine, user)
            found = await claims(engine, user, event_ids[0])
            entities = {node.content for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)}
        by_object = {node.metadata["object"]: (node, subject) for node, subject in found}
        # A claim about the partner and a claim about the trip.
        assert set(by_object) == {"Jordan", "Japan in late March next year"}
        for node, subject in by_object.values():
            # The claim keeps the literal I; the entity it is linked to is Alex Rivera.
            assert node.metadata["subject"] == "I"
            assert subject.content == "Alex Rivera" and subject.metadata["entity_type"] == "person"
            assert node.metadata["speaker_reference"] == {
                "speaker": "Alex Rivera", "fields": ["subject"], "basis": "self_introduction"}
        # The introduced turn has no entity of its own named I; a later turn, which names nobody, keeps its own.
        assert {"Alex Rivera", "Jordan", "Japan", "I"} <= entities
        assert len([name for name in entities if name == "I"]) == 1

    async def test_a_turn_that_names_no_one_keeps_its_own_i(self, config, user):
        async with MemoryEngine.open(batched(config)) as engine:
            event_ids = await ingest_trip(engine, user)
            found = await claims(engine, user, event_ids[3])
        assert found
        for node, subject in found:
            assert subject.content == "I" and subject.metadata["identity_status"] == "unresolved_reference"
            assert "speaker_reference" not in node.metadata

    async def test_the_turn_with_we_has_claims_for_the_cities_and_the_dropped_city(self, config, user):
        async with MemoryEngine.open(batched(config)) as engine:
            event_ids = await ingest_trip(engine, user)
            record = await engine.get_extraction(event_ids[2], user_id=user)
            found = await claims(engine, user, event_ids[2])
        assert [fact["object"] for fact in record.result["facts"]] == [
            "Tokyo, Kyoto, Kanazawa, and Osaka", "Hiroshima to save time"]
        assert sorted(node.content for node, _ in found) == sorted([CITIES, DROPPED])
        # A plural reference stays local to its message: the group is We, not a person.
        for node, subject in found:
            assert subject.content == "We" and subject.metadata["identity_status"] == "unresolved_reference"
            assert "speaker_reference" not in node.metadata
