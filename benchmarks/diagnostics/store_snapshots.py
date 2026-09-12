"""Measure complete vector snapshot writes during acknowledged direct stores.

Uses deterministic synthetic embeddings to isolate persistence work. This is not
an embedding throughput, cold-start latency, or memory-quality benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

from prme import MemoryEngine, PRMEConfig
from prme.storage import engine as engine_module
from prme.storage.vector_index import VectorIndex
from benchmarks.diagnostics._process import checked_report


class FixedProvider:
    dimension = 384
    model_name = "snapshot-write-probe"
    model_version = "1"

    async def embed(self, texts):
        return [[hashlib.sha256(t.encode()).digest()[i % 32] / 255 for i in range(self.dimension)] for t in texts]


async def run(count: int, interval: int):
    with tempfile.TemporaryDirectory(prefix="prme-snapshot-writes-") as tmp:
        root = Path(tmp)
        config = PRMEConfig(
            database_url=None, encryption_enabled=False,
            db_path=str(root / "memory.duckdb"), vector_path=str(root / "vectors.usearch"),
            lexical_path=str(root / "lexical"), vector_save_interval=interval,
            organizer={"opportunistic_enabled": False}, enable_qa_pairing=False,
            enable_store_supersedence=False, reinforce_similarity_threshold=None,
        )
        original_provider = engine_module.create_embedding_provider
        engine_module.create_embedding_provider = lambda _: FixedProvider()
        writes = []
        try:
            async with MemoryEngine.open(config) as engine:
                original_snapshot = engine._vector_index._save_snapshot

                def save_snapshot():
                    started = time.perf_counter()
                    original_snapshot()
                    writes.append({"bytes": Path(config.vector_path).stat().st_size,
                                   "milliseconds": (time.perf_counter() - started) * 1000})

                engine._vector_index._save_snapshot = save_snapshot
                started = time.perf_counter()
                events = [await engine.store(f"Observation {i}: always record timestamps in UTC.", user_id="probe")
                          for i in range(count)]
                store_ms = (time.perf_counter() - started) * 1000
                for eid in events:
                    assert (await engine.processing_status(eid, user_id="probe")).status == "complete"
                payloads = engine._conn.execute("SELECT count(*) FROM vector_payloads").fetchone()[0]
                assert payloads == count
                store_writes = list(writes)
                unsaved = engine._vector_index._unsaved_inserts
            async with MemoryEngine.open(config) as engine:
                assert await engine.count_nodes(user_id="probe") == count
                assert engine._conn.execute("SELECT count(*) FROM vector_payloads").fetchone()[0] == count
        finally:
            engine_module.create_embedding_provider = original_provider
    return {
        "passed": True, "kind": "direct-store-snapshot-write-work", "count": count,
        "configured_save_interval": interval, "payload_count": payloads,
        "store_snapshot_count": len(store_writes), "store_snapshot_bytes": sum(w["bytes"] for w in store_writes),
        "store_snapshot_ms": sum(w["milliseconds"] for w in store_writes),
        "store_total_ms": store_ms, "unsaved_before_close": unsaved,
        "close_snapshot_count": len(writes) - len(store_writes), "snapshots": writes,
        "source": {"engine_sha256": hashlib.sha256(Path(inspect.getfile(MemoryEngine)).read_bytes()).hexdigest(),
                   "vector_sha256": hashlib.sha256(Path(inspect.getfile(VectorIndex)).read_bytes()).hexdigest()},
        "python": platform.python_version(),
        "dependencies": {name: importlib.metadata.version(name) for name in ("prme", "duckdb", "usearch", "tantivy")},
        "limits": ["Synthetic fixed embeddings; not an end-to-end throughput or retrieval-quality measurement.",
                   "Snapshot file sizes measure serialized output, not physical disk writes or fsync behavior.",
                   "Other machine workloads can affect component and total timings."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=256)
    parser.add_argument("--interval", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.count < 1 or args.interval < 1:
        parser.error("Count and interval must be positive")
    if args.worker:
        report = asyncio.run(run(args.count, args.interval))
    else:
        report = checked_report([sys.executable, "-m", "benchmarks.diagnostics.store_snapshots", "--worker",
                                 "--count", str(args.count), "--interval", str(args.interval)], timeout=600)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
