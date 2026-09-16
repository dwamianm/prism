"""Installed public workspace workflow with real BGE, eviction and copying."""

import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import tempfile
from time import perf_counter

import prme
from prme import MemoryWorkspace, PRMEConfig, Scope, NodeType
from prme.storage.embedding import FastEmbedProvider
import psutil


def sample(report, stage):
    process = psutil.Process()
    values = {"stage": stage, "rss_bytes": process.memory_info().rss,
              "threads": process.num_threads(), "fds": process.num_fds()}
    report["resources"].append(values)
    if values["rss_bytes"] > 4 * 1024**3 or values["threads"] > 1000:
        raise RuntimeError("Authored workflow exceeded resource budget")


async def check(workspace, name, expected, foreign):
    async with workspace.namespace(name, create=False) as memory:
        assert str(memory.namespace.id) == expected["namespace_id"]
        response = await memory.retrieve("What is Aurora's retention period?", user_id="same-owner",
                                         scope=Scope.PROJECT, include_cross_scope=False)
        contents = [item.node.content for item in response.results]
        assert expected["fact"] in contents
        if foreign:
            assert foreign["fact"] not in contents
            for event_id in foreign["events"]:
                assert await memory.get_event(event_id, user_id="same-owner") is None
            assert await memory.get_node(foreign["entity_id"], user_id="same-owner") is None
        for event in expected["events"]:
            assert await memory.get_event(event, user_id="same-owner") is not None
        assert (await memory.processing_status(expected["pending"], user_id="same-owner")).status == "complete"


async def run(args, report):
    config = PRMEConfig(database_url=None, namespace_id=None, duckdb_threads=1,
                        organizer={"opportunistic_enabled": False}, encryption_enabled=False,
                        encryption_key=None, enable_store_supersedence=False,
                        enable_reranker=False, enable_query_reformulation=False)
    provider = FastEmbedProvider()
    await provider.embed(["Aurora"])
    sample(report, "shared_provider_warmed")
    report["provider"] = {"model": provider.model_name, "version": provider.model_version}
    with tempfile.TemporaryDirectory(prefix="prme-workspace-workflow-") as directory:
        original, copied = Path(directory) / "original", Path(directory) / "copy"
        expected = {}
        async with MemoryWorkspace.open(original, config=config, embedding_provider=provider, max_open=4) as ws:
            for index in range(args.count):
                name = f"project/{index}"
                async with ws.namespace(name) as memory:
                    events = [await memory.store("Aurora", user_id="same-owner", scope=Scope.PROJECT,
                                                 node_type=NodeType.ENTITY, metadata={"entity_type": "project"})
                              for _ in range(2)]
                    result = await memory.organize(jobs=["deduplicate"], budget_ms=30000)
                    assert result.per_job["deduplicate"].nodes_modified == 1
                    entities = await memory.query_nodes(user_id="same-owner", node_type=NodeType.ENTITY)
                    assert len(entities) == 1 and set(map(str, entities[0].evidence_refs)) == set(events)
                    fact = f"Aurora's retention period is {index + 1} days."
                    pending = await memory.ingest_fast(fact, user_id="same-owner", scope=Scope.PROJECT)
                    expected[name] = {"namespace_id": str(memory.namespace.id), "events": events + [pending],
                                      "entity_id": str(entities[0].id), "pending": pending, "fact": fact}
                    sample(report, f"populated_{index}")
            assert len(await ws.list_namespaces()) == args.count
            for index in range(args.count):
                name = f"project/{index}"
                foreign = expected[f"project/{(index + 1) % args.count}"] if args.count > 1 else None
                await check(ws, name, expected[name], foreign)
                sample(report, f"retrieved_{index}")
        shutil.copytree(original, copied)
        async with MemoryWorkspace.open(copied, config=config, embedding_provider=provider, max_open=4) as ws:
            for index in range(args.count):
                name = f"project/{index}"
                foreign = expected[f"project/{(index + 1) % args.count}"] if args.count > 1 else None
                await check(ws, name, expected[name], foreign)
                sample(report, f"copied_reopened_{index}")
        report["expected"] = expected
        report["complete"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=[2, 100], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite prior evidence")
    report = {"complete": False, "count": args.count, "max_open": 4, "duckdb_threads": 1,
              "python": platform.python_version(), "prme_import_path": str(Path(prme.__file__).resolve()),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "versions": {name: version(name) for name in ["prme", "duckdb", "fastembed", "filelock", "psutil"]},
              "resources": [], "limits": "One authored local public-API workflow; no cloud extraction, hosted grants, hardware power-loss or competitive QA claim."}
    start = perf_counter()
    try:
        asyncio.run(run(args, report))
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        report["elapsed_seconds"] = perf_counter() - start
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
