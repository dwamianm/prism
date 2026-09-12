"""Completion, context/request integrity and all-case scoring for the diagnostic."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics import verify_personamem_annotated as verifier
from benchmarks.diagnostics.packing_reader import canonical, digest, write
from benchmarks.diagnostics.personamem_annotated_reader import ARMS
from benchmarks.diagnostics.personamem_packing import reader_payload


@pytest.mark.parametrize("result", [{}, {"complete": False, "errors": 0, "process_exit_code": 0},
    {"complete": True, "errors": 0, "process_exit_code": -11}, {"complete": True, "errors": 1, "process_exit_code": 0}])
def test_incomplete_diagnostic_is_rejected_before_reading_predictions(result):
    with pytest.raises(ValueError, match="native-exit-zero"):
        verifier.require_complete(result, {}, b"plan")


def test_all_case_verification_scores_invalid_answers_and_rejects_tampering(tmp_path, monkeypatch):
    prepared, references, previous, details, repeats = [], [], [], [], []
    for i in range(96):
        question = {"id": str(i), "persona_id": str(i // 4), "query": "Which route?", "options": ["one", "two", "three", "four"]}
        contexts = {arm: {"context": "Only in summer." if arm == "annotated_source" else "",
                          "tokens": 4 if arm == "annotated_source" else 0,
                          "sha256": digest(("Only in summer." if arm == "annotated_source" else "").encode()),
                          "source_ids": ["t00000"] if arm == "annotated_source" else [],
                          "source_roles": ["user"] if arm == "annotated_source" else []} for arm in ARMS}
        prepared.append({"question": question, "contexts": contexts})
        references.append({"question_id": str(i), "persona_id": str(i // 4), "correct": "B", "pref_type": "authored",
                           "who": "self", "updated": "False", "conversation_scenario": "test"})
        order = sorted(ARMS, key=lambda arm: digest(canonical([str(i), arm])))
        outputs = {}
        for arm in ARMS:
            answer = (None if i == 0 else "B") if arm == "annotated_source" else "A"
            response = {"model": "authored", "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 7,
                        "message": {"content": "invalid" if answer is None else json.dumps({"answer": answer})}}
            body = reader_payload(question, contexts[arm]["context"], "authored")
            outputs[arm] = {"answer": answer, "response": response, "request_sha256": digest(canonical(body)),
                            "context_sha256": contexts[arm]["sha256"]}
        details.append({"question_id": str(i), "persona_id": str(i // 4), "arm_order": order, "arms": outputs})
        previous.append({"question_id": str(i), "arms": {"no_memory": outputs["no_memory"]}})
        if i % 4 == 0:
            repeats.append({"question_id": str(i), "arm": order[0], "same_answer": True, "response": outputs[order[0]]["response"]})
    args = SimpleNamespace(**{key: tmp_path / (key + ".json") for key in
        ("plan", "result", "pilot_plan", "pilot_verification", "pilot_result")}, data=tmp_path / "data", artifacts=tmp_path / "artifacts")
    write(args.pilot_plan, {"authored": True})
    write(args.pilot_result, {"details": previous})
    write(args.pilot_verification, {"source_output_sha256": digest(args.pilot_result.read_bytes())})
    write(args.artifacts / "prepared.json", prepared)
    write(args.artifacts / "references.json", references)
    identity = {"selected_question_ids": [str(i) for i in range(96)], "selected_persona_ids": [str(i) for i in range(24)]}
    reader = {"model": "authored"}
    plan = {"registered_at": "2026-01-01", "arms": list(ARMS), "maximum_memory_tokens": 4, "empty_annotated_contexts": 0,
            "protocol": ["Authored verifier fixture"], "inputs": {"runtime": {"commit": "authored", "modules": {}},
                "cohort": identity, "reader": reader, "prepared_sha256": digest(canonical(prepared)),
                "references_sha256": digest(canonical(references)), "pilot_plan_sha256": digest(args.pilot_plan.read_bytes()),
                "pilot_verification_sha256": digest(args.pilot_verification.read_bytes())}}
    write(args.plan, plan)
    output = {"complete": True, "errors": 0, "process_exit_code": 0, "started_at": "2026-01-02",
              "plan_sha256": digest(args.plan.read_bytes()), "details": details, "reader_repeats": repeats}
    write(args.result, output)
    monkeypatch.setattr(verifier, "inputs", lambda *_: (prepared, references, identity, reader))
    report = verifier.verify(args)
    assert report["questions"] == 96 and report["primary_answers_verified"] == 192
    assert report["comparison"]["arms"]["annotated_source"] == {"correct": 95, "total": 96, "accuracy": 95/96, "invalid_answers": 1}
    assert report["comparison"]["arms"]["no_memory"]["correct"] == 0
    assert report["no_memory_vs_pilot"] == {"identical_requests_verified": 96, "answer_disagreements": []}
    assert report["comparison"]["categories"]["who"]["self"]["annotated_source"]["total"] == 96
    modified = deepcopy(output)
    modified["details"][0]["arms"]["no_memory"]["request_sha256"] = "0" * 64
    write(args.result, modified)
    with pytest.raises(ValueError, match="reader request"):
        verifier.verify(args)
    write(args.result, output)
    modified_prepared = deepcopy(prepared)
    modified_prepared[0]["contexts"]["annotated_source"]["context"] = "Injected source"
    write(args.artifacts / "prepared.json", modified_prepared)
    with pytest.raises(ValueError, match="Prepared input"):
        verifier.verify(args)
