"""Join completed reader verdicts to frozen source retention without relabeling.

This is exploratory development error triage, not a new accuracy estimate or
proof that labelled source coverage makes an answer unambiguous.
"""

import argparse
from collections import Counter
import json
from pathlib import Path

from benchmarks.diagnostics import packing_reader as runtime
from benchmarks.diagnostics import reader_judge as judge


def load_reader(root, plan_path, report_path):
    plan = json.loads(plan_path.read_bytes())
    inputs = {}
    for name in ["reader_plan", "predictions", "raw_reader_state", "declaration"]:
        ref = plan["files"][name]
        raw = (root / ref["path"]).read_bytes()
        if runtime.digest(raw) != ref["sha256"]:
            raise ValueError("Registered reader input hash mismatch")
        inputs[name] = json.loads(raw)
    reader_plan = inputs["reader_plan"]
    prepared_raw = (root / reader_plan["prepared_file"]).read_bytes()
    cases = judge.study_cases(
        inputs["predictions"], (root / reader_plan["references_file"]).read_bytes(),
        prepared_raw, reader_plan, inputs["declaration"], inputs["raw_reader_state"],
    )
    report = json.loads(report_path.read_bytes())
    if not report.get("complete") or report.get("process_exit_code") != 0 or not report.get("passed"):
        raise ValueError("Completed normally exited judgments required")
    if report["identity"] != {"declaration": inputs["declaration"], "cases_sha256": runtime.digest(runtime.canonical(cases))}:
        raise ValueError("Judgments belong to different reader cases")
    judge.study_metrics(cases, report)  # Reject incomplete/duplicate/nonboolean labels.
    rows = {row["id"]: row for row in report["judgments"]}
    wanted = set()
    for case in cases:
        key = runtime.digest(runtime.canonical(judge.payload(case, inputs["declaration"])))
        saved = report["generations"][key]
        actual = judge.verdict(saved["response"], inputs["declaration"]["model"])
        if (runtime.digest(runtime.canonical(saved["response"])) != saved["response_sha256"]
                or rows[case["id"]]["prompt_sha256"] != key
                or any(rows[case["id"]][k] != actual[k] for k in actual)):
            raise ValueError("Judgment differs from raw response")
        wanted.add(key)
    if set(report["generations"]) != wanted:
        raise ValueError("Unexpected judge generations")
    return reader_plan, json.loads(prepared_raw), rows


def summarize(packing, readers, arm="score", budget=4096):
    if not packing.get("complete") or not packing.get("baseline_reproduction_passed"):
        raise ValueError("Complete packing comparison required")
    selected = packing["dataset"]["selected_question_ids"]
    details = {row["question_id"]: row for row in packing["details"]}
    if not selected or len(set(selected)) != len(selected) or len(details) != len(packing["details"]) or set(details) != set(selected):
        raise ValueError("Incomplete or duplicated packing cohort")
    if len(readers) != 2:
        raise ValueError("This map requires two completed readers")
    for plan, prepared, verdicts in readers:
        if plan["source_report_file_sha256"] != packing["input_sha256"] or plan["selected_question_ids"] != selected or plan["budget"] != budget:
            raise ValueError("Reader source, budget or cohort differs from packing study")
        contexts = {row["question_id"]: row for row in prepared["rows"]}
        if set(contexts) != set(selected) or len(contexts) != len(prepared["rows"]):
            raise ValueError("Invalid prepared context cohort")
        for qid in selected:
            ctx = contexts[qid]["contexts"][arm]
            measurement = details[qid]["variants"][arm][str(budget)]
            if runtime.digest(ctx["context"].encode()) != ctx["sha256"] or ctx["sha256"] != measurement["context_sha256"]:
                raise ValueError("Reader context differs from measured context")
    result, counts = [], Counter()
    for qid in selected:
        row = details[qid]
        measure = row["variants"][arm][str(budget)]
        evidence = set(row["evidence_source_ids"])
        retained = evidence & set(measure["content_source_ids"])
        if "_abs" in qid:
            coverage = "abstention"
        elif not evidence:
            coverage = "unlabelled"
        elif retained == evidence:
            coverage = "all_labelled_evidence"
        elif retained:
            coverage = "partial_labelled_evidence"
        else:
            coverage = "no_labelled_evidence"
        verdicts = [reader[2][qid + ":" + arm]["correct"] for reader in readers]
        if any(type(value) is not bool for value in verdicts):
            raise ValueError("Boolean verdict required")
        outcome = "both_accepted" if all(verdicts) else "both_rejected" if not any(verdicts) else "readers_differ"
        counts[coverage, outcome] += 1
        result.append({"question_id": qid, "category": "abstention" if "_abs" in qid else row["category"],
                       "coverage": coverage, "outcome": outcome, "reader_verdicts": verdicts,
                       "labelled_sources": len(evidence), "retained_labelled_sources": len(retained),
                       "missing_labelled_source_ids": sorted(evidence - retained)})
    return {"complete": True, "kind": "exploratory-reader-evidence-map", "arm": arm, "budget": budget,
            "questions": len(selected), "readers": [reader[0]["reader"] for reader in readers],
            "strata": [{"coverage": c, "outcome": o, "questions": n} for (c, o), n in sorted(counts.items())],
            "details": result,
            "limits": "Post-hoc development triage with a shared local judge and overlapping histories. "
            "Labels are not exhaustive source truth; all-labelled coverage does not prove context sufficiency. "
            "No new predictions, relabeling, independent accuracy estimate or causal attribution."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--packing", type=Path, required=True)
    parser.add_argument("--reader", nargs=2, action="append", required=True, metavar=("PLAN", "REPORT"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [args.packing] + [Path(p) for pair in args.reader for p in pair]
    readers = [load_reader(args.root, Path(p), Path(r)) for p, r in args.reader]
    result = summarize(json.loads(args.packing.read_bytes()), readers)
    result["input_files"] = [{"path": str(p), "sha256": runtime.digest(p.read_bytes())} for p in paths]
    result["diagnostic_sha256"] = runtime.digest(Path(__file__).read_bytes())
    runtime.write(args.output, result)


if __name__ == "__main__":
    main()
