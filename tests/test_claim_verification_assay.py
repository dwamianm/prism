import json
from pathlib import Path

import pytest

from benchmarks.integrations import run_claim_verification_assay as assay


def _sample(case_id: str, expected: str, observed: str, *, model_called: bool = True):
    return {
        "id": case_id,
        "category": "test",
        "expected_status": expected,
        "matched": expected == observed,
        "assessment": {"status": observed, "model_called": model_called},
    }


def test_machine_gates_separate_safety_and_status_coverage():
    registration = {
        "evaluation": {
            "gates": {
                "all_expected_statuses": True,
                "unsafe_supported_count_max": 0,
                "supported_correct_min": 1,
                "refuted_correct_min": 1,
                "contested_correct_min": 1,
                "incomplete_without_model_min": 1,
            }
        }
    }
    samples = [
        _sample("s", "supported", "supported"),
        _sample("r", "refuted", "refuted"),
        _sample("c", "contested", "contested"),
        _sample("i", "incomplete", "incomplete", model_called=False),
    ]

    result = assay._gate_results(registration, samples)

    assert result["passed"] is True
    assert result["results"]["unsafe_supported_count_max"]["observed"] == 0


def test_machine_gates_reject_unsafe_support():
    registration = {
        "evaluation": {
            "gates": {
                "all_expected_statuses": True,
                "unsafe_supported_count_max": 0,
                "supported_correct_min": 0,
                "refuted_correct_min": 0,
                "contested_correct_min": 0,
                "incomplete_without_model_min": 0,
            }
        }
    }

    result = assay._gate_results(
        registration,
        [_sample("unsafe", "insufficient", "supported")],
    )

    assert result["passed"] is False
    assert result["results"]["unsafe_supported_count_max"]["observed"] == 1


def test_registration_rejects_duplicate_case_ids(tmp_path: Path, monkeypatch):
    registration_path = tmp_path / "registration.json"
    registration_path.write_text("{}")
    project_root = Path(__file__).parents[1]
    case = {
        "id": "duplicate",
        "expected_status": "supported",
        "evidence": [],
    }
    registration = {
        "schema_version": 1,
        "kind": "claim-verification-assay-registration",
        "source": {
            "prme_revision": "revision",
            "files": {
                "runner_sha256": assay._sha256_file(Path(assay.__file__).resolve()),
                "implementation_sha256": assay._sha256_file(
                    project_root / "src/prme/retrieval/claim_verification.py"
                ),
            },
        },
        "protocol": {"cases": [case, case]},
        "evaluation": {"gates": {}},
    }
    monkeypatch.setattr(assay, "_head", lambda _root: "revision")

    with pytest.raises(ValueError, match="case IDs must be unique"):
        assay._validate_registration(
            registration,
            registration_path=registration_path,
            project_root=project_root,
        )


def test_canonical_digest_ignores_mapping_order():
    assert assay._canonical_sha256({"a": 1, "b": 2}) == assay._canonical_sha256(
        json.loads('{"b":2,"a":1}')
    )
