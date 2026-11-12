"""P8 bar 3: the current graph candidate stage against the SQL-cut one, interleaved, on one pack.

    PYTHONPATH=. .venv/bin/python research/graph-read-cost-2026-11/probes/interleave.py \
        --pack /tmp/p8/m3-pack --stored 20500 --rounds 3 \
        --out research/graph-read-cost-2026-11/results/interleave-m3.json

Both paths run in one process, on one pack copy, over the same 20 questions. Each question is read twice in a row,
once through each path, and the order inside the pair flips with every pair, so a change in the machine's load
hits both sides evenly. Three rounds, so 60 pairs and 120 reads.

Every pair also records whether the two reads gave the same answer: the ranked ids with their scores, the packed
ids, the context digest and the receipt digest. A faster path that returns a different context is not a cheaper
shape, it is a different one, and this comparison says so per pair rather than in a summary.

The pack is a scratch copy, because every read stores a RETRIEVAL_REQUEST receipt in the pack's operations table.
Both paths read the same copy, so both see the same store at every pair.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

USER = "robot-1"
REFERENCE_TIME = "2026-11-05T00:00:00+00:00"
REQUEST_ID = "<request-id>"

from stages import QUESTIONS  # noqa: E402  (the same 20 questions as bar 1)


def load() -> list[float]:
    return [round(value, 2) for value in os.getloadavg()]


def summary(values: list[float]) -> dict:
    ordered = sorted(values)
    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))], 3)
    return {"count": len(ordered), "p50": at(0.50), "p95": at(0.95), "mean": round(statistics.fmean(ordered), 3)}


def response_fingerprint(response) -> dict:
    """What a caller reads from one retrieval, without the receipt."""
    context = response.bundle.render()
    return {
        "ranked": [[str(candidate.node.id), round(candidate.composite_score, 9)] for candidate in response.results],
        "packed": [str(candidate.node.id) for group in response.bundle.sections.values() for candidate in group],
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "tokens_used": response.bundle.tokens_used,
    }


def receipt_sha256(receipt) -> str:
    """The receipt's digest, with the request id the only field replaced."""
    request_id = str(getattr(receipt, "request_id", "")) or ""
    text = receipt.model_dump_json()
    if request_id:
        text = text.replace(request_id, REQUEST_ID)
    return hashlib.sha256(text.encode()).hexdigest()


def same(left: dict, right: dict) -> dict:
    """Field by field: what matched, and the first difference when one did not."""
    result = {}
    for key in left:
        if left[key] == right[key]:
            result[key] = "same"
        elif key == "ranked":
            left_ids = [item[0] for item in left[key]]
            right_ids = [item[0] for item in right[key]]
            first = next((index for index, (a, b) in enumerate(zip(left_ids, right_ids)) if a != b),
                         min(len(left_ids), len(right_ids)))
            result[key] = {"ids_same": left_ids == right_ids, "scores_same": left[key] == right[key],
                           "lengths": [len(left_ids), len(right_ids)], "first_difference_at": first}
        elif key == "packed":
            result[key] = {"same_ids": left[key] == right[key], "lengths": [len(left[key]), len(right[key])]}
        else:
            result[key] = {"current": left[key], "cheap": right[key]}
    return result


async def main(args) -> dict:
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                       "PRME_EXTRACTION_API_KEY": ""})
    from datetime import datetime

    from benchmarks.diagnostics.product_packing import gate_config
    from prme import MemoryEngine
    import prme.retrieval.candidates as candidates

    from cheap_graph import cheap_graph_candidates

    current = candidates._generate_graph_candidates

    work = Path(args.work or "/tmp/p8/work/interleave")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copytree(Path(args.pack), work / "pack")
    pack = work / "pack"

    when = datetime.fromisoformat(REFERENCE_TIME)
    times: dict[str, list[float]] = {"current": [], "cheap": []}
    pairs: list[dict] = []
    load_before = load()

    async def read(engine, question: str) -> tuple[float, object]:
        started = time.perf_counter()
        response = await engine.retrieve(question, user_id=USER, reference_time=when)
        return 1000 * (time.perf_counter() - started), response

    async with MemoryEngine.open(gate_config(pack)) as engine:
        await engine.retrieve("warm-up question about the house", user_id=USER, reference_time=when)
        for round_index in range(args.rounds):
            for question_index, question in enumerate(QUESTIONS):
                flip = (round_index * len(QUESTIONS) + question_index) % 2 == 1
                order = ["cheap", "current"] if flip else ["current", "cheap"]
                measured: dict[str, tuple[float, object]] = {}
                for side in order:
                    candidates._generate_graph_candidates = current if side == "current" else cheap_graph_candidates
                    measured[side] = await read(engine, question)
                    times[side].append(measured[side][0])
                pair = {"round": round_index, "question": question, "order": order,
                        "current_ms": round(measured["current"][0], 1),
                        "cheap_ms": round(measured["cheap"][0], 1)}
                # Every pair is compared. The response alone gives the ranked ids, the packed ids and the
                # context digest; the receipt is fetched for round 0 only, so the extra read does not sit
                # inside a measured pair more than once per question.
                prints = {side: response_fingerprint(measured[side][1]) for side in ("current", "cheap")}
                pair["identity"] = same(prints["current"], prints["cheap"])
                if round_index == 0:
                    receipts = {}
                    for side in ("current", "cheap"):
                        response = measured[side][1]
                        receipts[side] = await engine.get_retrieval_receipt(
                            str(response.metadata.request_id), user_id=USER)
                    pair["receipt_identity"] = same(
                        {"receipt_sha256": receipt_sha256(receipts["current"])},
                        {"receipt_sha256": receipt_sha256(receipts["cheap"])})
                pairs.append(pair)
        candidates._generate_graph_candidates = current
    load_after = load()

    identical = [pair for pair in pairs if "identity" in pair]
    return {
        "pack": args.pack, "stored": args.stored, "questions": len(QUESTIONS), "rounds": args.rounds,
        "pairs": len(pairs), "reads_per_side": len(times["current"]), "backlog": 0,
        "reference_time": REFERENCE_TIME,
        "load_before": load_before, "load_after": load_after,
        "current_ms": {**summary(times["current"]), "values": [round(v, 1) for v in times["current"]]},
        "cheap_ms": {**summary(times["cheap"]), "values": [round(v, 1) for v in times["cheap"]]},
        "identity": {
            "pairs_checked": len(identical),
            "ranked_same": sum(1 for pair in identical if pair["identity"]["ranked"] == "same"),
            "packed_same": sum(1 for pair in identical if pair["identity"]["packed"] == "same"),
            "context_same": sum(1 for pair in identical if pair["identity"]["context_sha256"] == "same"),
            "tokens_same": sum(1 for pair in identical if pair["identity"]["tokens_used"] == "same"),
            "receipt_same": sum(1 for pair in identical
                                if pair.get("receipt_identity", {}).get("receipt_sha256") == "same"),
            "receipt_pairs": sum(1 for pair in identical if "receipt_identity" in pair),
            "differences": [{"question": pair["question"], **pair["identity"]}
                            for pair in identical
                            if any(value != "same" for value in pair["identity"].values())],
        },
        "timings": pairs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--work", default=None)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({key: result[key] for key in ("current_ms", "cheap_ms", "identity", "pairs",
                                                  "load_before", "load_after")}, indent=1))
