"""enable_owner_name_carry: a user turn that names nobody keeps the name the owner gave earlier.

Every test runs on DuckDB and, with PRME_TEST_DATABASE_URL set, on PostgreSQL,
and none calls a model: the extractor answers from a table of recorded results.
"""

from datetime import datetime, timedelta, timezone

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.schema import ExtractionResult
from prme.types import NodeType
from tests import test_durable_ingestion
from tests.test_ingest_names_and_claims import claims

config = test_durable_ingestion.config
user = test_durable_ingestion.user

INTRODUCTION = "Hi, I am Alex Rivera. I work at Northwind."
CASEY = "Hello, I am Casey Lee. I work at Brightpath."
PREFERENCE = "I prefer trains to flights."
WEEKEND = "On weekends I am learning Rust."
THANKS = "Thanks, that helps."
BOTH = "I prefer trains and I avoid flights."


def result(entities, subject, predicate, obj, quote, object_type=None):
    fact = {"subject": subject, "predicate": predicate, "object": obj, "object_entity_type": object_type,
            "polarity": "positive", "evidence_quote": quote}
    return ExtractionResult.model_validate({"entities": entities, "facts": [fact]})


def person(name):
    return {"name": name, "entity_type": "person"}


RESULTS = {
    INTRODUCTION: result([person("Alex Rivera"), {"name": "Northwind", "entity_type": "organization"}],
                         "I", "works_at", "Northwind", "I work at Northwind.", "organization"),
    CASEY: result([person("Casey Lee"), {"name": "Brightpath", "entity_type": "organization"}],
                  "I", "works_at", "Brightpath", "I work at Brightpath.", "organization"),
    PREFERENCE: result([{"name": "trains", "entity_type": "concept"}],
                       "I", "prefers", "trains", PREFERENCE, "concept"),
    WEEKEND: result([{"name": "Rust", "entity_type": "concept"}],
                    "I", "is_learning", "Rust", WEEKEND, "concept"),
    "Alex Rivera sent the report.": result([person("Alex Rivera")], "Alex Rivera", "sent", "the report",
                                          "Alex Rivera sent the report.", "concept"),
    THANKS: ExtractionResult(),
    # Two claims cite the same sentence, so the second gets a statement of its own.
    BOTH: ExtractionResult.model_validate({
        "entities": [{"name": "trains", "entity_type": "concept"}, {"name": "flights", "entity_type": "concept"}],
        "facts": [{"subject": "I", "predicate": "prefers", "object": "trains", "object_entity_type": "concept",
                   "polarity": "positive", "evidence_quote": BOTH},
                  {"subject": "I", "predicate": "avoids", "object": "flights", "object_entity_type": "concept",
                   "polarity": "positive", "evidence_quote": BOTH}]}),
}


def on(base, **updates):
    """The configuration with the introduction binding and the carry turned on."""
    return base.model_copy(update={"enable_ingest_claim_fixes": True, "enable_owner_name_carry": True, **updates})


class Chat:
    """An engine whose extractor answers from RESULTS and counts its requests."""

    def __init__(self, engine):
        self.engine = engine
        self.requests = []

        async def extract(content, *, role="user", **options):
            self.requests.append((content, role, options))
            return RESULTS[content]

        engine._pipeline._extraction_provider.extract = extract

    async def say(self, content, user, *, role="user", session_id="s1", **kwargs):
        event_id = await self.engine.ingest(content, user_id=user, role=role, session_id=session_id,
                                            wait_for_extraction=True, **kwargs)
        return await claims(self.engine, user, event_id)


def subjects(found):
    """What each claim is attached to: the subject entity's content, status and node ID."""
    return [(subject.content, subject.metadata.get("identity_status"), str(subject.id)) for _, subject in found]


async def entity_names(engine, user):
    return sorted(node.content for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY))


class TestCarry:
    async def test_a_later_turn_that_names_nobody_attaches_to_the_introduced_owner(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            first = await chat.say(INTRODUCTION, user)
            later = await chat.say(PREFERENCE, user)
            [(node, subject)] = later
            names = await entity_names(engine, user)
        [(_, _, owner)] = subjects(first)
        assert subjects(later) == [("Alex Rivera", None, owner)]
        # The claim keeps its literal I and says whose it is.
        assert node.metadata["subject"] == "I"
        assert node.metadata["speaker_reference"] == {"speaker": "Alex Rivera", "fields": ["subject"],
                                                      "basis": "owner_name"}
        assert subject.metadata["entity_type"] == "person"
        # No entity named I was made, for either turn.
        assert "I" not in names and names.count("Alex Rivera") == 1

    async def test_it_carries_across_sessions_and_through_turns_without_claims(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            [(_, _, owner)] = subjects(await chat.say(INTRODUCTION, user, session_id="s1"))
            await chat.say(THANKS, user, session_id="s1")
            await chat.say(THANKS, user, role="assistant", session_id="s2")
            later = await chat.say(WEEKEND, user, session_id="s2")
        assert subjects(later) == [("Alex Rivera", None, owner)]

    async def test_a_caller_speaker_on_a_user_turn_names_the_owner_for_later_turns(self, config, user):
        async with MemoryEngine.open(on(config, enable_ingest_claim_fixes=False)) as engine:
            chat = Chat(engine)
            # The turn lists no one and has no claim about its speaker, yet the owner has their entity.
            assert await chat.say(THANKS, user, speaker="Dana") == []
            later = await chat.say(PREFERENCE, user)
            names = await entity_names(engine, user)
        assert [name for name, _, _ in subjects(later)] == ["Dana"]
        assert names.count("Dana") == 1 and "I" not in names

    async def test_a_later_name_replaces_the_earlier_one_for_the_turns_after_it(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            first = await chat.say(INTRODUCTION, user)
            before = await chat.say(PREFERENCE, user)
            second = await chat.say(CASEY, user)
            after = await chat.say(WEEKEND, user)
            # An earlier claim is not rewritten.
            [(old_node, old_subject)] = await claims(engine, user, str(before[0][0].evidence_refs[0]))
        assert [name for name, _, _ in subjects(first)] == ["Alex Rivera"]
        assert [name for name, _, _ in subjects(before)] == ["Alex Rivera"]
        assert [name for name, _, _ in subjects(second)] == ["Casey Lee"]
        assert [name for name, _, _ in subjects(after)] == ["Casey Lee"]
        assert old_subject.content == "Alex Rivera"
        assert old_node.metadata["speaker_reference"]["speaker"] == "Alex Rivera"

    async def test_a_carried_name_stays_out_of_the_text_of_later_claims(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            later = await chat.say(BOTH, user)
        assert [name for name, _, _ in subjects(later)] == ["Alex Rivera", "Alex Rivera"]
        assert sorted(node.content for node, _ in later) == [BOTH, "The speaker avoids flights."]
        assert {node.metadata["speaker_reference"]["basis"] for node, _ in later} == {"owner_name"}

    async def test_the_extractor_is_asked_exactly_as_without_the_option(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            await chat.say(PREFERENCE, user)
        async with MemoryEngine.open(config) as engine:
            plain = Chat(engine)
            await plain.say(INTRODUCTION, user)
            await plain.say(PREFERENCE, user)
        assert [call[0] for call in chat.requests] == [INTRODUCTION, PREFERENCE]
        assert [call[2] for call in chat.requests] == [{"plural_first_person": True}] * 2
        assert [call[2] for call in plain.requests] == [{}] * 2


class TestBatchedExtraction:
    async def test_it_carries_between_the_turns_of_one_batched_request(self, config, user):
        async with MemoryEngine.open(config.model_copy(update={"extraction_batch_target_tokens": 2048,
                                                               "enable_ingest_claim_fixes": True,
                                                               "enable_owner_name_carry": True})) as engine:
            requests = []

            async def extract_batch(turns, **options):
                requests.append([turn.content for turn in turns])
                return {"turns": [{**RESULTS[turn.content].model_dump(mode="json"), "turn": turn.index}
                                  for turn in turns]}

            engine._pipeline._extraction_provider.extract_batch = extract_batch
            start = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
            messages = [{"content": content, "role": "user", "event_time": start + timedelta(minutes=index)}
                        for index, content in enumerate([INTRODUCTION, THANKS, PREFERENCE])]
            event_ids = await engine.ingest_batch(messages, user_id=user, session_id="s1", wait_for_extraction=True)
            first = await claims(engine, user, event_ids[0])
            later = await claims(engine, user, event_ids[2])
        assert requests == [[INTRODUCTION, THANKS, PREFERENCE]]
        assert subjects(later)[0][0] == "Alex Rivera" and subjects(later)[0][2] == subjects(first)[0][2]


class TestScope:
    """Only the owner's own user turns carry the name."""

    @pytest.mark.parametrize("role", ["participant", "assistant", "tool", "system"])
    async def test_a_turn_of_another_role_keeps_its_own_i(self, config, user, role):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            later = await chat.say(PREFERENCE, user, role=role)
        assert [name for name, _, _ in subjects(later)] == ["I"]
        assert subjects(later)[0][1] == "unresolved_reference"
        assert "speaker_reference" not in later[0][0].metadata

    async def test_a_participants_name_is_not_the_owners(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say(THANKS, user, role="participant", speaker="Sam")
            later = await chat.say(PREFERENCE, user)
            names = await entity_names(engine, user)
        assert [name for name, _, _ in subjects(later)] == ["I"]
        assert "Sam" not in names

    async def test_another_owner_does_not_get_the_name(self, config, user):
        other = f"{user}-other"
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            later = await chat.say(PREFERENCE, other)
        assert [name for name, _, _ in subjects(later)] == ["I"]

    async def test_a_name_no_turn_gave_is_never_invented(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            first = await chat.say(PREFERENCE, user)
            second = await chat.say(WEEKEND, user)
        assert [name for name, _, _ in subjects(first + second)] == ["I", "I"]

    async def test_a_person_an_assistant_turn_named_is_not_the_owner(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say("Alex Rivera sent the report.", user, role="assistant")
            await chat.say(THANKS, user)
            later = await chat.say(PREFERENCE, user)
        assert [name for name, _, _ in subjects(later)] == ["I"]

    async def test_a_callers_speaker_or_declared_owner_wins_on_a_later_turn(self, config, user):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            named = await chat.say(PREFERENCE, user, speaker="Dana")
            declared = await chat.say(WEEKEND, user, first_person_owner=True)
        assert [name for name, _, _ in subjects(named)] == ["Dana"]
        assert "basis" not in named[0][0].metadata["speaker_reference"]
        assert [name for name, _, _ in subjects(declared)] == ["I"]
        assert "owner_reference" in declared[0][0].metadata


class TestOff:
    def test_the_option_is_off_and_left_out_of_dumps(self):
        default = PRMEConfig(_env_file=None)
        assert default.enable_owner_name_carry is False
        assert "enable_owner_name_carry" not in default.model_dump(mode="json")
        turned_on = PRMEConfig(_env_file=None, enable_owner_name_carry=True)
        assert turned_on.model_dump(mode="json")["enable_owner_name_carry"] is True

    async def test_without_it_a_later_turn_keeps_its_own_i_and_no_entity_is_marked(self, config, user):
        async with MemoryEngine.open(on(config, enable_owner_name_carry=False)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            later = await chat.say(PREFERENCE, user)
            entities = await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
        assert [name for name, _, _ in subjects(later)] == ["I"]
        assert not any("owner_name" in (node.metadata or {}) for node in entities)

    async def test_it_needs_speaker_references(self, config, user):
        async with MemoryEngine.open(on(config, enable_speaker_references=False)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            later = await chat.say(PREFERENCE, user)
        assert [name for name, _, _ in subjects(later)] == ["I"]


class TestFailure:
    async def test_a_failed_lookup_leaves_the_turn_with_its_own_i(self, config, user, monkeypatch):
        async with MemoryEngine.open(on(config)) as engine:
            chat = Chat(engine)
            await chat.say(INTRODUCTION, user)
            scan = engine._graph_store.scan_nodes
            failures = []

            async def scan_nodes(*args, **kwargs):
                # The first scan of a turn that has an I is the owner's name lookup.
                if not failures:
                    failures.append(1)
                    raise RuntimeError("the graph is unavailable")
                return await scan(*args, **kwargs)

            monkeypatch.setattr(engine._graph_store, "scan_nodes", scan_nodes)
            later = await chat.say(PREFERENCE, user)
            after = await chat.say(WEEKEND, user)
        assert failures and [name for name, _, _ in subjects(later)] == ["I"]
        # The next turn finds the name again.
        assert [name for name, _, _ in subjects(after)] == ["Alex Rivera"]
