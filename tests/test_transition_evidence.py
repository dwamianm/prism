"""Pairwise lifecycle evidence must belong to the affected owner and scope."""

from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.types import EdgeType, LifecycleState, Scope
from tests import test_durable_ingestion
from tests.test_atomic_contradictions import operation_count

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def apply(engine, action, first, second, evidence, user):
    if action == "supersede":
        await engine.supersede(first, second, evidence_id=evidence, user_id=user)
    elif action == "contradict":
        await engine._graph_store.contradict(first, second, evidence_id=evidence)
    else:
        await engine._graph_store.resolve_contradiction(
            first, second, resolver_actor_id=user, evidence_id=evidence
        )


@pytest.mark.parametrize("action", ["supersede", "contradict", "resolve"])
@pytest.mark.parametrize(
    "evidence_kind", ["missing", "foreign_owner", "foreign_scope", "malformed"]
)
async def test_invalid_transition_evidence_is_rejected(
    config, user, action, evidence_kind
):
    async with MemoryEngine.open(config) as engine:
        sources = [
            await engine.store(text, user_id=user)
            for text in ("An earlier claim", "A later claim")
        ]
        nodes = [
            (await engine.get_event_nodes(source, user_id=user))[0]
            for source in sources
        ]
        first, second = [str(node.id) for node in nodes]
        if action == "resolve":
            await engine._graph_store.contradict(first, second)
        if evidence_kind == "foreign_owner":
            evidence = await engine.store(
                "An unrelated source", user_id=user + "-foreign"
            )
        elif evidence_kind == "foreign_scope":
            evidence = await engine.store(
                "Another scope", user_id=user, scope=Scope.PROJECT
            )
        elif evidence_kind == "malformed":
            evidence = "invalid-evidence"
        else:
            evidence = str(uuid4())
        before = [
            await engine.get_node(n, include_superseded=True, user_id=user)
            for n in (first, second)
        ]
        edges = await engine._graph_store.get_edges(node_ids=[first, second])
        count = await operation_count(engine._graph_store, [first, second])
        with pytest.raises(
            ValueError,
            match="Evidence event is not available in the node owner and scope",
        ):
            await apply(engine, action, first, second, evidence, user)
        assert [
            await engine.get_node(n, include_superseded=True, user_id=user)
            for n in (first, second)
        ] == before
        assert await engine._graph_store.get_edges(node_ids=[first, second]) == edges
        assert await operation_count(engine._graph_store, [first, second]) == count


@pytest.mark.parametrize("action", ["supersede", "contradict", "resolve"])
@pytest.mark.parametrize("evidence_kind", ["owned", "omitted", "uuid_object"])
async def test_valid_transition_evidence_survives_restart(
    config, user, action, evidence_kind
):
    async with MemoryEngine.open(config) as engine:
        sources = [
            await engine.store(text, user_id=user) for text in ("Earlier", "Later")
        ]
        nodes = [
            (await engine.get_event_nodes(source, user_id=user))[0]
            for source in sources
        ]
        first, second = [str(node.id) for node in nodes]
        if action == "resolve":
            await engine._graph_store.contradict(first, second)
        evidence = None if evidence_kind == "omitted" else sources[1]
        if evidence_kind == "uuid_object":
            evidence = UUID(evidence)
        await apply(engine, action, first, second, evidence, user)
        expected_states = {
            "supersede": [LifecycleState.SUPERSEDED, LifecycleState.TENTATIVE],
            "contradict": [LifecycleState.CONTESTED, LifecycleState.CONTESTED],
            "resolve": [LifecycleState.STABLE, LifecycleState.DEPRECATED],
        }[action]
    async with MemoryEngine.open(config) as engine:
        actual = [
            await engine.get_node(n, include_superseded=True, user_id=user)
            for n in (first, second)
        ]
        assert [node.lifecycle_state for node in actual] == expected_states
        if action != "resolve":
            edge_type = (
                EdgeType.SUPERSEDES if action == "supersede" else EdgeType.CONTRADICTS
            )
            edges = await engine._graph_store.get_edges(
                node_ids=[first, second], edge_type=edge_type
            )
            assert len(edges) == 1
            assert edges[0].provenance_event_id == (
                UUID(str(evidence)) if evidence else None
            )
        else:
            graph = engine._graph_store
            if hasattr(graph, "_conn"):
                payload = graph._conn.execute(
                    "SELECT payload FROM operations WHERE op_type='CONTRADICTION_RESOLVED' AND target_id=?",
                    [first],
                ).fetchone()[0]
            else:
                async with graph._pool.acquire() as conn:
                    payload = await conn.fetchval(
                        "SELECT payload FROM operations WHERE op_type='CONTRADICTION_RESOLVED' AND target_id=$1",
                        first,
                    )
            import json

            record = json.loads(payload) if isinstance(payload, str) else payload
            assert record["evidence_event_id"] == (str(evidence) if evidence else None)


async def test_invalid_batch_evidence_rolls_back_earlier_replacements(config, user):
    async with MemoryEngine.open(config) as engine:
        sources = [await engine.store(str(i), user_id=user) for i in range(4)]
        nodes = [
            (await engine.get_event_nodes(source, user_id=user))[0]
            for source in sources
        ]
        ids = [str(node.id) for node in nodes]
        with pytest.raises(ValueError, match="Evidence event is not available"):
            await engine._graph_store.supersede_many(
                [
                    (ids[0], ids[1], sources[1]),
                    (ids[2], ids[3], str(uuid4())),
                ]
            )
        assert [
            await engine.get_node(n, include_superseded=True, user_id=user) for n in ids
        ] == nodes
        assert (
            await engine._graph_store.get_edges(
                node_ids=ids, edge_type=EdgeType.SUPERSEDES
            )
            == []
        )


def test_sync_correction_preserves_sources_and_rejects_foreign_evidence(
    tmp_path, monkeypatch
):
    from prme import MemoryClient
    from prme.client import config_from_directory

    monkeypatch.setattr(
        "prme.storage.engine.create_embedding_provider",
        lambda _: test_durable_ingestion.MockEmbeddingProvider(),
    )
    config = config_from_directory(str(tmp_path))
    config.organizer.opportunistic_enabled = False
    with MemoryClient(config=config) as memory:
        old_event = memory.store("My current city is Boston.", user_id="alice")
        new_event = memory.store("I moved to Chicago.", user_id="alice")
        foreign = memory.store("An unrelated statement", user_id="bob")
        old = memory.get_event_nodes(old_event, user_id="alice")[0]
        new = memory.get_event_nodes(new_event, user_id="alice")[0]
        with pytest.raises(ValueError, match="Evidence event is not available"):
            memory.supersede(
                str(old.id), str(new.id), evidence_id=foreign, user_id="alice"
            )
        assert memory.get_node(str(old.id), user_id="alice") == old
        memory.supersede(
            str(old.id), str(new.id), evidence_id=new_event, user_id="alice"
        )
    with MemoryClient(config=config) as memory:
        assert memory.get_node(str(old.id), user_id="alice") is None
        retired = memory.get_node(str(old.id), user_id="alice", include_superseded=True)
        assert retired.superseded_by == new.id
        assert (
            retired.content == old.content
            and retired.evidence_refs == old.evidence_refs
        )
        assert memory.get_node(str(new.id), user_id="alice") == new
        assert memory.get_event(old_event, user_id="alice").content == old.content
