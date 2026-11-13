"""Task 27 Part B: which questions ROUTING_VERSION 4 routes differently from version 3, per set.

Reads the version 3 routes recorded in causes-v3.json and routes each question again with this tree's
route_question. Usage: python route_changes.py <repo> <causes-v3.json> <out.json>
"""
import json, sys
from collections import Counter
from pathlib import Path

repo, causes, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(repo / "src"))
from prme.retrieval import relations as rel  # noqa: E402

rows = json.loads(causes.read_text())["rows"]
changes, counts = [], Counter()
for row in rows:
    route = rel.route_question(row["question"])
    new = rel.route_as_list(route) if route else None
    if new == row["route"]:
        continue
    kind = "unrouted" if new is None else "another route"
    counts[(row["set"], kind)] += 1
    changes.append({"set": row["set"], "id": row["id"], "question": row["question"], "v3": row["route"], "v4": new})
sets = list(dict.fromkeys(row["set"] for row in rows))
summary = {s: {"questions": sum(r["set"] == s for r in rows),
               "routed_v3": sum(r["set"] == s and r["route"] is not None for r in rows),
               "unrouted_by_v4": counts[(s, "unrouted")], "another_route": counts[(s, "another route")]} for s in sets}
out.write_text(json.dumps({"routing_version": rel.ROUTING_VERSION, "summary": summary, "changes": changes}, indent=1))
for s, v in summary.items():
    print(f"{s}: {v}")
for c in changes:
    if not c["set"].startswith(("locomo", "blind")):
        print(f"  {c['set']} {c['id']}: {c['question']} | {c['v3']} -> {c['v4']}")
