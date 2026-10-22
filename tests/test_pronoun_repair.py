"""A claim that kept a pronoun is repaired from the owner's earlier memories, and no other claim changes."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.errors import ExtractionError
from prme.ingestion.pronoun_repair import (
    PronounClaim,
    WindowTurn,
    apply_repairs,
    flagged_claims,
    repair_messages,
    repair_request_sha256,
    resolved_from_context,
)
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.types import NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 3, 2, 9, tzinfo=timezone.utc)
PLAN = "Alex and Jordan are planning a trip to Japan in April."
CITIES = "They chose Tokyo, Kyoto, Kanazawa, and Osaka. They dropped Hiroshima to save time."
OTHER = "Priya manages the observability rewrite."


def fact(subject, predicate, value, quote, *, subject_type="person", object_type=None):
    return ExtractedFact(subject=subject, subject_entity_type=subject_type, predicate=predicate, object=value,
                         object_entity_type=object_type, evidence_quote=quote)


def result_of(*facts, entities=()):
    return ExtractionResult(entities=list(entities), facts=list(facts))


CHOSE = fact("They", "chose", "Tokyo, Kyoto, Kanazawa, and Osaka", CITIES)
DROPPED = fact("They", "dropped", "Hiroshima", CITIES)
WINDOW = [WindowTurn("w1", None, PLAN)]


class TestFlaggedClaims:
    def test_a_claim_on_a_third_person_pronoun_is_flagged_with_its_own_sentence(self):
        flagged = flagged_claims(result_of(CHOSE, DROPPED), CITIES)
        assert [(item.index, item.fact_index, item.fields, item.sentence) for item in flagged] == [
            (1, 0, ("subject",), "They chose Tokyo, Kyoto, Kanazawa, and Osaka."),
            (2, 1, ("subject",), "They dropped Hiroshima to save time."),
        ]

    def test_a_named_subject_is_not_flagged(self):
        named = fact("Priya", "manages", "the observability rewrite", OTHER)
        assert flagged_claims(result_of(named), OTHER) == []

    def test_a_first_person_singular_claim_is_never_flagged(self):
        mine = fact("I", "chose", "Tokyo", "I chose Tokyo.")
        assert flagged_claims(result_of(mine), "I chose Tokyo.") == []

    def test_a_first_person_plural_claim_is_never_flagged(self):
        # The group "we" means is not stated by the memory: "We use Tableau at Northwind" is not "Dana and Priya".
        for pronoun in ("We", "us", "our"):
            ours = fact(pronoun, "chose", "Tokyo", f"{pronoun} chose Tokyo.")
            assert flagged_claims(result_of(ours), f"{pronoun} chose Tokyo.") == []

    def test_an_object_pronoun_is_flagged_as_the_object(self):
        told = fact("Priya", "told", "them", "Priya told them.", object_type="person")
        (flagged,) = flagged_claims(result_of(told), "Priya told them.")
        assert flagged.fields == ("object",)

    def test_a_pronoun_typed_as_a_thing_is_not_flagged(self):
        stack = fact("It", "is", "Rust", "It is Rust.", subject_type="technology")
        assert flagged_claims(result_of(stack), "It is Rust.") == []

    def test_a_claim_with_resolved_text_or_no_shared_sentence_is_left_alone(self):
        done = CHOSE.model_copy(update={"resolved_text": "Alex chose Tokyo."})
        assert flagged_claims(result_of(done), CITIES) == []
        unshared = fact("They", "chose", "Osaka", "They left.")
        assert flagged_claims(result_of(unshared), "They left.") == []
        # Two sentences that together mention both are the claim's own sentences.
        spread = fact("They", "chose", "Osaka", "They left. Osaka was chosen.")
        (flagged,) = flagged_claims(result_of(spread), "They left. Osaka was chosen.")
        assert flagged.sentence == "They left. Osaka was chosen."


def claim(**changes):
    values = dict(index=1, subject="They", predicate="chose", object="Tokyo, Kyoto, Kanazawa, and Osaka",
                  fields=("subject",), sentence="They chose Tokyo, Kyoto, Kanazawa, and Osaka.", fact_index=0)
    return PronounClaim(**{**values, **changes})


def answer(*names, field="subject", index=1, kind="person"):
    return {"claims": [{"claim": index, field: [{"name": name, "entity_type": kind} for name in names]}]}


def repair(result, claims, raw, *, window=WINDOW, speaker=None, text=CITIES):
    return apply_repairs(result, claims, raw, source_text=text, source_event_id="e1", speaker=speaker, window=window)


class TestApplyRepairs:
    def test_a_group_pronoun_becomes_its_names_in_the_claims_own_sentence(self):
        result = result_of(CHOSE, DROPPED)
        repaired = repair(result, [claim()], answer("Alex", "Jordan"))
        first, second = repaired.facts
        assert first.resolved_text == "Alex and Jordan chose Tokyo, Kyoto, Kanazawa, and Osaka."
        # The quote is evidence as it was, a group stays on its pronoun, and its members are listed.
        assert (first.evidence_quote, first.subject) == (CITIES, "They")
        assert [entity.name for entity in repaired.entities] == ["Alex", "Jordan"]
        assert first.resolution["window_event_ids"] == ["w1"]
        assert resolved_from_context(first.resolution) == {
            "event_ids": ["w1"], "fields": {"subject": {"pronoun": "They", "names": ["Alex", "Jordan"]}}}
        # The other claim of the message is exactly as it was.
        assert second == DROPPED

    def test_one_name_rebinds_the_field_to_that_entity(self):
        result = result_of(fact("She", "manages", "the observability rewrite", "She manages the observability rewrite."))
        window = [WindowTurn("w1", None, "Priya joined the platform team.")]
        repaired = repair(result, [claim(subject="She", predicate="manages", object="the observability rewrite",
                                         sentence="She manages the observability rewrite.")],
                          answer("Priya"), window=window, text="She manages the observability rewrite.")
        (only,) = repaired.facts
        assert (only.subject, only.subject_entity_type) == ("Priya", "person")
        assert only.resolved_text == "Priya manages the observability rewrite."
        assert [(entity.name, entity.entity_type) for entity in repaired.entities] == [("Priya", "person")]

    def test_a_possessive_pronoun_takes_an_apostrophe(self):
        result = result_of(fact("their", "trip", "Japan", "Their trip is Japan."))
        repaired = repair(result, [claim(subject="their", predicate="trip", object="Japan",
                                         sentence="Their trip is Japan.")],
                          answer("Alex", "Jordan"), text="Their trip is Japan.")
        assert repaired.facts[0].resolved_text == "Alex and Jordan's trip is Japan."

    def test_a_listed_entity_keeps_its_type(self):
        result = result_of(CHOSE, entities=[ExtractedEntity(name="Alex", entity_type="person")])
        repaired = repair(result, [claim()], answer("Alex", "Jordan", kind="organization"))
        assert [(entity.name, entity.entity_type) for entity in repaired.entities] == [
            ("Alex", "person"), ("Jordan", "organization")]

    @pytest.mark.parametrize("names", [
        ["Alex", "Taylor"],        # Taylor is written nowhere
        ["alex"],                  # not a proper name
        ["the team"],              # a description
        ["Them"],                  # a pronoun
        ["Alex", "Alex"],          # the same name twice
        [],                        # the model does not know
    ])
    def test_a_name_written_nowhere_a_pronoun_or_a_guess_repairs_nothing(self, names):
        result = result_of(CHOSE)
        assert repair(result, [claim()], answer(*names)) == result

    def test_a_name_written_only_in_the_speaker_line_or_the_message_is_accepted(self):
        result = result_of(CHOSE)
        by_speaker = repair(result, [claim()], answer("Sam"), window=[], speaker="Sam Lindqvist")
        assert by_speaker.facts[0].resolved_text.startswith("Sam chose")
        assert by_speaker.facts[0].resolution["window_event_ids"] == []
        in_message = repair(result_of(fact("He", "met", "Kim", "Lee met Kim. He asked about Kim.")),
                            [claim(subject="He", predicate="met", object="Kim", sentence="He asked about Kim.")],
                            answer("Lee"), window=[], text="Lee met Kim. He asked about Kim.")
        assert in_message.facts[0].resolved_text == "Lee asked about Kim."

    def test_a_pronoun_the_sentence_does_not_hold_as_a_word_repairs_nothing(self):
        result = result_of(fact("They", "chose", "Tokyo", "They're choosing Tokyo."))
        assert repair(result, [claim(object="Tokyo", sentence="They're choosing Tokyo.")],
                      answer("Alex", "Jordan")) == result

    def test_a_response_that_does_not_parse_or_names_another_claim_repairs_nothing(self):
        result = result_of(CHOSE)
        assert repair(result, [claim()], {"claims": "none"}) == result
        assert repair(result, [claim()], answer("Alex", "Jordan", index=7)) == result

    def test_one_listed_field_that_fails_does_not_stop_the_other(self):
        told = fact("They", "told", "them", "They told them.", object_type="person")
        both = {"claims": [{"claim": 1, "subject": [{"name": "Alex"}], "object": [{"name": "Nobody"}]}]}
        repaired = repair(result_of(told), [claim(subject="They", predicate="told", object="them",
                                                  fields=("subject", "object"), sentence="They told them.")],
                          both, text="They told them.")
        (only,) = repaired.facts
        assert only.resolved_text == "Alex told them." and only.object == "them"
        assert [item["field"] for item in only.resolution["replacements"]] == ["subject"]


class TestTheRequest:
    def test_the_request_shows_the_earlier_memories_and_only_the_flagged_sentences(self):
        messages = repair_messages([PLAN], [claim()], "Sam")
        assert [message["role"] for message in messages] == ["system", "user", "user"]
        assert "[earlier memory 1] " + PLAN in messages[1]["content"]
        assert "Speaker: Sam" in messages[2]["content"]
        assert "sentence: They chose Tokyo, Kyoto, Kanazawa, and Osaka." in messages[2]["content"]
        assert "Hiroshima" not in messages[2]["content"] and "resolve: subject" in messages[2]["content"]

    def test_without_earlier_memories_there_is_no_reference_message(self):
        assert [message["role"] for message in repair_messages([], [claim()])] == ["system", "user"]

    def test_the_prompt_digest_is_stable(self):
        assert repair_request_sha256() == repair_request_sha256()


# --- Through the engine ----------------------------------------------------------------------------------


class Model:
    """A scripted extractor and repairer for the engine's own pipeline."""

    def __init__(self, repairs=None, fail=False):
        self.repairs = repairs or {}
        self.fail = fail
        self.asked = []

    async def extract_batch(self, turns):
        entries = []
        for turn in turns:
            facts = {
                PLAN: [("Alex", "plans", "a trip to Japan")],
                CITIES: [("They", "chose", "Tokyo, Kyoto, Kanazawa, and Osaka"), ("They", "dropped", "Hiroshima")],
                OTHER: [("Priya", "manages", "the observability rewrite")],
            }.get(turn.content, [])
            entries.append({
                "turn": turn.index,
                "entities": [{"name": subject, "entity_type": "person"} for subject, _, _ in facts
                             if subject not in {"They"}],
                "facts": [{"subject": subject, "subject_entity_type": "person", "predicate": predicate,
                           "object": value, "polarity": "positive", "epistemic_type": "observed",
                           "evidence_quote": turn.content} for subject, predicate, value in facts],
                "relationships": [],
            })
        return {"turns": entries}

    async def repair_pronouns(self, window, claims, *, speaker=None):
        self.asked.append({"window": list(window), "claims": list(claims), "speaker": speaker})
        if self.fail:
            raise ExtractionError("scripted failure", reason_code="APIConnectionError")
        return {"claims": [{"claim": item.index, "subject": [{"name": name} for name in self.repairs.get(item.sentence, [])]}
                           for item in claims if self.repairs.get(item.sentence)]}


REPAIRS = {"They chose Tokyo, Kyoto, Kanazawa, and Osaka.": ["Alex", "Jordan"]}


def settings(config, **changes):
    return config.model_copy(update={"extraction_batch_target_tokens": 512, "enable_reingest": True, **changes})


def install(engine, model):
    provider = engine._pipeline._extraction_provider
    provider.extract_batch = model.extract_batch
    provider.repair_pronouns = model.repair_pronouns
    provider.extract = AsyncMock(side_effect=AssertionError("one call per turn"))
    return model


async def store_all(engine, user, texts=(PLAN, CITIES, OTHER), *, session=None):
    return [await engine.store(text, user_id=user, session_id=session, speaker=None,
                               event_time=T0 + timedelta(hours=hours)) for hours, text in enumerate(texts)]


async def claims_of(engine, user):
    return await engine.query_nodes(user_id=user, node_type=NodeType.FACT, limit=1000)


async def test_reingest_repairs_only_the_claims_on_a_pronoun(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True, enable_mention_links=True)) as engine:
        model = install(engine, Model(REPAIRS))
        events = await store_all(engine, user)
        result = await engine.reingest(user)
        assert (result.processed, result.failed, result.remaining) == (3, 0, 0)
        # One request for the one source with pronoun claims, and it counts with the three first requests.
        assert len(model.asked) == 1 and result.model_calls == 4
        (asked,) = model.asked
        assert asked["window"] == [PLAN]
        assert [item.sentence for item in asked["claims"]] == [
            "They chose Tokyo, Kyoto, Kanazawa, and Osaka.", "They dropped Hiroshima to save time."]
        stored = {node.metadata["predicate"]: node for node in await claims_of(engine, user)}
        repaired, dropped = stored["chose"], stored["dropped"]
        assert repaired.content == "Alex and Jordan chose Tokyo, Kyoto, Kanazawa, and Osaka."
        assert repaired.metadata["grounding_method"] == "pronoun_repair_v1"
        assert repaired.metadata["evidence_quote"] == CITIES
        assert repaired.metadata["resolved_from_context"] == {
            "event_ids": [str(events[0])],
            "fields": {"subject": {"pronoun": "They", "names": ["Alex", "Jordan"]}}}
        # The other claim of the message, and the claims of the other memories, are as without the option.
        assert dropped.content == "They dropped Hiroshima to save time." and "resolved_from_context" not in dropped.metadata
        assert stored["plans"].content == PLAN and stored["manages"].content == OTHER
        # The repaired claim is linked to the people its text names.
        names = {entity.content: entity for entity in await engine.query_nodes(
            user_id=user, node_type=NodeType.ENTITY, limit=100)}
        assert {"Alex", "Jordan"} <= set(names)
        linked = {edge.target_id for edge in await engine._graph_store.get_edges(source_id=str(repaired.id))}
        assert {names["Alex"].id, names["Jordan"].id} <= linked


async def test_the_window_is_the_owners_earlier_memories_in_storage_order_and_bounded(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True, extraction_window_turns=2)) as engine:
        model = install(engine, Model(REPAIRS))
        await store_all(engine, user, texts=("First.", "Second.", PLAN, CITIES))
        # Another owner's and another scope's memories are never shown.
        await engine.store("Someone else's.", user_id=f"{user}-other", event_time=T0 + timedelta(hours=1))
        await engine.reingest(user)
        assert [call["window"] for call in model.asked] == [["Second.", PLAN]]


async def test_a_session_turn_is_shown_the_turns_before_it_in_its_session(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True)) as engine:
        model = install(engine, Model(REPAIRS))
        await engine.store("Unrelated owner memory.", user_id=user, event_time=T0)
        await store_all(engine, user, session="s1")
        await engine.reingest(user)
        assert [call["window"] for call in model.asked] == [[PLAN]]


async def test_ingest_repairs_too_with_batched_extraction_on(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True)) as engine:
        model = install(engine, Model(REPAIRS))
        for hours, text in enumerate((PLAN, CITIES)):
            await engine.ingest(text, user_id=user, session_id="s1", role="user",
                                event_time=T0 + timedelta(hours=hours), wait_for_extraction=True)
        assert [call["window"] for call in model.asked] == [[PLAN]]
        texts = {node.metadata["predicate"]: node.content for node in await claims_of(engine, user)}
        assert texts["chose"] == "Alex and Jordan chose Tokyo, Kyoto, Kanazawa, and Osaka."
        assert texts["dropped"] == "They dropped Hiroshima to save time."


async def test_ingest_repairs_with_one_request_per_turn(config, user):
    async with MemoryEngine.open(settings(
            config, enable_pronoun_repair=True, extraction_batch_target_tokens=0,
            enable_windowed_extraction=True)) as engine:
        model = Model(REPAIRS)
        install(engine, model)

        async def extract(text, *, role="user", context=(), **_):
            from prme.ingestion.extraction import validate_raw_output

            entry = (await model.extract_batch([type("Turn", (), {"content": text, "index": 1})()]))["turns"][0]
            return validate_raw_output({key: entry[key] for key in ("entities", "facts", "relationships")}, text)

        engine._pipeline._extraction_provider.extract = extract
        for hours, text in enumerate((PLAN, CITIES)):
            await engine.ingest(text, user_id=user, session_id="s1", role="user",
                                event_time=T0 + timedelta(hours=hours), wait_for_extraction=True)
        assert len(model.asked) == 1
        assert {node.content for node in await claims_of(engine, user)} >= {
            "Alex and Jordan chose Tokyo, Kyoto, Kanazawa, and Osaka."}


async def test_store_with_extraction_repairs(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True, enable_store_extraction=True)) as engine:
        model = install(engine, Model(REPAIRS))
        for hours, text in enumerate((PLAN, CITIES)):
            await engine.store(text, user_id=user, event_time=T0 + timedelta(hours=hours), extract=True)
        await engine.process_extractions(user_id=user, limit=10, budget_ms=60_000)
        assert [call["window"] for call in model.asked] == [[PLAN]]
        assert "Alex and Jordan chose Tokyo, Kyoto, Kanazawa, and Osaka." in {
            node.content for node in await claims_of(engine, user)}


async def test_a_failed_repair_request_keeps_the_first_extraction(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True)) as engine:
        model = install(engine, Model(REPAIRS, fail=True))
        await store_all(engine, user)
        result = await engine.reingest(user)
        assert (result.processed, result.failed) == (3, 0) and len(model.asked) == 1
        assert {node.content for node in await claims_of(engine, user)} == {
            PLAN, OTHER, "They chose Tokyo, Kyoto, Kanazawa, and Osaka.", "They dropped Hiroshima to save time."}


async def test_an_answer_that_names_no_one_leaves_the_claims_alone(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True)) as engine:
        install(engine, Model({}))
        await store_all(engine, user)
        await engine.reingest(user)
        assert "They chose Tokyo, Kyoto, Kanazawa, and Osaka." in {node.content for node in await claims_of(engine, user)}


async def test_two_claims_repaired_to_one_text_do_not_share_it(config, user):
    both = {"They chose Tokyo, Kyoto, Kanazawa, and Osaka.": ["Alex", "Jordan"]}
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True, enable_claim_sentence_text=True,
                                          enable_ingest_claim_fixes=True)) as engine:
        model = install(engine, Model(both))
        original = model.extract_batch

        async def extract_batch(turns):
            raw = await original(turns)
            for entry in raw["turns"]:
                # A second claim from the same sentence.
                if entry["facts"] and entry["facts"][0]["subject"] == "They":
                    entry["facts"][1] = {**entry["facts"][0], "predicate": "picked", "object": "Kyoto"}
            return raw

        engine._pipeline._extraction_provider.extract_batch = extract_batch
        await store_all(engine, user)
        await engine.reingest(user)
        texts = [node.content for node in await claims_of(engine, user) if node.metadata["subject"] in {"They"}]
        assert len(texts) == len(set(texts)) == 2
        assert "Alex and Jordan chose Tokyo, Kyoto, Kanazawa, and Osaka." in texts


async def test_with_the_option_off_nothing_is_asked_and_the_extraction_is_unchanged(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        model = install(engine, Model(REPAIRS))
        assert engine._pipeline._pronoun_repair_turns is None
        await store_all(engine, user)
        result = await engine.reingest(user)
        assert model.asked == [] and result.model_calls == 3
        for node in await claims_of(engine, user):
            assert "resolved_from_context" not in node.metadata and "resolution" not in node.metadata
            assert node.metadata["grounding_method"] != "pronoun_repair_v1"
        from prme.ingestion.reingest import _digest, extraction_settings

        # The saved output is what the first request returned, with no field the repair adds.
        ids = [str(event.id) for event in await engine.get_events(user_id=user, limit=10)]
        saved = await engine._event_store.reingest.saved_extractions(
            ids, user_id=user, extraction_key=_digest(extraction_settings(engine._pipeline)))
        assert len(saved) == 3
        for record in saved.values():
            assert "resolved_text" not in json.dumps(record.result) and "resolution" not in json.dumps(record.result)


async def test_the_option_changes_the_settings_a_reingest_compares(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        install(engine, Model(REPAIRS))
        await store_all(engine, user)
        off = await engine.reingest(user)
        assert (await engine.reingest(user, dry_run=True)).current == 3
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True)) as engine:
        install(engine, Model(REPAIRS))
        stale = await engine.reingest(user, dry_run=True)
        assert (stale.current, stale.selected) == (0, 3) and stale.fingerprint != off.fingerprint
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True, extraction_window_turns=2)) as engine:
        install(engine, Model(REPAIRS))
        assert (await engine.reingest(user, dry_run=True)).fingerprint != stale.fingerprint


async def test_a_reingest_replaces_the_pronoun_version_it_derived_before(config, user):
    async with MemoryEngine.open(settings(config)) as engine:
        install(engine, Model(REPAIRS))
        await store_all(engine, user)
        await engine.reingest(user)
        before = {node.content for node in await claims_of(engine, user)}
        assert "They chose Tokyo, Kyoto, Kanazawa, and Osaka." in before
        engine._pipeline._pronoun_repair_turns = 4
        result = await engine.reingest(user)
        assert (result.processed, result.failed, result.remaining) == (3, 0, 0)
        active = {node.content for node in await claims_of(engine, user)}
        assert "Alex and Jordan chose Tokyo, Kyoto, Kanazawa, and Osaka." in active
        assert "They chose Tokyo, Kyoto, Kanazawa, and Osaka." not in active
        assert "They dropped Hiroshima to save time." in active


async def test_a_provider_without_repair_pronouns_is_refused_when_the_option_is_on(config, user):
    from prme.ingestion.pipeline import IngestionPipeline

    class Plain:
        provider_name = "plain"
        model_name = "plain/plain"

        async def extract(self, content, *, role="user"):
            return ExtractionResult()

        async def extract_batch(self, turns):
            return {"turns": []}

    with pytest.raises(ValueError, match="repair_pronouns"):
        IngestionPipeline(None, None, None, None, Plain(), None, pronoun_repair_turns=4)


@pytest.fixture
def product_defaults(monkeypatch):
    from tests import conftest

    conftest.product_defaults(monkeypatch)


async def test_it_is_on_in_the_product_defaults_and_the_variable_set_to_false_restores_the_first_extraction(
        product_defaults, config, user, monkeypatch):
    from prme import PRMEConfig

    assert config.enable_pronoun_repair is True
    async with MemoryEngine.open(config) as engine:
        assert engine._pipeline._pronoun_repair_turns == config.extraction_window_turns == 4
    monkeypatch.setenv("PRME_ENABLE_PRONOUN_REPAIR", "false")
    assert PRMEConfig().enable_pronoun_repair is False
    async with MemoryEngine.open(config.model_copy(update={"enable_pronoun_repair": PRMEConfig().enable_pronoun_repair})
                                 ) as engine:
        assert engine._pipeline._pronoun_repair_turns is None


async def test_a_provider_swapped_in_without_repair_pronouns_leaves_the_first_extraction(config, user):
    async with MemoryEngine.open(settings(config, enable_pronoun_repair=True)) as engine:
        model = install(engine, Model(REPAIRS))
        provider = engine._pipeline._extraction_provider

        class Plain:
            provider_name, model_name = provider.provider_name, provider.model_name
            extract_batch = staticmethod(model.extract_batch)

        engine._pipeline._extraction_provider = Plain()
        await store_all(engine, user)
        result = await engine.reingest(user)
        assert (result.processed, result.failed) == (3, 0) and model.asked == []
        assert "They chose Tokyo, Kyoto, Kanazawa, and Osaka." in {node.content for node in await claims_of(engine, user)}
