"""Correctness judging must follow verified, complete raw reader responses."""

import json

import pytest

from benchmarks.diagnostics import public_context_judging as judging
from benchmarks.diagnostics import public_context_reader as reader
from tests.test_public_context_reader import prepared_files


def reader_artifacts(tmp_path, monkeypatch):
    prepared, path, plan, _ = prepared_files(tmp_path, monkeypatch)
    state_path = tmp_path / "state.json"
    result = reader.run(path, plan, state_path, "test")
    report_path = tmp_path / "predictions.json"
    reader.write(report_path, result)
    completion_path = tmp_path / "native.json"
    reader.write(
        completion_path,
        {
            "native_exit_code": 0,
            "report_sha256": reader.digest(report_path.read_bytes()),
        },
    )
    return {
        "prepared_path": path,
        "plan_path": plan,
        "state_path": state_path,
        "report_path": report_path,
        "completion_path": completion_path,
    }


def test_prediction_verification_is_offline_and_reproduces_exact_answers(
    tmp_path, monkeypatch
):
    paths = reader_artifacts(tmp_path, monkeypatch)

    def no_network(*args, **kwargs):
        pytest.fail("Verification must not regenerate an answer")

    monkeypatch.setattr(reader.runtime, "request", no_network)
    prepared, report = judging.verify_predictions(**paths)
    assert len(prepared["rows"]) == 1 and len(report["rows"]) == 3


@pytest.mark.parametrize("mutation", ["native", "hypothesis", "coverage", "response"])
def test_changed_or_incomplete_predictions_cannot_reach_judge(
    tmp_path, monkeypatch, mutation
):
    paths = reader_artifacts(tmp_path, monkeypatch)
    if mutation == "native":
        reader.write(
            paths["completion_path"],
            {
                "native_exit_code": 130,
                "report_sha256": reader.digest(paths["report_path"].read_bytes()),
            },
        )
    elif mutation == "response":
        state = json.loads(paths["state_path"].read_bytes())
        next(iter(state["generations"].values()))["response"]["message"]["content"] = (
            "Changed"
        )
        reader.write(paths["state_path"], state)
    else:
        report = json.loads(paths["report_path"].read_bytes())
        if mutation == "hypothesis":
            report["rows"][0]["hypothesis"] = "Unrecorded answer"
        else:
            report["rows"].pop()
        reader.write(paths["report_path"], report)
        reader.write(
            paths["completion_path"],
            {
                "native_exit_code": 0,
                "report_sha256": reader.digest(paths["report_path"].read_bytes()),
            },
        )
    with pytest.raises(ValueError):
        judging.verify_predictions(**paths)


def test_judge_export_separates_reader_identity_and_requires_registered_cohort(
    tmp_path, monkeypatch
):
    paths = reader_artifacts(tmp_path, monkeypatch)
    refs_path = tmp_path / "refs.json"
    reader.write(
        refs_path,
        {
            "references": [
                {
                    "case_id": "opaque-0",
                    "question_id": "q_abs",
                    "category": "single-session-user",
                    "answer": "JUDGE_ONLY_CANARY",
                }
            ]
        },
    )
    prepared = json.loads(paths["prepared_path"].read_bytes())
    plan = json.loads(paths["plan_path"].read_bytes())
    registration = {
        "reader_declarations": {"reader": plan["reader"]},
        "references_sha256": reader.digest(refs_path.read_bytes()),
        "expected_cases": 1,
        "budget": 4096,
        "logical_predictions_per_reader": 3,
        "neutral_inputs_sha256": prepared["inputs_sha256"],
    }
    registration_path = tmp_path / "registration.json"
    reader.write(registration_path, registration)
    cases, mapping = judging.prepare_judgments(
        {"reader": paths}, refs_path, registration_path
    )
    assert len(cases["cases"]) == 3 and len(mapping["mapping"]) == 3
    for case in cases["cases"]:
        assert set(case) == {"id", "question", "category", "reference", "hypothesis"}
        assert len(case["id"]) == 64 and case["category"] == "abstention"
        assert case["reference"] == "JUDGE_ONLY_CANARY"
    registration["reader_declarations"]["missing-reader"] = plan["reader"]
    reader.write(registration_path, registration)
    with pytest.raises(ValueError, match="Every registered reader"):
        judging.prepare_judgments({"reader": paths}, refs_path, registration_path)
