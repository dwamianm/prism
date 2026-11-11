"""Where each answer string first appears in a built arm's ranking (round 2 report, B1 against B1s).

For each question named, the probe question is asked again as the probe asks it (the build's settings, the
conversation's reference time), and the first non-entity record holding each answer string is printed with its
rank and text. It explains a miss: one string far down, or every string present but in different records.

    uv run python research/oracle-extraction-2026-11/first_ranks.py chat-v1 fx-B1 fx-B1s --ids dog start-date
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


async def first_ranks(name: str, label: str, ids: set[str]) -> list[dict]:
    from benchmarks.diagnostics import chat_probe
    from benchmarks.diagnostics.product_packing import gate_config
    from prme import MemoryEngine

    conversation = chat_probe.load_conversation(ROOT / "benchmarks" / "conversations" / f"{name}.json")
    folder = chat_probe.default_root() / name / label
    build = json.loads((folder / "build.json").read_text())
    reference_time = datetime.fromisoformat(conversation["reference_time"])
    rows = []
    async with MemoryEngine.open(gate_config(folder / "pack", build.get("overrides"))) as engine:
        for probe in conversation["key"]["probes"]:
            if ids and probe["id"] not in ids:
                continue
            response = await engine.retrieve(probe["question"], user_id=conversation["owner"],
                                             reference_time=reference_time)
            ranked = [(rank, candidate.node.node_type.value, candidate.node.content)
                      for rank, candidate in enumerate(response.results, start=1)
                      if candidate.node.node_type.value != "entity"]
            strings = {}
            for term in probe.get("all_of") or probe["any_of"]:
                hit = next((row for row in ranked if term.casefold() in row[2].casefold()), None)
                strings[term] = {"rank": hit[0], "type": hit[1], "content": hit[2]} if hit else None
            rows.append({"id": probe["id"], "question": probe["question"], "strings": strings})
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("name", choices=("defects-v1", "chat-v1", "member-v1"))
    parser.add_argument("labels", nargs="+")
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args(argv)
    for label in args.labels:
        for row in asyncio.run(first_ranks(args.name, label, set(args.ids))):
            print(f"{args.name} {label} {row['id']}: {row['question']}")
            for term, hit in row["strings"].items():
                print(f"  {term!r}: " + (f"{hit['rank']} ({hit['type']}) {hit['content'][:120]}" if hit else "not ranked"))


if __name__ == "__main__":
    main()
