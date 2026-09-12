"""Reader studies must retain product context, hide labels and fail closed."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from benchmarks.diagnostics import packing_reader as reader
from tests.test_product_packing_diagnostic import fixture_report


def inputs(tmp_path):
    report = fixture_report(tmp_path)
    raw = reader.canonical([{"question_id": "q", "question": "Which database?", "question_date": "2026-01-01",
                             "question_type": "single-session-user", "answer": "GOLD_CANARY_NOT_FOR_GENERATION"}])
    report["dataset"]["sha256"] = reader.digest(raw)
    return report, raw


def prepare(tmp_path):
    report, raw = inputs(tmp_path)
    result, refs = reader.prepare(report, tmp_path, raw, budget=report["budgets"][0])
    result["reader"] = {"model": "reader", "model_digest": "fixed-model", "options": dict(reader.OPTIONS),
                        "runner_sha256": reader.digest(Path(reader.__file__).read_bytes()),
                        "ollama_version": "test-version"}
    path = tmp_path / "prepared.json"
    reader.write(path, result)
    return result, refs, path


def fake_service(monkeypatch, *, truncate=False):
    calls = []
    def request(base, endpoint, body=None):
        if endpoint == "/api/tags":
            return {"models": [{"name": "reader", "digest": "fixed-model"}]}
        if endpoint == "/api/version":
            return {"version": "test-version"}
        assert endpoint == "/api/chat"
        calls.append(deepcopy(body))
        return {"model": "reader", "done": True, "done_reason": "length" if truncate else "stop",
                "prompt_eval_count": 100, "eval_count": 3, "message": {"content": "PostgreSQL."}}
    monkeypatch.setattr(reader, "request", request)
    return calls


def test_gold_labels_are_separate_and_cannot_change_reader_payload(tmp_path):
    report, raw = inputs(tmp_path)
    prepared, refs = reader.prepare(report, tmp_path, raw, budget=report["budgets"][0])
    assert refs[0]["answer"] == "GOLD_CANARY_NOT_FOR_GENERATION"
    assert b"GOLD_CANARY" not in reader.canonical(prepared)
    altered = json.loads(raw)
    altered[0]["answer"] = "OTHER_GOLD"
    new_raw = reader.canonical(altered)
    report["dataset"]["sha256"] = reader.digest(new_raw)
    changed, _ = reader.prepare(report, tmp_path, new_raw, budget=report["budgets"][0])
    for arm in reader.ARMS:
        original_payload = reader.payload(prepared["rows"][0], arm, prepared, "reader")
        assert original_payload == reader.payload(changed["rows"][0], arm, changed, "reader")
        assert b"GOLD_CANARY" not in reader.canonical(original_payload)


@pytest.mark.parametrize("mutation", ["split", "exit", "dataset", "snapshot", "control", "coverage"])
def test_preparation_rejects_invalid_or_unreproduced_capture(tmp_path, mutation):
    report, raw = inputs(tmp_path)
    if mutation == "split":
        report["dataset"]["split"] = "test"
    elif mutation == "exit":
        report["process_exit_code"] = -6
    elif mutation == "dataset":
        raw += b" "
    elif mutation == "snapshot":
        report["details"][0]["candidate_snapshot"]["sha256"] = "wrong"
    elif mutation == "coverage":
        report["dataset"]["selected_question_ids"].append("missing")
    else:
        ref = report["details"][0]["candidate_snapshot"]
        path = tmp_path / ref["filename"]
        snapshot = json.loads(path.read_bytes())
        snapshot["control"]["context"] = "different"
        encoded = reader.canonical(snapshot)
        path.write_bytes(encoded)
        ref["sha256"] = reader.digest(encoded)
    with pytest.raises(ValueError):
        reader.prepare(report, tmp_path, raw, budget=report["budgets"][0])


def test_identical_prompts_reuse_one_generation_and_resume_without_new_calls(tmp_path, monkeypatch):
    prepared, _, path = prepare(tmp_path)
    prepared["rows"][0]["contexts"]["score"] = deepcopy(prepared["rows"][0]["contexts"]["density"])
    reader.write(path, prepared)
    calls = fake_service(monkeypatch)
    state = tmp_path / "state.json"
    result = reader.run(path, state, model="reader", base_url="test")
    assert result["logical_predictions"] == 2 and result["unique_generations"] == 1
    assert result["reused_predictions"] == 1 and len(calls) == 1
    assert b"GOLD_CANARY" not in reader.canonical(calls)
    assert reader.run(path, state, model="reader", base_url="test") == result
    assert len(calls) == 1
    result["process_exit_code"] = 0
    reader.export_predictions(result, tmp_path / "predictions")
    for arm in reader.ARMS:
        exported = json.loads((tmp_path / "predictions" / f"{arm}.jsonl").read_text())
        assert exported == {"question_id": "q", "hypothesis": "PostgreSQL."}


def test_truncation_is_retained_as_failed_attempt_and_can_be_resumed(tmp_path, monkeypatch):
    _, _, path = prepare(tmp_path)
    fake_service(monkeypatch, truncate=True)
    state_path = tmp_path / "state.json"
    with pytest.raises(ValueError, match="truncated"):
        reader.run(path, state_path, model="reader", base_url="test")
    state = json.loads(state_path.read_text())
    assert not state["complete"] and state["generations"] == {}
    assert state["failed_attempts"][0]["response"]["done_reason"] == "length"
    fake_service(monkeypatch)
    result = reader.run(path, state_path, model="reader", base_url="test")
    assert result["complete"] and len(result["prior_failed_attempts"]) == 1


@pytest.mark.parametrize("mutation", ["input", "response", "cohort"])
def test_resume_rejects_changed_inputs_corruption_or_missing_questions(tmp_path, monkeypatch, mutation):
    prepared, _, path = prepare(tmp_path)
    fake_service(monkeypatch)
    state_path = tmp_path / "state.json"
    reader.run(path, state_path, model="reader", base_url="test")
    if mutation == "response":
        state = json.loads(state_path.read_text())
        next(iter(state["generations"].values()))["response"]["message"]["content"] = "tampered"
        reader.write(state_path, state)
    else:
        if mutation == "input":
            prepared["generation_system_prompt"] = "changed"
        else:
            prepared["dataset"]["selected_question_ids"].append("missing")
        reader.write(path, prepared)
    with pytest.raises(ValueError):
        reader.run(path, state_path, model="reader", base_url="test")


def test_native_failure_cannot_be_exported_as_complete_predictions(tmp_path):
    with pytest.raises(ValueError, match="process exit"):
        reader.export_predictions({"passed": True, "complete": True, "process_exit_code": -6}, tmp_path)


def test_state_lock_prevents_concurrent_writers(tmp_path):
    with reader.exclusive_state(tmp_path / "state.json"):
        with pytest.raises(BlockingIOError):
            with reader.exclusive_state(tmp_path / "state.json"):
                pytest.fail("Second writer acquired the lock")


def test_reader_identity_is_checked_before_generation(tmp_path, monkeypatch):
    prepared, _, path = prepare(tmp_path)
    prepared["reader"]["model_digest"] = "other-weights"
    reader.write(path, prepared)
    calls = fake_service(monkeypatch)
    with pytest.raises(ValueError, match="declaration"):
        reader.run(path, tmp_path / "state.json", model="reader", base_url="test")
    assert calls == []


def test_changed_model_during_generation_is_retained_but_not_accepted(tmp_path, monkeypatch):
    _, _, path = prepare(tmp_path)
    fake_service(monkeypatch)
    original = reader.request
    tags = []
    def changing(base, endpoint, body=None):
        result = original(base, endpoint, body)
        if endpoint == "/api/tags":
            tags.append(True)
            if len(tags) >= 3:
                result["models"][0]["digest"] = "changed"
        return result
    monkeypatch.setattr(reader, "request", changing)
    state_path = tmp_path / "state.json"
    with pytest.raises(ValueError, match="identity changed"):
        reader.run(path, state_path, model="reader", base_url="test")
    state = json.loads(state_path.read_text())
    assert not state["complete"] and state["generations"] == {}
    assert state["failed_attempts"][0]["response"]["message"]["content"] == "PostgreSQL."
