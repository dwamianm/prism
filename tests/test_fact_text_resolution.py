"""A fact's text can stand alone: names for pronouns, dates for relative dates (#91)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.extraction import (
    FactTextResolution,
    _CitedExtractionResult,
    _ResolvingCitedExtractionResult,
    _extraction_prompt_for_role,
    _source_details_message,
)
from prme.ingestion.pipeline import IngestionPipeline
from prme.ingestion.resolution import ResolutionSources, WindowTurn, resolve_fact_text
from prme.ingestion.schema import ExtractedFact, ExtractionResult
from prme.types import NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc)
CAROLINE_TURN = "(1:56 pm on 8 May, 2023) Caroline: I went to a support group yesterday and it was so powerful."


def check(resolved, text=CAROLINE_TURN, *, speaker="Caroline", subject="Caroline",
          obj="support group", window=(), reference_time=T0):
    sources = ResolutionSources(event_id="new", text=text, speaker=speaker,
                                reference_time=reference_time, window=tuple(window))
    return resolve_fact_text(resolved, text, sources, subject=subject, object_value=obj,
                             resolve_date=IngestionPipeline._resolve_temporal)


def changes(record):
    return [(item["original"], item["replacement"]) for item in record["replacements"]]


class TestAccepted:
    def test_a_pronoun_becomes_the_speaker_and_a_relative_date_its_date(self):
        record, reason = check("Caroline went to a support group on 7 May 2023 and it was so powerful.")
        assert reason is None
        assert changes(record) == [("I", "Caroline"), ("yesterday", "on 7 May 2023")]
        assert record["original"] == "I went to a support group yesterday and it was so powerful."
        date = record["replacements"][1]
        assert date["date"] == "2023-05-07"
        assert date["sources"] == [{"source": "source_time", "reference_time": T0.isoformat()}]

    def test_a_name_from_an_earlier_turn_links_that_turn(self):
        text = "Melanie: You'd be a great counselor!"
        window = [WindowTurn("earlier", "Caroline", "Caroline: I'm keen on counseling.")]
        record, _ = check("Caroline would be a great counselor!", text, speaker="Melanie",
                          subject="You", obj="counselor", window=window)
        assert changes(record) == [("You'd", "Caroline would")]
        assert record["window_event_ids"] == ["earlier"]

    def test_a_verb_may_agree_with_the_name(self):
        text = "Caroline: I don't eat meat, unless it is fish."
        record, _ = check("Caroline doesn't eat meat, unless it is fish.", text, obj="meat")
        assert changes(record) == [("I", "Caroline"), ("don't", "doesn't")]

    def test_a_relation_to_the_source_date_is_a_date(self):
        text = "Melanie: We went camping last week."
        record, _ = check("Melanie and Caroline went camping the week before 8 May 2023.", text,
                          speaker="Melanie", subject="We", obj="camping",
                          window=[WindowTurn("w", "Caroline", "Caroline: hi")])
        assert changes(record)[1] == ("last week", "the week before 8 May 2023")

    def test_one_sentence_of_a_longer_turn(self):
        text = "Caroline: Hi! I love painting. It relaxes me."
        record, _ = check("Caroline loves painting.", text, obj="painting")
        assert record["original"] == "I love painting."


class TestRejected:
    @pytest.mark.parametrize("resolved", [
        # A wrong date.
        "Caroline went to a support group on 6 May 2023 and it was so powerful.",
        # The wrong person for "I".
        "Melanie went to a support group yesterday and it was so powerful.",
        # A negation the turn does not state.
        "Caroline did not go to a support group yesterday and it was so powerful.",
        # A clipped sentence.
        "Caroline went to a support group yesterday",
        # A paraphrase.
        "Caroline attended a support group yesterday and it was so powerful.",
        # A word no turn gives.
        "Caroline went to a support group yesterday and it was so powerful for her wife Jane.",
    ])
    def test_any_other_change(self, resolved):
        record, reason = check(resolved)
        assert record is None and reason

    def test_a_condition_cannot_be_left_out(self):
        text = "Caroline: I love painting. Never on Sundays though."
        record, _ = check("Caroline loves painting.", text, obj="painting")
        assert record is None

    def test_you_cannot_be_the_speaker(self):
        record, _ = check("Melanie would be a great counselor!", "Melanie: You'd be a great counselor!",
                          speaker="Melanie", subject="You", obj="counselor")
        assert record is None

    def test_i_has_no_name_without_a_speaker(self):
        window = [WindowTurn("w", "Caroline", "Caroline: hello")]
        record, _ = check("Caroline loves painting.", "I love painting.", speaker=None,
                          obj="painting", window=window)
        assert record is None

    def test_a_name_no_turn_gives(self):
        record, _ = check("Jane's kids are tough.", "Melanie: They're tough.", speaker="Melanie",
                          subject="They", obj="tough")
        assert record is None

    def test_a_date_without_a_source_time(self):
        record, _ = check("Caroline went to a support group on 7 May 2023 and it was so powerful.",
                          reference_time=None)
        assert record is None

    def test_a_long_rewrite_is_rejected_quickly(self):
        record, _ = check("Some entirely different words about a zebra that went to town on a bus.")
        assert record is None


class TestWhatTheModelIsAsked:
    def test_resolved_text_is_only_in_the_resolving_schema(self):
        plain = json.dumps(_CitedExtractionResult.model_json_schema())
        resolving = json.dumps(_ResolvingCitedExtractionResult.model_json_schema())
        assert "resolved_text" not in plain
        assert "resolved_text" in resolving
        assert '"resolution"' not in plain and '"resolution"' not in resolving

    def test_saved_output_is_unchanged_without_resolution(self):
        fact = ExtractedFact(subject="Ann", predicate="likes", object="tea")
        assert "resolved_text" not in fact.model_dump(mode="json")
        assert "resolution" not in fact.model_dump(mode="json")

    def test_the_prompt_asks_only_when_resolving(self):
        assert "RESOLVED TEXT" not in _extraction_prompt_for_role("user")
        assert "RESOLVED TEXT" in _extraction_prompt_for_role("user", resolving=True)
        assert _extraction_prompt_for_role("user", windowed=True, resolving=True).startswith(
            _extraction_prompt_for_role("user", windowed=True))

    def test_the_source_details_give_the_speaker_and_utc_date(self):
        late = datetime(2023, 5, 8, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
        message = _source_details_message(FactTextResolution(speaker="Caroline", source_time=late))
        assert "spoken by Caroline" in message
        assert "sent on Tuesday, 9 May 2023" in message
        assert _source_details_message(FactTextResolution()) is None


def extractor(calls, facts):
    """Record what the extractor was asked and return fixed facts."""

    async def extract(content, *, role=None, context=(), resolve=None):
        calls.append({"context": list(context), "resolve": resolve})
        entities = [{"name": "Caroline", "entity_type": "person"},
                    {"name": "support group", "entity_type": "organization"}]
        return ExtractionResult.model_validate({"entities": entities, "facts": facts})

    return AsyncMock(side_effect=extract)


def went(**extra):
    return {"subject": "Caroline", "subject_entity_type": "person", "predicate": "attended",
            "object": "support group", "object_entity_type": "organization", "polarity": "positive",
            "evidence_quote": CAROLINE_TURN, "temporal_ref": "yesterday", **extra}


async def ingest(engine, user, text=CAROLINE_TURN):
    return await engine.ingest(text, user_id=user, session_id="s1", role="user", speaker="Caroline",
                               event_time=T0, wait_for_extraction=True)


async def fact_nodes(engine, user):
    return await engine.query_nodes(user_id=user, node_type=NodeType.FACT)


def resolving(base):
    return base.model_copy(update={"enable_fact_text_resolution": True})


RESOLVED = "Caroline went to a support group on 7 May 2023 and it was so powerful."


class TestIngestion:
    async def test_off_by_default_and_stray_output_is_dropped(self, config, user):
        calls = []
        async with MemoryEngine.open(config) as engine:
            engine._pipeline._extraction_provider.extract = extractor(
                calls, [went(resolved_text=RESOLVED, resolution={"method": "forged"})])
            await ingest(engine, user)
            [node] = await fact_nodes(engine, user)
        assert calls[0]["resolve"] is None
        assert node.content == CAROLINE_TURN
        assert "resolution" not in node.metadata
        assert node.metadata["grounding_method"] == "source_passage_v1"

    async def test_a_checked_text_becomes_the_fact_text(self, config, user):
        calls = []
        async with MemoryEngine.open(resolving(config)) as engine:
            engine._pipeline._extraction_provider.extract = extractor(calls, [went(resolved_text=RESOLVED)])
            event_id = await ingest(engine, user)
            [node] = await fact_nodes(engine, user)
        assert calls[0]["resolve"] == FactTextResolution(speaker="Caroline", source_time=T0)
        assert node.content == RESOLVED
        # The passage stays the evidence, and the claim's own turn its only support.
        assert node.metadata["evidence_quote"] == CAROLINE_TURN
        assert [str(ref) for ref in node.evidence_refs] == [event_id]
        assert node.metadata["grounding_method"] == "resolved_text_v1"
        assert node.metadata["resolution"]["method"] == "reference_resolution_v1"
        assert changes(node.metadata["resolution"]) == [("I", "Caroline"), ("yesterday", "on 7 May 2023")]

    async def test_a_forged_record_is_replaced_by_the_check(self, config, user):
        async with MemoryEngine.open(resolving(config)) as engine:
            engine._pipeline._extraction_provider.extract = extractor(
                [], [went(resolved_text=RESOLVED, resolution={"method": "forged", "window_event_ids": ["x"]})])
            await ingest(engine, user)
            [node] = await fact_nodes(engine, user)
        assert node.metadata["resolution"]["method"] == "reference_resolution_v1"
        assert node.metadata["resolution"]["window_event_ids"] == []

    async def test_a_rejected_text_keeps_the_passage(self, config, user):
        wrong = "Caroline did not go to a support group on 7 May 2023."
        async with MemoryEngine.open(resolving(config)) as engine:
            engine._pipeline._extraction_provider.extract = extractor([], [went(resolved_text=wrong)])
            await ingest(engine, user)
            [node] = await fact_nodes(engine, user)
        assert node.content == CAROLINE_TURN
        assert "resolution" not in node.metadata

    async def test_a_window_turn_that_gave_a_name_is_linked(self, config, user):
        texts = ["Caroline: I joined a support group.", "Melanie: You'd love the support group!"]
        fact = {"subject": "You", "predicate": "would_love", "object": "support group",
                "object_entity_type": "organization", "polarity": "positive",
                "evidence_quote": texts[1], "resolved_text": "Caroline would love the support group!"}
        results = iter([[], [fact]])

        async def extract(content, *, role=None, context=(), resolve=None):
            return ExtractionResult.model_validate({
                "entities": [{"name": "support group", "entity_type": "organization"}],
                "facts": next(results)})

        both = resolving(config).model_copy(update={"enable_windowed_extraction": True})
        async with MemoryEngine.open(both) as engine:
            engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=extract)
            first = await engine.ingest(texts[0], user_id=user, session_id="s1", role="user",
                                        speaker="Caroline", event_time=T0, wait_for_extraction=True)
            second = await engine.ingest(texts[1], user_id=user, session_id="s1", role="participant",
                                         speaker="Melanie", event_time=T0 + timedelta(minutes=1),
                                         wait_for_extraction=True)
            [node] = await fact_nodes(engine, user)
        assert node.content == "Caroline would love the support group!"
        assert node.metadata["resolution"]["window_event_ids"] == [first]
        assert [str(ref) for ref in node.evidence_refs] == [second]
