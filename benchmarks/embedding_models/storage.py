"""PostgreSQL pgvector size and speed per 10,000 memories, for each model's real vectors (``GATE.md``, measure 3).

The table has the engine's ``nodes`` columns that matter here (id, user, content, the vector column and the embedding
model and version) and the engine's index (HNSW, cosine, m=16, ef_construction=64). The 10,000 memories are the scale
test's look-alike store (the 189-memory core and 9,811 look-alikes), with the vectors the scale run made for them.
Sizes come from PostgreSQL itself. Speeds are one connection on this machine: the HNSW build, one ``UPDATE`` of a vector
column with the index in place (what ``store()`` does after the node row), and the exact cosine search the engine uses
by default (it materializes the user's rows and sorts, so its cost grows with the dimension).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import pickle
import statistics
import time
import uuid

import asyncpg

from benchmarks.embedding_models.latency import percentile
from benchmarks.embedding_models.models import MODELS
from benchmarks.graph_oracle.scale import load_core, make_clones

COUNT = 10000
SEARCHES = 100
UPDATES = 300


def memory_texts() -> list[str]:
    _, core = load_core()
    clones, _, _ = make_clones(core, COUNT - len(core.memories))
    return ([memory.text for memory in core.memories] + [memory.text for memory, _ in clones])[:COUNT]


def vector_text(vector) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


async def measure(url: str, key: str, vectors: dict, texts: list[str]) -> dict:
    model = MODELS[key]
    schema = f"emb_{key.replace('-', '_')}"
    connection = await asyncpg.connect(url)
    try:
        await connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await connection.execute(f"CREATE SCHEMA {schema}")
        await connection.execute(f"SET search_path TO {schema}, public")
        rows = [(uuid.uuid4(), "scale", text, vector_text(vectors[hashlib.sha1(text.encode()).hexdigest()])) for text in texts]
        await connection.execute("CREATE TABLE plain_nodes (id uuid PRIMARY KEY, user_id text NOT NULL, content text NOT NULL, "
                                 "embedding_model varchar, embedding_version varchar)")
        await connection.execute(f"CREATE TABLE nodes (id uuid PRIMARY KEY, user_id text NOT NULL, content text NOT NULL, "
                                 f"embedding_model varchar, embedding_version varchar, embedding vector({model.dimension}))")
        await connection.copy_records_to_table("plain_nodes", columns=["id", "user_id", "content"], records=[row[:3] for row in rows])
        await connection.execute("UPDATE plain_nodes SET embedding_model = 'm', embedding_version = 'v'")
        await connection.copy_records_to_table("nodes", columns=["id", "user_id", "content"], records=[row[:3] for row in rows])
        await connection.execute("UPDATE nodes SET embedding_model = 'm', embedding_version = 'v'")
        # The vector goes in through the text form, as the engine writes it.
        await connection.executemany(f"UPDATE nodes SET embedding = $2::vector({model.dimension}) WHERE id = $1",
                                     [(row[0], row[3]) for row in rows])
        await connection.execute("VACUUM FULL nodes")
        await connection.execute("VACUUM FULL plain_nodes")
        no_index = await connection.fetchrow("SELECT pg_table_size('nodes') AS table_bytes, pg_relation_size('nodes') AS heap, "
                                             "pg_table_size('plain_nodes') AS plain_bytes, "
                                             "sum(pg_column_size(embedding)) AS column_bytes FROM nodes")
        # The local container's shared memory is 64 MB, too small for a parallel HNSW build, so the build runs in one process.
        await connection.execute("SET max_parallel_maintenance_workers = 0")
        started = time.perf_counter()
        await connection.execute("CREATE INDEX idx_nodes_embedding_hnsw ON nodes USING hnsw (embedding vector_cosine_ops) "
                                 "WITH (m = 16, ef_construction = 64)")
        build = time.perf_counter() - started
        index_bytes = await connection.fetchval("SELECT pg_relation_size('idx_nodes_embedding_hnsw')")
        # One vector written with the index in place.
        sample = rows[:UPDATES]
        update_ms = []
        for row in sample:
            began = time.perf_counter()
            await connection.execute(f"UPDATE nodes SET embedding = $2::vector({model.dimension}) WHERE id = $1 AND user_id = 'scale'", row[0], row[3])
            update_ms.append((time.perf_counter() - began) * 1000)
        # The engine's default search: exact, over the user's rows.
        search_ms = []
        for row in rows[:SEARCHES]:
            began = time.perf_counter()
            await connection.fetch(
                "WITH eligible AS MATERIALIZED (SELECT id::text AS node_id, (embedding OPERATOR(public.<=>) $1::vector) AS distance, "
                "embedding_model, embedding_version, vector_dims(embedding) AS embedding_dim FROM nodes WHERE user_id = 'scale') "
                "SELECT * FROM eligible WHERE distance < 'Infinity'::float8 ORDER BY distance, node_id::uuid LIMIT 60", row[3])
            search_ms.append((time.perf_counter() - began) * 1000)
        total = await connection.fetchval("SELECT pg_total_relation_size('nodes')")
        await connection.execute(f"DROP SCHEMA {schema} CASCADE")
    finally:
        await connection.close()
    return {
        "model": key, "dimension": model.dimension, "memories": len(rows),
        "vector_column_bytes_per_row": round(no_index["column_bytes"] / len(rows), 1),
        "table_mb_without_index": round(no_index["table_bytes"] / 1e6, 2), "text_only_table_mb": round(no_index["plain_bytes"] / 1e6, 2),
        "vector_table_mb": round((no_index["table_bytes"] - no_index["plain_bytes"]) / 1e6, 2),
        "hnsw_index_mb": round(index_bytes / 1e6, 2), "total_mb": round(total / 1e6, 2),
        "vector_and_index_mb": round((no_index["table_bytes"] - no_index["plain_bytes"] + index_bytes) / 1e6, 2),
        "hnsw_build_seconds": round(build, 1),
        "update_ms": {"p50": round(statistics.median(update_ms), 2), "p95": round(percentile(update_ms, 0.95), 2)},
        "exact_search_ms": {"p50": round(statistics.median(search_ms), 2), "p95": round(percentile(search_ms, 0.95), 2)},
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models storage")
    parser.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    parser.add_argument("--scale-dir", type=Path, required=True, help="The folder holding scale-<model>/vectors.pkl")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    texts = memory_texts()
    results = json.loads(args.out.read_text()) if args.out.exists() else {}
    for key in args.models:
        vectors = pickle.loads((args.scale_dir / f"scale-{key}" / "vectors.pkl").read_bytes())
        results[key] = asyncio.run(measure(args.database_url, key, vectors, texts))
        print(key, results[key]["vector_and_index_mb"], "MB", results[key]["exact_search_ms"])
        args.out.write_text(json.dumps(results, indent=1) + "\n")
