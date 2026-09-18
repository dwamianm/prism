from __future__ import annotations

import json
from pathlib import Path

from benchmarks.diagnostics import llm_aggrefact_typed_tool_recovery as subject
from benchmarks.integrations import run_llm_aggrefact_factcg as factcg


def _write_result(path: Path, registration_sha256: str) -> None:
    value = {
        "schema_version": 1,
        "kind": "llm-aggrefact-typed-reference-invalid-result",
        "registration_sha256": registration_sha256,
        "status": "aborted_gate_mathematically_impossible",
        "development": {
            "observed_cases": 2,
            "observed_ids": ["a", "b"],
            "observed_identity_sha256": factcg._canonical_sha256(["a", "b"]),
            "reference_integrity_failures": 1,
        },
        "test": {"accessed": False, "samples": []},
    }
    value["result_sha256"] = factcg._canonical_sha256(value)
    path.write_text(json.dumps(value))


def test_failure_subset_is_bound_to_registered_result_and_private_state(
    tmp_path: Path,
) -> None:
    registration = tmp_path / "registration.json"
    registration.write_text("{}")
    registration_sha256 = factcg._sha256_file(registration)
    result = tmp_path / "result.json"
    _write_result(result, registration_sha256)
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "registration_sha256": registration_sha256,
                "samples": {
                    "a": {"reference_integrity": True},
                    "b": {"reference_integrity": False},
                },
            }
        )
    )

    failures, loaded_result, _loaded_state = subject._failure_ids(
        registration_path=registration,
        result_path=result,
        prior_state_path=state,
    )

    assert failures == ("b",)
    assert loaded_result["test"]["accessed"] is False


def test_strict_validation_rejects_extra_tool_fields() -> None:
    item = {
        "claim": "Maya moved to Rome",
        "evidence_segments": [("E0001", "Maya moved to Rome")],
    }
    arguments = {
        "atoms": [
            {
                "claim": {"start": "C0001", "end": "C0004"},
                "subject": {"start": "C0001", "end": "C0001"},
                "relation": {"start": "C0002", "end": "C0003"},
                "object": {"start": "C0004", "end": "C0004"},
                "qualifiers": [],
                "status": "supported",
                "evidence": [
                    {
                        "evidence_id": "E0001",
                        "subject": "aligned",
                        "relation": "aligned",
                        "object": "aligned",
                        "qualifiers": "aligned",
                    }
                ],
                "invented": True,
            }
        ]
    }

    valid, errors = subject._strict_validate(item, arguments)

    assert valid is False
    assert errors == ("tool_arguments_contain_unrecognized_fields",)


def test_cli_has_no_test_dataset_argument() -> None:
    actions = {action.dest for action in subject._parser()._actions}

    assert "dev" in actions
    assert "test" not in actions
