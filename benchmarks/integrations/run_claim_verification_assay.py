"""Run a fixed local-model assay for the minimal-evidence claim verifier."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from prme import (
    ClaimEvidence,
    ClaimVerificationConfig,
    ClaimVerificationStatus,
    ClaimVerifier,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _head(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        text=True,
    ).strip()


def _validate_registration(
    registration: dict[str, Any],
    *,
    registration_path: Path,
    project_root: Path,
) -> None:
    schema_version = registration.get("schema_version")
    if isinstance(schema_version, bool) or schema_version not in {1, 2}:
        raise ValueError("registration schema_version must be 1 or 2")
    if registration.get("kind") != "claim-verification-assay-registration":
        raise ValueError("registration kind is invalid")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source is required")
    if source.get("prme_revision") != _head(project_root):
        raise ValueError("registration PRME revision does not match HEAD")

    files = source.get("files")
    if not isinstance(files, dict):
        raise ValueError("registration source files are required")
    expected = {
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "implementation_sha256": _sha256_file(
            project_root / "src/prme/retrieval/claim_verification.py"
        ),
    }
    if files != expected:
        raise ValueError("registration source-file hashes do not match")

    cases = registration.get("protocol", {}).get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("registration must contain cases")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or len(set(ids)) != len(ids):
        raise ValueError("registration case IDs must be unique")
    statuses = {item.value for item in ClaimVerificationStatus}
    for case in cases:
        if case.get("expected_status") not in statuses:
            raise ValueError(f"invalid expected status for case {case.get('id')!r}")
        evidence = case.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError(f"case {case.get('id')!r} evidence must be a list")
        for item in evidence:
            ClaimEvidence.model_validate(item)

    gates = registration.get("evaluation", {}).get("gates")
    expected_gates = (
        {
            "all_expected_statuses",
            "unsafe_supported_count_max",
            "supported_correct_min",
            "refuted_correct_min",
            "contested_correct_min",
            "incomplete_without_model_min",
        }
        if schema_version == 1
        else {
            "unsafe_supported_count_max",
            "insufficient_refuted_count_max",
            "supported_correct_min",
            "minimal_group_correct_min",
            "refuted_correct_min",
            "insufficient_correct_min",
            "contested_correct_min",
            "incomplete_without_model_min",
        }
    )
    if not isinstance(gates, dict) or set(gates) != expected_gates:
        raise ValueError("registration gates are incomplete")
    if schema_version == 1 and gates["all_expected_statuses"] is not True:
        raise ValueError("all_expected_statuses gate must be true")
    for name, value in gates.items():
        if name == "all_expected_statuses":
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"gate {name!r} must be a nonnegative integer")

    if not registration_path.is_file():
        raise ValueError("registration path must be a file")


def _gate_results(
    registration: dict[str, Any],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    gates = registration["evaluation"]["gates"]
    expected_supported = [
        sample for sample in samples if sample["expected_status"] == "supported"
    ]
    expected_refuted = [
        sample for sample in samples if sample["expected_status"] == "refuted"
    ]
    expected_contested = [
        sample for sample in samples if sample["expected_status"] == "contested"
    ]
    expected_insufficient = [
        sample for sample in samples if sample["expected_status"] == "insufficient"
    ]
    expected_incomplete = [
        sample for sample in samples if sample["expected_status"] == "incomplete"
    ]
    observed = {
        "all_expected_statuses": all(sample["matched"] for sample in samples),
        "unsafe_supported_count_max": sum(
            sample["assessment"]["status"] == "supported"
            for sample in samples
            if sample["expected_status"] != "supported"
        ),
        "supported_correct_min": sum(
            sample["matched"] for sample in expected_supported
        ),
        "refuted_correct_min": sum(sample["matched"] for sample in expected_refuted),
        "contested_correct_min": sum(
            sample["matched"] for sample in expected_contested
        ),
        "incomplete_without_model_min": sum(
            sample["matched"] and not sample["assessment"]["model_called"]
            for sample in expected_incomplete
        ),
        "insufficient_refuted_count_max": sum(
            sample["assessment"]["status"] == "refuted"
            for sample in expected_insufficient
        ),
        "minimal_group_correct_min": sum(
            sample["matched"]
            for sample in samples
            if sample["category"] == "minimal_group"
        ),
        "insufficient_correct_min": sum(
            sample["matched"] for sample in expected_insufficient
        ),
    }
    results: dict[str, Any] = {}
    for name, requirement in gates.items():
        value = observed[name]
        if name == "all_expected_statuses":
            passed = value is requirement
        elif name.endswith("_max"):
            passed = value <= requirement
        else:
            passed = value >= requirement
        results[name] = {
            "required": requirement,
            "observed": value,
            "passed": passed,
        }
    return {
        "passed": all(item["passed"] for item in results.values()),
        "results": results,
    }


async def _execute(registration: dict[str, Any]) -> list[dict[str, Any]]:
    model = registration["model"]
    config = ClaimVerificationConfig(
        model=model["name"],
        revision=model["revision"],
        **registration["protocol"]["config"],
    )
    verifier = ClaimVerifier(config)
    samples: list[dict[str, Any]] = []
    for case in registration["protocol"]["cases"]:
        evidence = [ClaimEvidence.model_validate(item) for item in case["evidence"]]
        assessment = await verifier.verify(
            case["claim"],
            evidence,
            requires_complete_set=case.get("requires_complete_set", False),
        )
        if (
            assessment.model_called
            and assessment.resolved_revision != model["revision"]
        ):
            raise ValueError("resolved model revision does not match registration")
        samples.append(
            {
                "id": case["id"],
                "category": case["category"],
                "expected_status": case["expected_status"],
                "matched": assessment.status.value == case["expected_status"],
                "assessment": assessment.model_dump(mode="json"),
            }
        )
    return samples


async def run(
    *,
    registration_path: Path,
    project_root: Path,
    output_path: Path,
) -> bool:
    registration = json.loads(registration_path.read_text())
    _validate_registration(
        registration,
        registration_path=registration_path,
        project_root=project_root,
    )
    samples = await _execute(registration)
    quality_gates = _gate_results(registration, samples)
    result = {
        "schema_version": registration["schema_version"],
        "kind": "claim-verification-assay-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration_path.resolve()),
        "registration_sha256": _sha256_file(registration_path),
        "source": registration["source"],
        "model": registration["model"],
        "protocol": {
            key: value
            for key, value in registration["protocol"].items()
            if key != "cases"
        },
        "summary": {
            "cases": len(samples),
            "matched": sum(sample["matched"] for sample in samples),
            "expected_statuses": dict(
                sorted(Counter(sample["expected_status"] for sample in samples).items())
            ),
            "observed_statuses": dict(
                sorted(
                    Counter(
                        sample["assessment"]["status"] for sample in samples
                    ).items()
                )
            ),
        },
        "quality_gates": quality_gates,
        "samples": samples,
    }
    result["result_sha256"] = _canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return bool(quality_gates["passed"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    passed = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            project_root=args.project_root.resolve(),
            output_path=args.output.resolve(),
        )
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
