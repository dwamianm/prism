"""P4: fit read time against store size over the measured packs, and print the tables REPORT.md uses.

Usage: python3 research/chat-read-cost-2026-11/fit.py RESULT.json [RESULT.json ...]
Each RESULT.json is a chat_read_cost.py output. Fits are least squares on the per-pack medians and p95s.
"""
from __future__ import annotations

import json
import sys


def least_squares(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x
    total = sum((y - mean_y) ** 2 for y in ys)
    residual = sum((y - intercept - slope * x) ** 2 for x, y in zip(xs, ys))
    return intercept, slope, 1 - residual / total if total else 1.0


def main(paths: list[str]) -> None:
    packs = [pack for path in paths for pack in json.load(open(path))["packs"]]
    print("| Pack | Scale | Turns | Nodes | Entities | Edges | Entity degree mean / p90 / max | retrieve() median / p95 ms "
          "| Graph stage median / p95 ms | Graph share | Walk median / p95 ms | Seed reach median ms "
          "| Vector median ms | Seeds median / max | Rows walked median / max | Nodes reached median | Kept median |")
    print("|" + "---|" * 17)
    for p in packs:
        print(f"| {p['unit_id']} | {p.get('scale', 1)} | {p['turns']:,} | {p['nodes']:,} | {p['entities']:,} | {p['edges']:,} "
              f"| {p['entity_degree_mean']} / {p['entity_degree_p90']} / {p['entity_degree_max']} "
              f"| {p['retrieve_ms_median']:,.0f} / {p['retrieve_ms_p95']:,.0f} "
              f"| {p['graph_ms_median']:,.0f} / {p['graph_ms_p95']:,.0f} | {100 * p['graph_share_of_total']:.0f}% "
              f"| {p['walk_ms_median']:,.0f} / {p.get('walk_ms_p95', float('nan')):,.0f} "
              f"| {p['seed_reach_ms_median']:,.0f} | {p.get('vector_ms_median', float('nan')):,.0f} "
              f"| {p['seeds_median']:g} / {p['seeds_max']} | {p['rows_returned_median']:,.0f} / {p['rows_returned_max']:,} "
              f"| {p['nodes_reached_median']:,.0f} | {p['graph_kept_median']:g} |")
    print()
    for benchmark in sorted({p["benchmark"] for p in packs}):
        group = [p for p in packs if p["benchmark"] == benchmark]
        if len({p["nodes"] for p in group}) < 2:
            continue
        xs = [p["nodes"] for p in group]
        for key in ("retrieve_ms_median", "retrieve_ms_p95", "walk_ms_median", "graph_ms_median"):
            intercept, slope, r2 = least_squares(xs, [p[key] for p in group])
            crossing = (1000 - intercept) / slope if slope > 0 else float("inf")
            print(f"{benchmark} {key}: {intercept:,.0f} ms + {1000 * slope:,.1f} ms per 1,000 nodes "
                  f"(R2 {r2:.3f}, n {len(group)}); passes 1,000 ms at {crossing:,.0f} nodes")
        turns_per_node = sum(p["turns"] for p in group) / sum(p["nodes"] for p in group)
        print(f"{benchmark} turns per node {turns_per_node:.3f}")


if __name__ == "__main__":
    main(sys.argv[1:])
