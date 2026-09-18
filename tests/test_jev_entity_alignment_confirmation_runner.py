from __future__ import annotations

import json

import pytest

from benchmarks.integrations import run_jev_entity_alignment_confirmation as runner


def test_upstream_requires_passing_frozen_arm(tmp_path) -> None:
    registration_path = tmp_path / "registration.json"
    result_path = tmp_path / "result.json"
    registration = {
        "kind": "jev-entity-alignment-development-registration",
        "protocol": {"decision_arms": {runner.SELECTED_ARM: "frozen"}},
    }
    registration_path.write_text(json.dumps(registration))
    result = {
        "kind": "jev-entity-alignment-development-result",
        "passed": True,
        "selected_arm": runner.SELECTED_ARM,
        "registration_sha256": runner.common._sha256_file(registration_path),
        "protocol": registration["protocol"],
        "result_sha256": "a" * 64,
    }
    result_path.write_text(json.dumps(result))

    assert runner._upstream(registration_path, result_path) == (registration, result)

    result["selected_arm"] = "another_arm"
    result_path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="does not authorize"):
        runner._upstream(registration_path, result_path)


def test_confirmation_gate_is_frozen_to_development_boundary() -> None:
    gates = runner._evaluation(182)["gates"]

    assert gates == {
        "pairs_evaluated_min": 182,
        "response_validity_min": 182,
        "merge_precision_min": 0.98,
        "merge_recall_min": 0.5,
        "false_merges_max": 1,
        "recall_gain_over_current_min": 0.25,
    }
