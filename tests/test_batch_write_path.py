"""Research prototype (research/write-batch-2026-11): the batch write path stores what the current path stores.

enable_batch_write_path turns on four changes together: the filtered entity lookup, one privacy query per page of
planning inputs, one lexical commit per batch flush, and the claim key filter. These tests check each part on its
own rule and the whole path on a small batch of caller-stated facts.
"""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.ingestion.claim_merge import _normalize
from prme.ingestion.planning import PlanningGraph
from prme.models import Event, MemoryEdge, MemoryNode
from prme.privacy import PrivacyDenied
from prme.privacy.records import PRIVACY_KEY
from prme.types import EdgeType, LifecycleState, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

T0 = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)


def moves(count: int, *, objects=("red mug", "blue towel", "café box"), places=("kitchen shelf", "garage bench")):
    """Stated facts that move a few objects around, so later facts retire earlier ones."""
    messages = []
    for index in range(count):
        obj, place = objects[index % len(objects)], places[(index // len(objects)) % len(places)]
        messages.append({
            "content": f"The {obj} is on the {place}.", "role": "system", "event_time": T0 + timedelta(seconds=10 * index),
            "assertions": [{"subject": obj, "subject_type": "product", "relation": "located_in",
                            "object": place, "object_type": "location", "status": "current"}],
        })
    return messages


async def state(engine, user):
    """What a write left, without generated ids and times: active records, retired records, lexical answers."""
    nodes = await engine.query_nodes(user_id=user, limit=10_000)
    active = sorted((node.node_type.value, node.content) for node in nodes)
    retired = engine._graph_store._conn.execute(
        "SELECT node_type, content, lifecycle_state FROM nodes WHERE user_id=? AND lifecycle_state NOT IN "
        "('tentative','stable','contested') ORDER BY 1,2,3", [user]).fetchall()
    lexical = {}
    for obj in ("mug", "towel", "box", "kitchen", "garage"):
        hits = await engine._lexical_index.search(obj, user, limit=50)
        lexical[obj] = sorted(hit["content"] for hit in hits)
    return active, [tuple(row) for row in retired], lexical


async def test_option_is_off_by_default_and_turns_on_every_part(config):
    from prme import PRMEConfig

    assert PRMEConfig(_env_file=None).enable_batch_write_path is False
    async with MemoryEngine.open(config.model_copy(update={"enable_batch_write_path": True})) as engine:
        assert engine._pipeline._batch_write_path is True
        assert engine._pipeline._entity_merger._filter_by_name is True
    async with MemoryEngine.open(config) as engine:
        assert engine._pipeline._batch_write_path is False
        assert engine._pipeline._entity_merger._filter_by_name is False


@pytest.mark.parametrize("config", ["duckdb"], indirect=True)
async def test_batch_path_stores_what_the_current_path_stores(config, user, tmp_path):
    results = {}
    for name, on in (("current", False), ("batch", True)):
        directory = tmp_path / name
        (directory / "lexical").mkdir(parents=True)
        settings = config.model_copy(update={
            "db_path": str(directory / "memory.duckdb"), "lexical_path": str(directory / "lexical"),
            "vector_path": str(directory / "vectors.usearch"), "enable_batch_write_path": on,
        })
        async with MemoryEngine.open(settings) as engine:
            messages = moves(18)
            for offset in range(0, len(messages), 6):
                await engine.ingest_batch(messages[offset:offset + 6], user_id=user, wait_for_extraction=True)
            results[name] = await state(engine, user)
            # Nothing waits in the lexical writer after a batch: the flush committed it.
            assert engine._lexical_index._uncommitted == 0
        async with MemoryEngine.open(settings) as engine:
            assert await state(engine, user) == results[name], "a reopen reads what the session read"
    assert results["batch"] == results["current"]
    active, retired, lexical = results["batch"]
    # Every object's newest place is current, and the restated places are retired, not deleted.
    assert retired and lexical["mug"]


@pytest.mark.parametrize("config", ["duckdb"], indirect=True)
async def test_claim_key_filter_keeps_every_copy_the_key_matches(config, user):
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        subject = MemoryNode(node_type=NodeType.ENTITY, content="red mug", user_id=user, metadata={"entity_type": "product"})
        await graph.create_node(subject)
        values = [("located_in", "Kitchen  Shelf"), ("Located_IN", "kitchen shelf"), ("located in", "kitchen\tshelf"),
                  ("located_in", "garage bench"), ("owns", "kitchen shelf"), ("located_in", "ｋｉｔｃｈｅｎ shelf"),
                  ("located_in", "kitchen shelf ")]
        targets = {}
        for predicate, obj in values:
            fact = MemoryNode(node_type=NodeType.FACT, content=f"{predicate} {obj}", user_id=user,
                              metadata={"predicate": predicate, "object": obj})
            await graph.create_node(fact)
            await graph.create_edge(MemoryEdge(source_id=subject.id, target_id=fact.id, edge_type=EdgeType.HAS_FACT,
                                               user_id=user, provenance_event_id=uuid4()))
            targets[(predicate, obj)] = str(fact.id)
        retired = MemoryNode(node_type=NodeType.FACT, content="old", user_id=user, valid_to=T0,
                             metadata={"predicate": "located_in", "object": "kitchen shelf"})
        await graph.create_node(retired)
        await graph.create_edge(MemoryEdge(source_id=subject.id, target_id=retired.id, edge_type=EdgeType.HAS_FACT,
                                           user_id=user, provenance_event_id=uuid4()))
        found = set(await graph.fact_targets_by_key(str(subject.id), user, _normalize("located_in"),
                                                    _normalize("kitchen shelf")))
        exact = {target for (predicate, obj), target in targets.items()
                 if (_normalize(predicate), _normalize(obj)) == ("located in", "kitchen shelf")}
        # A superset of the exact matches; the non-ASCII value passes, the other predicate and place do not.
        assert exact <= found
        assert targets[("located_in", "ｋｉｔｃｈｅｎ shelf")] in found
        assert targets[("located_in", "garage bench")] not in found
        assert targets[("owns", "kitchen shelf")] not in found
        assert str(retired.id) not in found


@pytest.mark.parametrize("config", ["duckdb"], indirect=True)
async def test_privacy_page_check_refuses_what_the_object_check_refuses(config, user):
    async with MemoryEngine.open(config) as engine:
        privacy = engine._privacy
        graph = engine._graph_store
        event = Event(content="The red mug is on the shelf.", user_id=user, role="system")
        plain = [MemoryNode(node_type=NodeType.FACT, content=f"fact {index}", user_id=user, evidence_refs=[event.id])
                 for index in range(5)]
        for node in plain:
            await graph.create_node(node)
        await privacy.require_legacy_inputs((event,), plain)
        await privacy.require_legacy_page((event,), plain)
        # A marker in a node's stored row (not in the copy the caller holds) is refused by both checks the same way.
        marked = MemoryNode(node_type=NodeType.FACT, content="marked", user_id=user, evidence_refs=[event.id])
        await graph.create_node(marked)
        graph._conn.execute("UPDATE nodes SET metadata=? WHERE id=?",
                                  [json.dumps({PRIVACY_KEY: {"event_id": "not-a-uuid"}}), str(marked.id)])
        for check in (privacy.require_legacy_inputs, privacy.require_legacy_page):
            with pytest.raises(PrivacyDenied) as refused:
                await check((event,), [*plain, marked])
            assert refused.value.code == "privacy_processing_unavailable"
        # A marker on the copy the caller holds is refused before any read.
        held = plain[0].model_copy(update={"metadata": {PRIVACY_KEY: {}}})
        for check in (privacy.require_legacy_inputs, privacy.require_legacy_page):
            with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
                await check((event,), [held])


@pytest.mark.parametrize("config", ["duckdb"], indirect=True)
async def test_planning_graph_checks_a_page_once(config, user):
    async with MemoryEngine.open(config) as engine:
        nodes = [MemoryNode(node_type=NodeType.FACT, content=f"fact {index}", user_id=user) for index in range(4)]
        for node in nodes:
            await engine._graph_store.create_node(node)
        event = Event(content="x", user_id=user, role="system")
        for pages, expected in ((False, [1, 1, 1, 1]), (True, [4])):
            seen = []

            async def check(found=()):
                seen.append(len(found))

            graph = PlanningGraph(engine._graph_store, event, legacy_check=check, check_pages=pages)
            got = await graph.get_nodes([str(node.id) for node in nodes])
            assert [node.id for node in got] == [node.id for node in nodes]
            assert seen == expected


@pytest.mark.parametrize("config", ["duckdb"], indirect=True)
async def test_deferred_stage_keeps_the_identity_check(config, user):
    async with MemoryEngine.open(config) as engine:
        index = engine._lexical_index
        document = {"node_id": [str(uuid4())], "content": ["red mug"], "user_id": [user], "node_type": ["fact"],
                    "scope": [Scope.PERSONAL.value]}
        index._do_stage_deferred((document,))
        # The same document again in the same batch is found in the writer and not added twice.
        index._do_stage_deferred((document,))
        assert index._uncommitted == 1
        conflict = {**document, "content": ["blue towel"]}
        with pytest.raises(ValueError, match="existing lexical identity"):
            index._do_stage_deferred((conflict,))
        assert index._uncommitted == 1, "a refused stage adds nothing and keeps the batch"
        await index.commit_deferred()
        assert [hit["content"] for hit in await index.search("mug", user)] == ["red mug"]
        # After the commit the committed index decides, as in the current stage.
        with pytest.raises(ValueError, match="existing lexical identity"):
            index._do_stage_deferred((conflict,))
        index._do_stage_deferred((document,))
        assert index._uncommitted == 0
        await index.commit_deferred([document["node_id"][0]])
        assert await index.search("mug", user) == []
