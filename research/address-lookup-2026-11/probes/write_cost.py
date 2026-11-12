"""P7 bar (c) part 1: what the key costs per fact on the write path.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/write_cost.py \
        --pack /tmp/p6/build/current-4050/pack --facts 200 \
        --out research/address-lookup-2026-11/results/write-cost.json

The key is written by ``keys.install_write_path``, which wraps the graph's only insert site and its only metadata
update site. Two numbers matter:

1. The added time per fact, measured as the difference between the wrapped and unwrapped insert on the same nodes,
   in the same process, alternating so machine noise hits both sides.
2. The added time per fact as a share of one ``store()`` call, which is what a caller actually waits for.

The claim nodes are written with ``ingest()``-shaped metadata: the fields the key reads are the same ones a real
extraction writes, so the JSON parse cost here is the real one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))

from probes.baseline import blocked_environment, build_engine  # noqa: E402

import keys  # noqa: E402

SCRATCH = os.environ.get("P7_SCRATCH", "/tmp/p7")
USER = "robot-1"


def claim(index: int):
    from prme.models.nodes import MemoryNode
    from prme.types import LifecycleState, NodeType, Scope

    now = datetime.now(timezone.utc)
    return MemoryNode(
        id=uuid4(), node_type=NodeType.FACT, user_id=USER,
        session_id=f"write-cost-{index % 7}", scope=Scope.PERSONAL,
        content=f"The metal bottle number {index} is on the living room windowsill.",
        metadata={
            "subject_link_status": "resolved", "object_link_status": "resolved",
            "object_entity_type": "location", "extraction_kind": "fact",
            "subject_entity_type": "product", "subject": f"metal bottle {index}",
            "predicate": "located_in", "object": "living room windowsill",
            "polarity": "positive",
            "evidence_quote": "The metal bottle is on the living room windowsill.",
            "grounding_method": "stated_sentence_v1", "temporal_intent": None,
            "replaces_object": None, "relation": "located_in", "status": "current",
        },
        confidence=0.7, salience=0.5, lifecycle_state=LifecycleState.TENTATIVE,
        valid_from=now, created_at=now, updated_at=now,
    )


def quantiles(values: list[float]) -> dict:
    ordered = sorted(values)
    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
        return round(ordered[index], 3)
    return {"count": len(ordered), "p50": at(0.50), "p95": at(0.95), "mean": round(statistics.fmean(ordered), 3)}


async def main(args) -> dict:
    blocked_environment()
    engine = await build_engine(os.path.join(SCRATCH, "write-cost"), args.pack)
    graph = engine._graph_store
    keys.add_schema(graph._conn)

    plain = []
    keyed = []
    # Alternate the two sides on adjacent claims so a machine that slows down during the run slows both.
    for index in range(args.facts):
        node = claim(index)
        began = time.perf_counter()
        graph._create_node_sync(node)
        plain.append(1000 * (time.perf_counter() - began))

        other = claim(args.facts + index)
        began = time.perf_counter()
        graph._create_node_sync(other)
        keys.write_key(graph, other)
        keyed.append(1000 * (time.perf_counter() - began))

    # The same through the installed write path, so the wrapped function is what is measured and not a copy of it.
    keys.install_write_path(graph)
    wrapped = []
    for index in range(args.facts):
        node = claim(2 * args.facts + index)
        began = time.perf_counter()
        graph._create_node_sync(node)
        wrapped.append(1000 * (time.perf_counter() - began))

    # The recommended shape: the key travels in the insert that already writes the row.
    inlined = []
    for index in range(args.facts):
        node = claim(3 * args.facts + index)
        began = time.perf_counter()
        keys.insert_with_key(graph._conn, node)
        inlined.append(1000 * (time.perf_counter() - began))

    plain_stats = quantiles(plain)
    keyed_stats = quantiles(keyed)
    wrapped_stats = quantiles(wrapped)

    # One store() call, for the share. store() writes an event, an embedding and a node, so this is the whole cost a
    # caller waits for and the key's part is what matters next to it.
    from prme.types import NodeType
    store_times = []
    for index in range(args.stores):
        began = time.perf_counter()
        await engine.store(
            f"The brass lantern number {index} is in the garage.",
            user_id=USER, node_type=NodeType.FACT,
            metadata={"subject": f"brass lantern {index}", "predicate": "located_in",
                      "object": "garage", "polarity": "positive"},
        )
        store_times.append(1000 * (time.perf_counter() - began))

    await engine.close()
    return {
        "pack": args.pack, "facts": args.facts, "stores": args.stores,
        "insert_without_key_ms": plain_stats,
        "insert_with_key_inline_ms": keyed_stats,
        "insert_through_installed_path_ms": wrapped_stats,
        "insert_with_key_in_statement_ms": quantiles(inlined),
        "added_per_fact_ms": {
            "inline_p50": round(keyed_stats["p50"] - plain_stats["p50"], 4),
            "inline_mean": round(keyed_stats["mean"] - plain_stats["mean"], 4),
            "installed_p50": round(wrapped_stats["p50"] - plain_stats["p50"], 4),
            "installed_mean": round(wrapped_stats["mean"] - plain_stats["mean"], 4),
            "in_statement_p50": round(quantiles(inlined)["p50"] - plain_stats["p50"], 4),
            "in_statement_mean": round(quantiles(inlined)["mean"] - plain_stats["mean"], 4),
        },
        "store_call_ms": quantiles(store_times),
        "key_share_of_store_p50_percent": round(
            100 * (wrapped_stats["p50"] - plain_stats["p50"]) / quantiles(store_times)["p50"], 3),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--facts", type=int, default=200)
    parser.add_argument("--stores", type=int, default=20)
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    text = json.dumps(result, indent=1)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            handle.write(text)
    print(text, flush=True)
