"""What the model adds to one ``store()`` call and one ``retrieve()`` call, on PostgreSQL with one ONNX thread
(``GATE.md``, measure 3).

The store pack's 75 memories are stored one call at a time as ``POST /v1/store`` does, then its questions and the
three cities spellings are asked, 20 retrievals. Each call is timed, and the provider the engine builds is wrapped so
the seconds spent embedding (the stored text for a store, the question for a retrieve) are counted apart from the
rest. The time is this machine's, run with nothing else of mine running, and one connection to a local PostgreSQL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
import uuid
from unittest import mock

from pydantic import SecretStr

from benchmarks.diagnostics.extracted_packs import _drain_sources
from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.embedding_models.latency import percentile
from benchmarks.embedding_models.models import recipes
from benchmarks.embedding_models.pack import fixture_with_variants
from benchmarks.fast_cycle import store as fast_store
from prme import MemoryEngine
from prme.types import NodeType, Scope


async def fresh_database(url: str, name: str) -> str:
    """Drop and create a database of this name beside ``url``'s, with pgvector, and return its URL.

    The engine's table keeps the vector dimension of the first model that opened it, so each recipe gets its own database.
    """
    import asyncpg

    head, _, _ = url.rpartition("/")
    admin = await asyncpg.connect(f"{head}/postgres")
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {name}")
    finally:
        await admin.close()
    created = await asyncpg.connect(f"{head}/{name}")
    try:
        await created.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await created.close()
    return f"{head}/{name}"


class Timed:
    """Counts the seconds the wrapped provider spends embedding."""

    def __init__(self, provider):
        self._provider = provider
        self.seconds = 0.0

    model_name = property(lambda self: self._provider.model_name)
    model_version = property(lambda self: self._provider.model_version)
    dimension = property(lambda self: self._provider.dimension)

    async def embed(self, texts):
        began = time.perf_counter()
        try:
            return await self._provider.embed(texts)
        finally:
            self.seconds += time.perf_counter() - began

    async def embed_query(self, text):
        began = time.perf_counter()
        try:
            return await self._provider.embed_query(text)
        finally:
            self.seconds += time.perf_counter() - began


def summary(values: list[float]) -> dict:
    return {"p50": round(statistics.median(values), 1), "p95": round(percentile(values, 0.95), 1), "mean": round(statistics.fmean(values), 1)}


async def run_recipe(label: str, recipe, database_url: str, fixture_path: Path) -> dict:
    from prme.storage import engine as engine_module

    fixture = fast_store.load_fixture(fixture_path)
    original = engine_module.create_embedding_provider
    timed: list[Timed] = []

    def wrap(config):
        provider = Timed(original(config))
        timed.append(provider)
        return provider

    previous = os.environ.get("PRME_BENCH_EMBEDDING")
    os.environ["PRME_BENCH_EMBEDDING"] = json.dumps(recipe.embedding())
    try:
        with tempfile.TemporaryDirectory(prefix="embedding-ops-") as work, mock.patch.object(engine_module, "create_embedding_provider", wrap):
            pack = Path(work) / "pack"
            (pack / "lexical_index").mkdir(parents=True)
            config = gate_config(pack).model_copy(update={"database_url": SecretStr(database_url)})
            owner = f"{fixture['owner']}-{uuid.uuid4().hex[:8]}"
            async with MemoryEngine.open(config) as engine:
                # The first call loads the model; the report is about calls after that.
                await engine.store_with_receipt("A warm up memory, stored first so the model is loaded.", user_id=f"warm-{owner}", role="user",
                                                node_type=NodeType.NOTE, scope=Scope.PERSONAL, extract=False)
                await _drain_sources(engine, f"warm-{owner}")
                provider = timed[-1]
                provider.seconds = 0.0
                store_ms, store_embed_ms = [], []
                for memory in fixture["memories"]:
                    spent = provider.seconds
                    began = time.perf_counter()
                    await engine.store_with_receipt(memory["content"], user_id=owner, role=memory["role"], speaker=memory.get("speaker"),
                                                    node_type=NodeType(memory["node_type"]), scope=Scope(memory["scope"]),
                                                    metadata={"source": "embedding-ops"})
                    store_ms.append((time.perf_counter() - began) * 1000)
                    store_embed_ms.append((provider.seconds - spent) * 1000)
                await _drain_sources(engine, owner)
                retrieve_ms, retrieve_embed_ms = [], []
                for question in fixture["key"]["questions"]:
                    spent = provider.seconds
                    began = time.perf_counter()
                    await engine.retrieve(question["question"], user_id=owner)
                    retrieve_ms.append((time.perf_counter() - began) * 1000)
                    retrieve_embed_ms.append((provider.seconds - spent) * 1000)
    finally:
        if previous is None:
            os.environ.pop("PRME_BENCH_EMBEDDING", None)
        else:
            os.environ["PRME_BENCH_EMBEDDING"] = previous
    return {"recipe": label, "store_calls": len(store_ms), "retrieve_calls": len(retrieve_ms),
            "store_ms": summary(store_ms), "store_embedding_ms": summary(store_embed_ms),
            "retrieve_ms": summary(retrieve_ms), "retrieve_embedding_ms": summary(retrieve_embed_ms)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models operations")
    parser.add_argument("--recipes", nargs="+", choices=list(recipes()), default=["bge-small", "minilm", "bge-base", "bge-large", "mxbai", "nomic"])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    found = recipes()
    results = json.loads(args.out.read_text()) if args.out.exists() else {}
    with tempfile.TemporaryDirectory(prefix="embedding-ops-fixture-") as scratch:
        fixture_path, _ = fixture_with_variants(Path(scratch))
        for label in args.recipes:
            url = asyncio.run(fresh_database(args.database_url, "prism_we13_ops"))
            results[label] = asyncio.run(run_recipe(label, found[label], url, fixture_path))
            print(label, results[label]["store_ms"], results[label]["retrieve_ms"])
            args.out.write_text(json.dumps(results, indent=1) + "\n")
