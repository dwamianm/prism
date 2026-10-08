"""KAK-1114 consumer filters, role defaults, and unnamed first-person grounding."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme import MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.client import config_from_directory
from prme.config import MCPConfig
from prme.epistemic.inference import infer_epistemic_type
from prme.ingestion.extraction import validate_raw_output
from prme.ingestion.grounding import mentioned_or_spoken
from prme.ingestion.schema import ExtractionResult
from prme.mcp.server import create_mcp_server
from prme.retrieval.models import RetrievalCandidate
from prme.types import EpistemicType, NodeType, RetrievalMode, Scope, SourceType
from tests.test_durable_ingestion import MockEmbeddingProvider, config, user  # noqa: F401


@pytest.mark.parametrize("message,predicate,kind", [
    ("My name is Sam.", "name", "fact"),
    ("Call me Sam.", "wants_to_be_called", "preference"),
    ("Please call me Sam, and keep answers short.", "prefers_to_be_called", "preference"),
])
async def test_unnamed_first_person_survives_ingestion_and_restart(config, user, message, predicate, kind):  # noqa: F811
    raw = {"entities": [{"name": "I", "entity_type": "person"}, {"name": "Sam", "entity_type": "person"}],
           "facts": [{"subject": "I", "predicate": predicate, "object": "Sam", "fact_type": kind,
                      "polarity": "positive", "epistemic_type": "asserted", "evidence_quote": message}]}
    result = validate_raw_output(raw, message)
    assert len(result.facts) == 1
    async with MemoryEngine.open(config) as engine:
        assert not config.enable_speaker_grounding
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=result)
        event = await engine.ingest(message, user_id=user, role="user", wait_for_extraction=True)
        record = await engine.get_extraction(event, user_id=user)
        assert record.grounding_policy == "speech_act_v14"
        plan = await engine._event_store.get_derivation_plan(event, user_id=user)
        assert plan.materialization_policy == "speech_act_v14"
        nodes = await engine.get_event_nodes(event, user_id=user)
        claims = [node for node in nodes if node.node_type in {NodeType.FACT, NodeType.PREFERENCE}]
        assert len(claims) == 1
        assert claims[0].metadata["subject"] == "I" and claims[0].metadata["object"] == "Sam"
    async with MemoryEngine.open(config) as engine:
        assert (await engine.get_extraction(event, user_id=user)).model_dump() == record.model_dump()
        assert (await engine._event_store.get_derivation_plan(event, user_id=user)).checksum == plan.checksum


@pytest.mark.parametrize("form", ["I", "me", "my", "mine", "myself"])
def test_first_person_forms_never_bind_a_name_or_plural(form):
    assert mentioned_or_spoken("I", f"Sam mentioned {form}.")
    assert not mentioned_or_spoken("Dana", f"Sam mentioned {form}.")
    assert not mentioned_or_spoken("I", "Our family uses AI, i.e. models.")
    assert not mentioned_or_spoken("I", "Keep answers short.")
    assert not mentioned_or_spoken("I", "My name is Sam.", first_person_forms=False)


async def test_v12_replanning_keeps_literal_sentence_selection(config, user):  # noqa: F811
    message = "My name is Sam. The plan covers surgery."
    result = ExtractionResult.model_validate({
        "entities": [{"name": "I", "entity_type": "person"}],
        "facts": [{"subject": "I", "predicate": "name", "object": "Sam", "evidence_quote": message}],
    })
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast(message, user_id=user)
        event = await engine.get_event(event_id, user_id=user)
        old = await engine._pipeline._prepare_plan(result, event, materialization_policy="speech_act_v12")
        new = await engine._pipeline._prepare_plan(result, event, materialization_policy="speech_act_v13")
        assert [node.content for node in old.nodes if node.node_type == NodeType.FACT] == [message]
        assert [node.content for node in new.nodes if node.node_type == NodeType.FACT] == ["My name is Sam."]


@pytest.mark.parametrize("role", ["assistant", "system", "ASSISTANT"])
@pytest.mark.parametrize("node_type", [NodeType.NOTE, NodeType.EVENT, NodeType.FACT, NodeType.INSTRUCTION])
def test_generated_role_is_inferred_for_all_direct_node_types(role, node_type):
    assert infer_epistemic_type(node_type, role=role) == EpistemicType.INFERRED
    assert infer_epistemic_type(NodeType.EVENT, role="user") == EpistemicType.OBSERVED


async def test_role_defaults_and_explicit_override_survive_restart(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        note = await engine.store("Since you're planning a baby", user_id=user, role="assistant")
        raw = await engine.ingest_fast("Generated event", user_id=user, role="system")
        explicit = await engine.store("An explicit observed record", user_id=user, role="assistant",
                                      node_type=NodeType.EVENT, epistemic_type=EpistemicType.OBSERVED)
        await engine.process_pending(user_id=user)
    async with MemoryEngine.open(config) as engine:
        for event_id in (note, raw):
            [node] = await engine.get_event_nodes(event_id, user_id=user)
            assert node.epistemic_type == EpistemicType.INFERRED
            assert node.source_type == SourceType.SYSTEM_INFERRED
        [node] = await engine.get_event_nodes(explicit, user_id=user)
        assert node.epistemic_type == EpistemicType.OBSERVED


async def seed(engine, owner):
    for scope in (Scope.PERSONAL, Scope.PROJECT):
        for text, kind, role in [
            ("What does my plan cover for breast cancer treatment?", NodeType.NOTE, "user"),
            ("Hypothetically, if I had a $4,000 surgery, how much would I pay?", NodeType.NOTE, "user"),
            ("Since you're planning a baby, consider adding your spouse", NodeType.FACT, "assistant"),
            ("Sam", NodeType.ENTITY, "user"),
            ("I prefer short answers about plan coverage", NodeType.PREFERENCE, "user"),
        ]:
            await engine.store(text, user_id=owner, scope=scope, session_id="coverage", node_type=kind, role=role)


@pytest.mark.parametrize("mode", [RetrievalMode.DEFAULT, RetrievalMode.EXPLICIT])
async def test_filters_cover_primary_context_hints_limits_and_receipts(config, user, mode):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        control = await engine.retrieve("plan coverage", user_id=user, scope=Scope.PERSONAL)
        assert control.cross_scope_hints
        response = await engine.retrieve("plan coverage", user_id=user, scope=Scope.PERSONAL, limit=1,
                                         retrieval_mode=mode, exclude_node_types={NodeType.NOTE, NodeType.ENTITY},
                                         source_types={SourceType.USER_STATED})
        assert len(response.results) == 1 and response.cross_scope_hints
        for candidate in [*response.results, *response.cross_scope_hints]:
            assert candidate.node.node_type == NodeType.PREFERENCE
            assert candidate.node.source_type == SourceType.USER_STATED
        for text in ("breast cancer", "$4,000", "baby", "Sam"):
            assert text not in response.bundle.rendered_context
        assert {item.reason for item in response.excluded} >= {"node_type_filtered:note", "source_type_filtered:system_inferred"}
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert receipt.execution.parameters["exclude_node_types"] == ["entity", "note"]
        assert receipt.execution.parameters["source_types"] == ["user_stated"]
        assert response.filter_metadata.source_types == ["user_stated"]
        empty = await engine.retrieve("plan coverage", user_id=user, scope=Scope.PERSONAL, source_types=[])
        assert empty.results == [] and empty.cross_scope_hints == [] and not empty.bundle.rendered_context


@pytest.mark.parametrize("stage,setting", [
    ("expand_session_context", "session_context_window"),
    ("project_evidence_context", "evidence_projection_top_k"),
    ("augment_evidence_context", "evidence_augmentation_top_k"),
])
async def test_late_expansion_cannot_reintroduce_filtered_content(config, user, stage, setting, monkeypatch):  # noqa: F811
    config.packing = config.packing.model_copy(update={setting: 2})
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        nodes = await engine.query_nodes(user_id=user)
        blocked = [RetrievalCandidate(node=node, composite_score=1) for node in nodes
                   if node.node_type == NodeType.NOTE or node.source_type == SourceType.SYSTEM_INFERRED]
        async def inject(scored, **kwargs):
            return [*scored, *blocked]
        mock = AsyncMock(side_effect=inject)
        monkeypatch.setattr(f"prme.retrieval.pipeline.{stage}", mock)
        response = await engine.retrieve("plan coverage", user_id=user, exclude_node_types={NodeType.NOTE, NodeType.ENTITY},
                                         source_types={SourceType.USER_STATED})
        assert mock.await_count == 1
        assert response.results and all(c.node.node_type == NodeType.PREFERENCE for c in response.results)
        assert "baby" not in response.bundle.rendered_context and "$4,000" not in response.bundle.rendered_context


@pytest.mark.parametrize("bounds", [{"exclude_node_types": ["invalid"]}, {"exclude_node_types": "note"},
                                     {"source_types": ["invalid"]}, {"source_types": "user_stated"}])
async def test_invalid_type_filters_fail_before_draining_work(config, user, bounds):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        event = await engine.ingest_fast("Pending source", user_id=user)
        with pytest.raises(ValueError):
            await engine.retrieve("source", user_id=user, **bounds)
        assert (await engine.processing_status(event, user_id=user)).attempts == 0


async def test_http_type_filters(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            body = {"query": "plan coverage", "user_id": user,
                    "filters": {"exclude_node_types": ["note", "entity"], "source_types": ["user_stated"]}}
            result = await client.post("/v1/retrieve", json=body)
            assert result.status_code == 200
            assert result.json()["results"] and all(c["node_type"] == "preference" for c in result.json()["results"])
            body["filters"]["source_types"] = []
            assert (await client.post("/v1/retrieve", json=body)).json()["results"] == []
            body["filters"]["source_types"] = ["invalid"]
            assert (await client.post("/v1/retrieve", json=body)).status_code == 422


async def test_mcp_type_filters(config, user):  # noqa: F811
    config.mcp = MCPConfig(user_id=user)
    server = create_mcp_server(config=config)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
        await session.initialize()
        for node_type in ("note", "preference", "entity"):
            await session.call_tool("memory_store", {"content": "plan coverage", "node_type": node_type})
        result = await session.call_tool("memory_retrieve", {"query": "plan coverage", "exclude_node_types": ["note", "entity"],
                                                             "source_types": ["user_stated"], "include_context": True})
        data = json.loads(result.content[0].text)
        assert data["results"] and all(c["node_type"] == "preference" for c in data["results"])
        result = await session.call_tool("memory_retrieve", {"query": "plan coverage", "source_types": []})
        assert json.loads(result.content[0].text)["results"] == []


def test_sync_client_type_filters(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    with MemoryClient(config=config_from_directory(str(tmp_path))) as client:
        client.store("What does my coverage include?", user_id="member")
        client.store("I prefer short answers", user_id="member", node_type=NodeType.PREFERENCE)
        result = client.retrieve("coverage", user_id="member", exclude_node_types={NodeType.NOTE}, source_types={SourceType.USER_STATED})
        assert [c.node.node_type for c in result.results] == [NodeType.PREFERENCE]


async def test_projection_keeps_claim_when_its_raw_source_is_excluded(config, user):  # noqa: F811
    config.packing = config.packing.model_copy(update={"evidence_projection_top_k": 2})
    message = "My name is Sam."
    raw = {"entities": [{"name": "I", "entity_type": "person"}],
           "facts": [{"subject": "I", "predicate": "name", "object": "Sam",
                      "polarity": "positive", "epistemic_type": "asserted", "evidence_quote": message}]}
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=validate_raw_output(raw, message))
        await engine.ingest(message, user_id=user, wait_for_extraction=True)
        response = await engine.retrieve("Sam", user_id=user, exclude_node_types={NodeType.NOTE, NodeType.ENTITY},
                                         source_types={SourceType.USER_STATED})
        assert [c.node.node_type for c in response.results] == [NodeType.FACT]
        assert "My name is Sam." in response.bundle.rendered_context
