"""Answer scores must follow complete native evidence and preserve regressions."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics import public_context_judge as judge
from benchmarks.diagnostics import public_context_scores as scoring
from benchmarks.diagnostics.public_context_judging import prepare_judgments
from benchmarks.diagnostics.hindsight_capture import digest, write
from tests.test_public_context_judging import reader_artifacts
from tests.test_reader_judge import service, calibrated, controls


def pipeline(tmp_path, monkeypatch):
    paths = reader_artifacts(tmp_path, monkeypatch)
    prepared = json.loads(paths["prepared_path"].read_bytes())
    reader_plan = json.loads(paths["plan_path"].read_bytes())
    readers_path = tmp_path / "readers.json"
    write(readers_path, {"reader": {name: str(path) for name, path in paths.items()}})
    refs_path = tmp_path / "refs.json"
    write(
        refs_path,
        {
            "references": [
                {
                    "case_id": "opaque-0",
                    "question_id": "q_abs",
                    "category": "single-session-user",
                    "answer": "Unknown",
                }
            ]
        },
    )
    declared, calls = service(monkeypatch)
    registration_path = tmp_path / "registration.json"
    write(
        registration_path,
        {
            "reader_declarations": {"reader": reader_plan["reader"]},
            "judge_declaration": declared,
            "judge_worker_sha256": digest(Path(judge.__file__).read_bytes()),
            "scorer_sha256": digest(Path(scoring.__file__).read_bytes()),
            "capture_plan_sha256": {
                "prme": "registered-p",
                "hindsight": "registered-h",
            },
            "references_sha256": digest(refs_path.read_bytes()),
            "expected_cases": 1,
            "budget": 4096,
            "logical_predictions_per_reader": 3,
            "neutral_inputs_sha256": prepared["inputs_sha256"],
        },
    )
    exported, mapping = prepare_judgments(
        {"reader": paths}, refs_path, registration_path
    )
    inputs_path, mapping_path = (
        tmp_path / "judge-inputs.json",
        tmp_path / "mapping.json",
    )
    write(inputs_path, exported)
    write(mapping_path, mapping)
    controls_path, calibration_path = (
        tmp_path / "controls.json",
        tmp_path / "calibration.json",
    )
    write(controls_path, controls())
    write(calibration_path, calibrated(declared))
    plan_path = tmp_path / "judge-plan.json"
    write(
        plan_path,
        {
            "inputs_sha256": digest(inputs_path.read_bytes()),
            "controls_file_sha256": digest(controls_path.read_bytes()),
            "calibration_sha256": digest(calibration_path.read_bytes()),
            "worker_sha256": digest(Path(judge.__file__).read_bytes()),
            "case_ids": [r["id"] for r in exported["cases"]],
            "judge": declared,
            "registration_sha256": digest(registration_path.read_bytes()),
            "mapping_sha256": digest(mapping_path.read_bytes()),
        },
    )
    state_path = tmp_path / "judge-state.json"
    result = judge.run(
        inputs_path, plan_path, controls_path, calibration_path, state_path, "test"
    )
    report_path, completion_path = (
        tmp_path / "judgments.json",
        tmp_path / "judge-native.json",
    )
    write(report_path, result)
    write(
        completion_path,
        {"native_exit_code": 0, "report_sha256": digest(report_path.read_bytes())},
    )
    return SimpleNamespace(
        readers=readers_path,
        references=refs_path,
        registration=registration_path,
        neutral_inputs=tmp_path / "inputs.json",
        inputs=inputs_path,
        mapping=mapping_path,
        plan=plan_path,
        controls=controls_path,
        calibration=calibration_path,
        state=state_path,
        report=report_path,
        completion=completion_path,
        capture_analysis=tmp_path / "analysis.json",
        capture_completion=tmp_path / "completion.json",
    ), calls


def test_entire_reader_judge_chain_reproduces_scores_without_network(
    tmp_path, monkeypatch
):
    args, calls = pipeline(tmp_path, monkeypatch)
    assert (
        len(calls) == 1
    )  # Three identical judge requests, retained as three logical verdicts.

    def no_network(*args, **kwargs):
        pytest.fail("Scoring must not regenerate reader or judge outputs")

    monkeypatch.setattr(judge.runtime.runtime, "request", no_network)
    report = scoring.analyze(args)
    assert report["complete"] and report["verified"] and report["questions"] == 1
    metric = report["readers"]["reader"]["categories"]["abstention"][
        "prme_vs_hindsight"
    ]
    assert metric["before_correct"] == metric["after_correct"] == 1
    assert metric["group_bootstrap"]["interval_95"] is None


@pytest.mark.parametrize(
    "mutation",
    ["native", "verdict", "mapping", "reader-answer", "capture-native", "capture-plan"],
)
def test_scores_reject_native_failure_and_forged_verdict_or_product_mapping(
    tmp_path, monkeypatch, mutation
):
    args, _ = pipeline(tmp_path, monkeypatch)
    if mutation == "native":
        write(
            args.completion,
            {
                "native_exit_code": 130,
                "report_sha256": digest(args.report.read_bytes()),
            },
        )
    elif mutation == "verdict":
        result = json.loads(args.report.read_bytes())
        result["judgments"][0]["correct"] = False
        write(args.report, result)
        write(
            args.completion,
            {"native_exit_code": 0, "report_sha256": digest(args.report.read_bytes())},
        )
    elif mutation == "mapping":
        mapping = json.loads(args.mapping.read_bytes())
        mapping["mapping"][0]["arm"] = "empty"
        write(args.mapping, mapping)
    elif mutation.startswith("capture-"):
        if mutation == "capture-plan":
            capture = json.loads(args.capture_analysis.read_bytes())
            capture["source_artifacts"]["hindsight_plan"] = (
                "different-renderer-or-input-policy"
            )
            write(args.capture_analysis, capture)
        write(
            args.capture_completion,
            {
                "native_exit_code": 130 if mutation == "capture-native" else 0,
                "report_sha256": digest(args.capture_analysis.read_bytes()),
            },
        )
    else:
        paths = json.loads(args.readers.read_bytes())["reader"]
        p = Path(paths["report_path"])
        report = json.loads(p.read_bytes())
        report["rows"][0]["hypothesis"] = "Unrecorded improved answer"
        write(p, report)
        write(
            Path(paths["completion_path"]),
            {"native_exit_code": 0, "report_sha256": digest(p.read_bytes())},
        )
    with pytest.raises(ValueError):
        scoring.analyze(args)


def test_category_regressions_and_reader_families_are_not_hidden_in_a_pooled_score():
    details = [
        {
            "case_id": "a",
            "category": "updates",
            "answers": {
                "reader-1": {"memory_a": True, "memory_b": False, "empty": False},
                "reader-2": {"memory_a": False, "memory_b": True, "empty": False},
            },
        },
        {
            "case_id": "b",
            "category": "preferences",
            "answers": {
                "reader-1": {"memory_a": False, "memory_b": True, "empty": False},
                "reader-2": {"memory_a": False, "memory_b": True, "empty": False},
            },
        },
    ]
    report = scoring.summarize(details, {"a": "a", "b": "b"}, ["reader-1", "reader-2"])
    assert (
        report["reader-1"]["overall"]["prme_vs_hindsight"]["query_bootstrap"]["delta"]
        == 0
    )
    assert (
        report["reader-1"]["categories"]["preferences"]["prme_vs_hindsight"][
            "query_bootstrap"
        ]["delta"]
        == -1
    )
    assert (
        report["reader-2"]["overall"]["prme_vs_hindsight"]["query_bootstrap"]["delta"]
        == -1
    )


def test_judge_worker_rejects_exposed_identity_and_empty_hypotheses():
    base = {
        "id": "x",
        "question": "q",
        "reference": "r",
        "hypothesis": "h",
        "category": "abstention",
    }
    for case in [dict(base, arm="prme"), dict(base, hypothesis="")]:
        with pytest.raises(ValueError):
            judge.validate_inputs({"cases": [case]})


def test_failed_judge_request_cannot_be_replaced_by_a_successful_retry(
    tmp_path, monkeypatch
):
    from tests.test_reader_judge import response

    args, _ = pipeline(tmp_path, monkeypatch)
    truncated = response()
    truncated["done_reason"] = "length"
    service(monkeypatch, reply=truncated)
    failed_state = tmp_path / "failed-state.json"
    with pytest.raises(ValueError, match="truncated"):
        judge.run(
            args.inputs,
            args.plan,
            args.controls,
            args.calibration,
            failed_state,
            "test",
        )
    _, calls = service(monkeypatch)
    with pytest.raises(ValueError, match="failed judge study"):
        judge.run(
            args.inputs,
            args.plan,
            args.controls,
            args.calibration,
            failed_state,
            "test",
        )
    assert not calls
