"""Task 21 bar (b): build the LoCoMo pair table from chat_pairs.sh outputs and P4's base run 2.

Usage: uv run python locomo_pairs.py /tmp/task21/chat <p4 base-run2.json> <out.json>
Prints a markdown table. Only the read and graph stage times are compared: P4's walk column times
_get_neighborhood_with_depth_sync, which the walk branch no longer calls.
"""
import json, statistics, sys
from pathlib import Path

chat, base, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
p4 = {p["unit_id"]: p for p in json.loads(base.read_text())["packs"] if p["benchmark"] == "locomo"}
rows = []
for unit in sorted(p4):
    sides = {}
    for side in ("main", "walk"):
        path = chat / f"{side}-{unit}.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text())
        pack = report["packs"][0]
        sides[side] = {k: pack[k] for k in ("retrieve_ms_median", "retrieve_ms_p95", "graph_ms_median",
                                             "graph_ms_p95", "graph_share_of_total")}
        sides[side]["reads"] = len(pack["reads"])
        sides[side]["questions"] = [r["question"] for r in pack["reads"]]
        sides[side]["load_1m_at_end"] = report["provenance"]["load_1m_at_end"]
        sides[side]["commit"] = report["provenance"]["commit"]
    if len(sides) < 2:
        continue
    same = sides["main"].pop("questions") == sides["walk"].pop("questions")
    ref = p4[unit]
    rows.append({"unit": unit, "same_questions": same, **sides,
                 "p4_base_run2": {k: ref[k] for k in ("retrieve_ms_median", "retrieve_ms_p95", "graph_ms_median")}})
out.write_text(json.dumps({"pairs": rows}, indent=1))
print("| Pack | `origin/main` median / p95 | Graph | This branch median / p95 | Graph | P4 base run 2 median |")
print("|---|---|---|---|---|---|")
f = lambda v: f"{v:,.0f}"
for r in rows:
    m, w = r["main"], r["walk"]
    print(f"| {r['unit']} | {f(m['retrieve_ms_median'])} / {f(m['retrieve_ms_p95'])} ms | {f(m['graph_ms_median'])} ms "
          f"| {f(w['retrieve_ms_median'])} / {f(w['retrieve_ms_p95'])} ms | {f(w['graph_ms_median'])} ms "
          f"| {f(r['p4_base_run2']['retrieve_ms_median'])} ms |")
if rows:
    mm = [r["main"]["retrieve_ms_median"] for r in rows]; wm = [r["walk"]["retrieve_ms_median"] for r in rows]
    print(f"\nmain medians {min(mm):.0f}-{max(mm):.0f}, walk {min(wm):.0f}-{max(wm):.0f}; "
          f"per-pack ratio median {statistics.median(w / m for w, m in zip(wm, mm)):.2f}; "
          f"same questions {all(r['same_questions'] for r in rows)}")
