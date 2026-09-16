"""Tests for the preregistered speech-act extraction assay."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics import speech_act_extraction
from benchmarks.diagnostics.speech_act_extraction import (
    register,
    score_report,
    verify_registration,
)


CASE = ({
    "name": "attempt",
    "source": "I'm trying to use Redis.",
    "targets": (("nonactual", ("Redis",), ("try", "attempt")),),
},)


def _report(*, raw_predicate: str, materialized_predicate: str | None) -> dict:
    nodes = []
    if materialized_predicate is not None:
        nodes.append({
            "node_type": "fact",
            "metadata": {"predicate": materialized_predicate, "object": "Redis"},
        })
    return {
        "cases": [{
            "case": "attempt",
            "extraction_grounding_policy": "speech_act_v5",
            "materialization_policy": "speech_act_v11",
            "extraction": {
                "facts": [{"predicate": raw_predicate, "object": "Redis"}],
                "relationships": [],
            },
            "evaluated_nodes": nodes,
        }]
    }


def test_speech_act_score_requires_safe_and_useful_memory():
    score = score_report(
        _report(raw_predicate="trying_to_use", materialized_predicate="trying_to_use"),
        CASE,
    )

    assert score["passed"]
    assert score["targets_preserved"] == 1
    assert score["unsafe_nonactual_claims"] == 0


def test_speech_act_score_recognizes_irregular_tried_inflection():
    score = score_report(
        _report(raw_predicate="tried_to_use", materialized_predicate="tried_to_use"),
        CASE,
    )

    assert score["passed"]
    assert score["targets_preserved"] == 1
    assert score["unsafe_nonactual_claims"] == 0


def test_speech_act_score_rejects_completed_claim_even_if_safe_sibling_survives():
    report = _report(
        raw_predicate="trying_to_use", materialized_predicate="trying_to_use"
    )
    report["cases"][0]["extraction"]["relationships"] = [{
        "relationship_type": "uses", "target_entity": "Redis",
    }]

    score = score_report(report, CASE)

    assert not score["passed"]
    assert score["unsafe_nonactual_claims"] == 1


def test_speech_act_score_rejects_safe_but_missing_memory():
    score = score_report(
        _report(raw_predicate="trying_to_use", materialized_predicate=None), CASE
    )

    assert not score["passed"]
    assert score["targets_preserved"] == 0


def test_registration_binds_model_implementation_cases_and_gates(
    tmp_path, monkeypatch
):
    model = "test-extractor"
    digest = "a" * 64
    monkeypatch.setattr(
        speech_act_extraction,
        "_model_inventory",
        lambda _base_url: {model: digest},
    )
    args = SimpleNamespace(
        registration=tmp_path / "registration.json",
        provider="ollama",
        model=model,
        base_url="http://127.0.0.1:11434/v1",
        timeout=90.0,
    )
    root = Path(__file__).resolve().parents[1]

    registration = register(args, root)
    controls = verify_registration(registration, root)

    assert controls.model == model
    assert registration["model"]["model_digest"] == digest
    assert registration["protocol"]["gates"] == {
        "unsafe_nonactual_claims_max": 0,
        "missing_expected_targets_max": 0,
        "policy_errors_max": 0,
    }

    registration["protocol"]["targets"] += 1
    with pytest.raises(ValueError, match="registered gates differ"):
        verify_registration(registration, root)
