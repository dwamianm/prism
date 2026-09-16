"""Verify and score only a complete annotation-selected reader diagnostic."""

import argparse
from datetime import datetime
import importlib
import inspect
import json
from pathlib import Path

from benchmarks.diagnostics.packing_reader import canonical, digest
from benchmarks.diagnostics.personamem_annotated_reader import ARMS, inputs, require
from benchmarks.diagnostics.personamem_packing import reader_payload, parse_response
from benchmarks.diagnostics.verify_personamem import cluster_comparison


def require_complete(result, plan, raw):
    require(result.get("complete") is True and result.get("errors") == 0 and result.get("process_exit_code") == 0,
            "Complete native-exit-zero diagnostic required before scoring")
    require(result["plan_sha256"] == digest(raw), "Diagnostic plan changed")
    require(datetime.fromisoformat(plan["registered_at"]) < datetime.fromisoformat(result["started_at"]), "Run predates registration")
    ids = plan["inputs"]["cohort"]["selected_question_ids"]
    require(len(ids) == len(set(ids)) == 96 and [r["question_id"] for r in result["details"]] == ids,
            "Diagnostic question coverage changed")
    require(plan["arms"] == list(ARMS) and all(set(r["arms"]) == set(ARMS) for r in result["details"]), "Arm coverage changed")


def score_rows(references, predictions):
    rows = [{**ref, "correct_answer": ref["correct"], "answers": predictions[ref["question_id"]],
             "correct": {arm: predictions[ref["question_id"]][arm] == ref["correct"] for arm in ARMS}}
            for ref in references]
    def metrics(group):
        return {arm: {"correct": sum(r["correct"][arm] for r in group), "total": len(group),
                      "accuracy": sum(r["correct"][arm] for r in group) / len(group),
                      "invalid_answers": sum(r["answers"][arm] is None for r in group)} for arm in ARMS}
    categories = {field: {value: metrics([r for r in rows if r[field] == value]) for value in sorted({r[field] for r in rows})}
                  for field in ("pref_type", "who", "updated", "conversation_scenario")}
    return rows, {"arms": metrics(rows), "categories": categories,
                  "primary": cluster_comparison(rows, "annotated_source", "no_memory")}


def verify(args):
    plan_raw, result_raw = args.plan.read_bytes(), args.result.read_bytes()
    plan, result = json.loads(plan_raw), json.loads(result_raw)
    require_complete(result, plan, plan_raw)
    for name, expected in plan["inputs"]["runtime"]["modules"].items():
        module = importlib.import_module(name)
        require(digest(Path(inspect.getfile(module)).read_bytes()) == expected, "Use registered diagnostic/reader code")
    require(digest(args.pilot_plan.read_bytes()) == plan["inputs"]["pilot_plan_sha256"]
            and digest(args.pilot_verification.read_bytes()) == plan["inputs"]["pilot_verification_sha256"], "Pilot inputs changed")
    prepared, references, identity, reader = inputs(args.data, args.pilot_plan, args.pilot_verification)
    require(identity == plan["inputs"]["cohort"] and reader == plan["inputs"]["reader"], "Diagnostic cohort or reader changed")
    for filename, expected, key in (("prepared.json", prepared, "prepared_sha256"), ("references.json", references, "references_sha256")):
        saved = json.loads((args.artifacts / filename).read_bytes())
        require(canonical(saved) == canonical(expected) and digest(canonical(saved)) == plan["inputs"][key], "Prepared input or reference changed")
    require(max(r["contexts"]["annotated_source"]["tokens"] for r in prepared) == plan["maximum_memory_tokens"], "Token accounting changed")
    empty_ids = [r["question"]["id"] for r in prepared if not r["contexts"]["annotated_source"]["context"]]
    require(len(empty_ids) == plan["empty_annotated_contexts"], "Empty case coverage changed")
    pilot_verified = json.loads(args.pilot_verification.read_bytes())
    require(digest(args.pilot_result.read_bytes()) == pilot_verified["source_output_sha256"], "Verified pilot predictions changed")
    pilot = json.loads(args.pilot_result.read_bytes())
    previous = {r["question_id"]: r["arms"]["no_memory"] for r in pilot["details"]}
    require(set(previous) == {r["question"]["id"] for r in prepared}, "Pilot prediction coverage changed")
    predictions, drift = {}, []
    for detail, row in zip(result["details"], prepared, strict=True):
        question = row["question"]
        qid = question["id"]
        require(detail["persona_id"] == question["persona_id"], "Prediction persona changed")
        order = sorted(ARMS, key=lambda arm: digest(canonical([qid, arm])))
        require(detail["arm_order"] == order, "Diagnostic arm order changed")
        answers = {}
        for arm, saved in detail["arms"].items():
            context = row["contexts"][arm]
            body = reader_payload(question, context["context"], reader["model"])
            require(saved["request_sha256"] == digest(canonical(body)) and saved["context_sha256"] == context["sha256"],
                    "Diagnostic reader request changed")
            require(saved["response"]["model"] == reader["model"], "Reader response model changed")
            answer = parse_response(saved["response"])
            require(answer == saved["answer"], "Prediction differs from raw reader response")
            answers[arm] = answer
            if arm == "no_memory":
                require(saved["request_sha256"] == previous[qid]["request_sha256"], "No-memory request differs from pilot")
                if answer != previous[qid]["answer"]:
                    drift.append(qid)
        predictions[qid] = answers
    repeats = result["reader_repeats"]
    require([r["question_id"] for r in repeats] == identity["selected_question_ids"][::4], "Repeat coverage changed")
    disagreements = []
    for repeat in repeats:
        qid = repeat["question_id"]
        arm = sorted(ARMS, key=lambda a: digest(canonical([qid, a])))[0]
        require(repeat["arm"] == arm and repeat["response"]["model"] == reader["model"], "Repeat identity changed")
        same = parse_response(repeat["response"]) == predictions[qid][arm]
        require(repeat["same_answer"] is same, "Repeated response comparison changed")
        if not same:
            disagreements.append(qid)
    # Labels are joined only after native completion and all integrity checks.
    rows, comparison = score_rows(references, predictions)
    return {"source_output_sha256": digest(result_raw), "plan_sha256": digest(plan_raw),
            "native_study_exit_code": result["process_exit_code"], "study_commit": plan["inputs"]["runtime"]["commit"],
            "questions": len(rows), "personas": len(identity["selected_persona_ids"]), "primary_answers_verified": 2 * len(rows),
            "maximum_context_tokens": plan["maximum_memory_tokens"], "empty_annotated_contexts": len(empty_ids),
            "empty_context_answer_disagreements": [qid for qid in empty_ids if predictions[qid]["annotated_source"] != predictions[qid]["no_memory"]],
            "no_memory_vs_pilot": {"identical_requests_verified": len(rows), "answer_disagreements": drift},
            "reader_repeats": {"total": len(repeats), "answer_disagreements": disagreements,
                               "invalid_answers": sum(parse_response(r["response"]) is None for r in repeats)},
            "comparison": comparison, "details": rows, "protocol": plan["protocol"], "default_promotion": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "result", "pilot-plan", "pilot-verification", "pilot-result", "data", "artifacts", "verification"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    require(not args.verification.exists(), "Refusing to overwrite previous verification")
    report = verify(args)
    report["verifier_sha256"] = digest(Path(__file__).read_bytes())
    args.verification.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
