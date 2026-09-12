"""Measure separate local packs with shared BGE, using public engine operations.

Requires psutil in the diagnostic environment (not a PRME dependency). Run each
count/mode in a fresh process. This is an authored resource probe, not a namespace
implementation, application authorization test, or memory-quality benchmark.
"""

import argparse
import asyncio
from contextlib import AsyncExitStack
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import resource
import tempfile
from time import perf_counter

import prme
from prme import MemoryEngine, config_from_directory
from prme.storage.embedding import FastEmbedProvider
from prme.types import NodeType, Scope
import psutil


class ResourceBudgetExceeded(RuntimeError):
    pass


def resources():
    process = psutil.Process()
    return {
        "rss_bytes": process.memory_info().rss,
        "threads": process.num_threads(),
        "fds": process.num_fds(),
        "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                              * (1 if platform.system() == "Darwin" else 1024)),
    }


def sample(report, label, args):
    observed = resources()
    report["resources"].append({"stage": label, **observed})
    if observed["rss_bytes"] > args.max_rss_mib * 1024**2 or observed["threads"] > args.max_threads:
        raise ResourceBudgetExceeded("Diagnostic resource budget reached")


def config_for(directory):
    config = config_from_directory(str(directory))
    return config.model_copy(update={
        "database_url": None,
        "encryption_enabled": False,
        "encryption_key": None,
        "enable_store_supersedence": False,
        "enable_query_reformulation": False,
        "enable_reranker": False,
        "organizer": config.organizer.model_copy(update={"opportunistic_enabled": False}),
    })


async def populate(memory, index):
    # Same owner, scope, and entity name in every pack. Facts differ by project.
    events = [await memory.store("Aurora", user_id="same-owner", scope=Scope.PROJECT,
                               node_type=NodeType.ENTITY, metadata={"entity_type": "project"})
              for _ in range(2)]
    fact = f"Aurora's retention period is {index + 1} days."
    events.append(await memory.store(fact, user_id="same-owner", scope=Scope.PROJECT,
                                    node_type=NodeType.FACT))
    result = await memory.organize(jobs=["deduplicate"], budget_ms=30000)
    assert result.per_job["deduplicate"].nodes_modified == 1
    assert result.per_job["deduplicate"].errors == 0
    nodes = await memory.query_nodes(user_id="same-owner", scope=Scope.PROJECT)
    assert len(nodes) == 2
    entity = next(node for node in nodes if node.node_type == NodeType.ENTITY)
    assert set(map(str, entity.evidence_refs)) == set(events[:2])
    return {"event_ids": events, "node_ids": sorted(str(node.id) for node in nodes), "fact": fact}


async def check(memory, expected, foreign):
    nodes = await memory.query_nodes(user_id="same-owner", scope=Scope.PROJECT)
    assert sorted(str(node.id) for node in nodes) == expected["node_ids"]
    start = perf_counter()
    response = await memory.retrieve("What is Aurora's retention period?", user_id="same-owner",
                                     scope=Scope.PROJECT, include_cross_scope=False)
    elapsed = (perf_counter() - start) * 1000
    assert expected["fact"] in [item.node.content for item in response.results]
    assert {str(item.node.id) for item in response.results} <= set(expected["node_ids"])
    for event_id in expected["event_ids"]:
        assert await memory.get_event(event_id, user_id="same-owner") is not None
    if foreign:
        for node_id in foreign["node_ids"]:
            assert await memory.get_node(node_id, user_id="same-owner", include_superseded=True) is None
        for event_id in foreign["event_ids"]:
            assert await memory.get_event(event_id, user_id="same-owner") is None
    return elapsed


async def run(args, report):
    sample(report, "before_provider", args)
    provider = FastEmbedProvider()
    start = perf_counter()
    await provider.embed(["Aurora"])
    report["provider_warmup_ms"] = (perf_counter() - start) * 1000
    report["provider"] = {"model": provider.model_name, "version": provider.model_version,
                          "dimension": provider.dimension}
    sample(report, "provider_warmed", args)
    with tempfile.TemporaryDirectory(prefix="prme-partition-resources-") as root:
        configs = [config_for(Path(root) / str(index)) for index in range(args.count)]
        expectations = []
        async with AsyncExitStack() as resident:
            engines = []
            for index, config in enumerate(configs):
                async with AsyncExitStack() as lease:
                    stack = resident if args.mode == "resident" else lease
                    start = perf_counter()
                    memory = await stack.enter_async_context(MemoryEngine.open(config, embedding_provider=provider))
                    open_ms = (perf_counter() - start) * 1000
                    sample(report, f"opened_{index}", args)
                    start = perf_counter()
                    expected = await populate(memory, index)
                    report["packs"].append({"index": index, "cold_open_ms": open_ms,
                                            "populate_ms": (perf_counter() - start) * 1000})
                    expectations.append(expected)
                    if args.mode == "resident":
                        engines.append(memory)
                    sample(report, f"populated_{index}", args)
                sample(report, f"iteration_done_{index}", args)
            for index, config in enumerate(configs):
                async with AsyncExitStack() as lease:
                    start = perf_counter()
                    memory = (engines[index] if engines else await lease.enter_async_context(
                        MemoryEngine.open(config, embedding_provider=provider)))
                    report["packs"][index]["access_open_ms"] = (perf_counter() - start) * 1000
                    foreign = expectations[(index + 1) % args.count] if args.count > 1 else None
                    report["packs"][index]["retrieve_ms"] = await check(memory, expectations[index], foreign)
                    sample(report, f"queried_{index}", args)
        sample(report, "all_closed", args)
        # Both modes must survive actual closure and reopening of every pack.
        for index, config in enumerate(configs):
            start = perf_counter()
            async with MemoryEngine.open(config, embedding_provider=provider) as memory:
                report["packs"][index]["reopen_ms"] = (perf_counter() - start) * 1000
                foreign = expectations[(index + 1) % args.count] if args.count > 1 else None
                report["packs"][index]["reopened_retrieve_ms"] = await check(memory, expectations[index], foreign)
            sample(report, f"reopened_closed_{index}", args)
        report["disk_bytes"] = sum(path.stat().st_size for path in Path(root).rglob("*") if path.is_file())
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=[1, 10, 100], required=True)
    parser.add_argument("--mode", choices=["resident", "leased"], required=True)
    parser.add_argument("--max-rss-mib", type=int, default=4096)
    parser.add_argument("--max-threads", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite prior evidence")
    report = {
        "complete": False, "count": args.count, "mode": args.mode,
        "python": platform.python_version(), "platform": platform.platform(),
        "prme_import_path": str(Path(prme.__file__).resolve()),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "versions": {name: version(name) for name in ["prme", "duckdb", "fastembed", "tantivy", "usearch", "psutil"]},
        "budgets": {"rss_mib": args.max_rss_mib, "threads": args.max_threads},
        "resources": [], "packs": [],
        "limits": "Tiny authored local packs, shared warmed BGE, serial access, warm OS cache possible. No PostgreSQL, ingestion, grants, concurrency, recovery-fault, or QA claim.",
    }
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
