"""A repeated extracted claim becomes one current record (#209)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from prme import MemoryEngine
from prme.ingestion.schema import ExtractionResult
from prme.types import EdgeType, LifecycleState, NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 3, 2, 9, tzinfo=timezone.utc)


def claim(quote, *, obj="Alex", predicate="name_is", **extra):
    return {"subject": "Alex", "subject_entity_type": "person", "predicate": predicate,
            "object": obj, "polarity": "positive", "evidence_quote": quote, **extra}


def extractor(*facts_for):
    """Return one fixed extraction per call, keyed by call order."""
    calls = iter(facts_for)

    async def extract(content, *, role=None):
        entities = [{"name": "Alex", "entity_type": "person"}]
        return ExtractionResult.model_validate({"entities": entities, "facts": next(calls)})
    return AsyncMock(side_effect=extract)


async def say(engine, user, text, *, session, hours=0, role="user"):
    return await engine.ingest(text, user_id=user, session_id=session, role=role,
                               event_time=T0 + timedelta(hours=hours), wait_for_extraction=True)


async def facts(engine, user):
    return await engine.query_nodes(user_id=user, node_type=NodeType.FACT)


async def test_repeated_claim_across_sessions_leaves_one_record(config, user):
    texts = ["My name is Alex.", "Hi, my name is Alex!", "My name is Alex, as I said."]
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = extractor(*[[claim(text)] for text in texts])
        events = [await say(engine, user, text, session=f"s{i}", hours=i) for i, text in enumerate(texts)]
        [current] = await facts(engine, user)
        assert current.content == texts[-1]
        assert [str(ref) for ref in current.evidence_refs] == events
        assert len(current.metadata["merged_claim_ids"]) == 1
        # Each restatement retired the record before it; history stays readable.
        previous = await engine.get_node(current.metadata["merged_claim_ids"][0], include_superseded=True)
        assert previous.lifecycle_state == LifecycleState.SUPERSEDED
        assert previous.superseded_by == current.id
        assert [str(ref) for ref in previous.evidence_refs] == events[:2]
        edges = await engine._graph_store.get_edges(source_id=str(current.id), target_id=str(previous.id))
        assert [edge.edge_type for edge in edges] == [EdgeType.SUPERSEDES]


async def test_different_claims_stay_separate(config, user):
    lives = dict(predicate="lives_in", obj="Paris")
    ran = dict(predicate="ran", obj="5 km", quantity={"value": "5", "unit": "km", "source_text": "5 km"})
    variants = [
        claim("Alex lives in Paris.", **lives),
        claim("Alex lives in Rome.", predicate="lives_in", obj="Rome"),
        claim("Alex does not live in Paris.", **{**lives, "polarity": "negative"}),
        claim("Alex might live in Paris.", **lives, epistemic_type="hypothetical"),
        claim("Alex ran 5 km.", **ran),
        claim("Alex ran 5 km.", **ran),
    ]
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = extractor(*[[fact] for fact in variants])
        for i, fact in enumerate(variants):
            await say(engine, user, fact["evidence_quote"], session=f"s{i}", hours=i)
        stored = await facts(engine, user)
        # Object, polarity and epistemic type differ; repeated quantities are separate occurrences.
        assert len(stored) == len(variants)
        assert all("merged_claim_ids" not in node.metadata for node in stored)


async def test_assistant_restatement_is_a_different_source(config, user):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = extractor(
            [claim("My name is Alex.")], [claim("Your name is Alex.", subject_entity_type="person")])
        await say(engine, user, "My name is Alex.", session="s0")
        await say(engine, user, "Your name is Alex.", session="s1", hours=1, role="assistant")
        assert len(await facts(engine, user)) == 2


async def test_same_message_stating_a_claim_twice_makes_one_record(config, user):
    text = "My name is Alex. Yes, my name is Alex."
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = extractor(
            [claim("My name is Alex."), claim("Yes, my name is Alex.")])
        event_id = await say(engine, user, text, session="s0")
        [current] = await facts(engine, user)
        assert [str(ref) for ref in current.evidence_refs] == [event_id]


async def test_late_arriving_older_statement_does_not_retire_a_later_one(config, user):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = extractor(
            [claim("My name is Alex.")], [claim("Hi, my name is Alex!")])
        await say(engine, user, "My name is Alex.", session="s0", hours=5)
        await say(engine, user, "Hi, my name is Alex!", session="s1", hours=1)
        assert len(await facts(engine, user)) == 2


async def test_merged_record_keeps_the_most_established_state(config, user):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = extractor(
            [claim("My name is Alex.")], [claim("Hi, my name is Alex!")])
        await say(engine, user, "My name is Alex.", session="s0")
        [first] = await facts(engine, user)
        await engine.promote(str(first.id), user_id=user)
        await engine.reinforce(str(first.id), user_id=user)
        first = await engine.get_node(str(first.id))
        await say(engine, user, "Hi, my name is Alex!", session="s1", hours=1)
        [current] = await facts(engine, user)
        assert current.lifecycle_state == LifecycleState.STABLE
        assert current.confidence_base >= first.confidence_base
        assert current.reinforcement_boost == first.reinforcement_boost


async def test_setting_off_keeps_one_record_per_extraction(config, user):
    config = config.model_copy(update={"enable_claim_merge": False})
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = extractor(
            [claim("My name is Alex.")], [claim("Hi, my name is Alex!")])
        await say(engine, user, "My name is Alex.", session="s0")
        await say(engine, user, "Hi, my name is Alex!", session="s1", hours=1)
        assert len(await facts(engine, user)) == 2
