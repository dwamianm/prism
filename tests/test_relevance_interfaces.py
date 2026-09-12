"""Public sync, HTTP and MCP relevance workflows use authenticated receipts."""
from contextlib import asynccontextmanager
import json
from uuid import uuid4

import pytest

from mcp.shared.memory import create_connected_server_and_client_session

from prme import MemoryClient, MemoryEngine, RelevanceSubmission
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_http_records_owned_relevance_and_retry_identity(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue.", user_id=user)
        async with client_for(app_for(config, engine, user)) as client:
            retrieved = (await client.post("/v1/retrieve", json={"query": "telescope", "min_score": 0})).json()
            assert retrieved["metrics"]["receipt_persisted"]
            rid = retrieved["metrics"]["request_id"]
            receipt = await client.get(f"/v1/retrievals/{rid}")
            assert receipt.status_code == 200
            nid = receipt.json()["candidates"][0]["node_id"]
            fid = str(uuid4())
            body = {"request_id": rid, "feedback_id": fid, "labels": {nid: True}}
            first = await client.post("/v1/relevance", json=body)
            assert first.status_code == 200, first.text
            assert (await client.post("/v1/relevance", json=body)).json() == first.json()
            assert (await client.get(f"/v1/relevance/{fid}")).json() == first.json()
            assert (await client.get("/v1/relevance")).json() == [first.json()]
            foreign = {"Authorization": "Bearer other-token"}
            assert (await client.get(f"/v1/retrievals/{rid}", headers=foreign)).status_code == 404
            assert (await client.get(f"/v1/relevance/{fid}", headers=foreign)).status_code == 404
            assert (await client.post("/v1/relevance", json=body, headers=foreign)).status_code == 404
            assert (await client.post("/v1/relevance", json={**body, "user_id": user + "-other"})).status_code == 403
            assert (await client.post("/v1/relevance", json={**body, "labels": {nid: "true"}})).status_code == 422
            assert (await client.post("/v1/relevance", json={**body, "labels": {nid: False}})).status_code == 400
            assert (await client.post("/v1/relevance", json={**body, "feedback_id": str(uuid4()), "labels": {str(uuid4()): True}})).status_code == 400
            assert (await client.get("/v1/relevance?limit=-1")).status_code == 422
            assert (await client.get("/v1/retrievals/not-a-uuid")).status_code == 422
            assert (await client.get("/v1/relevance/not-a-uuid")).status_code == 422


def test_sync_client_records_and_reads_relevance(config, user):
    with MemoryClient(config=config) as client:
        client.store("The telescope is blue.", user_id=user)
        response = client.retrieve("telescope", user_id=user, min_score=0)
        receipt = client.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        submission = RelevanceSubmission(request_id=receipt.request_id, labels={receipt.candidates[0].node_id: True})
        record = client.record_relevance(submission, user_id=user)
        assert client.get_relevance(str(record.feedback_id), user_id=user) == record
        assert client.list_relevance(user_id=user) == [record]
        nid = str(receipt.candidates[0].node_id)
        original = client.get_node(nid, user_id=user)
        for action in (client.promote, client.archive):
            with pytest.raises(ValueError):
                action(nid, user_id=user + "-other")
        assert client.get_node(nid, user_id=user) == original
        client.promote(nid, user_id=user)
        assert client.get_node(nid, user_id=user).lifecycle_state.value == "stable"
        client.archive(nid, user_id=user)
        assert client.retrieve("telescope", user_id=user, min_score=0).results == []
        assert client.get_retrieval_receipt(str(receipt.request_id), user_id=user) == receipt
    with MemoryClient(config=config) as client:
        assert client.get_node(nid, user_id=user, include_superseded=True).lifecycle_state.value == "archived"
        assert client.get_relevance(str(record.feedback_id), user_id=user) == record
        assert client.get_event(str(original.evidence_refs[0]), user_id=user) is not None


async def test_mcp_relevance_tool_workflow_preserves_binding(config, user):
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue.", user_id=user)

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
            await session.initialize()
            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.isError, response
                return json.loads(response.content[0].text)
            retrieved = await call("memory_retrieve", {"query": "telescope", "min_score": 0})
            rid = retrieved["metrics"]["request_id"]
            receipt = await call("memory_get_retrieval_receipt", {"request_id": rid})
            nid = receipt["candidates"][0]["node_id"]
            arguments = {"request_id": rid, "feedback_id": str(uuid4()), "labels": {nid: True}}
            record = await call("memory_record_relevance", arguments)
            assert "error" not in record, record
            assert await call("memory_record_relevance", arguments) == record
            assert await call("memory_get_relevance", {"feedback_id": record["feedback_id"]}) == record
            assert await call("memory_list_relevance", {}) == [record]
            assert "error" in await call("memory_record_relevance", {**arguments, "user_id": user + "-other"})
            invalid = await session.call_tool("memory_record_relevance", {**arguments, "labels": {nid: "true"}})
            assert invalid.isError
