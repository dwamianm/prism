"""Time ``MemoryEngine.reembed()`` on a store of N memories: bge-small to bge-base (dwamianm/prism#368).

The store is built through ``store()`` under the first model with ``enable_multi_model_embeddings`` on, so it holds only
that model's vectors. The job then builds the second model's vectors for every memory in one call, on this machine's CPU
in one process, and the run records how long it took, what it wrote, and that the second index is complete. No model
service is called, and the memories are synthetic sentences of the length of a chat turn.

    python -m benchmarks.embedding_models job --backend duckdb --memories 10000 --output job-duckdb.json
    PRME_DATABASE_URL=postgresql://... python -m benchmarks.embedding_models job --backend postgres --memories 10000

``--threads`` fixes ONNX Runtime's thread count for both models (the default is one per core); the experiment's per-text
figures used one. Building 10,000 memories through ``store()`` takes a quarter of an hour, so ``--store-dir`` (DuckDB) or
``--schema`` (PostgreSQL) names a store that is built once and kept: a later run finds it, drops the second model's
vectors if a run left them, and times the job again.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import random
import tempfile
import time
import uuid
from pathlib import Path

from pydantic import SecretStr

from prme import MemoryEngine
from prme.config import PRMEConfig

FIRST = "BAAI/bge-small-en-v1.5"
SECOND = "BAAI/bge-base-en-v1.5"
OWNER = "job-timing"
NAMES = ["Alex", "Jordan", "Melanie", "Caroline", "Priya", "Tomas", "Wen", "Ingrid", "Samir", "Lucia"]
VERBS = ["booked", "tried", "talked about", "finished", "started", "forgot", "borrowed", "painted", "repaired", "planned"]
THINGS = ["a trip to the coast", "the quarterly report", "a sourdough starter", "the garden fence", "a piano lesson",
          "the old bicycle", "a chess club meeting", "the museum tickets", "a new coffee grinder", "the lake cabin"]
WHEN = ["on Monday", "last weekend", "before the move", "in March", "after work", "during the holiday", "this morning"]


def sentence(number: int, rng: random.Random) -> str:
    """A memory of about twenty words that differs from every other."""
    a, b = rng.sample(NAMES, 2)
    return (f"{a} {rng.choice(VERBS)} {rng.choice(THINGS)} {rng.choice(WHEN)} and then {rng.choice(VERBS)} "
            f"{rng.choice(THINGS)} with {b}, which was memory number {number} of the day.")


def configuration(backend: str, directory: Path, schema: str | None, threads: int | None, **extra) -> PRMEConfig:
    embedding = {"model_name": FIRST, **({"threads": threads} if threads else {}), **extra.pop("embedding", {})}
    values = dict(enable_multi_model_embeddings=True, embedding=embedding, organizer={"opportunistic_enabled": False},
                  **extra)
    if backend == "postgres":
        url = os.environ["PRME_DATABASE_URL"]
        values["database_url"] = SecretStr(f"{url}{'&' if '?' in url else '?'}search_path={schema}")
    else:
        (directory / "lexical").mkdir(exist_ok=True)
        # PRME_DATABASE_URL, which the postgres runs set, would otherwise select PostgreSQL here too.
        values.update(database_url=None, db_path=str(directory / "memory.duckdb"), lexical_path=str(directory / "lexical"),
                      vector_path=str(directory / "vectors.usearch"))
    return PRMEConfig(**values)


async def run(backend: str, memories: int, threads: int | None, batch_size: int, directory: Path, schema: str | None,
              build: bool) -> dict:
    rng = random.Random(7)
    texts = [sentence(number, rng) for number in range(memories)]
    built = time.monotonic()
    async with MemoryEngine.open(configuration(backend, directory, schema, threads)) as engine:
        if build:
            for text in texts:
                await engine.store(text, user_id=OWNER)
        status = await engine.embedding_status()
        if any(item["model"] == SECOND for item in status["models"]):
            await engine.drop_embeddings(SECOND)  # a run of this harness left it
            status = await engine.embedding_status()
    build_seconds = time.monotonic() - built if build else None

    async with MemoryEngine.open(configuration(backend, directory, schema, threads,
                                               embedding={"additional_models": [SECOND]})) as engine:
        await engine.embed_texts(["warm up"])  # load the first model before the clock starts
        started = time.monotonic()
        result = await engine.reembed(SECOND, batch_size=batch_size, threads=threads)
        seconds = time.monotonic() - started
        after = await engine.embedding_status()
    return {
        "backend": backend, "memories": memories, "threads": threads or "default", "batch_size": batch_size,
        "store_build_seconds": None if build_seconds is None else round(build_seconds, 1), "store_status": status,
        "job_seconds": round(seconds, 2), "per_memory_ms": round(1000 * seconds / max(memories, 1), 2),
        "result": result.model_dump(mode="json"), "status_after": after,
        "machine": {"cpu_count": os.cpu_count(), "platform": platform.platform(), "python": platform.python_version()},
    }


async def drop_schema(schema: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(os.environ["PRME_DATABASE_URL"])
    try:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await connection.close()


async def prepare_schema(schema: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(os.environ["PRME_DATABASE_URL"])
    try:
        await connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await connection.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=["duckdb", "postgres"], default="duckdb")
    parser.add_argument("--memories", type=int, default=10_000)
    parser.add_argument("--threads", type=int, help="ONNX Runtime threads (default: one per core)")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--store-dir", type=Path, help="DuckDB: keep the store here and reuse it when it is already built")
    parser.add_argument("--schema", help="PostgreSQL: keep the store in this schema and reuse it when it is already built")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    schema = (args.schema or f"job_{uuid.uuid4().hex[:10]}") if args.backend == "postgres" else None

    async def go() -> dict:
        scratch = tempfile.TemporaryDirectory() if not args.store_dir else None
        directory = Path(scratch.name) if scratch else args.store_dir
        directory.mkdir(parents=True, exist_ok=True)
        build = True
        try:
            if schema:
                import asyncpg

                connection = await asyncpg.connect(os.environ["PRME_DATABASE_URL"])
                try:
                    exists = await connection.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = $1", schema)
                finally:
                    await connection.close()
                build = not (exists and args.schema)
                if build:
                    await prepare_schema(schema)
            else:
                build = not (directory / "memory.duckdb").exists()
            return await run(args.backend, args.memories, args.threads, args.batch_size, directory, schema, build)
        finally:
            if schema and not args.schema:
                await drop_schema(schema)
            if scratch:
                scratch.cleanup()

    report = asyncio.run(go())
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
