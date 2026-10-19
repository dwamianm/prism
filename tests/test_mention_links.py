"""Entities link to the claims and notes whose text names them, and graph search finds them (#259)."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.mention_links import is_text_link, link_event_mentions
from prme.ingestion.schema import ExtractionResult
from prme.retrieval.candidates import _find_seed_entities, _seed_terms
from prme.retrieval.config import PackingConfig
from prme.retrieval.query_analysis import analyze_query
from prme.types import NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T1 = "Hi, I am Alex Rivera. My partner Jordan and I are planning a trip to Japan in late March next year."
T2 = "That is cherry blossom season. Which places are you thinking about?"
T3 = "We chose Tokyo, Kyoto, Kanazawa, and Osaka. We dropped Hiroshima to save time."
T4 = ("I prefer trains to flights, so I want a JR Pass. "
      "Jordan is allergic to shellfish, so I need an allergy card for restaurants.")
S1 = "My partner Jordan and I are planning a trip to Japan in late March next year."
S4 = "Jordan is allergic to shellfish, so I need an allergy card for restaurants."
QUESTIONS = ("which cities will Alex and jordan visit in Japan?", "which cities will Alex and Jordan visit in Japan?")


def person(name):
    return {"name": name, "entity_type": "person"}


def place(name):
    return {"name": name, "entity_type": "location"}


def claim(subject, predicate, obj, quote, **extra):
    return {"subject": subject, "subject_entity_type": "person", "predicate": predicate, "object": obj,
            "polarity": "positive", "evidence_quote": quote, **extra}


# What a model extracts from the four turns of the issue's conversation.
EXTRACTED = {
    T1: {
        "entities": [person("Alex Rivera"), person("Jordan"), place("Japan"), person("I")],
        "facts": [claim("I", "has_partner", "Jordan", S1, object_entity_type="person"),
                  claim("I", "planning_trip_to", "Japan in late March next year", S1)],
    },
    T2: {},
    T3: {"entities": [place(name) for name in ("Tokyo", "Kyoto", "Kanazawa", "Osaka", "Hiroshima")]},
    T4: {
        "entities": [person("I"), person("Jordan"), {"name": "JR Pass", "entity_type": "product"}],
        "facts": [claim("I", "wants", "JR Pass", "I want a JR Pass.", object_entity_type="product",
                        fact_type="preference"),
                  claim("Jordan", "allergic_to", "shellfish", S4)],
    },
}


class Model:
    """The scripted extraction model; a closed gate holds every call."""

    def __init__(self):
        self.gate = asyncio.Event()
        self.gate.set()

    async def __call__(self, content, **_):
        await self.gate.wait()
        return ExtractionResult.model_validate(EXTRACTED[content])


def linked(base, *, links=True, words=True):
    """``base`` with the two #259 options, which are off unless set."""
    return base.model_copy(update={
        "enable_mention_links": links,
        "packing": base.packing.model_copy(update={"graph_seed_words": words}),
    })


@asynccontextmanager
async def trip(config, user, *, links=True, words=True, note_first=False):
    """The issue's conversation, ingested. With ``note_first`` each turn's note exists before its claims."""
    model = Model()
    async with MemoryEngine.open(linked(config, links=links, words=words)) as engine:
        engine._pipeline._extraction_provider.extract = model
        for role, text in (("user", T1), ("assistant", T2), ("user", T3), ("user", T4)):
            if not note_first:
                await engine.ingest(text, user_id=user, role=role, session_id="trip-1", wait_for_extraction=True)
                continue
            model.gate.clear()
            await engine.ingest(text, user_id=user, role=role, session_id="trip-1")
            await engine.process_pending(user_id=user, budget_ms=5000)
            model.gate.set()
            await asyncio.gather(*engine._pipeline._background_tasks)
        await engine.process_pending(user_id=user, budget_ms=5000)
        yield engine


async def node_named(engine, user, content, node_type=NodeType.ENTITY):
    [node] = [n for n in await engine.query_nodes(user_id=user, node_type=node_type, limit=200)
              if n.content == content]
    return node


async def neighbor_ids(engine, user, name, hops):
    """What lies within ``hops`` of the entities called ``name``."""
    found = set()
    for entity in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY, limit=200):
        if entity.content == name:
            found |= {n.id for n in await engine._graph_store.get_neighborhood(str(entity.id), max_hops=hops)}
    return found


async def claims_by_predicate(engine, user):
    return {n.metadata["predicate"]: n for n in await engine.query_nodes(user_id=user, node_type=NodeType.FACT)}


async def graph_hits(engine, user, question):
    response = await engine.retrieve(question, user_id=user)
    return response.metadata.candidates_generated["GRAPH"], [
        c.node.content for c in response.results if "GRAPH" in c.paths
    ]


@pytest.mark.parametrize("note_first", [False, True])
async def test_entities_link_to_the_claims_and_notes_that_name_them(config, user, note_first):
    async with trip(config, user, note_first=note_first) as engine:
        claims = await claims_by_predicate(engine, user)
        intro = await node_named(engine, user, T1, NodeType.NOTE)
        cities = await node_named(engine, user, T3, NodeType.NOTE)

        # The trip claim names Japan only in its object text, "Japan in late March next year".
        japan = await neighbor_ids(engine, user, "Japan", 1)
        assert {claims["planning_trip_to"].id, claims["has_partner"].id, intro.id} <= japan
        # No claim names the cities; the note does.
        assert await neighbor_ids(engine, user, "Tokyo", 1) == {cities.id}
        assert cities.id in await neighbor_ids(engine, user, "Hiroshima", 2)
        assert intro.id in await neighbor_ids(engine, user, "Alex Rivera", 1)
        assert claims["allergic_to"].id in await neighbor_ids(engine, user, "Jordan", 1)


async def test_links_are_text_mentions_that_leave_subject_and_object_links_alone(config, user):
    async with trip(config, user) as engine:
        claims = await claims_by_predicate(engine, user)
        names = {n.id: n.content for n in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY, limit=200)}

        async def links(claim_node):
            edges = await engine._graph_store.get_edges(node_ids=[str(claim_node.id)])
            return sorted((e.edge_type.value, names.get(e.source_id, "claim"), names.get(e.target_id, "claim"),
                           is_text_link(e)) for e in edges)

        # I -> claim is the subject link; the claim's text names Japan and Jordan, not I.
        assert await links(claims["planning_trip_to"]) == [
            ("has_fact", "I", "claim", False),
            ("mentions", "claim", "Japan", True),
            ("mentions", "claim", "Jordan", True),
        ]
        # Jordan is this claim's object, so that link stays what it was.
        assert await links(claims["has_partner"]) == [
            ("has_fact", "I", "claim", False),
            ("mentions", "claim", "Japan", True),
            ("mentions", "claim", "Jordan", False),
        ]
        [event_id] = {str(ref) for ref in claims["planning_trip_to"].evidence_refs}
        created = await link_event_mentions(
            event_id, list(claims.values()), user_id=user, graph_store=engine._graph_store,
            event_store=engine._event_store, create_edge=engine._graph_store.create_edge, with_note=True,
        )
        assert created == 0


async def test_a_shared_name_a_pronoun_and_another_owners_entity_are_not_linked(config, user):
    source = "Apple opened a store in Paris and I met Alex there."
    script = ExtractionResult.model_validate({"entities": [
        {"name": "Apple", "entity_type": "organization"}, {"name": "Apple", "entity_type": "food"},
        person("Alex"), place("Paris"), person("I"),
    ]})
    async with MemoryEngine.open(linked(config)) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=script)
        owners = (user, user + "-other")
        for owner in owners:
            await engine.ingest(source, user_id=owner, wait_for_extraction=True)
            await engine.process_pending(user_id=owner, budget_ms=5000)
        for owner in owners:
            note = await node_named(engine, owner, source, NodeType.NOTE)
            around = {name: await neighbor_ids(engine, owner, name, 1)
                      for name in ("Alex", "Paris", "Apple", "I")}
            # Only the note names Alex and Paris, in this owner's graph; Apple is two entities.
            assert around == {"Alex": {note.id}, "Paris": {note.id}, "Apple": set(), "I": set()}


async def test_a_text_link_does_not_stop_a_repeated_claim_from_merging(config, user):
    def extract(content, **_):
        return ExtractionResult.model_validate({
            "entities": [person("Alex"), place("Paris"), place("Montmartre")],
            "facts": [claim("Alex", "lives_in", "Paris", content, object_entity_type="location")],
        })

    async with MemoryEngine.open(linked(config)) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=extract)
        for text in ("Alex lives in Paris, near Montmartre.", "Alex still lives in Paris, near Montmartre!"):
            await engine.ingest(text, user_id=user, wait_for_extraction=True)
        [current] = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        assert len(current.metadata["merged_claim_ids"]) == 1
        assert current.id in await neighbor_ids(engine, user, "Montmartre", 1)


async def test_batched_extraction_links_the_same_way(config, user):
    source = "Jordan moved to Denver, near Boulder."

    class Batches:
        async def extract_batch(self, turns):
            entries = [{
                "turn": turn.index, "relationships": [],
                "entities": [person("Jordan"), place("Denver"), place("Boulder")],
                "facts": [claim("Jordan", "moved_to", "Denver", source, object_entity_type="location",
                                epistemic_type="observed")],
            } for turn in turns]
            return {"turns": entries}

    async with MemoryEngine.open(linked(config).model_copy(update={"extraction_batch_target_tokens": 512})) as engine:
        engine._pipeline._extraction_provider.extract_batch = Batches().extract_batch
        engine._pipeline._batch_flush_seconds = 3600.0
        await engine.ingest(source, user_id=user)
        await engine.process_extractions(user_id=user)
        await engine.process_pending(user_id=user, budget_ms=5000)
        [moved] = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        note = await node_named(engine, user, source, NodeType.NOTE)
        assert await neighbor_ids(engine, user, "Boulder", 1) == {moved.id, note.id}
        assert note.id in await neighbor_ids(engine, user, "Jordan", 1)


@pytest.mark.parametrize("question", QUESTIONS)
async def test_graph_search_finds_the_trip_whatever_the_capitalization(config, user, question):
    async with trip(config, user) as engine:
        count, found = await graph_hits(engine, user, question)
        # Alex, Jordan and Japan are linked to the note and the claims about the trip.
        assert count > 0 and {T1, S1} <= set(found)


@pytest.mark.parametrize("question", ["what did we decide about tokyo?", "What did we decide about Tokyo?"])
async def test_a_city_in_the_question_finds_the_note_that_names_it(config, user, question):
    async with trip(config, user) as engine:
        count, found = await graph_hits(engine, user, question)
        assert count > 0 and T3 in found


async def test_the_same_entities_seed_the_graph_in_any_case(config, user):
    async with trip(config, user) as engine:
        found = set()
        for question in (*QUESTIONS, QUESTIONS[0].lower(), QUESTIONS[0].upper()):
            analysis = await analyze_query(question)
            seeds = await _find_seed_entities(analysis, engine._graph_store, user, None, True)
            found.add(tuple(sorted(n.content for n in seeds)))
            assert (await graph_hits(engine, user, question))[0] > 0
        assert found == {("Alex Rivera", "Japan", "Jordan")}


def test_the_options_are_on_in_the_product_defaults_and_off_in_a_bare_packing_config(monkeypatch):
    from tests.conftest import STRUCTURE_OPTIONS

    for name in STRUCTURE_OPTIONS:
        monkeypatch.delenv(name)
    # On since 2026-10-19 in the alpha channel (0.16.0a3); the fast gate decided it.
    assert PRMEConfig(_env_file=None).enable_mention_links is True
    assert PRMEConfig(_env_file=None).packing.graph_seed_words is True
    # A PackingConfig built in code keeps its field default, and an off setting is left out of a dump, so
    # receipts of retrievals that do not use the option keep their bytes.
    assert PackingConfig().graph_seed_words is False
    assert "graph_seed_words" not in PackingConfig().model_dump()
    assert PackingConfig(graph_seed_words=True).model_dump()["graph_seed_words"] is True


async def all_edges(engine, user):
    entities = await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY, limit=200)
    return await engine._graph_store.get_edges(node_ids=[str(node.id) for node in entities])


async def test_with_both_options_off_the_repro_is_what_it_was(config, user):
    async with trip(config, user, links=False, words=False) as engine:
        assert not any(is_text_link(edge) for edge in await all_edges(engine, user))
        for name in ("Tokyo", "Japan", "Alex Rivera"):
            assert await neighbor_ids(engine, user, name, 2) == set(), name
        # Jordan is the one entity with neighbors: two claims and the entity "I".
        assert len(await neighbor_ids(engine, user, "Jordan", 2)) == 3
        # Only the capitalized Jordan seeds the graph.
        assert [(await graph_hits(engine, user, q))[0] for q in QUESTIONS] == [0, 4]


async def test_each_option_works_without_the_other(config, user):
    lower = QUESTIONS[0].lower()
    async with trip(config, user, links=True, words=False) as engine:
        assert any(is_text_link(edge) for edge in await all_edges(engine, user))
        # The links exist, but a question with no capitalized name still seeds nothing.
        assert (await graph_hits(engine, user, lower))[0] == 0
        assert (await graph_hits(engine, user, QUESTIONS[0]))[0] > 0
    async with trip(config, user + "-words", links=False, words=True) as engine:
        assert not any(is_text_link(edge) for edge in await all_edges(engine, user + "-words"))
        # Jordan's neighbors are the only ones there are, and now a lower-case question finds them.
        count, found = await graph_hits(engine, user + "-words", lower)
        assert count > 0 and S4 in found


def test_seed_terms_skip_function_words_numbers_and_short_words():
    assert _seed_terms("Which cities will Alex and jordan visit in 2027, or go to JR?") == [
        "cities", "alex", "jordan", "visit",
    ]
    assert _seed_terms("alex alex ALEX") == ["alex"]
    assert len(_seed_terms(" ".join(f"word{i}" for i in range(100)))) == 32


async def test_seeds_match_whole_words_of_names_not_parts_of_words(config, user):
    script = ExtractionResult.model_validate(
        {"entities": [person("Will Smith"), person("Martin"), person("Alex Rivera")]}
    )
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=script)
        await engine.ingest("Will Smith met Martin and Alex Rivera.", user_id=user, wait_for_extraction=True)

        async def seeds(question, words=True):
            analysis = await analyze_query(question)
            return sorted(n.content for n in await _find_seed_entities(analysis, engine._graph_store, user, None, words))

        assert await seeds("where is alex?", words=False) == []
        assert await seeds("what will art be like?") == []
        assert await seeds("where is alex?") == ["Alex Rivera"]
        assert await seeds("where is martin?") == ["Martin"]
        assert await seeds("where is will smith?") == ["Will Smith"]
        # A capitalized name still matches as a substring, as it did before.
        assert await seeds("Did Mart arrive?") == ["Martin"]
