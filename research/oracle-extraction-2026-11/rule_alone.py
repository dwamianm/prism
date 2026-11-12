"""Task 12: the router with one of its two new rules turned off, to say which rule moves which route or rank.

    uv run python research/oracle-extraction-2026-11/rule_alone.py routes   # route changes per rule, no retrieval
    uv run python research/oracle-extraction-2026-11/rule_alone.py blind <rule> <out>   # graph oracle blind run

<rule> is the rule kept on: "name" keeps only rule 1 (skip the words that ask for a name), "stated" keeps only rule 2
(route the normalizer's own words). The other rule is turned off by patching the module for this process only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from prme.retrieval import relations

HERE = Path(__file__).resolve().parent


@contextmanager
def only(rule: str):
    saved = relations._NAME_WORDS, relations._PREDICATE_WORDS
    if rule == "name":
        relations._PREDICATE_WORDS = {}  # route_question reads it; nothing else runs in these measurements
    elif rule == "stated":
        relations._NAME_WORDS = frozenset()
    elif rule != "both":
        raise ValueError(rule)
    try:
        yield
    finally:
        relations._NAME_WORDS, relations._PREDICATE_WORDS = saved


def routes() -> None:
    before = json.loads((HERE / "results" / "router-fix" / "routes-before.json").read_text())["questions"]
    out = {}
    for rule in ("name", "stated", "both"):
        with only(rule):
            out[rule] = [None if (route := relations.route_question(row["question"])) is None else relations.route_as_list(route) for row in before]
    lines = []
    for index, row in enumerate(before):
        moved = [rule for rule in ("name", "stated") if out[rule][index] != row["route"]]
        if out["both"][index] != row["route"]:
            lines.append(f"{'+'.join(moved) or 'together only'}\t{row['set']}\t{row['id']}\t{row['question']}")
    print("\n".join(lines))
    print(f"changed by the name rule alone: {sum(1 for line in lines if line.split(chr(9))[0] in ('name', 'name+stated'))}; "
          f"by the stated rule alone: {sum(1 for line in lines if 'stated' in line.split(chr(9))[0])}; total {len(lines)}")


def blind(rule: str, out: Path) -> None:
    from benchmarks.graph_oracle import blind_run

    with only(rule):
        asyncio.run(blind_run.run(out))


if __name__ == "__main__":
    if sys.argv[1] == "routes":
        routes()
    else:
        blind(sys.argv[2], Path(sys.argv[3]))
