"""Check a facts file the way the oracle build will load it (GUIDE.md), and print what the loader refuses.

Each assertion goes, with its ``level`` removed, to the engine's own check
(``prme.ingestion.assertions.prepare_assertions``), turn by turn in conversation order: a subject or object is
accepted when the turn names it, when it is the turn's speaker and the turn says I, me, my, mine, myself, we, us
or our (only in a probe built with the owner as speaker: ``speaker_named`` in the turns file, user turns only), or
when it names an entity, with the same name and type, that an earlier accepted assertion put in memory. A literal
value must be in the turn, unless it repeats the value an earlier accepted assertion gave the same subject and
attribute (a closure). ``ingest()`` refuses a whole turn when one assertion fails, so the build loads each turn
with the assertions that pass. ``time`` is an ISO date (2026-05-04) or an ISO datetime with its offset.

Two sets are checked: O1 alone, and O1 with O2 (the memory differs between them). Only refusals are listed, then
the totals. From the worktree root:

    uv run python research/oracle-extraction-2026-11/check_facts.py research/oracle-extraction-2026-11/facts/defects-v1.json

The turns file is found by the facts file's name (``turns/<name>.json``); ``--turns`` names another. Exit status
0 when nothing is refused and the file is well formed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import HERE, SETS, check, tally  # noqa: E402


def _line(assertion: dict) -> str:
    relation = assertion.get("relation")
    if relation == "has":
        relation = f"has {assertion.get('predicate')}"
    details = [value for value in (assertion.get("role"), assertion.get("status"), assertion.get("time"),
                                   "negative" if assertion.get("polarity") == "negative" else None) if value]
    return (f"{assertion.get('subject')} {relation} {assertion.get('object')}"
            + (f" ({', '.join(details)})" if details else ""))


def report(facts: dict, turns_file: dict, label: str) -> tuple[list[str], bool]:
    lines = [f"{label}: the engine's check, turn by turn (text, speaker, memory, closure)"]
    refused_any, listed = False, set()
    results = {name: check(facts, turns_file, levels) for name, levels in SETS.items()}
    lines += [f"problem: {problem}" for problem in results["O1+O2"]["problems"]]
    for name in ("O1+O2", "O1"):
        for key, rows in results[name]["turns"].items():
            for row in rows:
                if row["reason"] is None or (key, row["position"], row["reason"]) in listed:
                    continue
                listed.add((key, row["position"], row["reason"]))
                refused_any = True
                lines.append(f"{key} {results[name]['roles'][key]} {row['level']} [{row['position']}] "
                             f"{_line(row['assertion'])}: {row['reason']}"
                             + (" (O1 alone)" if name == "O1" else ""))
    for name in SETS:
        counts = tally([row for rows in results[name]["turns"].values() for row in rows])
        needing, refused = counts["accepted_needing"], counts["refused_by_reason"]
        lines.append(
            f"{name}: {counts['assertions']} assertions, {counts['accepted']} accepted ({needing['text only']} by the "
            f"turn's text alone, {needing['speaker']} needing the speaker, {needing['memory']} memory, "
            f"{needing['closure']} closure), {counts['refused']} refused"
            + (" (" + ", ".join(f"{kind} {count}" for kind, count in refused.items()) + ")" if refused else ""))
    return lines, refused_any or bool(results["O1+O2"]["problems"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("facts", type=Path)
    parser.add_argument("--turns", type=Path, default=None, help="default: turns/<name>.json beside this script")
    args = parser.parse_args(argv)
    name = args.facts.name.removesuffix(".json")
    turns_file = json.loads((args.turns or HERE / "turns" / f"{name}.json").read_text())
    lines, failed = report(json.loads(args.facts.read_text()), turns_file, args.facts.name)
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
