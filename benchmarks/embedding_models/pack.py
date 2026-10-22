"""The store pack under each recipe, and the three spellings of the cities question (``GATE.md``, measure 1).

The pack is the fast cycle's store stage: 75 memories of one made-up user stored with ``store()``, 17 questions with
answer terms. Each recipe runs it through the engine's own option (``PRME_BENCH_EMBEDDING``, one ONNX thread), so the
prefixes are the provider's. The cities question is asked in its three spellings (the fixture's and the two variants of
the graph oracle's gold file) by adding the variants to a copy of the fixture, so each is scored exactly as the
fixture's own questions are: the rank of the first record that holds all four city names.

The cosine table does what the issue's example did: the question's cosine to the memory that answers it, and to the
memories that say "Japan trip", for the recipe's own vectors.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile

from benchmarks.embedding_models.models import recipes
from benchmarks.fast_cycle import store as fast_store
from benchmarks.graph_oracle.gold import load_gold

CITIES = "japan-cities"


def fixture_with_variants(directory: Path) -> tuple[Path, list[str]]:
    """A copy of the store fixture that also asks the cities question in its other spellings."""
    fixture = json.loads(fast_store.DEFAULT_FIXTURE.read_text())
    cities = next(question for question in fixture["key"]["questions"] if question["id"] == CITIES)
    gold = next(question for question in load_gold("store-v1").questions if question.id == CITIES)
    ids = [CITIES]
    for number, text in enumerate(gold.variants, start=1):
        fixture["key"]["questions"].append({**cities, "id": f"{CITIES}#v{number}", "question": text})
        ids.append(f"{CITIES}#v{number}")
    path = directory / "store-v1-with-variants.json"
    path.write_text(json.dumps(fixture))
    return path, ids


async def cosines(recipe, fixture: dict, texts: list[str]) -> dict:
    """The cities questions' cosines to the answer memory and to the Japan trip memories, in the recipe's vector space."""
    import numpy as np
    from prme.config import EmbeddingConfig
    from prme.storage.embedding import create_embedding_provider, encode_query, encode_texts

    provider = create_embedding_provider(EmbeddingConfig(_env_file=None, **recipe.embedding()))
    memories = [memory["content"] for memory in fixture["memories"]]
    matrix = np.asarray(await encode_texts(provider, memories))
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    answer = next(number for number, text in enumerate(memories) if all(term in text for term in ("Tokyo", "Kyoto", "Kanazawa", "Osaka")))
    trips = [number for number, text in enumerate(memories) if "Japan trip" in text and number != answer]
    out = {}
    for text in texts:
        vector = np.asarray(await encode_query(provider, text))
        vector /= np.linalg.norm(vector)
        scores = matrix @ vector
        order = list(np.argsort(-scores))
        out[text] = {"answer": round(float(scores[answer]), 3), "answer_rank_by_cosine": order.index(answer) + 1,
                     "japan_trip_memories": [round(float(scores[number]), 3) for number in trips],
                     "best_other": round(float(max(score for number, score in enumerate(scores) if number != answer)), 3)}
    return out


async def run_recipe(label: str, recipe, work: Path, fixture_path: Path, question_ids: list[str]) -> dict:
    previous = os.environ.get("PRME_BENCH_EMBEDDING")
    os.environ["PRME_BENCH_EMBEDDING"] = json.dumps(recipe.embedding())
    try:
        report = await fast_store.run(work / label, fixture_path=fixture_path)
        fixture = json.loads(fixture_path.read_text())
        by_id = {question["id"]: question for question in report["questions"]}
        texts = [by_id[question_id]["question"] for question_id in question_ids]
        similarity = await cosines(recipe, fixture, texts)
    finally:
        if previous is None:
            os.environ.pop("PRME_BENCH_EMBEDDING", None)
        else:
            os.environ["PRME_BENCH_EMBEDDING"] = previous
    standard = [question for question in report["questions"] if "#" not in question["id"]]
    ranks = [question["answer_rank"] for question in standard]
    cities = [by_id[question_id] for question_id in question_ids]
    return {
        "recipe": label, "store_seconds": round(report["store_seconds"], 1), "retrieve_seconds": round(report["retrieve_seconds"], 1),
        "store": {"questions": len(standard), "top1": sum(rank == 1 for rank in ranks),
                  "top3": sum(rank is not None and rank <= 3 for rank in ranks),
                  "mrr": round(sum(1 / rank for rank in ranks if rank) / len(ranks), 3),
                  "ranks": {question["id"]: question["answer_rank"] for question in standard},
                  "measures": {key: value for key, value in report["measures"].items() if not key.startswith("answer_rank:")}},
        "cities": {"ranks": [question["answer_rank"] for question in cities],
                   "mrr": round(sum(1 / question["answer_rank"] for question in cities if question["answer_rank"]) / len(cities), 3),
                   "top3": [[result if isinstance(result, str) else result.get("content", str(result)) for result in question["top_results"][:3]]
                            for question in cities],
                   "cosines": [similarity[text] for text in texts]},
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models pack")
    parser.add_argument("--recipes", nargs="+", choices=list(recipes()), default=list(recipes()))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    found = recipes()
    results = json.loads(args.out.read_text()) if args.out.exists() else {}
    with tempfile.TemporaryDirectory(prefix="embedding-pack-") as scratch:
        work = Path(scratch)
        fixture_path, question_ids = fixture_with_variants(work)
        for label in args.recipes:
            results[label] = asyncio.run(run_recipe(label, found[label], work, fixture_path, question_ids))
            print(label, results[label]["store"]["top3"], results[label]["store"]["mrr"], results[label]["cities"]["ranks"])
            args.out.write_text(json.dumps(results, indent=1) + "\n")
