"""Evaluate the guarded claim verifier on WiCE's oracle-retrieval test set.

The runner deliberately consumes an external, revision-pinned WiCE checkout. It
does not copy claims or evidence into the result artifact. WiCE oracle chunks
simulate perfect retrieval; this assay therefore measures verification after
retrieval, not PRME retrieval quality.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any
from uuid import UUID, uuid5

from prme import ClaimEvidence, ClaimVerificationConfig, ClaimVerifier


_EVIDENCE_NAMESPACE = UUID("5211a7d2-cf39-4c7c-8bbc-4f762448d294")
_LABELS = {"supported", "partially_supported", "not_supported"}


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


def _git_head(path: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        text=True,
    ).strip()


def _git_clean(path: Path) -> bool:
    return not subprocess.check_output(
        ["git", "status", "--porcelain"],
        cwd=path,
        text=True,
    ).strip()


def _git_is_ancestor(revision: str, path: Path) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _finite_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be between zero and one")
    return result


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid dataset JSON on line {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"dataset line {line_number} must be an object")
        if row.get("label") not in _LABELS:
            raise ValueError(f"dataset line {line_number} has an invalid label")
        claim = row.get("claim")
        evidence = row.get("evidence")
        metadata = row.get("meta")
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError(f"dataset line {line_number} has an invalid claim")
        if (
            not isinstance(evidence, list)
            or any(not isinstance(item, str) for item in evidence)
            or not any(item.strip() for item in evidence)
        ):
            raise ValueError(f"dataset line {line_number} has invalid evidence")
        if not isinstance(metadata, dict):
            raise ValueError(f"dataset line {line_number} has invalid metadata")
        case_id = metadata.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"dataset line {line_number} has an invalid case ID")
        rows.append(row)
    if not rows:
        raise ValueError("dataset must contain at least one row")
    return rows


def _group_dataset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        case_id = row["meta"]["id"]
        if case_id not in grouped:
            order.append(case_id)
        grouped[case_id].append(row)

    cases: list[dict[str, Any]] = []
    for case_id in order:
        variants = grouped[case_id]
        labels = {row["label"] for row in variants}
        claims = {row["claim"] for row in variants}
        if len(labels) != 1 or len(claims) != 1:
            raise ValueError(f"oracle variants disagree for case {case_id!r}")
        cases.append(
            {
                "id": case_id,
                "label": variants[0]["label"],
                "claim": variants[0]["claim"],
                "variants": variants,
            }
        )
    return cases


def _validate_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    dataset_root: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    if registration.get("schema_version") != 1:
        raise ValueError("registration schema_version must be 1")
    if registration.get("kind") != "wice-claim-verification-registration":
        raise ValueError("registration kind is invalid")

    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source is required")
    prme_revision = source.get("prme_revision")
    if not isinstance(prme_revision, str) or not _git_is_ancestor(
        prme_revision, project_root
    ):
        raise ValueError("registered PRME revision is not an ancestor of HEAD")
    expected_files = {
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "implementation_sha256": _sha256_file(
            project_root / "src/prme/retrieval/claim_verification.py"
        ),
    }
    if source.get("files") != expected_files:
        raise ValueError("registration source-file hashes do not match")

    dataset = registration.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("registration dataset is required")
    if not _git_clean(dataset_root):
        raise ValueError("WiCE checkout must be clean")
    if dataset.get("revision") != _git_head(dataset_root):
        raise ValueError("WiCE checkout revision does not match registration")
    relative_path = dataset.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("dataset relative_path is required")
    dataset_path = (dataset_root / relative_path).resolve()
    if not dataset_path.is_relative_to(dataset_root.resolve()):
        raise ValueError("dataset path escapes the WiCE checkout")
    if not dataset_path.is_file():
        raise ValueError("registered dataset file does not exist")
    if dataset.get("sha256") != _sha256_file(dataset_path):
        raise ValueError("WiCE dataset hash does not match registration")

    rows = _load_dataset(dataset_path)
    cases = _group_dataset(rows)
    observed_labels = dict(sorted(Counter(case["label"] for case in cases).items()))
    expected_shape = dataset.get("expected_shape")
    observed_shape = {
        "rows": len(rows),
        "claims": len(cases),
        "labels": observed_labels,
    }
    if expected_shape != observed_shape:
        raise ValueError("WiCE dataset shape does not match registration")

    model = registration.get("model")
    if not isinstance(model, dict):
        raise ValueError("registration model is required")
    config = model.get("config")
    if not isinstance(config, dict):
        raise ValueError("registration model config is required")
    ClaimVerificationConfig.model_validate(config)

    expected_protocol = {
        "task": "oracle_retrieval_entailment",
        "evidence_unit": "oracle_chunk",
        "oracle_aggregation": "product_decision_across_registered_variants",
        "empty_evidence_sentences": "omit",
        "positive_label": "supported",
        "negative_labels": ["partially_supported", "not_supported"],
        "emit_source_text": False,
    }
    if registration.get("protocol") != expected_protocol:
        raise ValueError("registration protocol is invalid")

    gates = registration.get("evaluation", {}).get("gates")
    expected_gates = {
        "claims_evaluated_min",
        "supported_precision_min",
        "supported_recall_min",
        "balanced_accuracy_min",
        "false_support_rate_max",
        "not_supported_false_support_rate_max",
    }
    if not isinstance(gates, dict) or set(gates) != expected_gates:
        raise ValueError("registration gates are incomplete")
    if isinstance(gates["claims_evaluated_min"], bool) or not isinstance(
        gates["claims_evaluated_min"], int
    ):
        raise ValueError("claims_evaluated_min must be an integer")
    if gates["claims_evaluated_min"] < 1:
        raise ValueError("claims_evaluated_min must be positive")
    for name, value in gates.items():
        if name != "claims_evaluated_min":
            _finite_number(value, name=name)
    return dataset_path, cases


def _division(numerator: int | float, denominator: int | float) -> float:
    return numerator / denominator if denominator else 0.0


def _binary_metrics(
    samples: list[dict[str, Any]], *, prediction: str
) -> dict[str, Any]:
    tp = sum(
        sample["label"] == "supported" and sample[prediction] for sample in samples
    )
    fn = sum(
        sample["label"] == "supported" and not sample[prediction] for sample in samples
    )
    fp = sum(
        sample["label"] != "supported" and sample[prediction] for sample in samples
    )
    tn = sum(
        sample["label"] != "supported" and not sample[prediction] for sample in samples
    )
    precision = _division(tp, tp + fp)
    recall = _division(tp, tp + fn)
    specificity = _division(tn, tn + fp)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "accuracy": _division(tp + tn, len(samples)),
        "balanced_accuracy": (recall + specificity) / 2,
        "precision": precision,
        "recall": recall,
        "f1": _division(2 * precision * recall, precision + recall),
        "false_support_rate": _division(fp, fp + tn),
    }


def _gate_results(
    registration: dict[str, Any],
    samples: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    gates = registration["evaluation"]["gates"]
    not_supported = [sample for sample in samples if sample["label"] == "not_supported"]
    observed = {
        "claims_evaluated_min": len(samples),
        "supported_precision_min": metrics["precision"],
        "supported_recall_min": metrics["recall"],
        "balanced_accuracy_min": metrics["balanced_accuracy"],
        "false_support_rate_max": metrics["false_support_rate"],
        "not_supported_false_support_rate_max": _division(
            sum(sample["product_supported"] for sample in not_supported),
            len(not_supported),
        ),
    }
    results: dict[str, Any] = {}
    for name, requirement in gates.items():
        value = observed[name]
        passed = value <= requirement if name.endswith("_max") else value >= requirement
        results[name] = {
            "required": requirement,
            "observed": value,
            "passed": passed,
        }
    return {
        "passed": all(item["passed"] for item in results.values()),
        "results": results,
    }


async def _execute(
    cases: list[dict[str, Any]],
    *,
    config: ClaimVerificationConfig,
) -> list[dict[str, Any]]:
    verifier = ClaimVerifier(config)
    samples: list[dict[str, Any]] = []
    for case in cases:
        evidence = [
            ClaimEvidence(
                reference=f"{case['id']}:oracle:{index}",
                memory_id=uuid5(
                    _EVIDENCE_NAMESPACE,
                    f"{case['id']}:{index}",
                ),
                text="\n".join(item for item in variant["evidence"] if item.strip()),
            )
            for index, variant in enumerate(case["variants"], start=1)
        ]
        assessment = await verifier.verify(case["claim"], evidence)
        if assessment.resolved_revision != config.revision:
            raise ValueError("resolved model revision does not match registration")
        raw_supported = any(
            score.entailment >= config.entailment_threshold
            for score in assessment.group_scores
        )
        samples.append(
            {
                "id": case["id"],
                "label": case["label"],
                "oracle_variants": len(case["variants"]),
                "product_status": assessment.status.value,
                "product_supported": assessment.status.value == "supported",
                "raw_supported": raw_supported,
                "limitations": list(assessment.limitations),
                "evaluation_id": assessment.evaluation_id,
                "result_sha256": assessment.result_sha256,
                "group_scores": [
                    score.model_dump(mode="json") for score in assessment.group_scores
                ],
            }
        )
    return samples


async def run(
    *,
    registration_path: Path,
    project_root: Path,
    dataset_root: Path,
    output_path: Path,
) -> bool:
    registration = json.loads(registration_path.read_text())
    dataset_path, cases = _validate_registration(
        registration,
        project_root=project_root,
        dataset_root=dataset_root,
    )
    config = ClaimVerificationConfig.model_validate(registration["model"]["config"])
    samples = await _execute(cases, config=config)
    product_metrics = _binary_metrics(samples, prediction="product_supported")
    raw_metrics = _binary_metrics(samples, prediction="raw_supported")
    quality_gates = _gate_results(
        registration,
        samples,
        product_metrics,
    )
    result = {
        "schema_version": 1,
        "kind": "wice-claim-verification-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration_path.resolve()),
        "registration_sha256": _sha256_file(registration_path),
        "source": registration["source"],
        "dataset": {
            **registration["dataset"],
            "resolved_path": str(dataset_path),
        },
        "model": registration["model"],
        "protocol": registration["protocol"],
        "summary": {
            "claims": len(samples),
            "labels": dict(sorted(Counter(item["label"] for item in samples).items())),
            "product_statuses": dict(
                sorted(Counter(item["product_status"] for item in samples).items())
            ),
            "product_metrics": product_metrics,
            "raw_threshold_metrics": raw_metrics,
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
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    passed = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            project_root=args.project_root.resolve(),
            dataset_root=args.dataset_root.resolve(),
            output_path=args.output.resolve(),
        )
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
