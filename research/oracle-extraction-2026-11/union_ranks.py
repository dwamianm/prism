"""An extra measure beside the probe's own (round 2): the answer held by the top k records together.

The probe ranks an answer at the first record whose text holds every answer string of an ``all_of`` question
(``chat_probe.answer_rank``). A claim rendered from its fields says one fact, so "Which languages does Alex write
at work?" can have Go at rank 1 and TypeScript at rank 2 and still rank its answer where a record names both. This
script asks each question again as the probe does and also reports the union rank: the smallest k for which the
non-entity records ranked 1 to k hold every answer string together (for an ``any_of`` question it equals the probe
rank). It is an exploratory check, not the guide's bar; the bars stay the probe's own ranks.

    uv run python research/oracle-extraction-2026-11/union_ranks.py chat-v1 fx-M1 fx-B1 ox-B1
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


def union_rank(probe: dict, ranked: list[dict]) -> int | None:
    terms = [term.casefold() for term in probe.get("all_of") or probe["any_of"]]
    every, seen = bool(probe.get("all_of")), ""
    for rank, result in enumerate(ranked, start=1):
        if result["type"] == "entity":
            continue
        seen += "\n" + result["content"].casefold()
        if (all if every else any)(term in seen for term in terms):
            return rank
    return None


async def ranks(name: str, label: str) -> list[dict]:
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
            response = await engine.retrieve(probe["question"], user_id=conversation["owner"],
                                             reference_time=reference_time)
            ranked = [{"content": candidate.node.content, "type": candidate.node.node_type.value}
                      for candidate in response.results]
            rows.append({"question": probe["question"], "probe_rank": chat_probe.answer_rank(probe, ranked),
                         "union_rank": union_rank(probe, ranked)})
    return rows


def within(rows: list[dict], key: str, limit: int) -> str:
    return f"{sum(row[key] is not None and row[key] <= limit for row in rows)}/{len(rows)}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("name", choices=("defects-v1", "chat-v1", "member-v1"))
    parser.add_argument("labels", nargs="+")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    found = {label: asyncio.run(ranks(args.name, label)) for label in args.labels}
    if args.json:
        args.json.write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n")
    for label, rows in found.items():
        print(f"{args.name} {label}: probe top 3 {within(rows, 'probe_rank', 3)}, top 5 {within(rows, 'probe_rank', 5)}; "
              f"union top 3 {within(rows, 'union_rank', 3)}, top 5 {within(rows, 'union_rank', 5)}")


if __name__ == "__main__":
    main()
