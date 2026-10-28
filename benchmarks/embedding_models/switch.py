"""What the engine does when the embedding model changes under an existing store (``GATE.md``, the switching question).

Five memories are stored under one recipe, then the same store is opened under another and asked a question, written
to, and where the engine has one, rebuilt. Every outcome is recorded as the engine reports it: an exception, the
``embedding_mismatch`` flag and the failed channels in the retrieval metadata, and whether the answer still ranks. The
probe changes no engine behavior; it is the evidence for what a user needs to switch models on a store they have.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile

from pydantic import SecretStr

from benchmarks.diagnostics.extracted_packs import _drain_sources
from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.embedding_models.models import recipes
from benchmarks.embedding_models.operations import fresh_database
from prme import MemoryEngine
from prme.types import NodeType, Scope

MEMORIES = ["Alex's dog is named Juniper, a rescue greyhound.", "Alex is a backend engineer at Northwind Logistics.",
            "Jordan is allergic to shellfish.", "The production cutover is on 2026-11-14.", "Alex prefers trains to flights."]
QUESTION = "What is Alex's dog called?"


def say(error: BaseException) -> str:
    return f"{type(error).__name__}: {str(error)[:160]}"


async def engine_for(recipe_label: str, pack: Path, database_url: str | None):
    previous = os.environ.get("PRME_BENCH_EMBEDDING")
    os.environ["PRME_BENCH_EMBEDDING"] = json.dumps(recipes()[recipe_label].embedding())
    try:
        config = gate_config(pack)
    finally:
        if previous is None:
            os.environ.pop("PRME_BENCH_EMBEDDING", None)
        else:
            os.environ["PRME_BENCH_EMBEDDING"] = previous
    if database_url:
        config = config.model_copy(update={"database_url": SecretStr(database_url)})
    return MemoryEngine.open(config)


async def ask(engine, owner: str) -> dict:
    try:
        response = await engine.retrieve(QUESTION, user_id=owner)
    except BaseException as error:  # the probe records whatever the engine does
        return {"error": say(error)}
    ranked = [candidate.node.content for candidate in response.results]
    rank = next((place for place, text in enumerate(ranked, start=1) if "Juniper" in text), None)
    return {"answer_rank": rank, "results": len(ranked), "embedding_mismatch": response.metadata.embedding_mismatch,
            "backend_failures": dict(response.metadata.backend_failures)}


async def write(engine, owner: str) -> str:
    try:
        await engine.store_with_receipt("Alex's partner Jordan works as a nurse practitioner.", user_id=owner, role="user",
                                        node_type=NodeType.FACT, scope=Scope.PERSONAL, extract=False)
        await _drain_sources(engine, owner)
        return "stored"
    except BaseException as error:
        return say(error)


async def scenario(backend: str, first: str, second: str, base: Path, database_url: str | None) -> dict:
    owner = "switch"
    pack = base / f"{backend}-{first}-{second}"
    (pack / "lexical_index").mkdir(parents=True)
    url = await fresh_database(database_url, "prism_we13_switch") if database_url else None
    async with await engine_for(first, pack, url) as engine:
        for text in MEMORIES:
            await engine.store_with_receipt(text, user_id=owner, role="user", node_type=NodeType.FACT, scope=Scope.PERSONAL, extract=False)
        await _drain_sources(engine, owner)
        before = await ask(engine, owner)
    out: dict = {"backend": backend, "stored_under": first, "opened_under": second, "before": before}
    try:
        async with await engine_for(second, pack, url) as engine:
            out["open"] = "opened"
            out["retrieve"] = await ask(engine, owner)
            out["store"] = await write(engine, owner)
            try:
                out["rebuild"] = await engine.rebuild_indexes()
            except BaseException as error:
                out["rebuild"] = say(error)
            else:
                out["retrieve_after_rebuild"] = await ask(engine, owner)
                out["store_after_rebuild"] = await write(engine, owner)
    except BaseException as error:
        out["open"] = say(error)
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models switch")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = [("duckdb", "bge-small", "minilm"), ("duckdb", "bge-small", "bge-base"), ("duckdb", "bge-small", "bge-small+q"),
             ("postgres", "bge-small", "minilm"), ("postgres", "bge-small", "bge-base"), ("postgres", "bge-small", "bge-small+q")]
    results = []
    with tempfile.TemporaryDirectory(prefix="embedding-switch-") as scratch:
        for backend, first, second in cases:
            results.append(asyncio.run(scenario(backend, first, second, Path(scratch), args.database_url if backend == "postgres" else None)))
            print(json.dumps(results[-1])[:300])
    args.out.write_text(json.dumps(results, indent=1) + "\n")
