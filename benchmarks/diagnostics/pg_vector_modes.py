"""Compare exact and planner-selected PostgreSQL search on authored vectors."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
from time import perf_counter
from uuid import UUID, uuid4

import asyncpg
import prme
from prme.storage.pg.schema import initialize_pg_database
from prme.storage.pg.vector_index import PgVectorIndex


class Provider:
    model_name = "pg-filter-probe"
    model_version = "1"
    dimension = 384

    async def embed(self, texts):
        return [[1., *([0.] * 383)] for _ in texts]


class TracedPool:
    def __init__(self, pool):
        self.pool = pool
        self.query = None

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as conn:
            trace = self
            class Connection:
                async def fetch(self, query, *args):
                    trace.query = query, args
                    return await conn.fetch(query, *args)
            yield Connection()


async def run(args, report):
    schema = "prme_mode_probe_" + uuid4().hex
    admin = await asyncpg.connect(os.environ["PRME_TEST_DATABASE_URL"])
    pool = None
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(os.environ["PRME_TEST_DATABASE_URL"], min_size=1, max_size=1,
                                        server_settings={"search_path": f'"{schema}", public'})
        await initialize_pg_database(pool, embedding_dim=384)
        async with pool.acquire() as conn:
            report["database"] = {"server_version": await conn.fetchval("SHOW server_version"),
                "pgvector_version": await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")}
            report["planner_settings"] = {name: await conn.fetchval(f"SHOW {name}") for name in
                ["enable_seqscan", "enable_indexscan", "enable_bitmapscan", "hnsw.ef_search", "hnsw.iterative_scan"]}
            await conn.execute("DROP INDEX idx_nodes_embedding_hnsw")
            start = perf_counter()
            await conn.execute("""
                INSERT INTO nodes (id,node_type,user_id,scope,content,embedding,embedding_model,embedding_version)
                SELECT ('00000000-0000-0000-0000-' || lpad(to_hex(i + 100),12,'0'))::uuid,
                       'fact','owner','project','authored distractor',
                       (ARRAY[1::real,(i % 512)::real / 1000000] || array_fill(0::real,ARRAY[382]))::vector,
                       'pg-filter-probe','1' FROM generate_series(1,$1::integer) i
            """, args.rows)
            for number, leading in enumerate([[0.8, 0.6], [0.6, 0.8], [0., 1.]], 1):
                vector = "[" + ",".join(map(str, leading + [0.] * 382)) + "]"
                await conn.execute("INSERT INTO nodes (id,node_type,user_id,scope,content,embedding,embedding_model,embedding_version) "
                                   "VALUES ($1,'fact','owner','personal','authored eligible',$2::vector,'pg-filter-probe','1')",
                                   UUID(int=number), vector)
            async with conn.transaction():
                if args.serial_build:
                    await conn.execute("SET LOCAL max_parallel_maintenance_workers=0")
                report["index_build_workers"] = await conn.fetchval("SHOW max_parallel_maintenance_workers")
                await conn.execute("CREATE INDEX idx_nodes_embedding_hnsw ON nodes USING hnsw (embedding vector_cosine_ops) "
                                   "WITH (m=16,ef_construction=64)")
            await conn.execute("ANALYZE nodes")
            report["populate_and_build_seconds"] = perf_counter() - start
        traced = TracedPool(pool)
        modes = {"exact": PgVectorIndex(traced, Provider(), exact_search=True),
                 "approximate_allowed": PgVectorIndex(traced, Provider(), exact_search=False)}
        for case, scope in [("selective_scope", ["personal"]), ("all_scopes", None)]:
            report["cases"][case] = {name: {"samples_ms": [], "returned_ids": [], "plan": None} for name in modes}
            for repetition in range(args.queries + 2):
                order = list(modes) if repetition % 2 == 0 else list(reversed(modes))
                for name in order:
                    start = perf_counter()
                    rows = await modes[name].search("query", "owner", k=10, scope=scope)
                    elapsed = (perf_counter() - start) * 1000
                    ids = [row["node_id"] for row in rows]
                    if case == "selective_scope":
                        assert set(ids) <= {str(UUID(int=i)) for i in [1, 2, 3]}
                        if name == "exact":
                            assert ids == [str(UUID(int=i)) for i in [1, 2, 3]]
                    else:
                        assert len(ids) == 10
                    if repetition >= 2:
                        target = report["cases"][case][name]
                        target["samples_ms"].append(elapsed)
                        target["returned_ids"].append(ids)
                        if target["plan"] is None:
                            query, parameters = traced.query
                            async with pool.acquire() as conn:
                                target["plan"] = json.loads(await conn.fetchval("EXPLAIN (FORMAT JSON) " + query, *parameters))
        report["complete"] = True
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, choices=[1000, 10000], required=True)
    parser.add_argument("--queries", type=int, default=20)
    parser.add_argument("--serial-build", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.queries < 1:
        parser.error("--queries must be positive")
    if args.output.exists():
        raise ValueError("Refusing to overwrite prior evidence")
    report = {"complete": False, "distractor_rows": args.rows, "selective_eligible_rows": 3, "dimension": 384,
              "measured_queries_per_arm": args.queries, "warmup_queries_per_arm": 2,
              "serial_build": args.serial_build,
              "python": platform.python_version(), "prme_import_path": str(Path(prme.__file__).resolve()),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "cases": {},
              "limits": "Authored sparse numerical vectors, one host, warm queries, serial access. Component test, not real-model corpus quality or production latency."}
    try:
        asyncio.run(run(args, report))
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
