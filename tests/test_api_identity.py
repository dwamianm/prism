"""Authenticated callers cannot select another user's memory or global jobs."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from prme import MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig
from prme.models import MemoryEdge
from prme.types import EdgeType, LifecycleState
from tests.test_durable_ingestion import config, user  # noqa: F401


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("values", [
    {"user_keys": {"alice": "same", "bob": "same"}},
    {"user_keys": {"": "key"}},
    {"user_keys": {"alice": " "}},
    {"api_key": "operator", "user_keys": {"alice": "key"}},
])
def test_ambiguous_or_empty_identity_configuration_rejected(values):
    with pytest.raises(ValueError):
        APIConfig(**values)


def test_user_credentials_are_redacted_and_load_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('PRME_API_USER_KEYS={"alice":"private-test-credential"}\n')
    cfg = APIConfig()
    assert cfg.user_keys["alice"].get_secret_value() == "private-test-credential"
    assert "private-test-credential" not in repr(cfg)
    assert "private-test-credential" not in cfg.model_dump_json()


async def test_bound_http_operations_isolate_two_users(config, user, monkeypatch):  # noqa: F811
    other = user + "-other"
    config.api = APIConfig(user_keys={user: "first-token", other: "second-token"})
    async with MemoryEngine.open(config) as engine:
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            # Identity comes from credentials; request user_id can be omitted.
            responses = await asyncio.gather(*[
                client.post("/v1/store", json={"content": f"private deployment region {token}"}, headers=auth(token))
                for token in ("first-token", "second-token")
            ])
            assert all(r.status_code == 200 for r in responses)
            a = (await engine.scan_nodes(user_id=user))[0]
            b = (await engine.scan_nodes(user_id=other))[0]
            before = await engine.get_node(str(b.id), user_id=other)
            for path in ("", "/neighborhood", "/chain"):
                response = await client.get(f"/v1/nodes/{b.id}{path}", headers=auth("first-token"))
                assert response.status_code == 404
            provenance = await client.get(
                f"/v1/nodes/{b.id}/provenance", headers=auth("first-token")
            )
            assert provenance.status_code == 404
            for operation, body in (
                ("promote", None), ("archive", None), ("reinforce", None),
                ("condition", {"state": "true"}),
            ):
                response = await client.put(
                    f"/v1/nodes/{b.id}/{operation}", headers=auth("first-token"),
                    json=body,
                )
                assert response.status_code == 404
            assert await engine.get_node(str(b.id), user_id=other) == before
            # Legacy cross-user graph edges must not expose the other endpoint.
            await engine._graph_store.create_edge(MemoryEdge(
                source_id=a.id, target_id=b.id, edge_type=EdgeType.RELATES_TO, user_id=user,
            ))
            result = await client.get(f"/v1/nodes/{a.id}/neighborhood", headers=auth("first-token"))
            assert result.status_code == 200 and result.json()["nodes"] == []
            for path in ("/v1/nodes", "/v1/stats"):
                denied = await client.get(path, params={"user_id": other}, headers=auth("first-token"))
                assert denied.status_code == 403
                assert (await client.get(path)).status_code == 401
                assert (await client.get(path, headers=auth("invalid"))).status_code == 401
            listed = await client.get("/v1/nodes", headers=auth("first-token"))
            assert {n["user_id"] for n in listed.json()["nodes"]} == {user}
            counted = await client.get("/v1/stats", headers=auth("first-token"))
            assert counted.json()["node_count"] == 1
            # Successful writes and reads cannot be redirected by a forged body.
            for path, body in (("store", {"content": "forged"}), ("ingest", {"content": "forged"}),
                               ("retrieve", {"query": "private"}), ("organize", {})):
                response = await client.post(f"/v1/{path}", json={**body, "user_id": other}, headers=auth("first-token"))
                assert response.status_code == 403
            ingestor = AsyncMock(return_value=str(uuid4()))
            monkeypatch.setattr(engine, "ingest", ingestor)
            assert (await client.post("/v1/ingest", json={"content": "new source"}, headers=auth("first-token"))).status_code == 200
            assert ingestor.call_args.kwargs["user_id"] == user
            retrieved = await client.post("/v1/retrieve", json={"query": "deployment region"}, headers=auth("first-token"))
            assert retrieved.status_code == 200
            assert {r["node_id"] for r in retrieved.json()["results"]} == {str(a.id)}
            # Maintenance defaults must never run the engine-global feedback job.
            organizer = AsyncMock()
            from prme.organizer.models import OrganizeResult
            organizer.return_value = OrganizeResult()
            monkeypatch.setattr(engine, "organize", organizer)
            result = await client.post("/v1/organize", json={}, headers=auth("first-token"))
            assert result.status_code == 200
            assert organizer.call_args.kwargs["user_id"] == user
            assert "feedback_apply" not in organizer.call_args.kwargs["jobs"]
            assert (await client.post("/v1/organize", json={"jobs": ["feedback_apply"]}, headers=auth("first-token"))).status_code == 403
            promoted = await client.put(f"/v1/nodes/{a.id}/promote", headers=auth("first-token"))
            assert promoted.status_code == 200
            archived = await client.put(f"/v1/nodes/{a.id}/archive", headers=auth("first-token"))
            assert archived.status_code == 200
            filtered = await client.get("/v1/nodes", params={"state": "archived"}, headers=auth("first-token"))
            assert [n["id"] for n in filtered.json()["nodes"]] == [str(a.id)]
            assert (await engine.get_node(str(b.id), user_id=other)).lifecycle_state == LifecycleState.TENTATIVE
            # Shared clients can alternate principals without sticky identity.
            for token, owner in (("second-token", other), ("first-token", user), ("second-token", other)):
                response = await client.get("/v1/nodes", headers=auth(token))
                assert all(n["user_id"] == owner for n in response.json()["nodes"])
            assert (await client.get("/v1/health")).status_code == 200
