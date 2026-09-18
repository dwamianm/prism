"""Measure vector snapshot startup/recovery with repeatable numerical payloads.

This isolates VectorIndex construction with an already-open DuckDB connection.
It is not total engine startup, cold filesystem latency, or a service SLO.
"""

import argparse
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import platform
import tempfile
import time

import duckdb
import numpy as np

from prme.storage.vector_index import VectorIndex


class OfflineProvider:
    dimension = 384
    model_name = "fixed-probe"
    model_version = "1"

    async def embed(self, texts):
        raise AssertionError("Startup recovery must not invoke inference")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", nargs="+", type=int, default=[1000, 10000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--skip-recovery", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1 or any(count < 1 for count in args.counts):
        parser.error("Counts and repeats must be positive")
    records = []
    for count in args.counts:
        with tempfile.TemporaryDirectory(prefix="prme-vector-probe-") as directory:
            root = Path(directory)
            conn = duckdb.connect(str(root / "memory.duckdb"))
            try:
                index = VectorIndex(conn, str(root / "vectors.usearch"), OfflineProvider())
                vectors = np.random.default_rng(42).normal(size=(count, 384)).astype("<f4")
                # Seed metadata/payloads directly: inference and ingestion are
                # deliberately excluded from this startup-only measurement.
                conn.execute(
                    "INSERT INTO vector_metadata (vector_key, node_id, user_id, "
                    "embedding_model, embedding_version, embedding_dim) "
                    "SELECT range+1, range::VARCHAR, 'alice', 'fixed-probe', '1', 384 FROM range(?)", [count],
                )
                conn.executemany("INSERT INTO vector_payloads VALUES (?, ?)", [
                    (i + 1, vector.tobytes()) for i, vector in enumerate(vectors)
                ])
                index._index.add(np.arange(1, count + 1, dtype=np.uint64), vectors, threads=4)
                index._save_snapshot()
                del index, vectors
                for attempt in range(args.repeats):
                    started = time.perf_counter()
                    opened = VectorIndex(conn, str(root / "vectors.usearch"), OfflineProvider())
                    elapsed = time.perf_counter() - started
                    assert len(opened._index) == count
                    records.append({"count": count, "mode": "intact", "attempt": attempt, "seconds": elapsed})
                    del opened
                if not args.skip_recovery:
                    (root / "vectors.usearch").unlink()
                    started = time.perf_counter()
                    opened = VectorIndex(conn, str(root / "vectors.usearch"), OfflineProvider())
                    elapsed = time.perf_counter() - started
                    assert len(opened._index) == count
                    records.append({"count": count, "mode": "missing_snapshot", "seconds": elapsed})
                    del opened
            finally:
                conn.close()
    report = {
        "records": records, "dimension": 384, "seed": 42,
        "python": platform.python_version(),
        "dependencies": {name: importlib.metadata.version(name) for name in ("duckdb", "usearch", "numpy")},
        "implementation_sha256": hashlib.sha256(Path(inspect.getfile(VectorIndex)).read_bytes()).hexdigest(),
        "limits": "Warm vector-component construction with an open database; no isolation from other workloads guaranteed. Not total engine startup or an SLO.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
