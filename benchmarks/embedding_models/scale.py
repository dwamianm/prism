"""The graph oracle's scale harness under another embedding model (``GATE.md``, measure 2).

It is ``benchmarks.graph_oracle.scale_run.baseline_lists`` run for one model: the combined core of 189 memories padded
in nested steps with look-alike memories to 2,000 and 10,000, and with conversation turns to 2,000, and the 204 frozen
questions asked of a store with no graph in it. Nothing about the questions, their answers or the padding changes.

Embedding is done through the harness's cache (each distinct text once, timed), which here also applies the recipe's
prefixes: ``document_prefix`` to every stored text, and for each *pass* a ``query_prefix`` to every question, so one
padded store serves a model's plain and instructed question recipes. ``--mode engine`` makes the engine's own
``embedding.query_prefix`` option do the same work instead, which a small run uses to check the two agree.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time

from benchmarks.embedding_models.models import MODELS, Model
from benchmarks.graph_oracle.run import commit, first_rank, metrics
from benchmarks.graph_oracle.scale import EmbeddingCache, load_core
from benchmarks.graph_oracle.scale_run import TYPES, baseline_lists, build_items, pad_memories

RUNS = {"lookalike": (2000, 10000), "conversation": (2000,)}
CITIES = ("store-v1:japan-cities", "store-v1:japan-cities#v1", "store-v1:japan-cities#v2")
BLIND = ("blind-v1", "blind-v2")


class RecipeCache(EmbeddingCache):
    """The harness's embedding cache with a recipe's prefixes, and a query encoder the engine uses for a question."""

    def __init__(self, path: Path | None, document_prefix: str | None, *, delegate_query: bool = False):
        super().__init__(None, path)
        self.document_prefix = document_prefix or ""
        self.query_prefix = ""
        self.query_seconds = 0.0
        self._delegate_query = delegate_query

    async def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [hashlib.sha1(text.encode()).hexdigest() for text in texts]
        missing = [(key, text) for key, text in dict(zip(keys, texts)).items() if key not in self._vectors]
        self.hits += len(texts) - len(missing)
        if missing:
            started = time.perf_counter()
            vectors = await self._provider.embed([self.document_prefix + text for _, text in missing])
            self.seconds += time.perf_counter() - started
            self.computed += len(missing)
            self._vectors.update({key: list(vector) for (key, _), vector in zip(missing, vectors)})
        return [self._vectors[key] for key in keys]

    async def embed_query(self, text: str) -> list[float]:
        if self._delegate_query:
            return await self._provider.embed_query(text)
        key = hashlib.sha1(f"query|{self.query_prefix}|{text}".encode()).hexdigest()
        if key not in self._vectors:
            started = time.perf_counter()
            vector = (await self._provider.embed([self.query_prefix + text]))[0]
            elapsed = time.perf_counter() - started
            self.seconds += elapsed
            self.query_seconds += elapsed
            self._vectors[key] = list(vector)
        return list(self._vectors[key])


def question_passes(model: Model, cache: RecipeCache, mode: str) -> list[tuple[str, object]]:
    """How each model's questions are asked: nomic only with its prefix, the others plain and with the instruction."""
    def activate(prefix: str | None):
        def run() -> None:
            cache.query_prefix = prefix or ""
        return run

    if model.document_prefix:
        return [(model.key, activate(model.query_prefix))]
    if mode == "engine":
        # The engine's option does the prefixing, so there is one pass: the instructed recipe where the model has one.
        return [(f"{model.key}+q" if model.query_prefix else "plain", activate(None))]
    passes = [("plain", activate(None))]
    if model.query_prefix:
        passes.append((f"{model.key}+q", activate(model.query_prefix)))
    return passes


def embedding_env(model: Model, mode: str) -> dict:
    values: dict = {"model_name": model.name, "dimension": model.dimension, "threads": 1}
    if mode == "engine":
        if model.query_prefix:
            values["query_prefix"] = model.query_prefix
        if model.document_prefix:
            values["document_prefix"] = model.document_prefix
    return values


def ranks_for(items: list[dict], lists: dict[str, list[str]]) -> dict[str, int | None]:
    return {item["id"]: first_rank(lists[item["id"]], item["answers"]) for item in items}


def summarize(items: list[dict], ranks: dict[str, int | None]) -> dict:
    """Pooled (all 204), blind (151), each set and each type: n, top 1, top 3, top 5, MRR."""
    def over(group: list[dict]) -> dict:
        return metrics([ranks[item["id"]] for item in group])

    summary = {"all": over(items), "blind": over([item for item in items if item["set"] in BLIND]),
               "by_set": {name: over([item for item in items if item["set"] == name]) for name in ("original", *BLIND)},
               "by_type": {kind: over([item for item in items if item["type"] == kind]) for kind in TYPES},
               "by_type_blind": {kind: over([item for item in items if item["type"] == kind and item["set"] in BLIND]) for kind in TYPES}}
    summary["cities"] = {item_id: ranks[item_id] for item_id in CITIES if item_id in ranks}
    return summary


async def run_model(out: Path, key: str, *, kinds: dict[str, tuple[int, ...]] = RUNS, mode: str = "cache", every: int = 1) -> dict:
    model = MODELS[key]
    started = time.perf_counter()
    golds, core = load_core()
    items = build_items(golds)[::every]
    out.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("PRME_BENCH_EMBEDDING")
    os.environ["PRME_BENCH_EMBEDDING"] = json.dumps(embedding_env(model, mode))
    report: dict = {"kind": "prme-embedding-models-scale", "model": key, "name": model.name, "dimension": model.dimension,
                    "mode": mode, "threads": 1, "commit": commit(), "questions": len(items), "core_memories": len(core.memories),
                    "kinds": {}}
    try:
        cache = RecipeCache(out / "vectors.pkl", model.document_prefix if mode == "cache" else None, delegate_query=mode == "engine")
        for kind, sizes in kinds.items():
            pads = pad_memories(kind, core, max(sizes, default=len(core.memories)) - len(core.memories))
            text = await baseline_lists(core, pads, items, sizes, cache, passes=question_passes(model, cache, mode))
            (out / f"lists-{kind}.json").write_text(json.dumps({label: stage.get("passes") or {"": {"lists": stage["lists"]}}
                                                                  for label, stage in text["stages"].items()}) + "\n")
            stages = {}
            for label, stage in text["stages"].items():
                passes = stage.get("passes") or {"": {"lists": stage["lists"], "retrieve_ms": stage["retrieve_ms"]}}
                stages[label] = {"memories": stage["memories"], "store_seconds": stage["store_seconds"],
                                 "embedding_seconds_so_far": stage["embedding_seconds_so_far"],
                                 "passes": {name: {"retrieve_ms": body["retrieve_ms"], "ranks": ranks_for(items, body["lists"]),
                                                   "summary": summarize(items, ranks_for(items, body["lists"]))}
                                            for name, body in passes.items()}}
            report["kinds"][kind] = {"padding": pads.info, "embedding": text["embedding"],
                                     "query_embedding_seconds": round(cache.query_seconds, 1), "stages": stages}
            cache.save()
            (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    finally:
        if previous is None:
            os.environ.pop("PRME_BENCH_EMBEDDING", None)
        else:
            os.environ["PRME_BENCH_EMBEDDING"] = previous
    report["seconds"] = round(time.perf_counter() - started, 1)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models scale")
    parser.add_argument("--model", choices=list(MODELS), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("cache", "engine"), default="cache")
    parser.add_argument("--core-only", action="store_true", help="The 189-memory core only, no padding (a quick check)")
    parser.add_argument("--kinds", nargs="+", choices=list(RUNS), default=list(RUNS))
    parser.add_argument("--every", type=int, default=1, help="Keep each n-th question, for a quick check")
    args = parser.parse_args(argv)
    kinds = {"conversation": ()} if args.core_only else {kind: RUNS[kind] for kind in args.kinds}
    report = asyncio.run(run_model(args.out, args.model, kinds=kinds, mode=args.mode, every=args.every))
    print(f"embedding scale {args.model}: {report['seconds']}s, report in {args.out}")
