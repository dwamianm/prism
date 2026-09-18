"""Independent judging must not bypass calibration or alter paired evidence."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from benchmarks.diagnostics import reader_judge as judge
from tests.test_packing_reader import prepare, fake_service


def controls():
    return json.loads(
        Path("benchmarks/fixtures/reader_judge_controls.json").read_text()
    )


def response(correct=True):
    return {
        "model": "judge",
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 100,
        "eval_count": 20,
        "message": {
            "content": json.dumps(
                {"correct": correct, "reason": "Matches the reference."}
            )
        },
    }


def service(monkeypatch, reply=None):
    calls = []

    def request(base, endpoint, body=None):
        if endpoint == "/api/tags":
            return {"models": [{"name": "judge", "digest": "judge-weights"}]}
        if endpoint == "/api/version":
            return {"version": "test"}
        assert endpoint == "/api/chat"
        calls.append(deepcopy(body))
        return deepcopy(response() if reply is None else reply)

    monkeypatch.setattr(judge.runtime, "request", request)
    declared = judge.declaration(
        "judge", "test", judge.runtime.digest(judge.runtime.canonical(controls()))
    )
    return declared, calls


def calibrated(declared):
    fixture = controls()
    rows, generations = [], {}
    for case in fixture["cases"]:
        key = judge.runtime.digest(
            judge.runtime.canonical(judge.payload(case, declared))
        )
        raw = response(case["expected_correct"])
        generations[key] = {
            "response": raw,
            "response_sha256": judge.runtime.digest(judge.runtime.canonical(raw)),
        }
        rows.append(
            {"id": case["id"], "prompt_sha256": key, **judge.verdict(raw, "judge")}
        )
    return {
        "passed": True,
        "complete": True,
        "process_exit_code": 0,
        "identity": {
            "declaration": declared,
            "cases_sha256": judge.runtime.digest(
                judge.runtime.canonical(fixture["cases"])
            ),
        },
        "judgments": rows,
        "generations": generations,
    }


def test_expected_labels_and_arm_are_not_given_to_judge(monkeypatch):
    declared, _ = service(monkeypatch)
    case = controls()["cases"][0]
    body = judge.payload(case, declared)
    altered = {
        **case,
        "expected_correct": False,
        "arm": "CANARY_ARM",
        "id": "CANARY_ID",
    }
    assert judge.payload(altered, declared) == body
    assert set(json.loads(body["messages"][1]["content"])) == {
        "question",
        "reference",
        "response",
    }
    assert (
        body["think"] is False
        and body["format"]["properties"]["correct"]["type"] == "boolean"
    )


def test_cloud_alias_accepts_only_its_resolved_remote_model_name():
    raw = response()
    raw["model"] = "gpt-oss:120b"

    assert judge.verdict(raw, "gpt-oss:120b-cloud")["correct"] is True
    raw["model"] = "deepseek-v4.1-flash"
    assert judge.verdict(raw, "deepseek-v4.1-flash:cloud")["correct"] is True
    with pytest.raises(ValueError):
        judge.verdict(raw, "other:cloud")


@pytest.mark.parametrize(
    "change",
    [
        "truncation",
        "model",
        "tool",
        "string_bool",
        "extra",
        "empty",
        "tokens",
        "bool_tokens",
        "overflow",
    ],
)
def test_invalid_verdicts_fail_closed(change):
    raw = response()
    if change == "truncation":
        raw["done_reason"] = "length"
    elif change == "model":
        raw["model"] = "reader"
    elif change == "tool":
        raw["message"]["tool_calls"] = [{}]
    elif change == "string_bool":
        raw["message"]["content"] = '{"correct":"false","reason":"x"}'
    elif change == "extra":
        raw["message"]["content"] = '{"correct":true,"reason":"x","override":true}'
    elif change == "empty":
        raw["message"]["content"] = '{"correct":true,"reason":" "}'
    elif change == "tokens":
        raw["eval_count"] = -1
    elif change == "bool_tokens":
        raw["eval_count"] = True
    else:
        raw["prompt_eval_count"] = judge.OPTIONS["num_ctx"]
    with pytest.raises(ValueError):
        judge.verdict(raw, "judge")


def test_resume_uses_exact_payload_and_retains_raw_response(tmp_path, monkeypatch):
    declared, calls = service(monkeypatch)
    case = controls()["cases"][0]
    cases = [case, {**case, "id": "duplicate-prompt"}]
    path = tmp_path / "state.json"
    result = judge.run_cases(cases, declared, path, "test")
    assert result["complete"] and result["unique_calls"] == 1 and len(calls) == 1
    assert judge.run_cases(cases, declared, path, "test") == result and len(calls) == 1
    state = json.loads(path.read_text())
    next(iter(state["generations"].values()))["response"]["message"]["content"] = (
        "tampered"
    )
    judge.runtime.write(path, state)
    with pytest.raises(ValueError, match="checksum"):
        judge.run_cases(cases, declared, path, "test")


def test_truncated_response_retained_and_not_counted(tmp_path, monkeypatch):
    raw = response()
    raw["done_reason"] = "length"
    declared, _ = service(monkeypatch, raw)
    path = tmp_path / "state.json"
    with pytest.raises(ValueError, match="truncated"):
        judge.run_cases(controls()["cases"][:1], declared, path, "test")
    state = json.loads(path.read_text())
    assert not state["complete"] and not state["generations"]
    assert state["failed_attempts"][0]["response"] == raw
    assert state["failed_attempts"][0]["response_sha256"] == judge.runtime.digest(
        judge.runtime.canonical(raw)
    )


@pytest.mark.parametrize("change", ["declaration", "cases", "model_during_call"])
def test_changed_runtime_or_cohort_is_rejected(tmp_path, monkeypatch, change):
    declared, calls = service(monkeypatch)
    cases = controls()["cases"][:1]
    path = tmp_path / "state.json"
    if change == "declaration":
        declared["options"]["seed"] = 3
    elif change == "cases":
        judge.run_cases(cases, declared, path, "test")
        cases[0]["hypothesis"] += " changed"
    else:
        original = judge.runtime.request

        def changing(base, endpoint, body=None):
            value = original(base, endpoint, body)
            if endpoint == "/api/tags" and calls:
                value["models"][0]["digest"] = "new-weights"
            return value

        monkeypatch.setattr(judge.runtime, "request", changing)
    with pytest.raises(ValueError):
        judge.run_cases(cases, declared, path, "test")
    if change == "declaration":
        assert not calls
    if change == "model_during_call":
        state = json.loads(path.read_text())
        assert not state["generations"] and len(state["failed_attempts"]) == 1


def test_calibration_gate_recomputed_from_raw_verdicts(monkeypatch):
    declared, _ = service(monkeypatch)
    fixture = controls()
    result = calibrated(declared)
    judge.validate_calibration(fixture, result, declared)
    assert judge.calibration_metrics(fixture, result)["correct"] == 42
    positive = next(row for row in result["judgments"] if row["correct"])
    positive["correct"] = False
    # A reported success flag cannot conceal a modified judgment.
    result["metrics"] = {"calibration_gate_passed": True}
    with pytest.raises(ValueError, match="recorded response"):
        judge.validate_calibration(fixture, result, declared)


@pytest.mark.parametrize(
    "change",
    ["native", "incomplete", "runtime", "control", "gate", "raw", "false_accept"],
)
def test_calibration_cannot_be_bypassed(monkeypatch, change):
    declared, _ = service(monkeypatch)
    fixture = controls()
    result = calibrated(declared)
    if change == "native":
        result["process_exit_code"] = -6
    elif change == "incomplete":
        result["complete"] = False
    elif change == "runtime":
        declared = {**declared, "model_digest": "changed"}
    elif change == "control":
        fixture["cases"][0]["reference"] = "Different"
    elif change == "gate":
        fixture["gate"]["minimum_correct"] = 0
    elif change == "raw":
        next(iter(result["generations"].values()))["response_sha256"] = "wrong"
    else:
        row = next(row for row in result["judgments"] if not row["correct"])
        row["correct"] = True
        raw = response(True)
        result["generations"][row["prompt_sha256"]] = {
            "response": raw,
            "response_sha256": judge.runtime.digest(judge.runtime.canonical(raw)),
        }
    with pytest.raises(ValueError):
        judge.validate_calibration(fixture, result, declared)


def study(tmp_path, monkeypatch):
    prepared, refs, path = prepare(tmp_path)
    fake_service(monkeypatch)
    state_path = tmp_path / "reader-state.json"
    predictions = judge.runtime.run(path, state_path, model="reader", base_url="test")
    predictions["process_exit_code"] = 0
    refs_raw = judge.runtime.canonical(refs)
    prepared_raw = path.read_bytes()
    plan = {
        "references_sha256": judge.runtime.digest(refs_raw),
        "prepared_sha256": judge.runtime.digest(prepared_raw),
        "reader": prepared["reader"],
        "selected_question_ids": ["q"],
    }
    declared, _ = service(monkeypatch)
    return (
        predictions,
        refs_raw,
        prepared_raw,
        plan,
        declared,
        json.loads(state_path.read_text()),
    )


def test_reader_study_requires_matching_raw_outputs(tmp_path, monkeypatch):
    args = study(tmp_path, monkeypatch)
    cases = judge.study_cases(*args)
    assert len(cases) == 2 and {case["arm"] for case in cases} == {"density", "score"}
    assert all(case["hypothesis"] == "PostgreSQL." for case in cases)


@pytest.mark.parametrize(
    "change",
    [
        "native",
        "references",
        "prepared",
        "self_judge",
        "cohort",
        "context",
        "hypothesis",
        "prompt",
        "raw",
        "raw_identity",
        "raw_incomplete",
        "foreign_raw",
    ],
)
def test_reader_study_rejects_corruption_or_incomplete_inputs(
    tmp_path, monkeypatch, change
):
    args = list(study(tmp_path, monkeypatch))
    predictions, _, _, plan, declared, state = args
    if change == "native":
        predictions["process_exit_code"] = -6
    elif change == "references":
        args[1] += b" "
    elif change == "prepared":
        args[2] += b" "
    elif change == "self_judge":
        declared["model_digest"] = plan["reader"]["model_digest"]
    elif change == "cohort":
        predictions["rows"].pop()
    elif change == "context":
        predictions["rows"][0]["context_sha256"] = "wrong"
    elif change == "hypothesis":
        predictions["rows"][0]["hypothesis"] = "Changed"
    elif change == "prompt":
        predictions["rows"][0]["prompt_sha256"] = "wrong"
    elif change == "raw":
        next(iter(state["generations"].values()))["response"]["message"]["content"] = (
            "Changed"
        )
    elif change == "raw_identity":
        state["identity"]["model_digest"] = "changed"
    elif change == "raw_incomplete":
        state["complete"] = False
    else:
        state["generations"]["unrelated"] = deepcopy(
            next(iter(state["generations"].values()))
        )
    with pytest.raises(ValueError):
        judge.study_cases(*args)


def test_paired_metrics_use_question_pairs_not_independent_samples():
    cases = [
        {"id": q + arm, "question_id": q, "arm": arm, "category": "multi-session"}
        for q in ["a", "b"]
        for arm in judge.runtime.ARMS
    ]
    result = {
        "judgments": [
            {"id": case["id"], "correct": case["arm"] == "score"} for case in cases
        ]
    }
    metrics = judge.study_metrics(cases, result)
    assert metrics["overall"]["queries"] == 2 and metrics["overall"]["delta"] == 1
    assert metrics["overall"]["interval_95"] == [1, 1]
    assert metrics["categories"]["multi-session"] == metrics["overall"]
    result["judgments"].append(result["judgments"][0])
    with pytest.raises(ValueError):
        judge.study_metrics(cases, result)


def test_calibration_acceptance_boundary_is_frozen(monkeypatch):
    declared, _ = service(monkeypatch)
    fixture = controls()
    result = calibrated(declared)
    positives = [row for row in result["judgments"] if row["correct"]]
    for row in positives[:2]:
        row["correct"] = False
    metrics = judge.calibration_metrics(fixture, result)
    assert metrics["correct"] == 40 and metrics["calibration_gate_passed"]
    positives[2]["correct"] = False
    assert not judge.calibration_metrics(fixture, result)["calibration_gate_passed"]
    result = calibrated(declared)
    next(row for row in result["judgments"] if not row["correct"])["correct"] = True
    metrics = judge.calibration_metrics(fixture, result)
    assert metrics["correct"] == 41 and metrics["false_accepts"] == 1
    assert not metrics["calibration_gate_passed"]


def test_oversized_judge_input_is_rejected_before_any_call(monkeypatch, tmp_path):
    declared, calls = service(monkeypatch)
    case = {**controls()["cases"][0], "hypothesis": "x" * judge.OPTIONS["num_ctx"]}
    with pytest.raises(ValueError, match="headroom"):
        judge.run_cases([case], declared, tmp_path / "state.json", "test")
    assert not calls
