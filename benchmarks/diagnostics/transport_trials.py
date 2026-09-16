"""Real-embedding HTTP/MCP ranking-trial workflow; not an accuracy benchmark."""
import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile

from benchmarks.diagnostics._process import checked_report


async def run():
    import httpx
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme import MemoryEngine, PRMEConfig, RankingMultipliers
    from prme.api.app import create_app
    from prme.mcp.server import create_mcp_server
    from prme.types import RepresentationLevel, Scope

    with tempfile.TemporaryDirectory(prefix="prme-transport-trials-") as directory:
        root = Path(directory)
        config = PRMEConfig(database_url=None, encryption_enabled=False,
            db_path=str(root / "memory.duckdb"), vector_path=str(root / "vectors.usearch"),
            lexical_path=str(root / "lexical"), organizer={"opportunistic_enabled": False},
            embedding={"provider": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dimension": 384, "api_key": None},
            extraction={"provider": "ollama", "model": "unused"},
            api={"user_keys": {"alice": "diagnostic-alice", "bob": "diagnostic-bob"}}, mcp={"user_id": "alice"})
        async with MemoryEngine.open(config) as engine:
            for owner, scope, content in (
                ("alice", Scope.PROJECT, "The Aster service uses PostgreSQL."),
                ("alice", Scope.PROJECT, "The Aster database has a nightly backup."),
                ("alice", Scope.PERSONAL, "Private diary about Aster."),
                ("bob", Scope.PROJECT, "Another owner's Aster service uses Redis."),
            ):
                await engine.store(content, user_id=owner, scope=scope)
            clock = datetime.now(timezone.utc)
            adjustment = RankingMultipliers(lexical=2)
            query = "What database does Aster use?"
            args = dict(scope="project", reference_time=clock, ranking_multipliers=adjustment,
                        include_cross_scope=False, token_budget=512, min_score=0,
                        min_fidelity=RepresentationLevel.FULL)
            expected = await engine.retrieve(query, user_id="alice", **args)
            assert expected.results and expected.bundle.render()
            assert all(c.node.user_id == "alice" and c.node.scope == Scope.PROJECT for c in expected.results)
            app = create_app(config)
            app.state.engine = engine
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://prme-local.invalid",
                                         headers={"Authorization": "Bearer diagnostic-alice"}) as client:
                response = await client.post("/v1/retrieve", json={"query": query,
                    "reference_time": clock.isoformat(), "ranking_multipliers": adjustment.model_dump(),
                    "filters": {"scope": "project", "include_cross_scope": False},
                    "token_budget": 512, "min_score": 0, "min_fidelity": "full"})
                assert response.status_code == 200, response.status_code
                http_result = response.json()
                assert http_result["bundle"] == expected.bundle.model_dump(mode="json")
                receipt_response = await client.get(f'/v1/retrievals/{http_result["metrics"]["request_id"]}')
                assert receipt_response.status_code == 200
                assert (await client.get(f'/v1/retrievals/{http_result["metrics"]["request_id"]}',
                    headers={"Authorization": "Bearer diagnostic-bob"})).status_code == 404
                assert (await client.post("/v1/retrieve", json={"query": query, "filters": {"scope": []}})).status_code == 422
            @asynccontextmanager
            async def lifespan(server):
                yield {"engine": engine}
            server = create_mcp_server(config, lifespan=lifespan)
            async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
                await session.initialize()
                response = await session.call_tool("memory_retrieve", {"query": query, "scope": ["project"],
                    "reference_time": clock.isoformat(), "ranking_multipliers": adjustment.model_dump(),
                    "include_cross_scope": False, "token_budget": 512, "min_score": 0,
                    "min_fidelity": "full", "include_context": True})
                assert not response.isError
                mcp_result = json.loads(response.content[0].text)
                assert mcp_result["context"] == expected.bundle.render()
                response = await session.call_tool("memory_get_retrieval_receipt", {"request_id": mcp_result["metrics"]["request_id"]})
                assert not response.isError
                exported = json.loads(response.content[0].text)
                assert exported["execution"]["parameters"]["ranking_multipliers"] == adjustment.model_dump()
            receipt = await engine.get_retrieval_receipt(mcp_result["metrics"]["request_id"], user_id="alice")
            assert [c.score for c in receipt.candidates] == [c.composite_score for c in expected.results]
            assert receipt.replay_ranking() == tuple(c.node.id for c in expected.results)
            return {"passed": True, "embedding_model": "BAAI/bge-small-en-v1.5",
                "package_path": str(Path(inspect.getfile(MemoryEngine)).resolve()),
                "candidate_count": len(expected.results), "receipt_version": receipt.schema_version,
                "context_sha256": hashlib.sha256(expected.bundle.render().encode()).hexdigest(),
                "execution_features": receipt.execution.features,
                "checks": ["real BGE provider through HTTP and MCP", "context equals Python pipeline at fixed clock",
                           "owner and scope boundaries", "receipt exported through both transports",
                           "exact trial score replay", "empty HTTP scope rejected"],
                "limits": "One authored interface workflow; no extraction, answer judging, or retrieval accuracy claim."}


def main():
    import asyncio
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        report = asyncio.run(run()) if args.worker else checked_report(
            [sys.executable, "-m", "benchmarks.diagnostics.transport_trials", "--worker"], timeout=180)
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__, "limits": "Incomplete workflow."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not args.worker:
        print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
