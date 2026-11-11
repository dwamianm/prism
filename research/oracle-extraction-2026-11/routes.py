"""The route ``route_question`` gives every question the probes and the graph oracle sets ask (task 12).

    uv run python research/oracle-extraction-2026-11/routes.py --json out.json

Reads the three probe conversations' questions, the graph oracle's gold questions, its first blind set
(``blind-v1``) with the paraphrases, and its second blind draw (``blind-v2``). For each question it records the
route, as receipts show it (``route_as_list``), or null when the question is not routed. Two files from two trees
compare with ``--compare before.json after.json``, which prints every question whose route changed.

No model, no pack: the router reads only the question's words.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import ROOT  # noqa: E402

from prme.retrieval.relations import ROUTING_VERSION, route_as_list, route_question  # noqa: E402

PROBES = ("defects-v1", "chat-v1", "member-v1")
FIXTURES = ROOT / "benchmarks" / "fixtures" / "graph-oracle"


def questions() -> list[dict]:
    rows = []
    for name in PROBES:
        conversation = json.loads((ROOT / "benchmarks" / "conversations" / f"{name}.json").read_text())
        rows += [{"set": f"probe:{name}", "id": probe["id"], "question": probe["question"]}
                 for probe in conversation["key"]["probes"]]
    for name in ("defects-v1", "chat-v1", "store-v1", "draw2-v1"):
        path = FIXTURES / f"{name}.json"
        if path.exists():
            rows += [{"set": f"gold:{name}", "id": item["id"], "question": item["question"]}
                     for item in json.loads(path.read_text()).get("questions", [])]
    blind = json.loads((FIXTURES / "blind-v1.json").read_text())["questions"]
    paraphrases = json.loads((FIXTURES / "blind-paraphrases-v1.json").read_text())["paraphrases"]
    for item in blind:
        rows.append({"set": "blind-v1", "id": item["id"], "type": item["type"], "question": item["question"]})
        rows += [{"set": "blind-v1:paraphrase", "id": f"{item['id']}#p{number}", "type": item["type"],
                  "question": text} for number, text in enumerate(paraphrases.get(item["id"], []), start=1)]
    rows += [{"set": "blind-v2", "id": item["id"], "type": item["type"], "question": item["question"]}
             for item in json.loads((FIXTURES / "blind-v2.json").read_text())["questions"]]
    return rows


def dump() -> dict:
    rows = questions()
    for row in rows:
        route = route_question(row["question"])
        row["route"] = route_as_list(route) if route is not None else None
    return {"routing_version": ROUTING_VERSION, "questions": rows}


def _short(route) -> str:
    if route is None:
        return "not routed"
    return " then ".join("+".join(step["relation"] + ("(" + "/".join(step["roles"]) + ")" if step.get("roles") else "")
                                  + (" negative" if step.get("polarity") == "negative" else "") for step in steps)
                         for steps in route)


def compare(before: dict, after: dict) -> list[dict]:
    old = {(row["set"], row["id"], row["question"]): row["route"] for row in before["questions"]}
    changed = []
    for row in after["questions"]:
        key = (row["set"], row["id"], row["question"])
        if key in old and old[key] != row["route"]:
            changed.append({**{name: row[name] for name in ("set", "id", "question") if name in row},
                            "type": row.get("type"), "before": _short(old[key]), "after": _short(row["route"])})
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        before, after = (json.loads(path.read_text()) for path in args.compare)
        changed = compare(before, after)
        routed = {name: sum(1 for row in report["questions"] if row["route"] is not None)
                  for name, report in (("before", before), ("after", after))}
        print(f"routing version {before['routing_version']} to {after['routing_version']}; "
              f"{len(after['questions'])} questions; routed before {routed['before']}, after {routed['after']}; "
              f"changed {len(changed)}")
        for row in changed:
            print(f"- {row['set']} {row['id']}: {row['question']!r}: {row['before']} to {row['after']}")
        return
    report = dump()
    text = json.dumps(report, indent=2) + "\n"
    if args.json:
        args.json.write_text(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
