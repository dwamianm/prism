"""Exercise identity binding through real MCP HTTP and in-memory protocols."""

import asyncio
import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

from prme import MemoryEngine
from prme.config import MCPConfig
from prme.mcp.server import create_http_app, create_mcp_server
from tests.test_durable_ingestion import config, user  # noqa: F401


@pytest.mark.parametrize("kwargs", [
    {"user_id": " "}, {"user_id": "alice", "user_keys": {"alice": "key"}},
    {"user_keys": {"alice": "same", "bob": "same"}}, {"user_keys": {"alice": ""}},
])
def test_invalid_mcp_identity_configuration_fails(kwargs):
    with pytest.raises(ValueError):
        MCPConfig(**kwargs)


def test_http_cannot_start_without_user_credentials(config):  # noqa: F811
    with pytest.raises(ValueError, match="requires PRME_MCP_USER_KEYS"):
        create_http_app(config)


async def seed(settings, owner):
    async with MemoryEngine.open(settings) as engine:
        await engine.store("other user's private deployment region", user_id=owner)
        return str((await engine.scan_nodes(user_id=owner))[0].id)


async def test_stdio_binding_protects_tools_and_resources(config, user):  # noqa: F811
    foreign = await seed(config, user + "-other")
    config.mcp = MCPConfig(user_id=user)
    server = create_mcp_server(config)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
        await session.initialize()
        result = await session.call_tool("memory_store", {"content": "my private memory"})
        data = json.loads(result.content[0].text)
        assert data.get("node_id") and "error" not in data
        own = data["node_id"]
        for name, arguments in (
            ("memory_store", {"content": "forged", "user_id": user + "-other"}),
            ("memory_retrieve", {"query": "private", "user_id": user + "-other"}),
            ("memory_ingest", {"content": "forged", "user_id": user + "-other"}),
            ("memory_organize", {"user_id": user + "-other"}),
            ("memory_organize", {"jobs": "feedback_apply"}),
            ("memory_get_node", {"node_id": foreign}),
            ("memory_promote_node", {"node_id": foreign}),
            ("memory_archive_node", {"node_id": foreign}),
        ):
            result = await session.call_tool(name, arguments)
            assert "error" in json.loads(result.content[0].text)
        result = await session.read_resource(AnyUrl("memory://stats"))
        assert json.loads(result.contents[0].text)["node_count"] == 1
        result = await session.read_resource(AnyUrl(f"memory://nodes/{foreign}"))
        assert "not found" in json.loads(result.contents[0].text)["error"]
        result = await session.read_resource(AnyUrl(f"memory://nodes/{own}"))
        assert json.loads(result.contents[0].text)["user_id"] == user


async def test_http_credentials_bind_each_request_and_resource(config, user):  # noqa: F811
    other = user + "-other"
    foreign = await seed(config, other)
    config.mcp = MCPConfig(user_keys={user: "first-token", other: "second-token"})
    app = create_http_app(config)
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000", headers=headers) as client:
            counter = 0

            async def rpc(method, params, token="first-token"):
                nonlocal counter
                counter += 1
                return await client.post("/mcp", json={"jsonrpc": "2.0", "id": counter, "method": method, "params": params},
                                         headers={"Authorization": f"Bearer {token}"} if token else {})

            for token in (None, "bad-token"):
                result = await rpc("tools/list", {}, token)
                assert result.status_code == 401
                assert "Bearer" in result.headers["WWW-Authenticate"]
            initialized = await rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                   "clientInfo": {"name": "identity-test", "version": "1"}})
            assert initialized.status_code == 200
            assert "mcp-session-id" not in initialized.headers
            stored = await rpc("tools/call", {"name": "memory_store", "arguments": {"content": "my deployment region"}})
            assert stored.status_code == 200
            stored_data = json.loads(stored.json()["result"]["content"][0]["text"])
            assert "error" not in stored_data
            own = stored_data["node_id"]
            for tool in ("memory_get_node", "memory_promote_node", "memory_archive_node"):
                denied = await rpc("tools/call", {"name": tool, "arguments": {"node_id": foreign}})
                assert "not found" in json.loads(denied.json()["result"]["content"][0]["text"])["error"]
            for name, arguments in (
                ("memory_store", {"content": "forged", "user_id": other}),
                ("memory_ingest", {"content": "forged", "user_id": other}),
                ("memory_retrieve", {"query": "private", "user_id": other}),
                ("memory_organize", {"user_id": other}),
                ("memory_organize", {"jobs": "feedback_apply"}),
            ):
                denied = await rpc("tools/call", {"name": name, "arguments": arguments})
                assert "error" in json.loads(denied.json()["result"]["content"][0]["text"])
            resource = await rpc("resources/read", {"uri": f"memory://nodes/{foreign}"})
            assert "not found" in json.loads(resource.json()["result"]["contents"][0]["text"])["error"]
            # Concurrent requests under alternating identities cannot share an owner context.
            replies = await asyncio.gather(*[
                rpc("resources/read", {"uri": f"memory://nodes/{node_id}"}, token)
                for token, node_id in (("first-token", own), ("second-token", foreign)) * 4
            ])
            for response, owner in zip(replies, [user, other] * 4):
                assert json.loads(response.json()["result"]["contents"][0]["text"])["user_id"] == owner
            stats = await rpc("resources/read", {"uri": "memory://stats"})
            assert json.loads(stats.json()["result"]["contents"][0]["text"])["node_count"] == 1
            retrieved = await rpc("tools/call", {"name": "memory_retrieve", "arguments": {"query": "deployment region"}})
            results = json.loads(retrieved.json()["result"]["content"][0]["text"])["results"]
            assert {r["node_id"] for r in results} == {own}
