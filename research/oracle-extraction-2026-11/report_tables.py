"""The tables of results/REPORT.md, from the built arms' reports (task 9c and 9d). No model call, no retrieval.

    uv run python research/oracle-extraction-2026-11/report_tables.py > research/oracle-extraction-2026-11/results/tables.md
    uv run python research/oracle-extraction-2026-11/report_tables.py fx results/round2 > .../results/round2/tables.md
    uv run python research/oracle-extraction-2026-11/report_tables.py round3 > .../results/round3/tables.md

Reads ``report.json``, ``build.json`` and ``oracle.json`` of each arm under the probe's build root, the B1 serve
paths written by ``serve_paths.py --json`` into ``results/``, and checks that each B1 rebuild (``ox-B1r``) repeats
its B1 exactly: the same probe results and the same packed contexts, byte for byte.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import HERE  # noqa: E402

NAMES = ("defects-v1", "chat-v1", "member-v1")
ARMS = ("M0", "M1", "A0", "A1", "B0", "B1")
# Only chat-v1 has O2 facts, so elsewhere the A arms are the B builds.
BUILT = {"chat-v1": ARMS}
BARS = {"defects-v1": 7, "chat-v1": 17, "member-v1": 11}


def root() -> Path:
    from benchmarks.diagnostics import chat_probe

    return chat_probe.default_root()


PREFIX = sys.argv[1] if len(sys.argv) > 1 else "ox"
RESULTS = HERE / (sys.argv[2] if len(sys.argv) > 2 else "results")


def arm_folder(name: str, arm: str) -> Path:
    built = BUILT.get(name, ("M0", "M1", "B0", "B1"))
    label = arm if arm in built else {"A0": "B0", "A1": "B1"}[arm]
    return root() / name / f"{PREFIX}-{label}"


def load(name: str, arm: str) -> dict:
    folder = arm_folder(name, arm)
    out = {"report": json.loads((folder / "report.json").read_text()),
           "build": json.loads((folder / "build.json").read_text()), "folder": folder}
    if (folder / "oracle.json").exists():
        out["oracle"] = json.loads((folder / "oracle.json").read_text())
    return out


def ratio(summary: dict, key: str) -> str:
    return summary[f"probes with the answer ranked in the {key}"]


def same_build(name: str) -> list[str]:
    """Differences between ox-B1 and ox-B1r: probe rows, graph measures and packed contexts."""
    first, second = root() / name / f"{PREFIX}-B1", root() / name / f"{PREFIX}-B1r"
    differences = []
    a, b = (json.loads((folder / "report.json").read_text()) for folder in (first, second))
    if a["probes"] != b["probes"]:
        differences.append("probe rows differ")
    if a["graph"] != b["graph"]:
        differences.append("graph measures differ")
    files = sorted(path.name for path in (first / "contexts").iterdir())
    if files != sorted(path.name for path in (second / "contexts").iterdir()):
        differences.append("context files differ")
    differences += [f"context {file} differs" for file in files
                    if (first / "contexts" / file).read_bytes() != (second / "contexts" / file).read_bytes()]
    return differences


def main() -> None:
    lines = ["## Top 3 and top 5 per arm", "",
             "| Probe | M0 | M1 | A0 | A1 | B0 | B1 | Bar (B1 top 3) | Result |", "|---|---|---|---|---|---|---|---|---|"]
    for name in NAMES:
        cells = []
        for arm in ARMS:
            summary = load(name, arm)["report"]["summary"]
            shared = "" if arm in BUILT.get(name, ("M0", "M1", "B0", "B1")) else " (= B)"
            cells.append(f"{ratio(summary, 'top 3')} / {ratio(summary, 'top 5')}{shared}")
        b1 = int(ratio(load(name, "B1")["report"]["summary"], "top 3").split("/")[0])
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {BARS[name]} | {'pass' if b1 >= BARS[name] else 'fail'} |")
    lines += ["", "## What each build stored", "",
              "| Probe | Arm | Active claims | User claims on the named owner | Entities | Facts loaded / written | "
              "Model calls | Empty stub extractions |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for name in NAMES:
        for arm in ARMS:
            if arm not in BUILT.get(name, ("M0", "M1", "B0", "B1")):
                continue
            data = load(name, arm)
            summary, totals = data["report"]["summary"], data["build"]["totals"]
            oracle = data.get("oracle")
            facts = (f"{oracle['tally']['accepted']} / {oracle['tally']['assertions']}" if oracle else "model")
            lines.append(f"| {name} | {arm} | {summary['active claims']} | "
                         f"{summary['user claims attached to the named owner']} | {summary['entities']} | {facts} | "
                         f"{totals.get('calls', 0)} | {oracle['empty_extractions'] if oracle else '-'} |")
    kinds = list(load(NAMES[0], "B0")["oracle"]["tally"]["accepted_needing"])
    lines += ["", "## Loader: accepted and refused, by the part of the mention rule each needed", "",
              "| Probe | Levels | Written | Accepted | " + " | ".join(kinds) + " | Refused (by reason) |",
              "|---|---|---:|---:|" + "---:|" * len(kinds) + "---|"]
    for name in NAMES:
        for arm in ("A0", "B0") if name == "chat-v1" else ("B0",):
            tally = load(name, arm)["oracle"]["tally"]
            needing = tally["accepted_needing"]
            refused = "; ".join(f"{reason}: {count}" for reason, count in tally["refused_by_reason"].items()) or "none"
            levels = "O1" if arm == "A0" else "O1+O2"
            lines.append(f"| {name} | {levels} | {tally['assertions']} | {tally['accepted']} | "
                         + " | ".join(str(needing[kind]) for kind in kinds) + f" | {refused} |")
    lines += ["", "## B1 rebuilt from scratch", ""]
    for name in NAMES:
        differences = same_build(name)
        lines.append(f"- {name}: " + ("identical (probe rows, graph measures, every packed context)"
                                     if not differences else "; ".join(differences)))
    lines += ["", "## How B1 serves each question (receipts)", "",
              "| Probe | Question | Answer rank | Path | Relations | Ledger |", "|---|---|---:|---|---|---|"]
    for name in NAMES:
        rows = json.loads((RESULTS / f"serve-{name}-B1.json").read_text())
        for row in rows:
            ledger = f"{row['ledger']['source']}: {', '.join(row['ledger']['states'])}" if row["ledger"] else ""
            lines.append(f"| {name} | {row['question']} | {row['answer_rank'] or 'none'} | {row['path']} | "
                         f"{', '.join(row['relations'])} | {ledger} |")
    lines += ["", "## Questions B1 misses in the top 3: its top 5", ""]
    for name in NAMES:
        rows = json.loads((RESULTS / f"serve-{name}-B1.json").read_text())
        for row in rows:
            if row["answer_rank"] is not None and row["answer_rank"] <= 3:
                continue
            lines += [f"**{name}: {row['question']}** answer rank {row['answer_rank'] or 'none'}, {row['path']}", ""]
            lines += [f"{item['rank']}. ({item['type']}) {item['content'][:160]}" for item in row["top5"]]
            lines.append("")
    lines += b1_against_b1s()
    print("\n".join(lines))


def _served(row: dict) -> str:
    relations = ", ".join(relation.split(" (")[0] + (" (" + relation.split(" (")[1].split("/")[0].rstrip(")") + ")"
                                                     if " (" in relation else "") for relation in row["relations"])
    return f"{row['answer_rank'] or 'none'} ({row['path']}{': ' + relations if relations else ''})"


def b1_against_b1s() -> list[str]:
    """Round 2 only: each question's rank and path in B1 (rendered claim text) and B1s (turn sentence text).

    Reads ``serve-<probe>-B1.json``, ``serve-<probe>-B1s.json`` and ``union-<probe>.json`` (with ``fx-M1``,
    ``ox-B1``, ``fx-B1`` and ``fx-B1s``); prints nothing when the B1s files are absent.
    """
    if not all((RESULTS / f"serve-{name}-B1s.json").exists() for name in NAMES):
        return []
    lines = ["", "## B1 against B1s per question (rank, then path; union rank from union_ranks.py)", ""]
    for name in NAMES:
        first, second = (json.loads((RESULTS / f"serve-{name}-{arm}.json").read_text()) for arm in ("B1", "B1s"))
        union = json.loads((RESULTS / f"union-{name}.json").read_text())

        def rank(label: str, index: int, key: str) -> str:
            value = union[label][index][key]
            return "none" if value is None else str(value)

        lines += [f"**{name}**", "", "| Question | M1 | Round 1 B1 | B1 | B1 union | B1s | B1s union |",
                  "|---|---:|---:|---|---:|---|---:|"]
        for index, (row, other) in enumerate(zip(first, second, strict=True)):
            if row["id"] != other["id"] or union["fx-B1"][index]["question"] != row["question"]:
                raise ValueError(f"{name}: questions out of step at {index}")
            lines.append(f"| {row['question']} | {rank('fx-M1', index, 'probe_rank')} | "
                         f"{rank('ox-B1', index, 'probe_rank')} | {_served(row)} | "
                         f"{rank('fx-B1', index, 'union_rank')} | {_served(other)} | "
                         f"{rank('fx-B1s', index, 'union_rank')} |")
        lines.append("")
    return lines


ROUND3 = (("fx-B1", "B1 (round 2)"), ("fx-B1s", "B1s"), ("tx-B1", "tx-B1"), ("fx-M1", "M1"))


def round3() -> None:
    """Round 3: B1 of round 2, B1s, tx-B1 and M1, per probe and then per question where any of them differ.

    Reads ``results/round3/union-<probe>.json`` (``union_ranks.py`` with fx-M1, ox-B1, fx-B1, fx-B1s and tx-B1)
    and ``results/round3/serve-<probe>-tx-B1.json``.
    """
    here = HERE / "results" / "round3"
    lines = ["## Top 3 / top 5 per probe", "",
             "| Probe | " + " | ".join(name for _, name in ROUND3) + " | Bar (tx-B1 top 3) | Result |",
             "|---|" + "---|" * len(ROUND3) + "---:|---|"]
    bars = {"defects-v1": "7", "chat-v1": "17 (and M1 + 2)", "member-v1": "11"}
    unions = {name: json.loads((here / f"union-{name}.json").read_text()) for name in NAMES}

    def count(rows: list[dict], key: str, k: int) -> int:
        return sum(1 for row in rows if row[key] is not None and row[key] <= k)

    for name in NAMES:
        union, total = unions[name], len(unions[name]["tx-B1"])
        cells = [f"{count(union[label], 'probe_rank', 3)}/{total} / {count(union[label], 'probe_rank', 5)}/{total}"
                 for label, _ in ROUND3]
        top3 = count(union["tx-B1"], "probe_rank", 3)
        bar = max(BARS[name], count(union["fx-M1"], "probe_rank", 3) + 2) if name == "chat-v1" else BARS[name]
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {bars[name]} | {'pass' if top3 >= bar else 'fail'} |")
    lines += ["", "## Per question, every row where the four differ (answer rank; tx-B1 path from the receipts)", ""]
    for name in NAMES:
        union = unions[name]
        served = json.loads((here / f"serve-{name}-tx-B1.json").read_text())
        rows = []
        for index, row in enumerate(served):
            ranks = [union[label][index]["probe_rank"] for label, _ in ROUND3]
            if union["tx-B1"][index]["question"] != row["question"]:
                raise ValueError(f"{name}: questions out of step at {index}")
            if len(set(ranks)) > 1:
                rows.append(f"| {row['question']} | " + " | ".join("none" if rank is None else str(rank)
                                                                  for rank in ranks) + f" | {_served(row)} |")
        lines += [f"**{name}**", ""]
        if rows:
            lines += ["| Question | " + " | ".join(n for _, n in ROUND3) + " | tx-B1 path |",
                      "|---|" + "---:|" * len(ROUND3) + "---|", *rows]
        else:
            lines.append("No row differs.")
        lines.append("")
    print("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "round3":
        round3()
    else:
        main()
