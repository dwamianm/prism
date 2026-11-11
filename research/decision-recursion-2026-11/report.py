"""Tables from the run directories: Experiment R and W per arm and model, the bars, and the repeat check.

    uv run python research/decision-recursion-2026-11/report.py --results research/decision-recursion-2026-11/results

Numbers come from run1 of each arm; run2 is the repeat from a cold cache with the same seed. Writes REPORT.md and
summary.json into the results directory.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ARMS_R = ("R0", "R1", "R2", "R3", "R2b", "R4")
ARMS_W = ("W0", "W1", "W2", "W2b")
POSTHOC = {"R2b", "R4", "W2b"}  # designed by the lead after the first results


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def same_decisions(a: dict | None, b: dict | None) -> dict:
    """Two repeat checks. ``identical``: the whole decision sequence is the same. ``same_prompt_changed``: of the
    prompts both runs asked, how many got a different choice (the model's own repeatability; a different
    sequence can also come from PRME giving a different order, which changes which prompts are asked)."""
    if not a or not b:
        return {"compared": False}
    x, y = a["decisions"], b["decisions"]
    first, second = {k: c for _, k, c in x}, {k: c for _, k, c in y}
    common = set(first) & set(second)
    result = {"compared": True, "identical": x == y, "decisions_run1": len(x), "decisions_run2": len(y),
              "prompts_in_both": len(common), "same_prompt_changed": sum(first[k] != second[k] for k in common)}
    if "rows" in a and "rows" in b:
        result["prme_r0_top5_differs"] = [ra["id"] for ra, rb in zip(a["rows"], b["rows"])
                                          if [o["text"] for o in ra["arms"]["R0"]["order"]]
                                          != [o["text"] for o in rb["arms"]["R0"]["order"]]]
    return result


FIXED_OPTION_TAGS = ("R1.judge", "R2.judge", "R2b.judge", "R3.conflict", "W1", "W2.depth1", "W2.depth2",
                     "W2b.recheck.depth1", "W2b.recheck.depth2")


def label_counts(run_dir: Path, name: str = "read_decisions.jsonl") -> dict:
    """How often each option was chosen, per decision tag. Tags with fixed options count each option; tags whose
    options are records or words count "none" choices against the rest."""
    counts: Counter = Counter()
    path = run_dir / name
    if not path.exists():
        return {}
    for line in path.read_text().splitlines():
        d = json.loads(line)
        chosen = d["options"][d["choice"]]
        if d["tag"] in FIXED_OPTION_TAGS:
            counts[f"{d['tag']}={chosen}"] += 1
        elif chosen in ("none", "none of them"):
            counts[f"{d['tag']}={chosen}"] += 1
        else:
            counts[f"{d['tag']}=an item"] += 1
    return dict(sorted(counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    args = parser.parse_args()
    root = Path(args.results)
    arms = sorted({p.name.rsplit("-run", 1)[0] for p in root.glob("*-run[12]") if p.is_dir()},
                  key=lambda a: ("random" not in a, a))
    summary: dict = {}
    lines = ["# Results", "", "Numbers from run1 of each arm; run2 repeats it from a cold cache with the same seed.", ""]

    lines += ["## Experiment R (read)", "",
              "Columns: chat store() and ingest() answer at rank 1 and all answers in top 3 (of 13); two hops rank 1 "
              "(placement first) and both records in top 5 (of 8); churn at 10 minutes, newest place first (of 18); "
              "flood rare event at rank 1 and in top 5 (of 8); unknown said when it should be (cat through store() "
              "plus 6 unseen, of 7) and the cat through ingest(); false unknowns (of 60 answerable); old above new "
              "(chat store / ingest of 4 each, churn of 18); decisions per question (median / max); added ms per "
              "question (median / p95).", "",
              "| arm | model | store r1 | store top3 | ingest r1 | ingest top3 | hops r1 | hops both5 | churn r1 | "
              "flood r1 | flood top5 | unknown /7 | cat ingest | false unk /60 | old>new s/i/c | calls | ms | READ |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for model in arms:
        read = load(root / f"{model}-run1" / "read.json")
        if not read:
            continue
        summary.setdefault(model, {})["read"] = read["summary"]
        for arm in ARMS_R:
            s = read["summary"][arm]
            lines.append(
                f"| {arm}{' (post-hoc)' if arm in POSTHOC else ''} | {model} | {s['chat_store']['rank1']} | {s['chat_store']['top3']} | "
                f"{s['chat_ingest']['rank1']} | {s['chat_ingest']['top3']} | {s['hops']['rank1']} | "
                f"{s['hops']['both_in_top5']} | {s['churn10']['rank1']} | {s['flood']['rank1']} | {s['flood']['top5']} | "
                f"{s['unknown_right']['of_7']} | {'yes' if s['unknown_right']['cat_ingest'] else 'no'} | "
                f"{s['false_unknown_total']} | {s['chat_store']['stale_above']}/{s['chat_ingest']['stale_above']}/"
                f"{s['churn10']['stale_above']} | {s['calls_per_question']['median']:g} / {s['calls_per_question']['max']} | "
                f"{s['added_ms_per_question']['median']:g} / {s['added_ms_per_question']['p95']:g} | "
                f"{'PASS' if s['READ_PASS'] else 'fail'} |")
    lines += ["", "Bars per arm (unknown >= 6 of 7; false unknowns <= 1; store rank-1 gain over R0 >= 4; median added "
              "ms <= 300):", ""]
    for model in arms:
        read = summary.get(model, {}).get("read")
        if not read:
            continue
        for arm in ARMS_R[1:]:
            s = read[arm]
            bars = ", ".join(f"{k} {'yes' if v else 'NO'}" for k, v in s["bars"].items())
            lines.append(f"- {model} {arm}: rank-1 gain {s['rank1_chat_store_gain_over_R0']:+d}; {bars}")
    lines += ["", "Decision counts in run1 (judgments by answer; other tags by count):", ""]
    for model in arms:
        lines.append(f"- {model} read: {label_counts(root / f'{model}-run1')}")
        lines.append(f"- {model} write: {label_counts(root / f'{model}-run1', 'write_decisions.jsonl')}")

    lines += ["", "## Experiment W (write)", "",
              "| arm | model | retired correctly /10 | retired wrongly | wrong ids | calls per new fact (mean / max) | "
              "ms per new fact (mean / p95) | WRITE |", "|---|---|---|---|---|---|---|---|"]
    for model in arms:
        write = load(root / f"{model}-run1" / "write.json")
        if not write:
            continue
        summary.setdefault(model, {})["write"] = {arm: {k: v for k, v in write["arms"][arm].items()
                                                        if k != "retirements"} for arm in ARMS_W}
        for arm in ARMS_W:
            a = write["arms"][arm]
            wrong = ", ".join(w["id"] + ("*" if w["contestable"] else "") for w in a["retired_wrongly_list"]) or "-"
            lines.append(
                f"| {arm}{' (post-hoc)' if arm in POSTHOC else ''} | {model} | {a['retired_correctly']} | {a['retired_wrongly']} | {wrong} | "
                f"{a['calls_per_new_fact']['mean']:g} / {a['calls_per_new_fact']['max']} | "
                f"{a['ms_per_new_fact']['mean']:g} / {a['ms_per_new_fact']['p95']:g} | "
                f"{'PASS' if a['WRITE_PASS'] else 'fail'} |")
    causes = json.loads((Path(__file__).parent / "write_causes_posthoc.json").read_text())["causes"]
    lines += ["", "A * marks a fact the key calls contestable.", "",
              "Post-hoc, not a bar: of the key's retirements that happened, how many were triggered by the new "
              "fact that really ends them (write_causes_posthoc.json, written after the first 4b run).", ""]
    for model in arms:
        write = load(root / f"{model}-run1" / "write.json")
        if not write:
            continue
        for arm in ARMS_W[1:]:
            got = write["arms"][arm]["retirements"]
            right = sorted(i for i, v in got.items() if i in causes and v["by"] in causes[i])
            lines.append(f"- {model} {arm}: {len(right)} of {write['arms'][arm]['retired_correctly']} for the right "
                         f"reason ({', '.join(right) or '-'})")
            summary[model]["write"][arm]["right_reason_posthoc"] = right
    lines += ["", "## Repeat from a cold cache", "",
              "Same prompt, different choice: the model's own repeatability. Sequence identical: the whole run "
              "repeated. PRME top 5 differs: questions where PRME's own order changed between the two fresh pack "
              "builds, which changes the prompts asked after it.", "",
              "| model | same prompt, different choice (read / write) | read sequence identical | write sequence "
              "identical | PRME top 5 differs | read wall s (run1 / run2) |",
              "|---|---|---|---|---|---|"]
    for model in arms:
        r1, r2 = load(root / f"{model}-run1" / "read.json"), load(root / f"{model}-run2" / "read.json")
        w1, w2 = load(root / f"{model}-run1" / "write.json"), load(root / f"{model}-run2" / "write.json")
        rs, ws = same_decisions(r1, r2), same_decisions(w1, w2)
        summary.setdefault(model, {})["repeat"] = {"read": rs, "write": ws}

        if not (rs["compared"] and ws["compared"]):
            lines.append(f"| {model} | not run | | | | |")
            continue
        walls = " / ".join(str(r["wall_seconds"]) if r else "-" for r in (r1, r2))
        lines.append(
            f"| {model} | {rs['same_prompt_changed']} of {rs['prompts_in_both']} / {ws['same_prompt_changed']} of "
            f"{ws['prompts_in_both']} | {'yes' if rs['identical'] else 'no'} | {'yes' if ws['identical'] else 'no'} | "
            f"{', '.join(rs['prme_r0_top5_differs']) or 'none'} | {walls} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    (root / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
