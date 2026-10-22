"""Run the offline evidence gate under another embedding model by re-embedding each saved pack's vectors (``GATE.md``).

The saved packs hold ``bge-small`` vectors, and the engine refuses vectors made by another model, so a gate under another
model needs the packs' vector indexes made again. ``prepare`` does that to the gate's verified scratch copy of a pack,
vector index only (the graph, the event log and the word index are untouched), and is a no-op when the copy already
holds the configured model's vectors, as a pack built through ``ingest()`` under that model does. Stored texts are
embedded once across the whole run: many LongMemEval-S packs share turns, and the cache keeps the vectors as float32.

    python -m benchmarks.embedding_models reembed --recipe nomic --cache <file> gate --lme-captures <dir> --output <json>

``--recipe`` sets ``PRME_BENCH_EMBEDDING`` for the gate; everything after the options is the gate's own command line.

One thread embeds a LongMemEval-S turn in about 50 ms and the saved packs hold 190,000 distinct ones, and more threads
barely help a single text, so ``warm`` embeds them first, in shards (``--shard i/n``, the texts split by hash), each in its
own process with its own cache file. The gate then reads every shard's file with a ``--cache`` for each and only indexes.

    python -m benchmarks.embedding_models reembed --recipe nomic warm --shard 0/8 --cache-out <file>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pickle
import sys
import time

import numpy as np

from benchmarks.embedding_models.models import recipes
from prme import MemoryEngine
from prme.storage.engine import _INDEXED_NODES_SQL, _INDEXED_STATES, _indexable_content

PAGE = 256


class ArrayCache:
    """A provider wrapper that embeds each distinct stored text once and keeps the vectors as float32."""

    def __init__(self, provider, vectors: dict[str, np.ndarray]):
        self._provider = provider
        self.vectors = vectors
        self.seconds = 0.0
        self.computed = 0
        self.hits = 0

    model_name = property(lambda self: self._provider.model_name)
    model_version = property(lambda self: self._provider.model_version)
    dimension = property(lambda self: self._provider.dimension)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            key = hashlib.sha1(text.encode()).hexdigest()
            if key in self.vectors:
                self.hits += 1
            else:
                began = time.perf_counter()
                self.vectors[key] = np.asarray((await self._provider.embed([text]))[0], dtype=np.float32)
                self.seconds += time.perf_counter() - began
                self.computed += 1
            out.append(self.vectors[key].tolist())
        return out

    async def embed_query(self, text: str) -> list[float]:
        return await self._provider.embed_query(text)


class Preparer:
    """The gate's ``PREPARE_PACK`` hook: re-embed a copy whose vectors are another model's."""

    def __init__(self, cache_paths: list[Path] | None):
        # The first file is written back; the others (the shards ``warm`` made) are only read.
        self._path = cache_paths[0] if cache_paths else None
        self.vectors: dict[str, np.ndarray] = {}
        for path in cache_paths or []:
            if path.exists():
                self.vectors.update(pickle.loads(path.read_bytes()))
        self.packs = 0
        self.reembedded = 0
        self.nodes = 0
        self.seconds = 0.0
        self.embedding_seconds = 0.0
        self.computed = 0

    async def __call__(self, copy: Path, config) -> None:
        self.packs += 1
        async with MemoryEngine.open(config) as engine:
            index = engine._vector_index
            provider = index._provider
            identity = (provider.model_name, provider.model_version, provider.dimension)
            held = {tuple(row) for row in engine._conn.execute(
                "SELECT DISTINCT embedding_model, embedding_version, embedding_dim FROM vector_metadata").fetchall()}
            if held == {identity}:
                return
            started = time.perf_counter()
            cache = ArrayCache(provider, self.vectors)
            index._provider = cache
            await engine._write_queue.submit(lambda: index.clear(), label="reembed.clear")
            offset = 0
            while rows := engine._conn.execute(_INDEXED_NODES_SQL + " LIMIT ? OFFSET ?", [*_INDEXED_STATES, PAGE, offset]).fetchall():
                for node_id, content, user_id, *_ in rows:
                    if _indexable_content(content):
                        await engine._write_queue.submit(lambda n=str(node_id), c=content, u=user_id: index.index(n, c, u),
                                                         label=f"reembed:{node_id}")
                        self.nodes += 1
                offset += PAGE
            await engine._write_queue.submit(lambda: index.save(), label="reembed.save")
            self.reembedded += 1
            self.seconds += time.perf_counter() - started
            self.embedding_seconds += cache.seconds
            self.computed += cache.computed

    def save(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_bytes(pickle.dumps(self.vectors))

    def summary(self) -> dict:
        return {"packs": self.packs, "reembedded_packs": self.reembedded, "nodes": self.nodes, "distinct_texts_embedded": self.computed,
                "embedding_seconds": round(self.embedding_seconds, 1), "reembed_seconds": round(self.seconds, 1)}


LME_CONTROLS = "data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline"


def saved_pack_texts(lme_captures: Path) -> list[str]:
    """The distinct stored texts of every saved pack the gate replays, in a fixed order."""
    import duckdb
    from benchmarks.diagnostics import product_packing as pp

    archive = pp.default_archive()
    cases = [*pp._locomo_cases(archive), *pp._longmemeval_cases(archive, lme_captures)]
    seen: dict[str, str] = {}
    for pack in dict.fromkeys(case.pack for case in cases):
        connection = duckdb.connect(str(Path(pack) / "memory.duckdb"), read_only=True)
        try:
            for (content,) in connection.execute(
                    "SELECT content FROM nodes WHERE COALESCE(lifecycle_state, 'tentative') IN "
                    f"({', '.join('?' for _ in _INDEXED_STATES)}) AND content IS NOT NULL ORDER BY id", list(_INDEXED_STATES)).fetchall():
                if _indexable_content(content):
                    seen.setdefault(hashlib.sha1(content.encode()).hexdigest(), content)
        finally:
            connection.close()
    return list(seen.values())


def warm(recipe_label: str, shard: int, shards: int, out: Path, lme_captures: Path) -> dict:
    """Embed one shard of the saved packs' distinct texts with the engine's provider (one thread) and save the vectors."""
    import asyncio

    from prme.config import EmbeddingConfig
    from prme.storage.embedding import create_embedding_provider

    recipe = recipes()[recipe_label]
    provider = create_embedding_provider(EmbeddingConfig(_env_file=None, **recipe.embedding(1)))._provider
    texts = [text for text in saved_pack_texts(lme_captures) if int(hashlib.sha1(text.encode()).hexdigest(), 16) % shards == shard]
    vectors = pickle.loads(out.read_bytes()) if out.exists() else {}
    started = time.perf_counter()

    async def run() -> None:
        for number, text in enumerate(texts, start=1):
            key = hashlib.sha1(text.encode()).hexdigest()
            if key not in vectors:
                vectors[key] = np.asarray((await provider.embed([text]))[0], dtype=np.float32)
            if number % 2000 == 0:
                print(f"shard {shard}/{shards}: {number}/{len(texts)} in {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
                out.write_bytes(pickle.dumps(vectors))

    asyncio.run(run())
    out.write_bytes(pickle.dumps(vectors))
    return {"recipe": recipe_label, "shard": shard, "shards": shards, "texts": len(texts), "seconds": round(time.perf_counter() - started, 1)}


def main(argv: list[str] | None = None) -> None:
    from benchmarks.diagnostics import product_packing

    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models reembed", usage="%(prog)s --recipe NAME [--cache FILE ...] gate ... | warm --shard I/N --cache-out FILE")
    parser.add_argument("--recipe", choices=list(recipes()), required=True)
    parser.add_argument("--cache", type=Path, action="append", default=[], help="A file of stored texts' vectors; the first is written back")
    parser.add_argument("--summary", type=Path, default=None, help="Write what was re-embedded here")
    args, rest = parser.parse_known_args(argv)
    if rest[:1] == ["warm"]:
        warm_parser = argparse.ArgumentParser(prog="reembed warm")
        warm_parser.add_argument("--shard", required=True, help="I/N: this process embeds the texts whose hash is I modulo N")
        warm_parser.add_argument("--cache-out", type=Path, required=True)
        warm_parser.add_argument("--lme-captures", type=Path, default=None, help=f"Default: the main checkout's {LME_CONTROLS}")
        options = warm_parser.parse_args(rest[1:])
        shard, shards = (int(part) for part in options.shard.split("/"))
        from benchmarks import checkout

        captures = options.lme_captures or checkout.main_checkout() / LME_CONTROLS
        print(json.dumps(warm(args.recipe, shard, shards, options.cache_out, captures)))
        return
    if rest[:1] != ["gate"]:
        parser.error("the gate's own command line must follow, starting with gate (or use warm)")
    os.environ[product_packing.EMBEDDING_ENV] = json.dumps(recipes()[args.recipe].embedding())
    preparer = Preparer(args.cache)
    product_packing.PREPARE_PACK = preparer
    started = time.perf_counter()
    try:
        product_packing.main(rest)
    finally:
        preparer.save()
        summary = {**preparer.summary(), "recipe": args.recipe, "total_seconds": round(time.perf_counter() - started, 1)}
        print(json.dumps(summary), file=sys.stderr)
        if args.summary:
            args.summary.write_text(json.dumps(summary, indent=1) + "\n")
