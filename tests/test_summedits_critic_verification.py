import json

import pytest

from benchmarks.integrations import run_summedits_critic_verification as critic
from benchmarks.integrations import run_summedits_proof_verification as proof


def test_primary_artifacts_require_exact_hashes(tmp_path):
    result = {
        "kind": "summedits-segment-verification-result",
        "registration_sha256": "registration",
        "samples": [{"id": "case", "predicted_supported": True}],
    }
    result["result_sha256"] = proof._canonical_sha256(result)
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result))
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "registration_sha256": "registration",
                "decompositions": {},
            }
        )
    )
    registration = {
        "primary_artifacts": {
            "result_file_sha256": proof._file_sha256(result_path),
            "state_file_sha256": proof._file_sha256(state_path),
            "result_canonical_sha256": result["result_sha256"],
            "primary_registration_sha256": "registration",
            "candidate_ids": ["case"],
            "candidate_ids_sha256": proof._canonical_sha256(["case"]),
        }
    }

    loaded_result, loaded_state = critic._validate_primary_artifacts(
        registration,
        primary_result_path=result_path,
        primary_state_path=state_path,
    )

    assert loaded_result == result
    assert loaded_state["registration_sha256"] == "registration"

    result_path.write_text(result_path.read_text() + "\n")
    with pytest.raises(ValueError, match="result file"):
        critic._validate_primary_artifacts(
            registration,
            primary_result_path=result_path,
            primary_state_path=state_path,
        )


def test_quality_gates_measure_only_called_critic_integrity():
    registration = {
        "evaluation": {
            "gates": {
                "cases_evaluated_min": 2,
                "supported_precision_min": 0.5,
                "supported_recall_min": 0.5,
                "balanced_accuracy_min": 0.5,
                "false_support_rate_max": 0.5,
                "critic_reference_integrity_rate_min": 1.0,
            }
        }
    }
    samples = [
        {
            "critic_called": True,
            "critic_reference_integrity": True,
        },
        {
            "critic_called": False,
            "critic_reference_integrity": False,
        },
    ]
    metrics = {
        "precision": 1.0,
        "recall": 0.5,
        "balanced_accuracy": 0.75,
        "false_support_rate": 0.0,
    }

    gates = critic._quality_gates(registration, samples, metrics)

    assert gates["passed"] is True
    assert gates["results"]["critic_reference_integrity_rate_min"]["observed"] == 1.0
