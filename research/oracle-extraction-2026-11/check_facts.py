"""Check a facts file the way the oracle build will load it (GUIDE.md), and print what the loader refuses.

Each assertion is checked alone, with its ``level`` removed: first the engine's form
(``prme.ingestion.assertions.Assertion``: relation vocabulary, types, status, role), then this experiment's
mention rule (``oracle.py``): a subject or object is in the turn's text (``turn``), is the owner named by the
turn's I, me, my, we, us or our, or an assistant turn's you or your (``owner``), or was named by an earlier turn
(``carried``); a literal value must be in the turn, or, for an O2 closure, in an earlier turn (``closure``).
``ingest()`` refuses a whole turn when one assertion fails, so the build loads each turn with the assertions that
pass; the accepted ones of each turn are then given to ``prepare_assertions`` together, under the rule, as the
build gives them, and any refusal there is listed as a problem.

Two sets are checked: O1 alone, and O1 with O2. Only turns with a refusal are listed, then the totals. From the
worktree root:

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

from oracle import HERE, SETS, check, plan, tally, verify_plan  # noqa: E402


def _line(assertion: dict) -> str:
    relation = assertion.get("relation")
    if relation == "has":
        relation = f"has {assertion.get('predicate')}"
    details = [value for value in (assertion.get("role"), assertion.get("status"),
                                   "negative" if assertion.get("polarity") == "negative" else None) if value]
    return (f"{assertion.get('subject')} {relation} {assertion.get('object')}"
            + (f" ({', '.join(details)})" if details else ""))


def report(result: dict, label: str, turns_file: dict) -> list[str]:
    lines = [f"{label}: the engine's form, then the experiment's mention rule (turn, owner, carried, closure)"]
    lines += [f"problem: {problem}" for problem in result["problems"]]
    for key, rows in result["turns"].items():
        refused = [row for row in rows if row["reason"] is not None]
        if refused:
            lines.append(f"{key} {result['roles'][key]}: {len(refused)} of {len(rows)} refused")
            lines += [f"  {row['level']} [{row['position']}] {_line(row['assertion'])}: {row['reason']}"
                      for row in refused]
    for name, levels in SETS.items():
        lines += [f"problem: {failure}" for failure in verify_plan(plan(result, levels), turns_file)]
        counts = tally(result, levels)
        refused = counts["refused_by_reason"]
        needing = counts["accepted_needing"]
        lines.append(
            f"{name}: {counts['assertions']} assertions on {counts['turns']} turns, {counts['accepted']} accepted "
            f"({needing['turn only']} by the turn's text alone, {needing['owner']} needing owner, "
            f"{needing['carried']} carried, {needing['closure']} closure), {counts['refused']} refused"
            + (" (" + ", ".join(f"{kind} {count}" for kind, count in refused.items()) + ")" if refused else ""))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("facts", type=Path)
    parser.add_argument("--turns", type=Path, default=None, help="default: turns/<name>.json beside this script")
    args = parser.parse_args(argv)
    name = args.facts.name.removesuffix(".json")
    turns_file = json.loads((args.turns or HERE / "turns" / f"{name}.json").read_text())
    result = check(json.loads(args.facts.read_text()), turns_file)
    lines = report(result, args.facts.name, turns_file)
    print("\n".join(lines))
    refused = any(row["reason"] for rows in result["turns"].values() for row in rows)
    return 1 if refused or any(line.startswith("problem:") for line in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
