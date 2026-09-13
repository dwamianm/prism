"""Reader evidence stays exact, label-free, complete and immutable on failure."""

from copy import deepcopy
import json

import pytest
import tiktoken

from benchmarks.diagnostics import public_context_reader as reader
from tests.test_packing_reader import fake_service


def fixture_inputs(tmp_path):
    def save(name, value):
        path = tmp_path / (name + ".json")
        reader.write(path, value)
        return path

    inputs = save(
        "inputs",
        {
            "cases": [
                {
                    "case_id": "opaque-0",
                    "question": "Which season?",
                    "question_date": "2026-01-01T00:00:00+00:00",
                    "turns": [{"content": "SOURCE_NOT_RETURNED_CANARY"}],
                }
            ]
        },
    )
    captures, products = {}, {}
    for product, text in [
        ("prme", "Only in summer."),
        ("hindsight", "Only in summer."),
    ]:
        directory = tmp_path / product
        directory.mkdir()
        context = {
            "context": text,
            "sha256": reader.digest(text.encode()),
            "tokens": len(tiktoken.get_encoding("cl100k_base").encode(text)),
        }
        reader.write(
            directory / "opaque-0.json",
            {
                "case_id": "opaque-0",
                "contexts": {"4096": context},
                "unreturned_document": "DOCUMENT_RECONSTRUCTION_CANARY",
            },
        )
        captures[product] = directory
        products[product] = {"4096": {"actual": {"context_sha256": context["sha256"]}}}
    analysis = save(
        "analysis",
        {
            "complete": True,
            "verification_passed": True,
            "questions": 1,
            "inputs_sha256": reader.digest(inputs.read_bytes()),
            "source_artifacts": {
                "prme_plan": "registered-p",
                "hindsight_plan": "registered-h",
            },
            "details": [
                {
                    "case_id": "opaque-0",
                    "products": products,
                    "answer": "GOLD_LABEL_CANARY",
                    "category": "CATEGORY_CANARY",
                }
            ],
        },
    )
    completion = save(
        "completion",
        {"native_exit_code": 0, "report_sha256": reader.digest(analysis.read_bytes())},
    )
    return inputs, analysis, completion, captures


def prepared_files(tmp_path, monkeypatch):
    calls = fake_service(monkeypatch)
    values = fixture_inputs(tmp_path)
    prepared = reader.prepare(*values)
    path = tmp_path / "prepared.json"
    reader.write(path, prepared)
    plan_path = tmp_path / "plan.json"
    reader.write(
        plan_path,
        {
            "prepared_sha256": reader.digest(path.read_bytes()),
            "case_ids": ["opaque-0"],
            "reader": reader.declaration("reader", "test"),
        },
    )
    return prepared, path, plan_path, calls


def test_export_and_payload_do_not_contain_labels_or_reconstructed_documents(tmp_path):
    prepared = reader.prepare(*fixture_inputs(tmp_path))
    assert b"CANARY" not in reader.canonical(prepared)
    for arm in reader.ARMS:
        body = reader.payload(
            prepared["rows"][0],
            arm,
            {
                "model": "reader",
                "system_prompt": reader.GENERATION_SYSTEM_PROMPT,
            },
        )
        assert b"CANARY" not in reader.canonical(body)
        assert b"memory_a" not in reader.canonical(body)
    assert prepared["rows"][0]["contexts"]["empty"]["context"] == ""


@pytest.mark.parametrize("mutation", ["native", "hash", "context", "identity"])
def test_preparation_rejects_unverified_or_changed_artifacts(tmp_path, mutation):
    inputs, analysis, completion, captures = fixture_inputs(tmp_path)
    if mutation == "native":
        reader.write(
            completion,
            {
                "native_exit_code": 130,
                "report_sha256": reader.digest(analysis.read_bytes()),
            },
        )
    elif mutation == "hash":
        analysis.write_bytes(analysis.read_bytes() + b" ")
    else:
        path = captures["hindsight"] / "opaque-0.json"
        snapshot = json.loads(path.read_bytes())
        if mutation == "context":
            snapshot["contexts"]["4096"]["context"] += " Extra reconstructed text."
        else:
            snapshot["case_id"] = "other"
        reader.write(path, snapshot)
    with pytest.raises(ValueError):
        reader.prepare(inputs, analysis, completion, captures)


@pytest.mark.parametrize("mutation", ["label", "budget", "date", "arm", "empty"])
def test_worker_rejects_non_neutral_or_invalid_inputs(tmp_path, mutation):
    prepared = reader.prepare(*fixture_inputs(tmp_path))
    row = prepared["rows"][0]
    if mutation == "label":
        row["answer"] = "Gold"
    elif mutation == "budget":
        prepared["budget"] = 1
    elif mutation == "date":
        row["question_date"] = "2026-01-01"
    elif mutation == "arm":
        row["contexts"]["unexpected"] = deepcopy(row["contexts"]["empty"])
    else:
        row["contexts"]["empty"] = deepcopy(row["contexts"]["memory_a"])
    with pytest.raises(ValueError):
        reader.validate_prepared(prepared)


def test_identical_prompts_reuse_responses_and_completed_resume_has_no_calls(
    tmp_path, monkeypatch
):
    _, path, plan, calls = prepared_files(tmp_path, monkeypatch)
    state = tmp_path / "state.json"
    result = reader.run(path, plan, state, "test")
    assert result["questions"] == 1 and result["logical_predictions"] == 3
    assert result["unique_generations"] == len(calls) == 2
    assert reader.run(path, plan, state, "test") == result
    assert len(calls) == 2
    assert b"CANARY" not in reader.canonical(calls)


def test_truncated_response_is_retained_and_cannot_be_replaced_by_retry(
    tmp_path, monkeypatch
):
    _, path, plan, _ = prepared_files(tmp_path, monkeypatch)
    fake_service(monkeypatch, truncate=True)
    state = tmp_path / "state.json"
    with pytest.raises(ValueError, match="truncated"):
        reader.run(path, plan, state, "test")
    assert (
        json.loads(state.read_bytes())["failed_attempts"][0]["response"]["done_reason"]
        == "length"
    )
    calls = fake_service(monkeypatch)
    with pytest.raises(ValueError, match="failed attempts"):
        reader.run(path, plan, state, "test")
    assert not calls
