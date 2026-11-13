"""Task 27 Part A: per-question effect of routing, by cause word, from two mid tier reports on one build.

Usage: python effects.py <causes.json> <off_label> <on_label> [<out.json>]
Measures per question: top_25 (evidence turns in the top 25), packed, all_packed, projected, and the
sum of evidence ranks (capped at 41 for missing) from the gate files, so a move inside the top 25 shows too.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

R = Path.home() / "Sites/prism/data/mid-tier-v1/reports"
F = ["question_id", "category", "group", "annotated", "packed", "top_25", "all_packed", "all_returned", "projected"]
causes = {row["id"]: row for row in json.loads(Path(sys.argv[1]).read_text())["rows"]
          if row["set"] in ("locomo", "longmemeval-12")}


def load(label):
    report = json.loads((R / label / "report.json").read_text())
    rows = {}
    for bench in ("locomo", "longmemeval"):
        for row in report["rows"][bench]:
            rows[row[0]] = dict(zip(F, row))
    for gate in sorted((R / label).glob("gate-*.json")):
        for row in json.loads(gate.read_text())["rows"]:
            ev = row.get("evidence") or {}
            ranks = ev.get("ranks") or {}
            rows[row["question_id"]]["ranks"] = {k: (v if v is not None else 41) for k, v in ranks.items()}
    return rows


off, on = load(sys.argv[2]), load(sys.argv[3])
result = []
for qid, a in off.items():
    b = on[qid]
    if not a["annotated"]:
        continue
    delta = {m: (b[m] or 0) - (a[m] or 0) for m in ("top_25", "packed", "projected")}
    delta["all_packed"] = int(bool(b["all_packed"])) - int(bool(a["all_packed"]))
    ra, rb = a.get("ranks", {}), b.get("ranks", {})
    delta["rank_sum"] = sum(rb.values()) - sum(ra.values())
    c = causes.get(qid, {})
    words = sorted({(x["word"], x["kind"]) for x in c.get("causes", [])})
    result.append({"id": qid, "category": a["category"], "question": c.get("question"), "routed": bool(c.get("route")),
                   "causes": words, "delta": delta, "ranks_off": ra, "ranks_on": rb})

changed = [r for r in result if any(r["delta"][m] for m in ("top_25", "packed", "all_packed"))
           or abs(r["delta"]["projected"]) > 1e-9]
print(f"{sys.argv[2]} -> {sys.argv[3]}: {len(result)} annotated, {len(changed)} change in top_25/packed/all_packed/projected")
for m in ("top_25", "packed", "all_packed"):
    wins = [r["id"] for r in result if r["delta"][m] > 0]
    losses = [r["id"] for r in result if r["delta"][m] < 0]
    print(f"  {m}: W {len(wins)} L {len(losses)}")
print(f"  unrouted questions that changed: {sum(1 for r in changed if not r['routed'])}")
for r in sorted(changed, key=lambda r: (r["delta"]["top_25"], r["id"])):
    d = r["delta"]
    print(f"  {r['id']:16s} t25 {d['top_25']:+d} pk {d['packed']:+d} all {d['all_packed']:+d} "
          f"proj {d['projected']:+.2f} rank {d['rank_sum']:+d} | {[w for w, _ in r['causes']]} | {r['question']}")

by_word = defaultdict(lambda: {"questions": 0, "top25_gain": 0, "top25_loss": 0, "packed_gain": 0, "packed_loss": 0,
                               "rank_up": 0, "rank_down": 0})
for r in result:
    for word, kind in r["causes"]:
        s = by_word[f"{word} ({kind})"]
        s["questions"] += 1
        s["top25_gain"] += r["delta"]["top_25"] > 0
        s["top25_loss"] += r["delta"]["top_25"] < 0
        s["packed_gain"] += r["delta"]["packed"] > 0
        s["packed_loss"] += r["delta"]["packed"] < 0
        s["rank_up"] += r["delta"]["rank_sum"] < 0
        s["rank_down"] += r["delta"]["rank_sum"] > 0
if len(sys.argv) > 4:
    Path(sys.argv[4]).write_text(json.dumps({"off": sys.argv[2], "on": sys.argv[3], "rows": result,
                                             "by_word": by_word}, indent=1))
