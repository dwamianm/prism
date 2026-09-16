"""Public provenance reconstructs owned evidence and paged node history."""

from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.models import MemoryEdge
from prme.types import EdgeType, EpistemicType, Scope
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _node(engine, event_id, owner):
    return (await engine.get_event_nodes(event_id, user_id=owner))[0]


async def test_provenance_returns_sources_and_pages_operations(config, user):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store(
            "If approved, deploy Atlas.", user_id=user, scope=Scope.PROJECT,
            epistemic_type=EpistemicType.CONDITIONAL,
            metadata={"condition": "approved"},
        )
        node = await _node(engine, source, user)
        evidence = await engine.store(
            "Atlas was approved.", user_id=user, scope=Scope.PROJECT
        )
        await engine.evaluate_condition(
            str(node.id), "true", user_id=user, evidence_id=evidence,
            request_id=str(uuid4()),
        )
        await engine.evaluate_condition(
            str(node.id), "false", user_id=user, request_id=str(uuid4()),
        )

        first = await engine.get_provenance(
            str(node.id), user_id=user, operation_limit=1
        )
        assert first.node.id == node.id
        assert [str(event.id) for event in first.evidence_events] == [source, evidence]
        assert first.missing_evidence_refs == ()
        assert len(first.operations) == 1
        assert first.operations[0].op_type == "EPISTEMIC_TRANSITION"
        assert first.operations[0].payload["record"]
        assert first.next_operation_cursor

        second = await engine.get_provenance(
            str(node.id), user_id=user,
            operation_cursor=first.next_operation_cursor, operation_limit=1,
        )
        assert len(second.operations) == 1
        assert second.operations[0].id != first.operations[0].id
        assert second.next_operation_cursor is None
        assert second.operations[0].created_at >= first.operations[0].created_at

        assert await engine.get_provenance(
            str(node.id), user_id=user + "-other"
        ) is None
        with pytest.raises(ValueError, match="Invalid operation_cursor"):
            await engine.get_provenance(
                str(node.id), user_id=user, operation_cursor="not-a-cursor"
            )
        with pytest.raises(ValueError, match="operation_limit"):
            await engine.get_provenance(
                str(node.id), user_id=user, operation_limit=0
            )


async def test_provenance_reports_missing_evidence_and_hides_foreign_edges(config, user):
    async with MemoryEngine.open(config) as engine:
        event_a = await engine.store("Claim A", user_id=user)
        event_b = await engine.store("Claim B", user_id=user)
        foreign_event = await engine.store("Foreign", user_id=user + "-other")
        a, b = await _node(engine, event_a, user), await _node(engine, event_b, user)
        foreign = await _node(engine, foreign_event, user + "-other")
        await engine.contradict(str(a.id), str(b.id), user_id=user)
        await engine._graph_store.create_edge(MemoryEdge(
            source_id=a.id, target_id=foreign.id, edge_type=EdgeType.CONTRADICTS,
            user_id=user,
        ))
        missing = uuid4()
        await engine._graph_store.update_node(
            str(a.id), evidence_refs=[*a.evidence_refs, missing]
        )

        result = await engine.get_provenance(str(a.id), user_id=user)
        assert result.missing_evidence_refs == (missing,)
        assert len(result.contradiction_edges) == 1
        edge = result.contradiction_edges[0]
        assert {edge.source_id, edge.target_id} == {a.id, b.id}
        assert foreign.id not in {edge.source_id, edge.target_id}
