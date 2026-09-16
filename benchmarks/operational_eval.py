"""Measure PRME retrieval latency, repeatability, and owner isolation.

This is an operational benchmark, not a relevance or answer-quality score. It
uses public store and retrieve paths, fixes the retrieval clock, records exact
runtime provenance, and writes an incomplete artifact before measured work.

Run: uv run python -m benchmarks.operational_eval --output /tmp/prme-ops.json
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import platform
from pathlib import Path
import subprocess
import tempfile
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from prme import MemoryEngine, PRMEConfig
from prme.storage.embedding import FastEmbedProvider


class DeterministicEmbeddingProvider:
    """Cheap documented provider for harness tests and smoke runs only."""

    model_name = "prme-operational-deterministic"
    model_version = "1"
    dimension = 64

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [hashlib.sha256(text.encode()).digest()[index % 32] / 255 for index in range(self.dimension)]
            for text in texts
        ]


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False))
    temporary.replace(path)


def _percentile(values: list[float], quantile: float) -> float:
    """Nearest-rank percentile, including the observed maximum at p100."""
    if not values or not 0 < quantile <= 1:
        raise ValueError("percentiles require samples and a quantile in (0, 1]")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _latency(values: list[float]) -> dict:
    return {
        "samples": len(values),
        "p50_ms": _percentile(values, .50),
        "p95_ms": _percentile(values, .95),
        "p99_ms": _percentile(values, .99),
        "max_ms": max(values),
    }


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, check=False)
    return result.stdout.decode().strip() if result.returncode == 0 else ""


def _provenance(provider, config: PRMEConfig) -> dict:
    implementation = Path(__file__).read_bytes()
    worktree = subprocess.run(
        ["git", "diff", "HEAD"], capture_output=True, check=False,
    ).stdout
    for name in _git("ls-files", "--others", "--exclude-standard").splitlines():
        path = Path(name)
        if path.is_file() and path.suffix in {".py", ".toml", ".lock"}:
            worktree += name.encode() + hashlib.sha256(path.read_bytes()).digest()
    return {
        "commit": _git("rev-parse", "HEAD") or None,
        "dirty": bool(worktree),
        "worktree_sha256": hashlib.sha256(worktree).hexdigest(),
        "implementation_sha256": hashlib.sha256(implementation).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("prme", "duckdb", "usearch", "tantivy", "fastembed")
        },
        "embedding": {
            "model": provider.model_name,
            "version": provider.model_version,
            "dimension": provider.dimension,
            "weights_digest": None,
        },
        "engine": {
            "backend": config.backend,
            "vector_exact_search": config.vector_exact_search,
            "duckdb_threads": config.duckdb_threads,
            "opportunistic_organizer": config.organizer.opportunistic_enabled,
            "qa_pairing": config.enable_qa_pairing,
            "query_reformulation": config.enable_query_reformulation,
            "store_supersedence": config.enable_store_supersedence,
            "surprise_gating": config.enable_surprise_gating,
            "reranker": config.enable_reranker,
            "encryption": config.encryption_enabled,
        },
    }


def _signature(response) -> dict:
    context = response.bundle.render().encode()
    return {
        "node_ids": [str(candidate.node.id) for candidate in response.results],
        "scores": [candidate.composite_score for candidate in response.results],
        "context_sha256": hashlib.sha256(context).hexdigest(),
    }


async def _case(args, size: int, *, isolation: bool) -> dict:
    provider = (
        DeterministicEmbeddingProvider()
        if args.embedding_provider == "deterministic"
        else FastEmbedProvider(args.embedding_model)
    )
    with tempfile.TemporaryDirectory(prefix=f"prme-operational-{size}-") as directory:
        root = Path(directory)
        config = PRMEConfig(
            database_url=None,
            db_path=str(root / "memory.duckdb"),
            vector_path=str(root / "vectors.usearch"),
            lexical_path=str(root / "lexical"),
            duckdb_threads=args.duckdb_threads,
            vector_exact_search=True,
            enable_qa_pairing=False,
            enable_query_reformulation=False,
            enable_store_supersedence=False,
            enable_surprise_gating=False,
            enable_reranker=False,
            encryption_enabled=False,
            organizer={"opportunistic_enabled": False},
        )
        owners = [f"operational-owner-{index}" for index in range(args.owners)]
        async with MemoryEngine.open(config, embedding_provider=provider) as engine:
            started = perf_counter()
            for index in range(size):
                for owner_index, owner in enumerate(owners):
                    receipt = await engine.store_with_receipt(
                        f"Namespace {owner_index} private record {index}: "
                        f"tenant secret owner-{owner_index}-{index} topic {index % 7}",
                        user_id=owner,
                    )
                    if receipt.processing_status.status != "complete":
                        raise RuntimeError("Store did not reach its durable completion boundary")
            ingestion_ms = (perf_counter() - started) * 1000
            for owner in owners:
                if len(await engine.scan_nodes(user_id=owner, limit=size + 1)) != size:
                    raise RuntimeError("Owner fixture count mismatch")

            clock = datetime.now(timezone.utc)
            measured_owner = owners[0]
            query = f"tenant secret owner-0-{min(3, size - 1)} topic {min(3, size - 1) % 7}"
            baseline = None
            for _ in range(args.warmups):
                baseline = await engine.retrieve(
                    query, user_id=measured_owner, reference_time=clock,
                    min_score=0, limit=args.result_limit, include_cross_scope=False,
                )
            if baseline is None:
                baseline = await engine.retrieve(
                    query, user_id=measured_owner, reference_time=clock,
                    min_score=0, limit=args.result_limit, include_cross_scope=False,
                )
            expected = _signature(baseline)
            samples: list[float] = []
            repeat_matches = 0
            for index in range(max(args.latency_samples, args.determinism_samples)):
                started = perf_counter()
                response = await engine.retrieve(
                    query, user_id=measured_owner, reference_time=clock,
                    min_score=0, limit=args.result_limit, include_cross_scope=False,
                )
                elapsed = (perf_counter() - started) * 1000
                if index < args.latency_samples:
                    samples.append(elapsed)
                if index < args.determinism_samples and _signature(response) == expected:
                    repeat_matches += 1
            receipt = await engine.get_retrieval_receipt(
                str(baseline.metadata.request_id), user_id=measured_owner,
            )
            if receipt is None or receipt.execution is None:
                raise RuntimeError("Measured retrieval lacks an execution receipt")

            isolation_result = None
            if isolation:
                leak_queries: list[int] = []
                ids_by_owner = {
                    owner: {
                        str(node.id) for node in await engine.scan_nodes(
                            user_id=owner, limit=size + 1,
                        )
                    }
                    for owner in owners
                }
                forbidden_by_owner = {
                    requester: set().union(*(
                        ids for owner, ids in ids_by_owner.items() if owner != requester
                    ))
                    for requester in owners
                }
                started = perf_counter()
                for sample in range(args.isolation_samples):
                    requester_index = sample % len(owners)
                    target_index = (requester_index + 1) % len(owners)
                    requester = owners[requester_index]
                    record_index = sample % size
                    forbidden = forbidden_by_owner[requester]
                    query_text = (
                        f"Namespace {target_index} private record {record_index}: "
                        f"tenant secret owner-{target_index}-{record_index}"
                    )
                    response = await engine.retrieve(
                        query_text,
                        user_id=requester,
                        reference_time=clock,
                        min_score=0,
                        limit=args.result_limit,
                        include_cross_scope=False,
                    )
                    returned = {str(candidate.node.id) for candidate in response.results}
                    if returned & forbidden or any(
                        candidate.node.user_id != requester for candidate in response.results
                    ):
                        leak_queries.append(sample)
                isolation_result = {
                    "queries": args.isolation_samples,
                    "owner_partitions": len(owners),
                    "rotating_requesters_and_foreign_targets": True,
                    "leak_count": len(leak_queries),
                    "leak_query_indexes": leak_queries,
                    "elapsed_seconds": perf_counter() - started,
                }

            return {
                "stored_nodes_per_owner": size,
                "stored_nodes_total": size * len(owners),
                "owner_partitions": len(owners),
                "ingestion_ms": ingestion_ms,
                "retrieval": _latency(samples),
                "repeatability": {
                    "queries": args.determinism_samples,
                    "exact_matches": repeat_matches,
                    "all_exact": repeat_matches == args.determinism_samples,
                    "signature": expected,
                },
                "isolation": isolation_result,
                "receipt_features": receipt.execution.features,
            }


async def run(args: SimpleNamespace) -> dict:
    if any(size < 1 for size in args.sizes) or len(set(args.sizes)) != len(args.sizes):
        raise ValueError("sizes must be unique positive integers")
    for name in ("latency_samples", "determinism_samples", "isolation_samples", "result_limit"):
        if getattr(args, name) < 1:
            raise ValueError(f"{name} must be positive")
    if args.warmups < 0:
        raise ValueError("warmups must be nonnegative")
    if args.owners < 2:
        raise ValueError("owners must be at least 2")
    provider = (
        DeterministicEmbeddingProvider()
        if args.embedding_provider == "deterministic"
        else FastEmbedProvider(args.embedding_model)
    )
    declaration = PRMEConfig(
        database_url=None,
        duckdb_threads=args.duckdb_threads,
        vector_exact_search=True,
        enable_qa_pairing=False,
        enable_query_reformulation=False,
        enable_store_supersedence=False,
        enable_surprise_gating=False,
        enable_reranker=False,
        encryption_enabled=False,
        organizer={"opportunistic_enabled": False},
    )
    report = {
        "schema_version": 1,
        "run_id": str(uuid4()),
        "kind": "operational-retrieval",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "complete": False,
        "parameters": {
            "sizes": args.sizes,
            "warmups": args.warmups,
            "latency_samples": args.latency_samples,
            "determinism_samples": args.determinism_samples,
            "isolation_samples": args.isolation_samples,
            "result_limit": args.result_limit,
            "owner_partitions": args.owners,
        },
        "provenance": _provenance(provider, declaration),
        "limitations": [
            "Operational latency and isolation measurement; no relevance or answer-quality claim.",
            "Wall-clock latency depends on this host and concurrent workloads.",
            "Embedding provider metadata does not include a model-weights digest.",
            "Synthetic source text exercises public storage and retrieval but is not a user workload.",
        ],
        "cases": [],
        "conformance": {
            "rfc_0005_operational_requirements_met": False,
            "required_sizes": [10, 50, 200],
            "required_repeat_queries": 100,
            "required_isolation_queries": 10000,
            "required_owner_partitions": 5,
        },
    }
    _write(args.output, report)
    try:
        for size in args.sizes:
            report["cases"].append(
                await _case(args, size, isolation=size == max(args.sizes))
            )
            _write(args.output, report)
        report["complete"] = all(
            case["repeatability"]["all_exact"]
            and (case["isolation"] is None or case["isolation"]["leak_count"] == 0)
            for case in report["cases"]
        )
        report["conformance"]["rfc_0005_operational_requirements_met"] = (
            report["complete"]
            and sorted(args.sizes) == [10, 50, 200]
            and args.determinism_samples >= 100
            and args.isolation_samples >= 10000
            and args.owners >= 5
        )
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write(args.output, report)
        return report
    except BaseException as exc:
        report["benchmark_error"] = type(exc).__name__
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write(args.output, report)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", nargs="+", type=int, default=[10, 50, 200])
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--latency-samples", type=int, default=100)
    parser.add_argument("--determinism-samples", type=int, default=100)
    parser.add_argument("--isolation-samples", type=int, default=10000)
    parser.add_argument("--result-limit", type=int, default=10)
    parser.add_argument("--owners", type=int, default=5)
    parser.add_argument("--duckdb-threads", type=int)
    parser.add_argument(
        "--embedding-provider", choices=["fastembed", "deterministic"],
        default="fastembed",
    )
    parser.add_argument("--embedding-model", default="BAAI/bge-small-en-v1.5")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
