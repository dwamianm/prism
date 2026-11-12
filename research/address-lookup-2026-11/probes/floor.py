"""P7 bar (b), part 2: where the indexed lookup's remaining time goes, and what its floor is.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/floor.py \
        --directory /tmp/p7/timing-20500/run --stored 20500 --calls 10 \
        --out research/address-lookup-2026-11/results/floor-20500.json

``timing.py`` shows the indexed lookup at about 41 ms p50 and 53 ms p95 on the 20,500-fact pack, against a 5 ms bar.
This probe names each part of one indexed lookup so the report can say what the floor is and which part sets it:

1. ``fence_first_ms``     ``engine._ordinary_graph``, the privacy range check the read runs before the page.
2. ``page_ms``            the indexed page: SQL, row hydration, owner view.
3. ``counts_ms``          the two scan counts (total claims, claims with no key).
4. ``classify_ms``        ``classify_page`` over the page.
5. ``edges_ms``           ``_contradiction_edges``, which resolves the facade again for its second fence.
6. ``state_ms``           the rest of ``build_state``.

The directory is opened in place. It is a pack that ``timing.py`` already migrated, so this probe adds no copy and no
disk. The floor it reports is the sum of parts 2 to 6 with parts 1, 3 and the fence inside part 5 taken out, which is
what an engine carrying an indexed address read, an indexed privacy marker and a maintained count would pay.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))

from probes.baseline import blocked_environment, pick_pairs  # noqa: E402

import keys  # noqa: E402
import lookup  # noqa: E402

USER = "robot-1"


def load() -> list[float]:
    return [round(value, 2) for value in os.getloadavg()]


def summary(values: list[float]) -> dict:
    ordered = sorted(values)
    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))], 3)
    return {"count": len(ordered), "p50": at(0.50), "p95": at(0.95), "mean": round(statistics.fmean(ordered), 3)}


async def main(args) -> dict:
    blocked_environment()
    from datetime import datetime, timezone

    from prme.models.temporal import AssertionStateQuery
    from prme.retrieval.aggregation import normalize_assertion_value
    from prme.retrieval.temporal import _contradiction_edges, build_state, classify_page
    from prme.storage.embedding import create_embedding_provider
    from prme.storage.engine import MemoryEngine
    from prme.types import Scope
    from tests.contract.harness import product_config

    # Drawn before the engine opens, because DuckDB will not open the pack read-only beside a live connection.
    pairs = pick_pairs(os.path.join(args.directory, "pack"), args.pairs)

    config = product_config(args.directory)
    engine = await MemoryEngine.create(config, embedding_provider=create_embedding_provider(config.embedding))
    graph = engine._graph_store
    keys.add_schema(graph._conn)
    reference = datetime(2026, 12, 1, tzinfo=timezone.utc)
    parts: dict[str, list[float]] = {name: [] for name in
                                     ("fence_first_ms", "page_ms", "counts_ms", "classify_ms", "edges_ms",
                                      "state_ms", "total_ms")}

    for subject, predicate in pairs:
        query = AssertionStateQuery(subject=subject, predicate=predicate, scope=Scope.PERSONAL,
                                    valid_at=reference)
        subject_key = normalize_assertion_value("subject", subject)
        predicate_key = normalize_assertion_value("predicate", predicate)
        node_types = tuple(query.node_types)
        for _ in range(args.calls):
            run_started = time.perf_counter()
            began = time.perf_counter()
            facade = await engine._ordinary_graph(USER, scopes=[query.scope])
            parts["fence_first_ms"].append(1000 * (time.perf_counter() - began))

            began = time.perf_counter()
            page_sql = lookup.address_page_sql(node_types)
            page = [facade._row_to_node(row) for row in facade._conn.execute(
                page_sql, lookup.address_page_params(USER, query.scope, node_types, subject_key,
                                                     predicate_key)).fetchall()]
            page = facade._owned_view_sync(page, USER)
            parts["page_ms"].append(1000 * (time.perf_counter() - began))

            began = time.perf_counter()
            total, unkeyed = facade._conn.execute(
                lookup.selector_count_sql(node_types),
                lookup.selector_count_params(USER, query.scope, node_types)).fetchone()
            parts["counts_ms"].append(1000 * (time.perf_counter() - began))

            from collections import Counter
            exclusions: Counter[str] = Counter()
            began = time.perf_counter()
            records, matched = classify_page(
                page, engine=engine, query=query, normalized_subject=subject_key,
                normalized_predicate=predicate_key, exclusions=exclusions)
            parts["classify_ms"].append(1000 * (time.perf_counter() - began))

            # The two counts come from the query above rather than the scan, which is the shape a real change would
            # carry once the numbers are maintained instead of counted.
            for name, count in (("missing_structured_assertion", unkeyed),
                                ("selector_mismatch", total - unkeyed - matched)):
                if count:
                    exclusions[name] = count
                else:
                    exclusions.pop(name, None)

            began = time.perf_counter()
            await _contradiction_edges(engine, records)
            parts["edges_ms"].append(1000 * (time.perf_counter() - began))

            began = time.perf_counter()
            await build_state(engine, query, user_id=USER, records=records,
                              normalized_subject=subject_key, normalized_predicate=predicate_key,
                              exclusions=exclusions, scanned_nodes=int(total), matched_records=matched)
            parts["state_ms"].append(1000 * (time.perf_counter() - began))

            parts["total_ms"].append(1000 * (time.perf_counter() - run_started))

    await engine.close()
    medians = {name: summary(values)["p50"] for name, values in parts.items()}
    # ``build_state`` resolves the facade again for ``_contradiction_edges``, so its time holds one more fence and
    # one edge fetch. Everything after the page that is neither a fence nor a count is what remains.
    report = {
        "directory": args.directory,
        "stored": args.stored,
        "calls": args.calls,
        "pairs": [f"{subject} / {predicate}" for subject, predicate in pairs],
        "load_before": load(),
        "parts": {name: summary(values) for name, values in parts.items()},
    }
    # The floor: the indexed page and the work after it, with no fence and no count query. That is what an engine
    # carrying an indexed address read plus an indexed privacy marker plus a maintained count would still pay.
    # Per call, not a sum of medians: the floor is what one lookup would pay with no fence and no count query, and a
    # bar is stated at p95, which needs the distribution and not only the middle.
    per_call_floor = [
        parts["page_ms"][index] + parts["classify_ms"][index]
        + (parts["state_ms"][index] - parts["edges_ms"][index])
        for index in range(len(parts["page_ms"]))
    ]
    report["floor_per_call"] = summary(per_call_floor)
    report["floor_ms"] = {
        "fence_each_p50": medians["fence_first_ms"],
        "counts_p50": medians["counts_ms"],
        "p50": round(summary(per_call_floor)["p50"], 3),
        "p95": round(summary(per_call_floor)["p95"], 3),
    }
    report["load_after"] = load()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    text = json.dumps(result, indent=1)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            handle.write(text)
    print(json.dumps({"parts": {name: value["p50"] for name, value in result["parts"].items()},
                      "floor_ms": result["floor_ms"], "load_before": result["load_before"],
                      "load_after": result["load_after"]}, indent=1), flush=True)
