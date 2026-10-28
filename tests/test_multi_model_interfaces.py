"""reembed() through the client, the HTTP route, the MCP tool and the command line (dwamianm/prism#368)."""

from __future__ import annotations

import json

import httpx
import pytest

from prme import MemoryClient, MemoryEngine, ReembedBusyError, ReembedDisabledError, ReembedResult
from prme.api.app import create_app
from prme.config import APIConfig, MCPConfig
from prme.mcp.server import create_http_app
from tests.multi_model_support import large, memory_text, small
from tests import multi_model_support

store = multi_model_support.store

USER = "iface-user"


def fakes(monkeypatch, first=None, second=None):
    """The configured model names open as these providers, so no model is downloaded."""
    first, second = first or small(), second or large()
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _config: first)
    for module in ("prme.storage.multi_model", "prme.storage.reembed"):
        monkeypatch.setattr(f"{module}.fastembed_provider_for",
                            lambda name, threads=None: second if name == second.model_name else first)
    return first, second


async def filled(config, count=6, owner=USER, first=None):
    async with MemoryEngine.open(config, embedding_provider=first or small()) as engine:
        for number in range(count):
            await engine.store(memory_text(number), user_id=owner)


async def test_the_sync_client_exposes_the_job(store):
    first, second = small(), large()
    with MemoryClient(config=store.multi(), embedding_provider=first) as client:
        for number in range(5):
            client.store(memory_text(number), user_id=USER)
        assert client.reembed(second, dry_run=True).remaining == 5
        result = client.reembed(second, limit=3)
        assert isinstance(result, ReembedResult) and (result.processed, result.remaining) == (3, 2)
        assert client.reembed(second, cursor=result.cursor).remaining == 0
        status = client.embedding_status()
        assert {m["model"]: m["complete"] for m in status["models"]} == {"test/small": True, "test/large": True}
        assert client.drop_embeddings("test/large") == 5


def app_for(config, engine):
    config.api = APIConfig(user_keys={USER: "owner-token", f"{USER}-other": "other-token"})
    app = create_app(config)
    app.state.engine = engine
    return app


async def test_the_http_route_is_owned_validated_and_off_unless_enabled(store, monkeypatch):
    first, second = fakes(monkeypatch)
    config = store.multi()
    await filled(config, first=first)
    async with MemoryEngine.open(config, embedding_provider=first) as engine:
        app = app_for(config, engine)
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers={"Authorization": "Bearer owner-token"}) as client:
            assert (await client.post("/v1/reembed", json={"user_id": f"{USER}-other"})).status_code == 403
            assert (await client.post("/v1/reembed", json={"limit": -1})).status_code == 422
            assert (await client.post("/v1/reembed", json={"cursor": "bad", "model": second.model_name})).status_code == 422
            dry = await client.post("/v1/reembed", json={"dry_run": True, "model": second.model_name})
            assert dry.status_code == 200
            assert (dry.json()["total"], dry.json()["remaining"], dry.json()["processed"]) == (6, 6, 0)
            part = (await client.post("/v1/reembed", json={"model": second.model_name, "limit": 4, "batch_size": 2})).json()
            assert (part["processed"], part["done"], part["remaining"]) == (4, 4, 2) and part["cursor"]
            last = (await client.post("/v1/reembed", json={"model": second.model_name, "cursor": part["cursor"]})).json()
            assert (last["remaining"], last["done"], last["cursor"]) == (0, 6, None)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers={"Authorization": "Bearer other-token"}) as other:
            assert (await other.post("/v1/reembed", json={"model": second.model_name})).json()["total"] == 0
        engine._config = engine._config.model_copy(update={"enable_multi_model_embeddings": False})
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers={"Authorization": "Bearer owner-token"}) as client:
            refused = await client.post("/v1/reembed", json={})
            assert refused.status_code == 409 and "enable_multi_model_embeddings" in refused.json()["detail"]


async def test_the_mcp_tool_uses_the_callers_identity(store, monkeypatch):
    first, second = fakes(monkeypatch)
    on = store.multi()
    on.mcp = MCPConfig(user_keys={USER: "first-token", f"{USER}-other": "second-token"})
    await filled(on, first=first)
    app = create_http_app(on)
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000",
                                     headers=headers) as client:
            async def call(arguments, token="first-token"):
                response = await client.post("/mcp", headers={"Authorization": "Bearer " + token}, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "memory_reembed", "arguments": {"model": second.model_name, **arguments}},
                })
                assert response.status_code == 200
                return json.loads(response.json()["result"]["content"][0]["text"])

            assert (await call({"dry_run": True}))["remaining"] == 6
            assert (await call({}, "second-token"))["total"] == 0
            done = await call({"limit": 10})
            assert (done["processed"], done["remaining"]) == (6, 0)
            assert "error" in await call({"cursor": "bad"})


async def test_the_command_line_runs_resumes_and_reports(store, monkeypatch, capsys):
    from prme import cli

    first, second = fakes(monkeypatch)
    await filled(store.multi(), first=first)
    target = [str(store.tmp_path / "memory.duckdb")] if store.backend == "duckdb" else ["--postgres"]
    if store.backend == "postgres":
        monkeypatch.setenv("PRME_DATABASE_URL", store.url)
    parser = cli.build_parser()

    async def run(*argv):
        args = parser.parse_args(list(argv))
        await args.func(args)
        return capsys.readouterr().out

    def as_json(output: str):
        # The engine's own log lines share standard output when the command is not run through main().
        lines = output.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("{"))
        end = next(i for i in range(start, len(lines)) if lines[i] in ("}",) or (i == start and lines[i].endswith("}")))
        return json.loads("\n".join(lines[start:end + 1]))

    out = as_json(await run("reembed", *target, "--model", second.model_name, "--dry-run", "--format", "json"))
    assert (out["total"], out["remaining"], out["processed"]) == (6, 6, 0)
    out = as_json(await run("reembed", *target, "--model", second.model_name, "--limit", "4", "--batch-size", "2",
                               "--pause-ms", "1", "--format", "json"))
    assert (out["processed"], out["remaining"]) == (4, 2) and out["cursor"]
    out = as_json(await run("reembed", *target, "--model", second.model_name, "--cursor", out["cursor"], "--format", "json"))
    assert (out["remaining"], out["done"]) == (0, 6)
    table = await run("embeddings", "status", *target)
    assert "test/small" in table and "test/large" in table and "yes" in table
    dropped = as_json(await run("embeddings", "drop", *target, "--model", second.model_name, "--format", "json"))
    assert dropped == {"model": second.model_name, "vectors_removed": 6}
    with pytest.raises(ValueError, match="active"):
        await run("embeddings", "drop", *target, "--model", first.model_name)


async def test_the_command_line_refuses_without_a_target(monkeypatch, capsys):
    from prme import cli

    parser = cli.build_parser()
    args = parser.parse_args(["reembed"])
    with pytest.raises(SystemExit, match="pack path"):
        await args.func(args)
    monkeypatch.delenv("PRME_DATABASE_URL", raising=False)
    args = parser.parse_args(["reembed", "--postgres"])
    with pytest.raises(SystemExit, match="PRME_DATABASE_URL"):
        await args.func(args)


async def test_the_job_needs_the_option(store):
    async with MemoryEngine.open(store.config(), embedding_provider=small()) as engine:
        with pytest.raises(ReembedDisabledError):
            await engine.reembed()
    assert ReembedBusyError
