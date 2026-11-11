"""The tables of results/REPORT.md, from the built arms' reports (task 9c and 9d). No model call, no retrieval.

    uv run python research/oracle-extraction-2026-11/report_tables.py > research/oracle-extraction-2026-11/results/tables.md

Reads ``report.json``, ``build.json`` and ``oracle.json`` of each arm under the probe's build root, the B1 serve
paths written by ``serve_paths.py --json`` into ``results/``, and checks that each B1 rebuild (``ox-B1r``) repeats
its B1 exactly: the same probe results and the same packed contexts, byte for byte.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oracle import HERE, ROOT  # noqa: E402

NAMES = ("defects-v1", "chat-v1", "member-v1")
ARMS = ("M0", "M1", "A0", "A1", "B0", "B1")
# Only chat-v1 has O2 facts, so elsewhere the A arms are the B builds.
BUILT = {"chat-v1": ARMS}
BARS = {"defects-v1": 7, "chat-v1": 17, "member-v1": 11}


def root() -> Path:
    from benchmarks.diagnostics import chat_probe

    return chat_probe.default_root()


def arm_folder(name: str, arm: str) -> Path:
    built = BUILT.get(name, ("M0", "M1", "B0", "B1"))
    label = arm if arm in built else {"A0": "B0", "A1": "B1"}[arm]
    return root() / name / f"ox-{label}"


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
    first, second = root() / name / "ox-B1", root() / name / "ox-B1r"
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
    lines += ["", "## Loader: accepted and refused, by the part of the mention rule each needed", "",
              "| Probe | Levels | Written | Accepted | By the turn's text alone | Needing owner | Carried | Closure | "
              "Refused (by reason) |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for name in NAMES:
        for arm in ("A0", "B0") if name == "chat-v1" else ("B0",):
            tally = load(name, arm)["oracle"]["tally"]
            needing = tally["accepted_needing"]
            refused = "; ".join(f"{reason}: {count}" for reason, count in tally["refused_by_reason"].items()) or "none"
            levels = "O1" if arm == "A0" else "O1+O2"
            lines.append(f"| {name} | {levels} | {tally['assertions']} | {tally['accepted']} | {needing['turn only']} | "
                         f"{needing['owner']} | {needing['carried']} | {needing['closure']} | {refused} |")
    lines += ["", "## B1 rebuilt from scratch", ""]
    for name in NAMES:
        differences = same_build(name)
        lines.append(f"- {name}: " + ("identical (probe rows, graph measures, every packed context)"
                                     if not differences else "; ".join(differences)))
    lines += ["", "## How B1 serves each question (receipts)", "",
              "| Probe | Question | Answer rank | Path | Relations | Ledger |", "|---|---|---:|---|---|---|"]
    for name in NAMES:
        rows = json.loads((HERE / "results" / f"serve-{name}-B1.json").read_text())
        for row in rows:
            ledger = f"{row['ledger']['source']}: {', '.join(row['ledger']['states'])}" if row["ledger"] else ""
            lines.append(f"| {name} | {row['question']} | {row['answer_rank'] or 'none'} | {row['path']} | "
                         f"{', '.join(row['relations'])} | {ledger} |")
    lines += ["", "## Questions B1 misses in the top 3: its top 5", ""]
    for name in NAMES:
        rows = json.loads((HERE / "results" / f"serve-{name}-B1.json").read_text())
        for row in rows:
            if row["answer_rank"] is not None and row["answer_rank"] <= 3:
                continue
            lines += [f"**{name}: {row['question']}** answer rank {row['answer_rank'] or 'none'}, {row['path']}", ""]
            lines += [f"{item['rank']}. ({item['type']}) {item['content'][:160]}" for item in row["top5"]]
            lines.append("")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
