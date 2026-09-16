"""Fixed quarter-length/composition regression on the already examined 381 cases.

The original score-policy confirmation remains failed. This reused partition
cannot become an unseen holdout by registering another experiment on it.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import json
import inspect
import multiprocessing
from pathlib import Path

from benchmarks.diagnostics import packing_composition as composition
from benchmarks.diagnostics.compare_public_captures import cluster_statistics
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from benchmarks.diagnostics.product_packing import measure
from benchmarks.diagnostics.verify_packing_composition import account, statistics_for
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context

ARMS = ("density", "score", "quarter", "head1_quarter")
BUDGETS = (2048, 4096, 8192)
COMPARISONS = {"quarter_vs_density": ("density", "quarter"),
               "composition_vs_density": ("density", "head1_quarter"),
               "composition_vs_quarter": ("quarter", "head1_quarter"),
               "score_vs_density": ("density", "score")}
LABEL_FIELDS = ("evidence_recall", "all_evidence_retained")


def runtime():
    return {**composition.runtime(), "regression_module_sha256": digest(Path(__file__).read_bytes()),
            "accounting_module_sha256": digest(Path(inspect.getfile(account)).read_bytes())}


def grouping(dataset_path, selected):
    cases = json.loads(dataset_path.read_bytes())
    indexed = {c["question_id"]: c for c in cases}
    if len(indexed) != len(cases) or not set(selected) <= set(indexed):
        raise ValueError("Complete unambiguous raw dataset required")
    parent = {qid: qid for qid in selected}

    def root(qid):
        while parent[qid] != qid:
            parent[qid] = parent[parent[qid]]
            qid = parent[qid]
        return qid

    seen = {}
    for qid in selected:
        c = indexed[qid]
        # Labels, answers and questions do not enter history identity.
        history = [[session, date, [[m["role"], m["content"]] for m in messages]]
                   for session, date, messages in zip(c["haystack_session_ids"],
                                                      c["haystack_dates"],
                                                      c["haystack_sessions"], strict=True)]
        for key in (("base", qid.removesuffix("_abs")), ("history", digest(canonical(history)))):
            if key in seen:
                parent[root(qid)] = root(seen[key])
            else:
                seen[key] = qid
    return {qid: root(qid) for qid in selected}


def sources(args):
    completion = json.loads(args.source_completion.read_bytes())
    for name in ("source", "comparison", "source_plan"):
        entry = completion["files"]["plan" if name == "source_plan" else name]
        if digest(getattr(args, name).read_bytes()) != entry["sha256"]:
            raise ValueError("Original completed artifact changed")
    source = json.loads(args.source.read_bytes())
    comparison = json.loads(args.comparison.read_bytes())
    if (completion["source_native_exit_code"] != 0
            or completion["comparison_native_exit_code"] != 0
            or not source["complete"] or source["errors"] or source["process_exit_code"] != 0
            or not comparison["complete"] or not comparison["baseline_reproduction_passed"]
            or comparison["quality_gate_passed"] is not False
            or comparison["input_sha256"] != digest(args.source.read_bytes())
            or comparison["dataset"] != source["dataset"]
            or digest(args.dataset.read_bytes()) != source["dataset"]["sha256"]):
        raise ValueError("Complete original source and failed confirmation required")
    selected = source["dataset"]["selected_question_ids"]
    if (len(selected) != 381 or len(set(selected)) != 381
            or [r["question_id"] for r in source["details"]] != selected
            or [r["question_id"] for r in comparison["details"]] != selected):
        raise ValueError("Exact original 381-question cohort required")
    for row, old in zip(source["details"], comparison["details"], strict=True):
        ref = row["candidate_snapshot"]
        if (ref["filename"] != digest(row["question_id"].encode()) + ".json"
                or ref["sha256"] != old["candidate_snapshot_sha256"]
                or digest((args.snapshots / ref["filename"]).read_bytes()) != ref["sha256"]
                or old["evidence_source_ids"] != sorted(set(row["evidence_source_ids"]))
                or old["category"] != row["category"]):
            raise ValueError("Original candidate/label identity differs")
    groups = grouping(args.dataset, selected)
    identity = {name: digest(getattr(args, name).read_bytes()) for name in
                ("source", "comparison", "source_plan", "source_completion", "dataset")}
    identity["snapshots_sha256"] = digest(canonical([r["candidate_snapshot"] for r in source["details"]]))
    identity["grouping_sha256"] = digest(canonical(groups))
    return source, comparison, groups, identity


def capture(payload):
    candidates = [RetrievalCandidate.model_validate(c) for c in payload["candidates"]]
    before = canonical([c.model_dump(mode="json") for c in candidates])
    config = PackingConfig.model_validate(payload["packing"])
    result = {}
    for budget in BUDGETS:
        current = config.model_copy(update={"token_budget": budget, "multipath_ordering": "density"})
        for name in ARMS:
            if name == "score":
                bundle = pack_context(candidates, current.model_copy(update={"multipath_ordering": "score"}))
            else:
                bundle = composition.pack(candidates, current, reserve_head=name == "head1_quarter",
                                          alpha=1 if name == "density" else .25)
            measured = measure(bundle, set(), current)
            if name in ("density", "score"):
                control = payload["controls"][name][str(budget)]
                if any(key in control for key in LABEL_FIELDS):
                    raise ValueError("Outcome labels cannot enter the packing worker")
                if {k: v for k, v in measured.items() if k not in LABEL_FIELDS} != control:
                    raise ValueError("Original control did not reproduce")
            if name == "density" and budget == config.token_budget and bundle.render() != payload["original_context"]:
                raise ValueError("Original source capture did not reproduce")
            result[f"{name}:{budget}"] = {**measured, "context": bundle.render()}
    if canonical([c.model_dump(mode="json") for c in candidates]) != before:
        raise ValueError("Candidate inputs changed")
    return result


def summary(rows, *, independent=False):
    def calculate(before, after):
        if independent:
            return statistics_for(rows, before, after)
        valid = [r for r in rows if r["arms"][before]["evidence_recall"] is not None]
        return cluster_statistics([(r["arms"][before]["evidence_recall"],
                                    r["arms"][after]["evidence_recall"]) for r in valid],
                                  [r["group"] for r in valid])

    return {name: {str(b): calculate(f"{before}:{b}", f"{after}:{b}") for b in BUDGETS}
            for name, (before, after) in COMPARISONS.items()}


def run(args):
    source, old, groups, identity = sources(args)
    plan = json.loads(args.plan.read_bytes())
    if (plan["runtime"] != runtime() or plan["source"] != identity or runtime()["dirty"]
            or plan["arms"] != list(ARMS) or plan["budgets"] != list(BUDGETS)
            or plan["comparisons"] != {k: list(v) for k, v in COMPARISONS.items()}
            or plan["registered_at"] >= datetime.now(timezone.utc).isoformat()):
        raise ValueError("Registered study changed")
    if args.output.exists() or args.contexts.exists():
        raise ValueError("Fresh report and context directory required")
    args.contexts.mkdir()
    report = {"complete": False, "errors": 0, "source": identity, "runtime": runtime(),
              "plan_sha256": digest(args.plan.read_bytes()), "details": [], "limits": plan["limits"]}
    try:
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
            # Bounded batches avoid eagerly retaining hundreds of large snapshots.
            for start in range(0, 381, 8):
                batch = source["details"][start:start + 8]
                payloads = []
                for row, prior in zip(batch, old["details"][start:start + 8], strict=True):
                    saved = json.loads((args.snapshots / row["candidate_snapshot"]["filename"]).read_bytes())
                    payloads.append({"candidates": saved["candidates"], "packing": saved["packing_config"],
                                     "original_context": saved["control"]["context"],
                                     "controls": {name: {b: {k: v for k, v in values.items() if k not in LABEL_FIELDS}
                                                          for b, values in prior["variants"][name].items()}
                                                  for name in ("density", "score")}})
                for row, arms in zip(batch, pool.map(capture, payloads), strict=True):
                    qid = row["question_id"]
                    text = {key: values.pop("context") for key, values in arms.items()}
                    path = args.contexts / (digest(qid.encode()) + ".json")
                    write(path, {"question_id": qid, "contexts": text})
                    gold = set(row["evidence_source_ids"])
                    for values in arms.values():
                        retained = set(values["content_source_ids"])
                        values.update(evidence_recall=len(retained & gold) / len(gold) if gold else None,
                                      all_evidence_retained=gold <= retained if gold else None)
                    report["details"].append({"question_id": qid, "category": row["category"],
                                              "group": groups[qid], "arms": arms,
                                              "context_file": {"filename": path.name, "sha256": digest(path.read_bytes())},
                                              "candidate_snapshot": row["candidate_snapshot"]})
                    print(f"Completed {len(report['details'])}/381", flush=True)
        if runtime() != plan["runtime"] or sources(args)[3] != identity:
            raise ValueError("Study changed during execution")
        report.update(complete=True, contexts_evaluated=4572, controls_reproduced=2286,
                      overall=summary(report["details"]),
                      categories={c: summary([r for r in report["details"] if r["category"] == c])
                                  for c in sorted({r["category"] for r in report["details"]})})
    except BaseException as exc:
        report.update(errors=1, error_type=type(exc).__name__)
        raise
    finally:
        write(args.output, report)


def verify(args):
    complete = json.loads(args.completion.read_bytes())
    raw = args.output.read_bytes()
    report = json.loads(raw)
    source, old, groups, identity = sources(args)
    plan = json.loads(args.plan.read_bytes())
    if (complete["native_exit_code"] != 0 or complete["output_sha256"] != digest(raw)
            or report["plan_sha256"] != digest(args.plan.read_bytes())
            or report["source"] != identity or plan["source"] != identity
            or report["runtime"] != plan["runtime"] or not report["complete"] or report["errors"]
            or report["contexts_evaluated"] != 4572 or report["controls_reproduced"] != 2286
            or [r["question_id"] for r in report["details"]] != source["dataset"]["selected_question_ids"]):
        raise ValueError("Complete native-exited registered result required")
    verified = []
    for row, original, prior in zip(report["details"], source["details"], old["details"], strict=True):
        qid = row["question_id"]
        ref = row["context_file"]
        if (ref["filename"] != digest(qid.encode()) + ".json"
                or row["candidate_snapshot"] != original["candidate_snapshot"]
                or row["group"] != groups[qid] or row["category"] != original["category"]):
            raise ValueError("Case provenance changed")
        raw_context = (args.contexts / ref["filename"]).read_bytes()
        if digest(raw_context) != ref["sha256"]:
            raise ValueError("Rendered context file changed")
        saved = json.loads(raw_context)
        expected = {f"{name}:{b}" for name in ARMS for b in BUDGETS}
        if saved["question_id"] != qid or set(saved["contexts"]) != expected or set(row["arms"]) != expected:
            raise ValueError("Complete registered context coverage required")
        candidates = json.loads((args.snapshots / original["candidate_snapshot"]["filename"]).read_bytes())
        measured = {}
        for key, context in saved["contexts"].items():
            name, budget = key.split(":")
            config = {**candidates["packing_config"], "token_budget": int(budget)}
            values = account(context, candidates["candidates"], config, set(original["evidence_source_ids"]))
            if values != row["arms"][key]:
                raise ValueError("Independent rendered source accounting differs")
            if name in ("density", "score") and values != prior["variants"][name][budget]:
                raise ValueError("Original completed control differs")
            measured[key] = values
        verified.append({"question_id": qid, "category": row["category"], "group": groups[qid], "arms": measured})
    overall = summary(verified, independent=True)
    categories = {c: summary([r for r in verified if r["category"] == c], independent=True)
                  for c in sorted({r["category"] for r in verified})}
    if overall != report["overall"] or categories != report["categories"]:
        raise ValueError("Recomputed summaries differ")
    return {"complete": True, "questions": 381, "contexts_verified": 4572, "controls_verified": 2286,
            "output_sha256": digest(raw), "plan_sha256": digest(args.plan.read_bytes()),
            "completion_sha256": digest(args.completion.read_bytes()), "overall": overall,
            "categories": categories, "limits": report["limits"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "run", "verify"))
    for name in ("source", "comparison", "source-plan", "source-completion", "dataset", "snapshots",
                 "plan", "output", "contexts"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--completion", type=Path)
    parser.add_argument("--verification-output", type=Path)
    args = parser.parse_args()
    if args.mode == "register":
        if args.plan.exists() or runtime()["dirty"]:
            raise ValueError("Fresh registration and committed source required")
        write(args.plan, {"registered_at": datetime.now(timezone.utc).isoformat(), "runtime": runtime(),
                          "source": sources(args)[3], "arms": ARMS, "budgets": BUDGETS, "comparisons": COMPARISONS,
                          "primary": "quarter versus density at 4096; report all preference changes and individual losses",
                          "limits": ["Already examined 381-question partition: reused regression evidence, not an unseen holdout.",
                                     "Original score-policy confirmation remains failed; no criteria are rewritten.",
                                     "Fixed source candidates and four policies; no answer generation or production promotion.",
                                     "All 4572 contexts and 2286 controls require native completion and independent accounting.",
                                     "Whole-source retention is not answer correctness; clustered intervals are descriptive."]})
    elif args.mode == "run":
        run(args)
    else:
        if args.completion is None or args.verification_output is None or args.verification_output.exists():
            raise ValueError("Native completion and fresh verification output required")
        write(args.verification_output, verify(args))


if __name__ == "__main__":
    main()
