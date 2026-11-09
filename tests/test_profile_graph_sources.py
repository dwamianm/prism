"""A named person's profile takes the claims the graph binds to them (#448).

A first-person claim is stored under the speaker's entity without the
person's name, and a claim taken from their turn may name someone else, so
name matching alone never sees either. The profile reads the HAS_FACT edge
from the person's entity and the source turn's speaker instead.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from prme import MemoryEngine
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.profiles import build_profile
from prme.types import EdgeType, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

BUDGET = 2000


async def make_node(
    engine, owner, node_type, content, *, metadata=None, evidence=None,
    scope=Scope.PERSONAL, identifier=None, event_time=None,
):
    node = MemoryNode(
        id=identifier if identifier is not None else uuid4(),
        user_id=owner, node_type=node_type, scope=scope,
        content=content, metadata=metadata, event_time=event_time,
        evidence_refs=[UUID(value) for value in evidence or ()],
    )
    await engine._graph_store.create_node(node)
    return node


async def bind_subject(engine, owner, entity, claim):
    """The edge ingest() writes from a claim's subject entity to the claim."""
    await engine._graph_store.create_edge(MemoryEdge(
        source_id=entity.id, target_id=claim.id, edge_type=EdgeType.HAS_FACT, user_id=owner,
    ))


async def active_profile(engine, owner, name):
    nodes = await engine.query_nodes(user_id=owner, node_type=NodeType.SUMMARY)
    matches = [
        node for node in nodes
        if (node.metadata or {}).get("entity_profile") and (node.metadata or {}).get("entity_name") == name
    ]
    assert len(matches) == 1, matches
    return matches[0]


async def test_a_first_person_claim_reaches_the_profile_through_its_entity(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("Maya Okafor is a member of the family plan", user_id=user)
        entity = await make_node(engine, user, NodeType.ENTITY, "Maya Okafor")
        claim = await make_node(
            engine, user, NodeType.FACT, "I am pregnant",
            metadata={"subject": "I", "predicate": "is_pregnant", "object": "pregnant"},
        )
        await bind_subject(engine, user, entity, claim)

        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL, entity_names=["Maya Okafor"], max_profile_tokens=BUDGET,
        ) == 1
        profile = await active_profile(engine, user, "Maya Okafor")

    assert "I am pregnant" in profile.content
    assert profile.metadata["source_paths"]["subject"] == {"available": 1, "included": 1}


async def test_a_claim_from_the_persons_turn_reaches_the_profile(config, user):
    async with MemoryEngine.open(config) as engine:
        turn = await engine.store(
            "OK. My husband is Theo and my child is Nico.", user_id=user, speaker="Maya Okafor",
        )
        await engine.store("Maya Okafor is a member of the family plan", user_id=user)
        await make_node(engine, user, NodeType.ENTITY, "Maya Okafor")
        # The claim names Theo and Maya, not the speaker, and it was written
        # with the name in place of her I, so no speaker key is stored on it.
        await make_node(
            engine, user, NodeType.FACT, "My husband is Theo and my child is Nico.",
            metadata={"subject": "Theo", "predicate": "is_husband_of", "object": "Maya Okafor"},
            evidence=[turn],
        )

        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL, entity_names=["Maya Okafor"], max_profile_tokens=BUDGET,
        ) == 1
        profile = await active_profile(engine, user, "Maya Okafor")

    assert "My husband is Theo and my child is Nico." in profile.content
    assert profile.metadata["source_paths"]["spoken"] == {"available": 1, "included": 1}


async def test_one_event_time_keeps_the_turn_over_its_narrower_claim(config, user):
    """Issue #497: with one budget slot, the turn keeps both facts, not the claim."""
    at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    turn_text = "OK. My husband is Theo and my child is Nico."
    async with MemoryEngine.open(config) as engine:
        await engine.store(turn_text, user_id=user, speaker="Maya Okafor", event_time=at)
        entity = await make_node(
            engine, user, NodeType.ENTITY, "Maya Okafor", metadata={"entity_type": "person"},
        )
        claim = await make_node(
            engine, user, NodeType.FACT, "Nico is child of Maya Okafor.",
            # The lowest id sorts before the turn under the old id tie-break.
            identifier=UUID(int=0), event_time=at,
            metadata={"subject": "Nico", "predicate": "is_child_of", "object": "Maya Okafor"},
        )
        await bind_subject(engine, user, entity, claim)
        turn = next(
            node for node in await engine.query_nodes(user_id=user, node_type=NodeType.NOTE)
            if node.content == turn_text
        )
        # Only the header and the turn fit.
        budget = build_profile(
            "Maya Okafor", [turn], token_budget=10_000,
            tokenizer=engine._config.packing.tokenizer,
        )[2]

        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL, entity_names=["Maya Okafor"],
            max_profile_tokens=budget, include_speaker_turns=True,
        ) == 1
        profile = await active_profile(engine, user, "Maya Okafor")

    assert profile.metadata["source_count_available"] == 3
    assert profile.metadata["source_node_ids"] == [str(turn.id)]
    assert "Theo" in profile.content


async def test_one_users_first_person_claims_never_reach_another_users_profile(config):
    first, second = f"first-{uuid4()}", f"second-{uuid4()}"
    async with MemoryEngine.open(config) as engine:
        for owner, name, text, subject, obj in (
            (first, "Maya Okafor", "I am pregnant", "is_pregnant", "pregnant"),
            (second, "Dana Whitfield", "I was just diagnosed with breast cancer", "diagnosed_with", "breast cancer"),
        ):
            turn = await engine.store(text, user_id=owner, speaker=name)
            await engine.store(f"{name} is a member of the family plan", user_id=owner)
            entity = await make_node(engine, owner, NodeType.ENTITY, name)
            claim = await make_node(
                engine, owner, NodeType.FACT, text,
                metadata={"subject": "I", "predicate": subject, "object": obj}, evidence=[turn],
            )
            await bind_subject(engine, owner, entity, claim)

        assert await engine.consolidate_knowledge(
            user_id=first, scope=Scope.PERSONAL, entity_names=["Maya Okafor"], max_profile_tokens=BUDGET,
        ) == 1
        profile = await active_profile(engine, first, "Maya Okafor")

    assert "I am pregnant" in profile.content
    assert "breast cancer" not in profile.content
    assert "Dana Whitfield" not in profile.content


async def test_the_staleness_read_sees_a_graph_bound_claim(config, user):
    """A profile built before extraction finishes is rebuilt when its claims land."""
    from prme.organizer.owner_profiles import _mentioning

    async with MemoryEngine.open(config) as engine:
        await engine.store("Maya Okafor is a member of the family plan", user_id=user)
        entity = await make_node(engine, user, NodeType.ENTITY, "Maya Okafor",
                                 metadata={"entity_type": "person"})
        claim = await make_node(
            engine, user, NodeType.FACT, "I am pregnant",
            metadata={"subject": "I", "predicate": "is_pregnant", "object": "pregnant"},
        )
        await bind_subject(engine, user, entity, claim)

        found = await _mentioning(engine, user_id=user, scope=Scope.PERSONAL, name="Maya Okafor")

    assert claim.id in {node.id for node in found}


async def test_the_staleness_read_sees_a_claim_from_the_persons_turn(config, user):
    """A claim that names someone else is found through the turn it came from."""
    from prme.organizer.owner_profiles import _mentioning

    async with MemoryEngine.open(config) as engine:
        turn = await engine.store(
            "OK. My husband is Theo and my child is Nico.", user_id=user, speaker="Maya Okafor",
        )
        await engine.store("Maya Okafor is a member of the family plan", user_id=user)
        await make_node(engine, user, NodeType.ENTITY, "Maya Okafor",
                        metadata={"entity_type": "person"})
        claim = await make_node(
            engine, user, NodeType.FACT, "My husband is Theo and my child is Nico.",
            metadata={"subject": "Theo", "predicate": "is_husband_of", "object": "Maya Okafor"},
            evidence=[turn],
        )

        found = await _mentioning(engine, user_id=user, scope=Scope.PERSONAL, name="Maya Okafor")

    assert claim.id in {node.id for node in found}


async def test_a_person_and_an_organisation_of_the_same_name_keep_their_claims_apart(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("Maya Okafor is a member of the family plan", user_id=user)
        person = await make_node(engine, user, NodeType.ENTITY, "Maya Okafor",
                                 metadata={"entity_type": "person"})
        organisation = await make_node(engine, user, NodeType.ENTITY, "Maya Okafor",
                                       metadata={"entity_type": "organisation"})
        mine = await make_node(
            engine, user, NodeType.FACT, "I am pregnant",
            metadata={"subject": "I", "predicate": "is_pregnant", "object": "pregnant"},
        )
        theirs = await make_node(
            engine, user, NodeType.FACT, "We acquired Acme",
            metadata={"subject": "we", "predicate": "acquired", "object": "Acme"},
        )
        await bind_subject(engine, user, person, mine)
        await bind_subject(engine, user, organisation, theirs)

        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL, entity_names=["Maya Okafor"], max_profile_tokens=BUDGET,
        ) == 1
        profile = await active_profile(engine, user, "Maya Okafor")

    assert "I am pregnant" in profile.content
    assert "We acquired Acme" not in profile.content


async def test_a_claim_from_another_scope_never_reaches_the_profile(config, user):
    async with MemoryEngine.open(config) as engine:
        for scope, text, predicate, obj in (
            (Scope.PERSONAL, "I am pregnant", "is_pregnant", "pregnant"),
            (Scope.PROJECT, "I was just diagnosed with breast cancer", "diagnosed_with", "breast cancer"),
        ):
            await engine.store(f"Maya Okafor is a member of the {scope.value} plan", user_id=user, scope=scope)
            entity = await make_node(engine, user, NodeType.ENTITY, "Maya Okafor", scope=scope)
            claim = await make_node(
                engine, user, NodeType.FACT, text, scope=scope,
                metadata={"subject": "I", "predicate": predicate, "object": obj},
            )
            await bind_subject(engine, user, entity, claim)

        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL, entity_names=["Maya Okafor"], max_profile_tokens=BUDGET,
        ) == 1
        profile = await active_profile(engine, user, "Maya Okafor")

    assert "I am pregnant" in profile.content
    assert "breast cancer" not in profile.content


async def test_without_an_entity_node_the_claims_are_left_out(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("Aurora likes the private plan", user_id=user)
        await engine.store("Aurora renewed the private plan", user_id=user)
        turn = await engine.store("I take the bus to work", user_id=user, speaker="Aurora")
        await make_node(
            engine, user, NodeType.FACT, "I take the bus to work",
            metadata={"subject": "I", "predicate": "commutes_by", "object": "bus"}, evidence=[turn],
        )

        assert await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PERSONAL, entity_names=["Aurora"], max_profile_tokens=BUDGET,
        ) == 1
        profile = await active_profile(engine, user, "Aurora")

    assert "I take the bus to work" not in profile.content
    assert profile.metadata["source_paths"]["subject"]["available"] == 0
    assert profile.metadata["source_paths"]["spoken"]["available"] == 0
