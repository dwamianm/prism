"""P5 bar 3: compare packs built from the same observations through two write paths, judged on records.

    PYTHONPATH=. uv run python research/write-batch-2026-11/compare.py \
        --a ~/Sites/prism-write-rate/data/write-rate-2026-11/m4/current-a:current \
        --b /tmp/p5/build/batch:batch --points 400 4000 --out research/write-batch-2026-11/results/bar3.json

Each side is ``<dir>:<path>``: a directory with a closed pack copy at ``<dir>/<point>/pack`` (``--pack-a`` and
``--pack-b`` name one pack directly instead, for a pack kept elsewhere), and the write path that built it, whose
settings it is opened with. The batch path's settings change only writes; no write happens here.

This reuses P2's comparison unchanged (``research/write-rate-2026-11/m4_compare.py`` and ``m4_checks.py``): the same
normalization (every UUID replaced by a label, build clocks masked and set to one fixed time before the questions),
the same 25 questions, and the same levels. Records are every event, node, edge, vector row and lexical document,
and the operations log with plan checksums dropped and each plan's references sorted (P2's ``m4_checks.py``: the
references' order is the order lookups return them, which is not fixed). Results are compared as P2 did, at the
whole response, the first 10 results exact, the first 10 and 50 with scores rounded to 9 places, and the bundle.
Per prme-manager the judgment is on records, not on whole responses.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import sys

import batch as harness

sys.path.insert(0, os.path.join(harness.HERE, "..", "write-rate-2026-11"))
import m4_checks  # noqa: E402
import m4_compare  # noqa: E402

common = harness.common
m4_compare.SCRATCH = os.path.join(harness.SCRATCH, "compare")


def side_arg(text: str) -> tuple[str, str]:
    root, _, path = text.rpartition(":")
    if path not in harness.PATHS:
        raise argparse.ArgumentTypeError(f"unknown path {path!r}")
    return os.path.expanduser(root), path


async def main(args) -> dict:
    common.blocked_environment()
    provider = await common.embedding_provider()
    report = {"a": args.a, "b": args.b, "points": {}}
    for point in args.points:
        reads, ops = [], []
        for label, (root, path), pack in (("a", args.a, args.pack_a), ("b", args.b, args.pack_b)):
            pack = os.path.expanduser(pack) if pack else os.path.join(root, str(point), "pack")
            name = f"{label}-{path}"
            ops.append(m4_checks.operations(pack, f"{name}-{point}"))
            reads.append(await m4_compare.read_side(name, pack, point, provider, harness.PATHS[path]))
        sides = [m4_compare.normalized(read) for read in reads]
        result = m4_compare.compare_point(*sides)
        left, right = ops
        result["operations_settled"] = {
            "rows": [sum(left.values()), sum(right.values())], "same": left == right,
            "rows_only_a": sum((left - right).values()), "rows_only_b": sum((right - left).values())}
        result["records_same"] = (all(result[name]["same"] for name in ("events", "nodes", "edges", "vectors",
                                                                         "lexical"))
                                  and result["operations_settled"]["same"])
        for count in (10, 50):
            result[f"top{count}_same_scores_rounded_9"] = sum(
                m4_checks.top(x["response"], count) == m4_checks.top(y["response"], count)
                for x, y in zip(sides[0]["answers"], sides[1]["answers"]))
        result["expected_ranks"] = collections.Counter(
            str(answer["expected_rank"]) for answer in result["answers"])
        report["points"][str(point)] = result
        print(f"{point}: records same {result['records_same']}, "
              + ", ".join(f"{name} {result[name]['count']} same={result[name]['same']}"
                          for name in ("events", "nodes", "edges", "vectors", "lexical"))
              + f", operations settled {result['operations_settled']}, answers by level "
              f"{result['answers_same_by_level']}, top10/9 places {result['top10_same_scores_rounded_9']}, "
              f"top50/9 places {result['top50_same_scores_rounded_9']}, expected rank same "
              f"{result['expected_rank_same']}/25", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", type=side_arg, required=True)
    parser.add_argument("--b", type=side_arg, required=True)
    parser.add_argument("--pack-a")
    parser.add_argument("--pack-b")
    parser.add_argument("--points", type=int, nargs="+", required=True)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
