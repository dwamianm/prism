"""Evaluate a pinned binary grounding model on WiCE oracle-retrieval claims."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import time
from typing import Any

from benchmarks.integrations import run_wice_claim_verification as wice


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    dataset_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if registration.get("schema_version") != 1:
        raise ValueError("registration schema_version must be 1")
    if registration.get("kind") != "wice-grounding-model-registration":
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
    relative_path = dataset.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("dataset relative_path is required")
    dataset_path = (dataset_root / relative_path).resolve()
    if not dataset_path.is_relative_to(dataset_root.resolve()):
        raise ValueError("dataset path escapes the WiCE checkout")
    if not dataset_path.is_file():
        raise ValueError("registered dataset file does not exist")
    if dataset.get("sha256") != wice._sha256_file(dataset_path):
        raise ValueError("WiCE dataset hash does not match registration")
    rows = wice._load_dataset(dataset_path)
    cases = wice._group_dataset(rows)
    observed_shape = {
        "rows": len(rows),
        "claims": len(cases),
        "labels": dict(sorted(Counter(case["label"] for case in cases).items())),
    }
    if dataset.get("expected_shape") != observed_shape:
        raise ValueError("WiCE dataset shape does not match registration")

    model = registration.get("model")
    if not isinstance(model, dict):
        raise ValueError("registration model is required")
    common_model_fields = {
        "name",
        "revision",
        "license",
        "weights_filename",
        "weights_sha256",
        "support_threshold",
        "architecture",
    }
    architecture = model.get("architecture")
    if architecture == "sequence_classification":
        required_model_fields = common_model_fields | {"support_label_index"}
    elif architecture == "flan_t5_label_logits":
        required_model_fields = common_model_fields | {
            "unsupported_token_id",
            "support_token_id",
            "input_prefix",
        }
    else:
        raise ValueError("registration model architecture is invalid")
    if set(model) != required_model_fields:
        raise ValueError("registration model fields are invalid")
    for name in (
        "name",
        "revision",
        "license",
        "weights_filename",
        "weights_sha256",
    ):
        value = model[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"model {name} must be nonempty")
    if architecture == "sequence_classification":
        if model["support_label_index"] not in {0, 1}:
            raise ValueError("support_label_index must be zero or one")
    else:
        if any(
            isinstance(model[name], bool)
            or not isinstance(model[name], int)
            or model[name] < 0
            for name in ("unsupported_token_id", "support_token_id")
        ):
            raise ValueError("label token IDs must be nonnegative integers")
        if model["unsupported_token_id"] == model["support_token_id"]:
            raise ValueError("label token IDs must be distinct")
        if not isinstance(model["input_prefix"], str):
            raise ValueError("input_prefix must be a string")
    threshold = model["support_threshold"]
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0.0 <= threshold <= 1.0
    ):
        raise ValueError("support_threshold must be finite and between zero and one")

    expected_protocol = {
        "task": "oracle_retrieval_binary_grounding",
        "input_serialization": "document_eos_claim",
        "empty_evidence_sentences": "omit",
        "max_length": 2048,
        "truncation": True,
        "variant_aggregation": "maximum_support_probability",
        "threshold_operator": "strictly_greater_than",
        "device": "auto",
        "emit_source_text": False,
    }
    protocol = registration.get("protocol")
    if not isinstance(protocol, dict):
        raise ValueError("registration protocol is required")
    batch_size = protocol.get("batch_size")
    protocol_without_batch = {
        name: value for name, value in protocol.items() if name != "batch_size"
    }
    if (
        protocol_without_batch != expected_protocol
        or isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= 32
    ):
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
    if gates["claims_evaluated_min"] != len(cases):
        raise ValueError("claims_evaluated_min must equal the registered cohort")
    for name, value in gates.items():
        if name == "claims_evaluated_min":
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0.0 <= value <= 1.0
        ):
            raise ValueError(f"gate {name} must be between zero and one")
    return cases, model


def _select_device(torch_module: Any) -> str:
    if torch_module.cuda.is_available():
        return "cuda"
    if torch_module.backends.mps.is_available():
        return "mps"
    return "cpu"


def _score_variants(
    cases: list[dict[str, Any]],
    *,
    model_spec: dict[str, Any],
    protocol: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import torch
    import transformers
    from huggingface_hub import hf_hub_download
    from transformers import (
        AutoModelForSeq2SeqLM,
        AutoModelForSequenceClassification,
        AutoTokenizer,
    )

    weight_path = Path(
        hf_hub_download(
            repo_id=model_spec["name"],
            filename=model_spec["weights_filename"],
            revision=model_spec["revision"],
        )
    )
    if wice._sha256_file(weight_path) != model_spec["weights_sha256"]:
        raise ValueError("grounding model weight hash does not match registration")

    tokenizer = AutoTokenizer.from_pretrained(
        model_spec["name"],
        revision=model_spec["revision"],
        use_fast=True,
        trust_remote_code=False,
    )
    if not tokenizer.eos_token:
        raise ValueError("grounding tokenizer must define an EOS token")
    if model_spec["architecture"] == "sequence_classification":
        model = AutoModelForSequenceClassification.from_pretrained(
            model_spec["name"],
            revision=model_spec["revision"],
            trust_remote_code=False,
            use_safetensors=True,
        )
    else:
        model = AutoModelForSeq2SeqLM.from_pretrained(
            model_spec["name"],
            revision=model_spec["revision"],
            trust_remote_code=False,
            use_safetensors=True,
        )
    resolved_revision = getattr(model.config, "_commit_hash", None)
    if resolved_revision != model_spec["revision"]:
        raise ValueError(
            "resolved grounding model revision does not match registration"
        )
    if (
        model_spec["architecture"] == "sequence_classification"
        and model.config.num_labels != 2
    ):
        raise ValueError("grounding model must expose exactly two labels")
    device = _select_device(torch)
    model.to(device)
    model.eval()

    serialized: list[str] = []
    row_identity: list[tuple[str, int]] = []
    for case in cases:
        for variant_number, variant in enumerate(case["variants"], start=1):
            document = "\n".join(item for item in variant["evidence"] if item.strip())
            serialized.append(tokenizer.eos_token.join([document, case["claim"]]))
            row_identity.append((case["id"], variant_number))

    probabilities: list[float] = []
    batch_size = protocol["batch_size"]
    started = time.perf_counter()
    with torch.inference_mode():
        for start in range(0, len(serialized), batch_size):
            batch_text = serialized[start : start + batch_size]
            if model_spec["architecture"] == "flan_t5_label_logits":
                batch_text = [model_spec["input_prefix"] + text for text in batch_text]
            encoded = tokenizer(
                batch_text,
                max_length=protocol["max_length"],
                truncation=protocol["truncation"],
                padding=True,
                return_tensors="pt",
            )
            encoded = {name: value.to(device) for name, value in encoded.items()}
            if model_spec["architecture"] == "sequence_classification":
                logits = model(**encoded).logits
                batch_probabilities = torch.softmax(logits, dim=1)[
                    :, model_spec["support_label_index"]
                ]
            else:
                decoder_input_ids = torch.zeros(
                    (encoded["input_ids"].size(0), 1),
                    dtype=torch.long,
                    device=device,
                )
                logits = model(
                    input_ids=encoded["input_ids"],
                    attention_mask=encoded["attention_mask"],
                    decoder_input_ids=decoder_input_ids,
                ).logits.squeeze(1)
                label_logits = logits[
                    :,
                    torch.tensor(
                        [
                            model_spec["unsupported_token_id"],
                            model_spec["support_token_id"],
                        ],
                        device=device,
                    ),
                ]
                batch_probabilities = torch.softmax(label_logits, dim=1)[:, 1]
            probabilities.extend(float(value) for value in batch_probabilities.cpu())
    elapsed_seconds = time.perf_counter() - started
    if len(probabilities) != len(row_identity) or any(
        not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in probabilities
    ):
        raise ValueError("grounding model returned invalid probabilities")

    probabilities_by_case: dict[str, list[float]] = {}
    for (case_id, _variant_number), probability in zip(
        row_identity, probabilities, strict=True
    ):
        probabilities_by_case.setdefault(case_id, []).append(probability)
    samples: list[dict[str, Any]] = []
    for case in cases:
        variant_probabilities = probabilities_by_case[case["id"]]
        support_probability = max(variant_probabilities)
        samples.append(
            {
                "id": case["id"],
                "label": case["label"],
                "oracle_variants": len(variant_probabilities),
                "variant_support_probabilities": variant_probabilities,
                "support_probability": support_probability,
                "predicted_supported": support_probability
                > model_spec["support_threshold"],
            }
        )
    runtime = {
        "device": device,
        "elapsed_seconds": elapsed_seconds,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "resolved_model_revision": resolved_revision,
        "weights_sha256": wice._sha256_file(weight_path),
        "architecture": model_spec["architecture"],
    }
    return samples, runtime


def _quality_gates(
    registration: dict[str, Any],
    samples: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    requirements = registration["evaluation"]["gates"]
    not_supported = [sample for sample in samples if sample["label"] == "not_supported"]
    not_supported_false_support_rate = sum(
        sample["predicted_supported"] for sample in not_supported
    ) / len(not_supported)
    observed = {
        "claims_evaluated_min": len(samples),
        "supported_precision_min": metrics["precision"],
        "supported_recall_min": metrics["recall"],
        "balanced_accuracy_min": metrics["balanced_accuracy"],
        "false_support_rate_max": metrics["false_support_rate"],
        "not_supported_false_support_rate_max": not_supported_false_support_rate,
    }
    results: dict[str, Any] = {}
    for name, requirement in requirements.items():
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


def run(
    *,
    registration_path: Path,
    project_root: Path,
    dataset_root: Path,
    output_path: Path,
) -> bool:
    registration = json.loads(registration_path.read_text())
    cases, model_spec = _validate_registration(
        registration,
        project_root=project_root,
        dataset_root=dataset_root,
    )
    samples, runtime = _score_variants(
        cases,
        model_spec=model_spec,
        protocol=registration["protocol"],
    )
    metrics = wice._binary_metrics(samples, prediction="predicted_supported")
    quality_gates = _quality_gates(registration, samples, metrics)
    result = {
        "schema_version": 1,
        "kind": "wice-grounding-model-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": wice._sha256_file(registration_path),
        "source": registration["source"],
        "dataset": registration["dataset"],
        "model": registration["model"],
        "protocol": registration["protocol"],
        "runtime": runtime,
        "summary": {
            "claims": len(samples),
            "labels": dict(sorted(Counter(item["label"] for item in samples).items())),
            "predicted_supported": sum(
                sample["predicted_supported"] for sample in samples
            ),
            "metrics": metrics,
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
