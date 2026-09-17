"""HTTP writes preserve source semantics and expose accepted-work recovery."""
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from prme import MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig, PRMEConfig
from prme.ingestion.schema import ExtractionResult
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def app_for(config, engine, owner):
    config.api = APIConfig(user_keys={owner: "owner-token", owner + "-other": "other-token"})
    app = create_app(config)
    app.state.engine = engine
    return app


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=False),
                             base_url="http://test", headers={"Authorization": "Bearer owner-token"})


async def test_store_preserves_explicit_fields_and_ttl_presence(config, user):
    config.organizer.default_ttl_days["note"] = 17
    async with MemoryEngine.open(config) as engine:
        async with client_for(app_for(config, engine, user)) as client:
            for extra, expected_ttl in [({}, 17), ({"ttl_days": None}, None), ({"ttl_days": 0}, 0), ({"ttl_days": 12}, 12)]:
                body = {"content": "Observation recorded by the telescope", "role": "tool",
                        "session_id": "observation-session", "node_type": "note", "scope": "project",
                        "source_type": "tool_output", "epistemic_type": "observed", "confidence": .81,
                        "event_time": "2024-03-10T01:30:00-06:00",
                        "valid_from": "2024-03-11T00:00:00-06:00",
                        "valid_to": "2024-04-11T00:00:00-06:00",
                        "metadata": {"instrument": "cobalt"}, **extra}
                response = await client.post("/v1/store", json=body)
                assert response.status_code == 200, response.text
                receipt = response.json()
                assert receipt["processing_status"]["status"] == "complete"
                event = (await client.get(f'/v1/events/{receipt["event_id"]}')).json()
                node = (await client.get(f'/v1/nodes/{receipt["node_id"]}')).json()
                assert event["session_id"] == node["session_id"] == body["session_id"]
                assert event["metadata"] == node["metadata"] == body["metadata"]
                assert datetime.fromisoformat(event["event_time"]) == datetime.fromisoformat(body["event_time"])
                assert datetime.fromisoformat(node["event_time"]) == datetime.fromisoformat(body["event_time"])
                assert datetime.fromisoformat(node["valid_from"]) == datetime.fromisoformat(body["valid_from"])
                assert datetime.fromisoformat(node["valid_to"]) == datetime.fromisoformat(body["valid_to"])
                assert node["confidence"] == pytest.approx(.81) and node["source_type"] == "tool_output"
                assert node["epistemic_type"] == "observed" and node["scope"] == "project"
                assert node["ttl_days"] == expected_ttl
                assert node["evidence_refs"] == [receipt["event_id"]]
            result = await client.post("/v1/retrieve", json={"query": "telescope", "filters": {"scope": "project"}})
            assert result.status_code == 200
            item = next(r for r in result.json()["results"] if r["node_id"] == receipt["node_id"])
            for field in ("source_type", "scope", "session_id", "event_time", "valid_from", "valid_to", "evidence_refs"):
                assert item[field] == node[field]


async def test_store_preserves_raw_source_and_exposes_retrieval_projection(config, user):
    async with MemoryEngine.open(config) as engine:
        async with client_for(app_for(config, engine, user)) as client:
            response = await client.post(
                "/v1/store",
                json={
                    "content": '{"scratchpad":"raw tool trace","answer":"cobalt route"}',
                    "retrieval_content": "Compact final answer: cobalt route",
                },
            )
            assert response.status_code == 200, response.text
            receipt = response.json()
            event = (await client.get(f'/v1/events/{receipt["event_id"]}')).json()
            node = (await client.get(f'/v1/nodes/{receipt["node_id"]}')).json()
            assert event["content"] == '{"scratchpad":"raw tool trace","answer":"cobalt route"}'
            assert node["content"] == "Compact final answer: cobalt route"


async def test_store_accepts_source_bound_value_bindings(config, user):
    async with MemoryEngine.open(config) as engine:
        async with client_for(app_for(config, engine, user)) as client:
            source = '{"plan":"Current City: Salt Lake City(Utah)"}'
            response = await client.post(
                "/v1/store",
                json={
                    "content": source,
                    "retrieval_content": "Current City: Salt Lake City(Utah)",
                    "value_bindings": [{
                        "reference": "current-city-1",
                        "kind": "city",
                        "presentation": "Salt Lake City(Utah)",
                        "lookup": "Salt Lake City",
                    }],
                },
            )
            assert response.status_code == 200, response.text
            receipt = response.json()
            event = (await client.get(f'/v1/events/{receipt["event_id"]}')).json()
            node = (await client.get(f'/v1/nodes/{receipt["node_id"]}')).json()
            assert event["content"] == source
            assert event["metadata"] == node["metadata"]
            assert event["metadata"]["prme_value_bindings_v1"][0] == {
                "schema_version": 1,
                "reference": "current-city-1",
                "kind": "city",
                "presentation": "Salt Lake City(Utah)",
                "lookup": "Salt Lake City",
                "lookup_authority": "caller",
            }
            retrieved = await client.post(
                "/v1/retrieve",
                json={"query": "Salt Lake City", "token_budget": 4096},
            )
            assert retrieved.status_code == 200, retrieved.text
            assert retrieved.json()["value_bindings"][0]["presentation"] == (
                "Salt Lake City(Utah)"
            )


async def test_store_rejects_invalid_validity_before_source_admission(config, user):
    async with MemoryEngine.open(config) as engine:
        async with client_for(app_for(config, engine, user)) as client:
            response = await client.post(
                "/v1/store",
                json={
                    "content": "Incomplete interval",
                    "valid_to": "2025-02-01T00:00:00Z",
                },
            )
        assert response.status_code == 422
        assert response.json()["detail"] == "valid_to requires an explicit valid_from"
        assert await engine.get_events(user) == []


@pytest.mark.parametrize("endpoint,extra", [
    ("store", {"event_tim": "typo"}), ("store", {"ttl_days": True}),
    ("store", {"ttl_days": -1}), ("store", {"confidence": 1.1}),
    ("store", {"event_time": "2024-01-01T00:00:00"}),
    ("store", {"valid_from": "2024-01-01T00:00:00"}),
    ("store", {"valid_from": "2024-01-01T00:00:00Z", "valid_to": "2024-02-01T00:00:00"}),
    ("ingest", {"namespace": "unimplemented-private-space"}),
    ("ingest", {"source_type": "tool_output"}), ("ingest", {"wait_for_extraction": "false"}),
])
async def test_unsupported_or_invalid_write_fields_fail_before_engine(endpoint, extra):
    cfg = PRMEConfig(api=APIConfig(user_keys={"alice": "owner-token"}))
    app = create_app(cfg)
    async with client_for(app) as client:
        response = await client.post(f"/v1/{endpoint}", json={"content": "source", **extra})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][0] == "body"


async def test_ingest_retains_session_metadata_and_waits_for_extraction(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        extract = AsyncMock(return_value=ExtractionResult())
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", extract)
        async with client_for(app_for(config, engine, user)) as client:
            response = await client.post("/v1/ingest", json={"content": "A source note", "session_id": "s-1",
                                         "metadata": {"channel": "support"}, "scope": "project", "wait_for_extraction": True})
            assert response.status_code == 200, response.text
            eid = response.json()["event_id"]
            assert (await client.get(f"/v1/events/{eid}/extraction-status")).json()["status"] == "complete"
            event = (await client.get(f"/v1/events/{eid}")).json()
            assert event["session_id"] == "s-1" and event["metadata"] == {"channel": "support"}
            assert event["scope"] == "project"
            extract.assert_awaited_once()


@pytest.mark.parametrize("operation", ["store", "ingest"])
async def test_accepted_failure_has_private_receipt_and_repairs_original_source(config, user, monkeypatch, operation):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._retry_delays = ()
        no_extraction = AsyncMock(side_effect=AssertionError("Raw source repair must not invoke extraction"))
        if operation == "store":
            monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", no_extraction)
        app = app_for(config, engine, user)
        async with client_for(app) as client:
            with monkeypatch.context() as fault:
                if operation == "store":
                    fault.setattr(engine._graph_store, "create_node", AsyncMock(side_effect=OSError("private-provider-secret")))
                else:
                    fault.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(side_effect=TimeoutError("private-provider-secret")))
                response = await client.post(f"/v1/{operation}", json={"content": "Recover this exact source",
                                              **({"wait_for_extraction": True} if operation == "ingest" else {})})
            assert response.status_code == 503, response.text
            receipt = response.json()
            assert receipt["accepted"] is True and "private-provider-secret" not in response.text
            assert receipt["reason_code"] == ("OSError" if operation == "store" else "TimeoutError")
            eid = receipt["event_id"]
            status_path = f"/v1/events/{eid}/" + ("processing-status" if operation == "store" else "extraction-status")
            assert (await client.get(f"/v1/events/{eid}")).json()["content"] == "Recover this exact source"
            assert (await client.get(status_path)).json()["status"] == ("pending" if operation == "store" else "failed")
            assert (await client.get(status_path, headers={"Authorization": "Bearer other-token"})).status_code == 404
            if operation == "store":
                assert (await client.post("/v1/materializations/process", json={"user_id": user + "-other"})).status_code == 403
                foreign = await client.post("/v1/materializations/process", json={}, headers={"Authorization": "Bearer other-token"})
                assert foreign.json() == {"processed": 0, "pending": 0, "failed": 0}
                repair = await client.post("/v1/materializations/process", json={"budget_ms": 5000})
                assert repair.json() == {"processed": 1, "pending": 0, "failed": 0}
            else:
                monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=ExtractionResult()))
                assert (await client.post(f"/v1/events/{eid}/retry-extraction")).status_code == 200
                assert (await client.post("/v1/extractions/process", json={})).status_code == 200
            assert (await client.get(status_path)).json()["status"] == "complete"
            assert len(await engine.get_events(user)) == 1
            if operation == "store":
                no_extraction.assert_not_awaited()


async def test_index_outage_returns_pending_receipt_then_explicit_http_repair(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        async with client_for(app_for(config, engine, user)) as client:
            with monkeypatch.context() as fault:
                fault.setattr(engine._vector_index, "index", AsyncMock(side_effect=OSError("offline")))
                result = await client.post("/v1/store", json={"content": "Pending telescope source"})
            assert result.status_code == 200
            receipt = result.json()
            assert receipt["node_id"] and receipt["processing_status"]["status"] == "pending"
            inspect_only = await client.post("/v1/materializations/process", json={"budget_ms": 0})
            assert inspect_only.json() == {"processed": 0, "pending": 1, "failed": 1}
            repaired = await client.post("/v1/materializations/process", json={"budget_ms": 5000})
            assert repaired.json() == {"processed": 1, "pending": 0, "failed": 0}
            assert (await client.get(f'/v1/events/{receipt["event_id"]}/nodes')).json()["nodes"][0]["id"] == receipt["node_id"]


async def test_receipt_read_failure_still_returns_accepted_identity(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        async with client_for(app_for(config, engine, user)) as client:
            with monkeypatch.context() as fault:
                fault.setattr(engine, "get_event_nodes", AsyncMock(side_effect=OSError("private-read-error")))
                response = await client.post("/v1/store", json={"content": "Already completed source"})
            assert response.status_code == 503 and response.json()["accepted"] is True
            assert "private-read-error" not in response.text
            eid = response.json()["event_id"]
            assert (await client.get(f"/v1/events/{eid}/processing-status")).json()["status"] == "complete"
            assert len((await client.get(f"/v1/events/{eid}/nodes")).json()["nodes"]) == 1
            assert len(await engine.get_events(user)) == 1


async def test_http_materialization_receipt_survives_engine_restart(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        async with client_for(app_for(config, engine, user)) as client:
            with monkeypatch.context() as fault:
                fault.setattr(engine._graph_store, "create_node", AsyncMock(side_effect=OSError("unavailable")))
                response = await client.post("/v1/store", json={"content": "Keep this original instruction",
                    "node_type": "instruction", "source_type": "user_stated", "confidence": .87,
                    "session_id": "restart-session", "ttl_days": None})
            assert response.status_code == 503 and response.json()["accepted"]
            eid = response.json()["event_id"]
    async with MemoryEngine.open(config) as engine:
        no_extraction = AsyncMock(side_effect=AssertionError("Saved direct-store repair never extracts"))
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", no_extraction)
        async with client_for(app_for(config, engine, user)) as client:
            assert (await client.get(f"/v1/events/{eid}/processing-status")).json()["status"] == "pending"
            repaired = await client.post("/v1/materializations/process", json={"budget_ms": 5000})
            assert repaired.json() == {"processed": 1, "pending": 0, "failed": 0}
            nodes = (await client.get(f"/v1/events/{eid}/nodes")).json()["nodes"]
            assert len(nodes) == 1 and nodes[0]["node_type"] == "instruction"
            assert nodes[0]["session_id"] == "restart-session" and nodes[0]["ttl_days"] is None
            assert nodes[0]["confidence"] == pytest.approx(.87)
            assert nodes[0]["source_type"] == "user_stated" and nodes[0]["evidence_refs"] == [eid]
            assert len(await engine.get_events(user)) == 1
        no_extraction.assert_not_awaited()
