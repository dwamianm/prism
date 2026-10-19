"""A named speaker's own I, me and my can ground a claim about them (enable_speaker_grounding)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.extraction import validate_raw_output
from prme.ingestion.grounding import claim_sentences, mentioned_or_spoken, validate_grounding
from prme.ingestion.schema import ExtractionResult
from prme.types import EdgeType, LifecycleState, NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

HOME = "Hi! I'm Dana. I live in Denver with my partner Sam."
HOME_QUOTE = "I live in Denver with my partner Sam."
JOB = "I left Northwind in April. Now I work at Brightpath."
ESLINT = "I'm trying to set up ESLint."


def _fact(subject, predicate, obj, quote, **extra):
    return {"subject": subject, "predicate": predicate, "object": obj, "polarity": "positive",
            "epistemic_type": "observed", "evidence_quote": quote, **extra}


# What the scripted model returns for each message, before any check. It knows
# Dana's name and writes it, as a model shown "I'm Dana" or a speaker label does.
RAW = {
    HOME: {
        "entities": [{"name": "Dana", "entity_type": "person"}, {"name": "Denver", "entity_type": "location"},
                     {"name": "Sam", "entity_type": "person"}],
        "facts": [_fact("Dana", "lives_in", "Denver", HOME_QUOTE)],
        "relationships": [{"source_entity": "Dana", "target_entity": "Sam", "relationship_type": "partner_of",
                           "polarity": "positive", "epistemic_type": "observed", "evidence_quote": HOME_QUOTE}],
    },
    JOB: {
        "entities": [{"name": "Dana", "entity_type": "person"},
                     {"name": "Brightpath", "entity_type": "organization"}],
        "facts": [_fact("Dana", "works_at", "Brightpath", "Now I work at Brightpath.")],
    },
    ESLINT: {
        "entities": [{"name": "Dana", "entity_type": "person"}, {"name": "ESLint", "entity_type": "technology"}],
        "facts": [_fact("Dana", "uses", "ESLint", ESLINT),
                  _fact("Dana", "trying_to_set_up", "ESLint", ESLINT)],
    },
}


async def _extract(content, *, role="user", speaker=None, **_):
    return validate_raw_output(RAW[content], content, role=role, speaker=speaker)


def grounding(base, **extra):
    return base.model_copy(update={"enable_speaker_grounding": True, **extra})


@pytest.mark.parametrize(("text", "speaker", "expected"), [
    ("I live in Denver.", "Dana", True),
    ("I’m in Denver.", "dana", True),
    ("My partner is Sam.", "Dana", True),
    ("Sam called me.", "Dana", True),
    ("I live in Denver.", None, False),
    ("I live in Denver.", "Sam", False),
    ("We live in Denver.", "Dana", False),
    ("Our dog is Biscuit.", "Dana", False),
    ("AI tools, i.e. models.", "Dana", False),
])
def test_only_the_speakers_own_singular_reference_mentions_them(text, speaker, expected):
    assert mentioned_or_spoken("Dana", text, speaker) is expected
    # A name in the text is a mention with or without a speaker.
    assert mentioned_or_spoken("Denver", "I live in Denver.", speaker)


@pytest.mark.parametrize(("value", "text", "speaker", "expected"), [
    ("I", "My sister Rachel had a baby.", "Dana", True),
    ("I", "Melanie talked me into it.", "Dana", True),
    ("me", "I left Northwind.", "Dana", True),
    ("I", "My sister Rachel had a baby.", None, True),
    ("I", "Our dog is Biscuit.", "Dana", False),
])
def test_the_speakers_i_me_and_my_mention_each_other(value, text, speaker, expected):
    assert mentioned_or_spoken(value, text, speaker) is expected


def test_the_provider_check_keeps_a_claim_that_names_the_speaker_only_with_them():
    without = validate_raw_output(RAW[HOME], HOME)
    assert without.facts == [] and without.relationships == []
    elsewhere = validate_raw_output(RAW[HOME], HOME, speaker="Sam")
    assert elsewhere.facts == [] and elsewhere.relationships == []
    kept = validate_raw_output(RAW[HOME], HOME, speaker="Dana")
    assert [(fact.subject, fact.object) for fact in kept.facts] == [("Dana", "Denver")]
    assert [(rel.source_entity, rel.target_entity) for rel in kept.relationships] == [("Dana", "Sam")]
    # The quote still widens to the paragraph, as for any kept claim.
    assert kept.facts[0].evidence_quote == HOME


def test_an_intention_still_needs_a_predicate_that_keeps_it():
    kept = validate_raw_output(RAW[ESLINT], ESLINT, speaker="Dana")
    # The speaker's own "I'm trying to" still blocks a completed "uses".
    assert [fact.predicate for fact in kept.facts] == ["trying_to_set_up"]


def test_pipeline_grounding_keeps_the_speakers_entity_and_claims():
    result = ExtractionResult.model_validate(RAW[JOB])
    assert validate_grounding(result, JOB).facts == []
    assert [entity.name for entity in validate_grounding(result, JOB).entities] == ["Brightpath"]
    kept = validate_grounding(result, JOB, speaker="Dana")
    assert [entity.name for entity in kept.entities] == ["Dana", "Brightpath"]
    assert [(fact.subject, fact.object) for fact in kept.facts] == [("Dana", "Brightpath")]


def test_claim_sentences_find_the_speakers_own_sentence():
    assert claim_sentences(JOB, "Dana", "Brightpath") is None
    assert claim_sentences(JOB, "Dana", "Brightpath", speaker="Dana") == "Now I work at Brightpath."


async def say(engine, user, text, *, speaker="Dana"):
    return await engine.ingest(text, user_id=user, session_id="s1", role="user", speaker=speaker,
                               event_time=datetime(2026, 2, 3, 19, tzinfo=timezone.utc), wait_for_extraction=True)


async def claims(engine, user):
    """(subject entity, predicate, object, text) for each claim, from its HAS_FACT edge."""
    found = []
    for node in await engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState)):
        if node.node_type != NodeType.FACT:
            continue
        [edge] = await engine._graph_store.get_edges(target_id=str(node.id), edge_type=EdgeType.HAS_FACT)
        subject = await engine.get_node(str(edge.source_id), include_superseded=True, user_id=user)
        found.append((subject.content, node.metadata["predicate"], node.metadata["object"], node.content))
    return sorted(found)


async def test_off_by_default_the_claims_are_discarded(config, user):
    assert config.enable_speaker_grounding is False
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = mock = AsyncMock(side_effect=_extract)
        event_id = await say(engine, user, HOME)
        record = await engine.get_extraction(event_id, user_id=user)
        found = await claims(engine, user)
    # The speaker is not passed while the option is off, so older providers keep working.
    assert "speaker" not in mock.call_args.kwargs
    assert record.grounding_policy == "speech_act_v14"
    assert found == []


async def test_on_the_speakers_claims_attach_to_their_entity(config, user):
    async with MemoryEngine.open(grounding(config, enable_claim_sentence_text=True)) as engine:
        engine._pipeline._extraction_provider.extract = mock = AsyncMock(side_effect=_extract)
        home = await say(engine, user, HOME)
        await say(engine, user, JOB)
        record = await engine.get_extraction(home, user_id=user)
        found = await claims(engine, user)
        names = [node.content for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)]
    assert mock.call_args.kwargs["speaker"] == "Dana"
    assert record.grounding_policy == "speech_act_v14"
    assert found == [
        ("Dana", "lives_in", "Denver", HOME_QUOTE),
        ("Dana", "partner_of", "Sam", HOME_QUOTE),
        # The speaker's own sentence is the claim's text, not the whole message.
        ("Dana", "works_at", "Brightpath", "Now I work at Brightpath."),
    ]
    assert names.count("Dana") == 1


async def test_on_a_turn_without_a_speaker_is_unchanged(config, user):
    async with MemoryEngine.open(grounding(config)) as engine:
        engine._pipeline._extraction_provider.extract = mock = AsyncMock(side_effect=_extract)
        event_id = await say(engine, user, HOME, speaker=None)
        record = await engine.get_extraction(event_id, user_id=user)
        found = await claims(engine, user)
    assert "speaker" not in mock.call_args.kwargs
    assert record.grounding_policy == "speech_act_v14"
    assert found == []
