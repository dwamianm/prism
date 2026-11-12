"""M4 follow-up checks (REPORT.md, "M4"): what is left once the two known sources of noise are set aside.

    PYTHONPATH=. uv run python research/write-rate-2026-11/m4_checks.py --point 400 \
        --out data/write-rate-2026-11/m4-checks-400.json

``m4_compare.py`` found every event, node, edge, vector and lexical document the same, the operations log not the
same, and no whole ``retrieve()`` response the same, even for the current path against itself. These checks name
why, on the same kept packs (current-a, current-b and prototype under ``data/write-rate-2026-11/m4``):

1. Operations log, with the plan checksums dropped (``checksum`` and ``plan_checksum``) and each plan's
   ``references`` list sorted. A plan's references are the entities the plan reads; their order is the order the
   lookups returned them, which is not fixed, and the plan checksum is a digest over the plan, so it differs with it.
2. ``retrieve()`` results, as (record without its id, composite score rounded to 9 places), for the first 10 and the
   first 50 results. Two builds of the same observations score the same record about 1e-14 apart (floating point
   order), and records with equal scores are ordered by node id, which is random.

These are checks only. They do not change ``m4_compare.py``'s normalization, which stays as recorded.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import m4_compare  # noqa: E402

ROOT = "data/write-rate-2026-11/m4"
SIDES = ("current-a", "current-b", "prototype")


def operations(pack: str, name: str) -> collections.Counter:
    import duckdb

    target = os.path.join(m4_compare.SCRATCH, f"ops-{name}.duckdb")
    os.makedirs(m4_compare.SCRATCH, exist_ok=True)
    shutil.copy(os.path.join(pack, "memory.duckdb"), target)
    conn = duckdb.connect(target, read_only=True)
    try:
        rows = conn.execute("SELECT op_type, payload, actor_id, namespace_id FROM operations").fetchall()
    finally:
        conn.close()

    def settled(value):
        if isinstance(value, dict):
            value = {key: settled(item) for key, item in value.items() if key not in ("checksum", "plan_checksum")}
            if isinstance(value.get("references"), list):
                value["references"] = sorted(value["references"], key=lambda item: json.dumps(item, sort_keys=True))
            return value
        if isinstance(value, list):
            return [settled(item) for item in value]
        return value

    counted = collections.Counter()
    for op_type, payload, actor, namespace in rows:
        value = settled(m4_compare.unfolded(m4_compare.plain(payload)))
        counted[m4_compare.mask(json.dumps([op_type, value, actor, namespace], sort_keys=True), {})] += 1
    return counted


def top(response: dict, count: int) -> list:
    return [(m4_compare._record(result["node"]), round(result["composite_score"], 9))
            for result in response["results"][:count]]


async def main(args) -> dict:
    common.blocked_environment()
    provider = await common.embedding_provider()
    packs = {side: os.path.join(ROOT, side, str(args.point), "pack") for side in SIDES}
    ops = {side: operations(pack, f"{side}-{args.point}") for side, pack in packs.items()}
    answers = {}
    for side, pack in packs.items():
        settings = common.PROTOTYPE if side == "prototype" else {}
        read = await m4_compare.read_side(f"checks-{side}", pack, args.point, provider, settings)
        answers[side] = m4_compare.normalized(read)["answers"]
    report = {"point": args.point, "pairs": {}}
    for other in ("current-b", "prototype"):
        left, right = ops["current-a"], ops[other]
        pair = {
            "operations_rows": [sum(left.values()), sum(right.values())],
            "operations_same_with_checksums_dropped_and_references_sorted": left == right,
            "operations_rows_only_a": sum((left - right).values()),
            "operations_rows_only_b": sum((right - left).values()),
        }
        for count in (10, 50):
            pair[f"top{count}_same_scores_rounded_9"] = sum(
                top(x["response"], count) == top(y["response"], count)
                for x, y in zip(answers["current-a"], answers[other]))
        pair["expected_rank_same"] = sum(x["expected_rank"] == y["expected_rank"]
                                         for x, y in zip(answers["current-a"], answers[other]))
        pair["questions"] = len(answers["current-a"])
        report["pairs"][f"current-a vs {other}"] = pair
        print(args.point, other, json.dumps(pair), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--point", type=int, required=True)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
