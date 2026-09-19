"""Compare call-local typed-value guidance with its matched PRME control."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.diagnostics.memoryarena_value_binding_audit import (
    audit_value_bindings,
    _load_registered_cohort,
)
from benchmarks.integrations import run_memoryarena_travel as runner


CONTROL_ARM = "prme_no_result_guidance"
CANDIDATE_ARM = "prme"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_confirmation_audits(
    *,
    control: dict[str, Any],
    candidate: dict[str, Any],
    paired_result: dict[str, Any],
    decision_rules: dict[str, Any],
) -> dict[str, Any]:
    """Apply preregistered broad and targeted confirmation rules."""
    control_values = control["qualified_changed_values"]
    candidate_values = candidate["qualified_changed_values"]
    if control_values["total"] != candidate_values["total"]:
        raise ValueError("Confirmation arms have different targeted denominators")
    delta = candidate_values["passed"] - control_values["passed"]
    maximum_executed = decision_rules[
        "maximum_executed_qualified_arguments"
    ]
    minimum_delta = decision_rules[
        "minimum_candidate_minus_control_exact_qualified_values"
    ]
    gates = {
        "broad_result_passed": paired_result.get("gates", {}).get("passed") is True,
        "targeted_audit_complete": (
            control["coverage"]["complete"] is True
            and candidate["coverage"]["complete"] is True
            and control["tool_boundary_resolution"]["trace_coverage_complete"] is True
            and candidate["tool_boundary_resolution"]["trace_coverage_complete"] is True
        ),
        "control_execution_safe": control["tool_boundary_resolution"][
            "executed_qualified_argument_count"
        ]
        <= maximum_executed,
        "candidate_execution_safe": candidate["tool_boundary_resolution"][
            "executed_qualified_argument_count"
        ]
        <= maximum_executed,
        "candidate_output_gain": delta >= minimum_delta,
    }
    return {
        "comparison": {
            "qualified_value_total": candidate_values["total"],
            "control_exact_qualified_values": control_values["passed"],
            "candidate_exact_qualified_values": candidate_values["passed"],
            "candidate_minus_control_exact_qualified_values": delta,
        },
        "gates": {**gates, "passed": all(gates.values())},
    }


def run(
    *,
    upstream: Path,
    registration_path: Path,
    checkpoint_path: Path,
    paired_result_path: Path,
) -> dict[str, Any]:
    registration_bytes = registration_path.read_bytes()
    registration_sha256 = _digest(registration_bytes)
    registration, cohort = _load_registered_cohort(
        registration_path, upstream=upstream
    )
    arms = tuple(registration["protocol"].get("arms") or ())
    if arms != runner.CONFIRMATION_ARMS:
        raise ValueError("Registration is not a matched typed-value confirmation")
    if registration["cohort"].get("role") != "confirmation":
        raise ValueError("Registered cohort is not a confirmation cohort")

    checkpoint_bytes = checkpoint_path.read_bytes()
    checkpoints = [
        json.loads(line)
        for line in checkpoint_bytes.decode().splitlines()
        if line.strip()
    ]
    control = audit_value_bindings(
        cohort,
        checkpoints,
        registration_sha256=registration_sha256,
        arms=arms,
        target_arm=CONTROL_ARM,
        expect_result_guidance=False,
    )
    candidate = audit_value_bindings(
        cohort,
        checkpoints,
        registration_sha256=registration_sha256,
        arms=arms,
        target_arm=CANDIDATE_ARM,
        expect_result_guidance=True,
    )

    paired_result_bytes = paired_result_path.read_bytes()
    paired_result = json.loads(paired_result_bytes)
    if paired_result.get("registration_sha256") != registration_sha256:
        raise ValueError("Paired result registration differs")
    comparison = compare_confirmation_audits(
        control=control,
        candidate=candidate,
        paired_result=paired_result,
        decision_rules=registration["evaluation"]["decision_rules"],
    )
    return {
        "schema_version": 1,
        "kind": "memoryarena-tool-boundary-value-confirmation-audit",
        "registration_sha256": registration_sha256,
        "coverage": candidate["coverage"],
        "arms": {CONTROL_ARM: control, CANDIDATE_ARM: candidate},
        **comparison,
        "evidence": {
            "registration_file": registration_path.name,
            "registration_sha256": registration_sha256,
            "person_checkpoints_file": checkpoint_path.name,
            "person_checkpoints_sha256": _digest(checkpoint_bytes),
            "paired_result_file": paired_result_path.name,
            "paired_result_sha256": _digest(paired_result_bytes),
            "audit_source_sha256": _digest(Path(__file__).read_bytes()),
            "registered_prme_revision": registration["source"]["prme_revision"],
            "upstream_revision": runner.UPSTREAM_REVISION,
        },
        "limits": [
            "Exact normalized-string comparison; it is not a semantic itinerary-validity judge.",
            "The candidate and control share retrieval, resolution, cohort and model route.",
            "One generation per arm does not estimate model variance.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--paired-result", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(
        upstream=args.upstream.resolve(),
        registration_path=args.registration.resolve(),
        checkpoint_path=args.checkpoints.resolve(),
        paired_result_path=args.paired_result.resolve(),
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()
