"""A named speaker's I, me and my can bind to that speaker's entity (enable_speaker_references)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from prme import MemoryEngine
from prme.ingestion.schema import ExtractionResult
from prme.types import EdgeType, LifecycleState, NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 2, 3, 19, tzinfo=timezone.utc)
# Claims the scripted model finds, by message: (subject, predicate, object, object type, extra fields).
CLAIMS = {
    "I work at Northwind.": [("I", "works_at", "Northwind", "organization", {})],
    "I left Northwind and now work at Brightpath.": [
        ("I", "works_at", "Brightpath", "organization",
         {"temporal_intent": "update", "replaces_object": "Northwind", "epistemic_type": "observed"})],
    "I prefer Python.": [("I", "prefers", "Python", "technology", {"fact_type": "preference"})],
    "We went camping.": [("We", "went", "camping", None, {})],
    "Sam called me.": [("Sam", "called", "me", None, {})],
}


async def _extract(content, *, role=None, **_):
    facts, entities = [], []
    for subject, predicate, obj, kind, extra in CLAIMS[content]:
        facts.append({"subject": subject, "predicate": predicate, "object": obj, "object_entity_type": kind,
                      "polarity": "positive", "evidence_quote": content, **extra})
        if subject == "Sam":
            entities.append({"name": "Sam", "entity_type": "person"})
        if kind:
            entities.append({"name": obj, "entity_type": kind})
    return ExtractionResult.model_validate({"entities": entities, "facts": facts})


def speaking(base):
    return base.model_copy(update={"enable_speaker_references": True})


async def say(engine, user, texts, *, speaker="Dana"):
    for minutes, text in enumerate(texts):
        await engine.ingest(text, user_id=user, session_id="s1", role="user", speaker=speaker,
                            event_time=T0 + timedelta(minutes=minutes), wait_for_extraction=True)


async def claims(engine, user):
    """Each claim with its state and the entity its HAS_FACT edge comes from, by object."""
    found = {}
    nodes = await engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState))
    for node in nodes:
        if node.node_type not in (NodeType.FACT, NodeType.PREFERENCE):
            continue
        [edge] = await engine._graph_store.get_edges(target_id=str(node.id), edge_type=EdgeType.HAS_FACT)
        subject = await engine.get_node(str(edge.source_id), include_superseded=True, user_id=user)
        found.setdefault(node.metadata["object"], []).append((node, subject))
    return found


async def entities(engine, user):
    return await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)


async def test_off_by_default_each_message_keeps_its_own_i(config, user):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=_extract)
        await say(engine, user, ["I work at Northwind.", "I left Northwind and now work at Brightpath."])
        found = await claims(engine, user)
    [(northwind, first)], [(brightpath, second)] = found["Northwind"], found["Brightpath"]
    assert first.content == second.content == "I" and first.id != second.id
    assert first.metadata["identity_status"] == "unresolved_reference"
    # Different subjects, so the named update cannot find the value it replaces.
    assert northwind.lifecycle_state == LifecycleState.TENTATIVE
    assert "speaker_reference" not in brightpath.metadata


async def test_a_named_speakers_claims_share_one_entity_and_an_update_supersedes(config, user):
    async with MemoryEngine.open(speaking(config)) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=_extract)
        await say(engine, user, ["I work at Northwind.", "I left Northwind and now work at Brightpath."])
        found = await claims(engine, user)
        names = sorted(node.content for node in await entities(engine, user))
    [(northwind, first)], [(brightpath, second)] = found["Northwind"], found["Brightpath"]
    assert first.id == second.id and first.content == "Dana"
    assert first.metadata["entity_type"] == "person" and "identity_status" not in first.metadata
    assert names == ["Brightpath", "Dana", "Northwind"]
    assert northwind.lifecycle_state == LifecycleState.SUPERSEDED
    assert brightpath.lifecycle_state == LifecycleState.TENTATIVE
    # The literal pronoun stays the claim's subject; the binding names who it is.
    assert brightpath.metadata["subject"] == "I"
    assert brightpath.metadata["speaker_reference"] == {"speaker": "Dana", "fields": ["subject"]}


async def test_a_repeated_claim_from_two_messages_merges(config, user):
    async with MemoryEngine.open(speaking(config)) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=_extract)
        await say(engine, user, ["I prefer Python.", "I prefer Python."])
        [(earlier, _), (later, _)] = sorted(
            (await claims(engine, user))["Python"], key=lambda pair: pair[0].lifecycle_state.value)
    # One current record holds both statements (#209).
    assert (earlier.lifecycle_state, later.lifecycle_state) == (LifecycleState.SUPERSEDED, LifecycleState.TENTATIVE)
    assert len(later.evidence_refs) == 2


async def test_only_a_named_speakers_singular_references_bind(config, user):
    async with MemoryEngine.open(speaking(config)) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=_extract)
        await say(engine, user, ["We went camping.", "Sam called me."])
        await say(engine, user, ["I work at Northwind."], speaker=None)
        found = await claims(engine, user)
        named = {node.content: node for node in await entities(engine, user)}
        [(called, sam)] = found["me"]
        mentions = await engine._graph_store.get_edges(source_id=str(called.id), edge_type=EdgeType.MENTIONS)
    [(camping, we)], [(_, unnamed)] = found["camping"], found["Northwind"]
    # A plural reference and a turn without a speaker stay local to their message.
    assert we.content == "We" and we.metadata["identity_status"] == "unresolved_reference"
    assert unnamed.content == "I" and unnamed.metadata["identity_status"] == "unresolved_reference"
    assert "speaker_reference" not in camping.metadata
    # "me" as an object binds to the speaker, whom the claim mentions.
    assert sam.content == "Sam" and called.metadata["speaker_reference"] == {"speaker": "Dana", "fields": ["object"]}
    assert [edge.target_id for edge in mentions] == [named["Dana"].id]
