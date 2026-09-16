"""Evaluate all-subclaim grounding on WiCE's human claim decomposition."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from benchmarks.integrations import run_wice_claim_verification as wice
from benchmarks.integrations import run_wice_grounding_model as grounding


_SUBCLAIM_ID_RE = re.compile(r"^(.+)-(\d+)$")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _load_bound_dataset(
    spec: dict[str, Any],
    *,
    dataset_root: Path,
) -> list[dict[str, Any]]:
    relative_path = spec.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("dataset relative_path is required")
    path = (dataset_root / relative_path).resolve()
    if not path.is_relative_to(dataset_root.resolve()):
        raise ValueError("dataset path escapes the WiCE checkout")
    if not path.is_file() or wice._sha256_file(path) != spec.get("sha256"):
        raise ValueError("dataset file does not match registration")
    rows = wice._load_dataset(path)
    cases = wice._group_dataset(rows)
    observed_shape = {
        "rows": len(rows),
        "claims": len(cases),
        "labels": dict(sorted(Counter(case["label"] for case in cases).items())),
    }
    if observed_shape != spec.get("expected_shape"):
        raise ValueError("dataset shape does not match registration")
    return cases


def _validate_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    dataset_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if registration.get("schema_version") != 1:
        raise ValueError("registration schema_version must be 1")
    if registration.get("kind") != "wice-atomic-grounding-registration":
        raise ValueError("registration kind is invalid")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source is required")
    revision = source.get("prme_revision")
    if not isinstance(revision, str) or not wice._git_is_ancestor(
        revision, project_root
    ):
        raise ValueError("registered PRME revision is not an ancestor of HEAD")
    expected_files = {
        "runner_sha256": wice._sha256_file(Path(__file__).resolve()),
        "grounding_runner_sha256": wice._sha256_file(
            Path(grounding.__file__).resolve()
        ),
    }
    if source.get("files") != expected_files:
        raise ValueError("registration source-file hashes do not match")

    dataset = registration.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("registration dataset is required")
    if not wice._git_clean(dataset_root):
        raise ValueError("WiCE checkout must be clean")
    if dataset.get("revision") != wice._git_head(dataset_root):
        raise ValueError("WiCE checkout revision does not match registration")
    parent_spec = dataset.get("parent_claims")
    subclaim_spec = dataset.get("subclaims")
    if not isinstance(parent_spec, dict) or not isinstance(subclaim_spec, dict):
        raise ValueError("parent and subclaim dataset bindings are required")
    parent_cases = _load_bound_dataset(parent_spec, dataset_root=dataset_root)
    subclaim_cases = _load_bound_dataset(subclaim_spec, dataset_root=dataset_root)

    parent_ids = {case["id"] for case in parent_cases}
    subclaim_parent_ids: set[str] = set()
    for case in subclaim_cases:
        match = _SUBCLAIM_ID_RE.fullmatch(case["id"])
        if match is None:
            raise ValueError(f"invalid subclaim ID: {case['id']!r}")
        subclaim_parent_ids.add(match.group(1))
    if parent_ids != subclaim_parent_ids:
        raise ValueError("subclaims do not cover the exact parent cohort")

    model = registration.get("model")
    expected_model_fields = {
        "name",
        "revision",
        "license",
        "weights_filename",
        "weights_sha256",
        "support_threshold",
        "architecture",
        "unsupported_token_id",
        "support_token_id",
        "input_prefix",
    }
    if not isinstance(model, dict) or set(model) != expected_model_fields:
        raise ValueError("registration model is invalid")
    if model.get("architecture") != "flan_t5_label_logits":
        raise ValueError("atomic trial requires the registered Flan architecture")

    protocol = registration.get("protocol")
    expected_protocol = {
        "task": "oracle_retrieval_atomic_claim_grounding",
        "model_inference": {
            "task": "oracle_retrieval_binary_grounding",
            "input_serialization": "document_eos_claim",
            "empty_evidence_sentences": "omit",
            "max_length": 2048,
            "truncation": True,
            "variant_aggregation": "maximum_support_probability",
            "threshold_operator": "strictly_greater_than",
            "batch_size": 4,
            "device": "auto",
            "emit_source_text": False,
        },
        "parent_aggregation": "all_subclaims_supported",
        "parent_support_probability": "minimum_subclaim_support_probability",
        "subclaim_parent_mapping": "final_hyphen_integer_suffix",
        "emit_source_text": False,
    }
    if protocol != expected_protocol:
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
    if gates["claims_evaluated_min"] != len(parent_cases):
        raise ValueError("claims_evaluated_min must equal the parent cohort")
    return parent_cases, subclaim_cases


def _aggregate_parent_samples(
    parent_cases: list[dict[str, Any]],
    subclaim_samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for sample in subclaim_samples:
        match = _SUBCLAIM_ID_RE.fullmatch(sample["id"])
        if match is None:
            raise ValueError(f"invalid subclaim ID: {sample['id']!r}")
        by_parent.setdefault(match.group(1), []).append(sample)

    samples: list[dict[str, Any]] = []
    for parent in parent_cases:
        subclaims = by_parent.get(parent["id"], [])
        if not subclaims:
            raise ValueError(f"parent {parent['id']!r} has no subclaims")
        ordered = sorted(
            subclaims,
            key=lambda item: int(item["id"].rsplit("-", 1)[1]),
        )
        samples.append(
            {
                "id": parent["id"],
                "label": parent["label"],
                "subclaims": [
                    {
                        "id": item["id"],
                        "label": item["label"],
                        "support_probability": item["support_probability"],
                        "predicted_supported": item["predicted_supported"],
                    }
                    for item in ordered
                ],
                "support_probability": min(
                    item["support_probability"] for item in ordered
                ),
                "predicted_supported": all(
                    item["predicted_supported"] for item in ordered
                ),
                "oracle_decomposition_supported": all(
                    item["label"] == "supported" for item in ordered
                ),
            }
        )
    return samples


def run(
    *,
    registration_path: Path,
    project_root: Path,
    dataset_root: Path,
    output_path: Path,
) -> bool:
    registration = json.loads(registration_path.read_text())
    parent_cases, subclaim_cases = _validate_registration(
        registration,
        project_root=project_root,
        dataset_root=dataset_root,
    )
    subclaim_samples, runtime = grounding._score_variants(
        subclaim_cases,
        model_spec=registration["model"],
        protocol=registration["protocol"]["model_inference"],
    )
    samples = _aggregate_parent_samples(parent_cases, subclaim_samples)
    metrics = wice._binary_metrics(samples, prediction="predicted_supported")
    oracle_metrics = wice._binary_metrics(
        samples,
        prediction="oracle_decomposition_supported",
    )
    quality_gates = grounding._quality_gates(registration, samples, metrics)
    result = {
        "schema_version": 1,
        "kind": "wice-atomic-grounding-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": wice._sha256_file(registration_path),
        "source": registration["source"],
        "dataset": registration["dataset"],
        "model": registration["model"],
        "protocol": registration["protocol"],
        "runtime": runtime,
        "summary": {
            "claims": len(samples),
            "subclaims": len(subclaim_samples),
            "labels": dict(sorted(Counter(item["label"] for item in samples).items())),
            "predicted_supported": sum(
                sample["predicted_supported"] for sample in samples
            ),
            "metrics": metrics,
            "oracle_decomposition_metrics": oracle_metrics,
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
    passed = run(
        registration_path=args.registration.resolve(),
        project_root=args.project_root.resolve(),
        dataset_root=args.dataset_root.resolve(),
        output_path=args.output.resolve(),
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
