"""How each probe question of a built arm is served, from its retrieval receipt (task 9d), and its top 5.

For each probe question this asks the pack again exactly as the probe's ``measure()`` does (the build's own
settings, the conversation's reference time), checks that the answer's rank equals the one in ``report.json``,
and reads the receipt's ``execution.features``:

- ``routed``: relation routing walked a route (``relation_routing``), with the relations of its steps; and
  whether the belief ledger placed the claims it reached (``belief_state`` with source ``route``);
- ``catalog``: no route, but the ledger found the question's address in the catalog (``belief_state`` with source
  ``catalog``);
- ``text ranking only``: neither.

    uv run python research/oracle-extraction-2026-11/serve_paths.py chat-v1 ox-B1 [--json out.json]

Retrieval makes no model call; providers stay blocked as for the builds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import ROOT  # noqa: E402


def _relations(route: list) -> list[str]:
    names = []
    for steps in route or []:
        for step in steps:
            roles = step.get("roles")
            names.append(step["relation"] + (f" ({'/'.join(roles)})" if roles else "")
                         + (" negative" if step.get("polarity") == "negative" else ""))
    return names


async def serve(name: str, label: str) -> list[dict]:
    from benchmarks.diagnostics import chat_probe
    from benchmarks.diagnostics.product_packing import gate_config
    from prme import MemoryEngine

    conversation = chat_probe.load_conversation(ROOT / "benchmarks" / "conversations" / f"{name}.json")
    folder = chat_probe.default_root() / name / label
    build = json.loads((folder / "build.json").read_text())
    measured = {probe["id"]: probe for probe in json.loads((folder / "report.json").read_text())["probes"]}
    owner = conversation["owner"]
    reference_time = datetime.fromisoformat(conversation["reference_time"])
    rows = []
    async with MemoryEngine.open(gate_config(folder / "pack", build.get("overrides"))) as engine:
        for probe in conversation["key"]["probes"]:
            response = await engine.retrieve(probe["question"], user_id=owner, reference_time=reference_time)
            ranked = [{"content": candidate.node.content, "type": candidate.node.node_type.value}
                      for candidate in response.results]
            rank = chat_probe.answer_rank(probe, ranked)
            receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=owner)
            features = receipt.execution.features if receipt is not None and receipt.execution else {}
            routing = features.get("relation_routing")
            belief = features.get("belief_state")
            if routing:
                path = "routed"
            elif belief and belief.get("source") == "catalog":
                path = "catalog"
            else:
                path = "text ranking only"
            rows.append({
                "id": probe["id"], "question": probe["question"], "answer_rank": rank,
                "report_rank": measured.get(probe["id"], {}).get("answer_rank"),
                "path": path, "receipt_found": receipt is not None,
                "relations": _relations(routing.get("route")) if routing else [],
                "route_candidates": len(routing.get("candidates", [])) if routing else 0,
                "ledger": ({"source": belief.get("source"),
                            "states": [claim.get("state") for claim in belief.get("claims", [])]}
                           if belief else None),
                "top5": [{"rank": index + 1, "type": item["type"], "content": item["content"]}
                         for index, item in enumerate(ranked[:5])],
            })
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("name", choices=("defects-v1", "chat-v1", "member-v1"))
    parser.add_argument("label")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    rows = asyncio.run(serve(args.name, args.label))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    for row in rows:
        same = "" if row["answer_rank"] == row["report_rank"] else f" (report says {row['report_rank']})"
        relations = f" via {', '.join(row['relations'])}" if row["relations"] else ""
        ledger = f"; ledger from {row['ledger']['source']}" if row["ledger"] else ""
        print(f"{row['answer_rank'] or 'none'}{same} | {row['path']}{relations}{ledger} | {row['question']}")


if __name__ == "__main__":
    main()
