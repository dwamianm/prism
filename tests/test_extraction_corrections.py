"""Corrections the extractor names among earlier memories (enable_extraction_corrections, #277)."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.extraction import (
    BATCH_EXTRACTION_SYSTEM_PROMPT,
    BatchTurn,
    CorrectionMemory,
    _BatchExtractionResult,
    _CorrectingBatchExtractionResult,
    batch_request_messages,
)
from prme.ingestion.supersedence import SupersedenceDetector
from prme.types import LifecycleState, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 3, 2, 18, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=43)


def fact(subject, predicate, obj, quote, **extra):
    return {"subject": subject, "predicate": predicate, "object": obj, "polarity": "positive",
            "epistemic_type": "observed", "evidence_quote": quote, **extra}


class Scripted:
    """A batched model with one scripted response per turn text.

    ``answers`` maps a turn's text to its facts, or to a function of the
    memories the request showed that returns them, which is how a response
    names a shown memory's label.
    """

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    async def extract_batch(self, turns, **extra):
        self.requests.append(extra)
        memories = extra.get("memories", [])
        entries = []
        for turn in turns:
            answer = self.answers.get(turn.content, [])
            facts = answer(memories) if callable(answer) else answer
            entities = [{"name": name, "entity_type": "person"} for name in dict.fromkeys(f["subject"] for f in facts)]
            entries.append({"turn": turn.index, "entities": entities, "facts": facts, "relationships": []})
        return {"turns": entries}


def corrections(base, *, on=True):
    return base.model_copy(update={"extraction_batch_target_tokens": 512, "enable_extraction_corrections": on})


def label_of(memories, word):
    return next(memory.label for memory in memories if word in memory.claim)


OLD = "My sister Jo's birthday is March 3."
NEW = "Sorry, I gave you Jo's birthday wrong. Jo's birthday is actually March 13."
OLD_FACT = fact("Jo", "birthday", "March 3", OLD, temporal_intent="assertion")


def correction(**extra):
    """The corrected birthday, naming the shown memory about it."""
    return lambda memories: [fact("Jo", "birthday", "March 13", NEW, temporal_intent="update",
                                  replaces_memory=label_of(memories, "birthday"), **extra)]


async def say(engine, model, text, *, at, scope=Scope.PERSONAL, owner):
    engine._pipeline._extraction_provider.extract_batch = model.extract_batch
    return await engine.ingest(text, user_id=owner, session_id=f"s-{at.isoformat()}", event_time=at,
                               scope=scope, wait_for_extraction=True)


async def birthdays(engine, owner):
    nodes = await engine.query_nodes(user_id=owner, node_type=NodeType.FACT, lifecycle_states=list(LifecycleState))
    return {node.metadata["object"]: node for node in nodes if node.metadata["predicate"] == "birthday"}


async def batch_records(engine):
    rows = engine._conn.execute(
        "SELECT payload FROM operations WHERE op_type = 'EXTRACTION_BATCH_RECORDED' ORDER BY created_at"
    ).fetchall() if engine._conn is not None else []
    return [json.loads(json.loads(row[0])["batch"]) for row in rows]


class TestTheOption:
    def test_it_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("PRME_EXTRACTION_BATCH_TARGET_TOKENS", raising=False)
        assert PRMEConfig().enable_extraction_corrections is False

    def test_it_needs_batched_extraction(self):
        with pytest.raises(ValidationError, match="extraction_batch_target_tokens above zero"):
            PRMEConfig(enable_extraction_corrections=True, extraction_batch_target_tokens=0)


class TestTheRequest:
    TURNS = [BatchTurn(1, "user", "I paint.", speaker="Caro", time=datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc))]
    MEMORIES = [CorrectionMemory(label="m1", node_id="00000000-0000-4000-8000-000000000277",
                                 claim="I planning_to_move_to Austin",
                                 text="Big news: I'm planning\nto move to Austin this summer.",
                                 time=T0, speaker="Riley")]

    def test_without_memories_the_request_and_schema_keep_their_bytes(self):
        # Digests taken on main before #277.
        messages = json.dumps(batch_request_messages(self.TURNS))
        assert hashlib.sha256(messages.encode()).hexdigest() == \
            "44affe0ca785b677e830d2d49ef9e87bd47d73f9066baad6384cb9a5ef7e8464"
        # The schema digest leaves out pydantic's rendering of the one Decimal
        # field, which differs between pydantic 2.12 and 2.14. The raw digest
        # under the locked pydantic 2.12.5 was 57d60830...e93f.
        from tests.test_batched_extraction import _version_independent_schema
        schema = json.dumps(_version_independent_schema(_BatchExtractionResult.model_json_schema()), sort_keys=True)
        assert hashlib.sha256(schema.encode()).hexdigest() == \
            "1012016c5ba4a376e59f9e7e9ad5867c3f3520d1ecfe761e8eeba320154a28d6"
        assert "replaces_memory" not in schema

    def test_memories_add_guidance_a_reference_message_and_the_field(self):
        system, memories, turns = batch_request_messages(self.TURNS, self.MEMORIES)
        assert system["content"].startswith(BATCH_EXTRACTION_SYSTEM_PROMPT)
        assert "EARLIER MEMORIES" in system["content"]
        assert memories == {"role": "user", "content": (
            "Earlier memories, for reference only:\n\n"
            '[m1] (2026-03-02, said by Riley) I planning_to_move_to Austin. '
            'Text: "Big news: I\'m planning to move to Austin this summer."')}
        assert turns == batch_request_messages(self.TURNS)[1]
        assert "replaces_memory" in json.dumps(_CorrectingBatchExtractionResult.model_json_schema())
        assert "00000000-0000-4000-8000-000000000277" not in json.dumps([system, memories, turns])


class TestIngest:
    async def test_off_shows_no_memories_and_saves_no_new_fields(self, config, user):
        # A model that names a label anyway, though none was shown.
        model = Scripted({OLD: [OLD_FACT], NEW: [fact("Jo", "birthday", "March 13", NEW,
                                                      temporal_intent="update", replaces_memory="m1")]})
        async with MemoryEngine.open(corrections(config, on=False)) as engine:
            await say(engine, model, OLD, at=T0, owner=user)
            event_id = await say(engine, model, NEW, at=LATER, owner=user)
            assert model.requests == [{}, {}]
            facts = await birthdays(engine, user)
            assert facts["March 3"].lifecycle_state == LifecycleState.TENTATIVE
            record = await engine.get_extraction(event_id, user_id=user)
            assert all("replaces_memory" not in item and "replaces_node_id" not in item
                       for item in record.result["facts"])
            assert all("memories" not in batch for batch in await batch_records(engine))

    async def test_a_named_correction_retires_the_earlier_claim(self, config, user):
        model = Scripted({OLD: [OLD_FACT], NEW: correction()})
        async with MemoryEngine.open(corrections(config)) as engine:
            await say(engine, model, OLD, at=T0, owner=user)
            event_id = await say(engine, model, NEW, at=LATER, owner=user)
            facts = await birthdays(engine, user)
            old, new = facts["March 3"], facts["March 13"]
            assert old.lifecycle_state == LifecycleState.SUPERSEDED and old.superseded_by == new.id
            assert new.lifecycle_state == LifecycleState.TENTATIVE
            shown = model.requests[-1]["memories"]
            assert [(memory.node_id, memory.claim) for memory in shown] == [(str(old.id), "Jo birthday March 3")]
            record = await engine.get_extraction(event_id, user_id=user)
            assert record.result["facts"][0]["replaces_node_id"] == str(old.id)
            assert record.result["facts"][0]["replaces_memory"] == "m1"
            if engine._conn is not None:
                assert (await batch_records(engine))[-1]["memories"] == [{"label": "m1", "node_id": str(old.id)}]
            # The retired claim leaves default retrieval; its source message stays.
            assert await engine.get_node(str(old.id), user_id=user) is None

    @pytest.mark.parametrize("answer", ["unshown label", "model node id", "assertion", "conditional", "hypothetical"])
    async def test_a_correction_the_checks_refuse_retires_nothing(self, config, user, answer):
        def respond(memories):
            label = label_of(memories, "birthday")
            extra = {
                "unshown label": {"replaces_memory": "m9"},
                "model node id": {"replaces_node_id": memories[0].node_id},
                "assertion": {"replaces_memory": label, "temporal_intent": "assertion"},
                "conditional": {"replaces_memory": label, "epistemic_type": "conditional",
                                "condition": "Jo's birthday is actually March 13"},
                "hypothetical": {"replaces_memory": label, "epistemic_type": "hypothetical"},
            }[answer]
            return [{**fact("Jo", "birthday", "March 13", NEW, temporal_intent="update"), **extra}]

        model = Scripted({OLD: [OLD_FACT], NEW: respond})
        async with MemoryEngine.open(corrections(config)) as engine:
            await say(engine, model, OLD, at=T0, owner=user)
            event_id = await say(engine, model, NEW, at=LATER, owner=user)
            facts = await birthdays(engine, user)
            assert facts["March 3"].lifecycle_state == LifecycleState.TENTATIVE
            assert facts["March 13"].lifecycle_state == LifecycleState.TENTATIVE
            [saved] = (await engine.get_extraction(event_id, user_id=user)).result["facts"]
            # A label the request did not show, or a node ID from the model, names no target.
            named = answer not in {"unshown label", "model node id"}
            assert ("replaces_node_id" in saved) == named

    async def test_only_the_owner_s_scope_s_earlier_claims_are_shown(self, config, user):
        model = Scripted({OLD: [OLD_FACT], NEW: lambda memories: []})
        async with MemoryEngine.open(corrections(config)) as engine:
            await say(engine, model, OLD, at=T0, owner=user + "-other")
            await say(engine, model, OLD, at=T0, owner=user, scope=Scope.PROJECT)
            await say(engine, model, NEW, at=LATER, owner=user)
            assert model.requests[-1] == {}

    async def test_a_claim_effective_after_the_turn_is_not_shown(self, config, user):
        model = Scripted({OLD: [OLD_FACT], NEW: lambda memories: []})
        async with MemoryEngine.open(corrections(config)) as engine:
            await say(engine, model, OLD, at=LATER, owner=user)
            await say(engine, model, NEW, at=T0, owner=user)
            assert model.requests[-1] == {}

    async def test_a_claim_named_twice_is_retired_once(self, config, user):
        twice = lambda memories: [  # noqa: E731
            fact("Jo", "birthday", "March 13", NEW, temporal_intent="update",
                 replaces_memory=label_of(memories, "birthday")),
            fact("Jo", "birthday_is", "March 13", NEW, temporal_intent="update",
                 replaces_memory=label_of(memories, "birthday")),
        ]
        model = Scripted({OLD: [OLD_FACT], NEW: twice})
        async with MemoryEngine.open(corrections(config)) as engine:
            await say(engine, model, OLD, at=T0, owner=user)
            await say(engine, model, NEW, at=LATER, owner=user)
            facts = await birthdays(engine, user)
            assert facts["March 3"].lifecycle_state == LifecycleState.SUPERSEDED
            assert facts["March 13"].lifecycle_state == LifecycleState.TENTATIVE


class TestTheCheck:
    async def test_a_later_earlier_claim_or_another_source_type_is_kept(self, config, user):
        model = Scripted({OLD: [OLD_FACT], NEW: [fact("Jo", "birthday", "March 13", NEW, temporal_intent="update")]})
        async with MemoryEngine.open(corrections(config)) as engine:
            await say(engine, model, OLD, at=LATER, owner=user)
            await say(engine, model, NEW, at=T0, owner=user)
            facts = await birthdays(engine, user)
            old, new = facts["March 3"], facts["March 13"]
            detector = SupersedenceDetector(engine._graph_store, engine._pipeline._supersedence_detector._graph_writer)
            assert await detector.apply_named_correction(str(new.id), str(old.id), user_id=user,
                                                         evidence_event_id=str(new.evidence_refs[0])) == []
            assert await detector.apply_named_correction(str(new.id), str(old.id), user_id=user + "-other",
                                                         evidence_event_id=str(new.evidence_refs[0])) == []
            assert (await birthdays(engine, user))["March 3"].lifecycle_state == LifecycleState.TENTATIVE
