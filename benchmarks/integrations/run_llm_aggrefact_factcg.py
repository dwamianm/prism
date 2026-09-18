"""Evaluate a pinned FactCG model on sealed LLM-AggreFact cohorts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import time
from typing import Any, Iterable


INSTRUCTION_TEMPLATE = (
    "{document}\n\nChoose your answer: based on the paragraph above can we "
    'conclude that "{claim}"?\n\nOPTIONS:\n- Yes\n- No\nI think the answer is '
)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_is_ancestor(revision: str, root: Path) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _load_rows(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    expected = {"dataset", "doc", "claim", "label", "contamination_identifier"}
    if set(table.column_names) != expected:
        raise ValueError("LLM-AggreFact parquet columns do not match")
    rows = table.to_pylist()
    for row in rows:
        if (
            not isinstance(row["dataset"], str)
            or not isinstance(row["doc"], str)
            or not isinstance(row["claim"], str)
            or row["label"] not in {0, 1}
            or not isinstance(row["contamination_identifier"], str)
        ):
            raise ValueError("LLM-AggreFact row is malformed")
    return rows


def _shape(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    datasets = Counter(row["dataset"] for row in materialized)
    labels = Counter(str(row["label"]) for row in materialized)
    return {
        "rows": len(materialized),
        "datasets": dict(sorted(datasets.items())),
        "labels": dict(sorted(labels.items())),
    }


def _select_cohort(
    rows: list[dict[str, Any]],
    *,
    split: str,
    seed: str,
    per_label_per_dataset: int,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["dataset"], row["label"])].append(row)
    selected: list[dict[str, Any]] = []
    for key in sorted(groups):
        candidates = sorted(
            groups[key],
            key=lambda row: (
                hashlib.sha256(
                    f"{seed}\0{split}\0{row['contamination_identifier']}".encode()
                ).digest(),
                row["contamination_identifier"],
            ),
        )
        if len(candidates) < per_label_per_dataset:
            raise ValueError(f"cohort group {key!r} is too small")
        selected.extend(candidates[:per_label_per_dataset])
    return selected


def _identity_sha256(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            [row["contamination_identifier"] for row in rows],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _validate_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    dev_path: Path,
    test_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if registration.get("schema_version") != 1:
        raise ValueError("registration schema_version must be 1")
    if registration.get("kind") != "llm-aggrefact-factcg-registration":
        raise ValueError("registration kind is invalid")

    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source is required")
    revision = source.get("prme_revision")
    if not isinstance(revision, str) or not _git_is_ancestor(revision, project_root):
        raise ValueError("registered PRME revision is not an ancestor of HEAD")
    expected_source_files = {"runner_sha256": _sha256_file(Path(__file__).resolve())}
    if source.get("files") != expected_source_files:
        raise ValueError("registration runner hash does not match")

    dataset = registration.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("registration dataset is required")
    expected_dataset_keys = {
        "name",
        "repository",
        "revision",
        "license",
        "files",
        "source_text_policy",
    }
    if set(dataset) != expected_dataset_keys:
        raise ValueError("registration dataset fields are invalid")
    if dataset["name"] != "LLM-AggreFact":
        raise ValueError("registration dataset name is invalid")

    paths = {"dev": dev_path, "test": test_path}
    all_rows: dict[str, list[dict[str, Any]]] = {}
    file_specs = dataset.get("files")
    if not isinstance(file_specs, dict) or set(file_specs) != set(paths):
        raise ValueError("registration dataset files are incomplete")
    for split, path in paths.items():
        if not path.is_file():
            raise ValueError(f"registered {split} parquet does not exist")
        spec = file_specs[split]
        if not isinstance(spec, dict) or set(spec) != {"sha256", "expected_shape"}:
            raise ValueError(f"registered {split} file fields are invalid")
        if spec["sha256"] != _sha256_file(path):
            raise ValueError(f"registered {split} parquet hash does not match")
        rows = _load_rows(path)
        if spec["expected_shape"] != _shape(rows):
            raise ValueError(f"registered {split} parquet shape does not match")
        all_rows[split] = rows

    cohort = registration.get("cohort")
    expected_cohort_keys = {
        "seed",
        "per_label_per_dataset",
        "sampling",
        "dev_selected_identity_sha256",
        "test_selected_identity_sha256",
    }
    if not isinstance(cohort, dict) or set(cohort) != expected_cohort_keys:
        raise ValueError("registration cohort fields are invalid")
    seed = cohort["seed"]
    per_label = cohort["per_label_per_dataset"]
    if not isinstance(seed, str) or not seed:
        raise ValueError("registration cohort seed is invalid")
    if isinstance(per_label, bool) or not isinstance(per_label, int) or per_label < 1:
        raise ValueError("registration cohort size is invalid")
    selected = {
        split: _select_cohort(
            all_rows[split],
            split=split,
            seed=seed,
            per_label_per_dataset=per_label,
        )
        for split in paths
    }
    for split in paths:
        if cohort[f"{split}_selected_identity_sha256"] != _identity_sha256(
            selected[split]
        ):
            raise ValueError(f"registered {split} cohort identity does not match")

    model = registration.get("model")
    expected_model_keys = {
        "name",
        "revision",
        "license",
        "architecture",
        "weights_filename",
        "weights_sha256",
        "support_label_index",
    }
    if not isinstance(model, dict) or set(model) != expected_model_keys:
        raise ValueError("registration model fields are invalid")
    if model["architecture"] != "sequence_classification":
        raise ValueError("registration model architecture is invalid")
    if model["support_label_index"] not in {0, 1}:
        raise ValueError("registration support label is invalid")
    for name in ("name", "revision", "license", "weights_filename", "weights_sha256"):
        if not isinstance(model[name], str) or not model[name]:
            raise ValueError(f"registration model {name} is invalid")

    expected_protocol = {
        "task": "document_level_grounding_with_dev_calibration",
        "input_serialization": "factcg_instruction_v1",
        "source_chunking": "nltk_sentence_chunks_max_550_word_tokens",
        "chunk_aggregation": "maximum_support_probability",
        "max_length": 2048,
        "truncation": True,
        "threshold_operator": "strictly_greater_than",
        "device": "auto",
        "emit_source_text": False,
    }
    protocol = registration.get("protocol")
    if not isinstance(protocol, dict):
        raise ValueError("registration protocol is required")
    batch_size = protocol.get("batch_size")
    without_batch = {
        key: value for key, value in protocol.items() if key != "batch_size"
    }
    if without_batch != expected_protocol:
        raise ValueError("registration protocol does not match")
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= 32
    ):
        raise ValueError("registration batch size is invalid")

    evaluation = registration.get("evaluation")
    if not isinstance(evaluation, dict) or set(evaluation) != {
        "calibration",
        "gates",
        "decision_rule",
    }:
        raise ValueError("registration evaluation fields are invalid")
    calibration = evaluation["calibration"]
    if not isinstance(calibration, dict) or set(calibration) != {
        "supported_precision_min",
        "supported_recall_min",
        "selection",
        "test_unlock",
    }:
        raise ValueError("registration calibration fields are invalid")
    if (
        calibration["selection"]
        != "maximum_recall_then_precision_then_lowest_threshold"
    ):
        raise ValueError("registration calibration selection is invalid")
    if calibration["test_unlock"] != "all_calibration_gates_pass":
        raise ValueError("registration test unlock is invalid")
    gates = evaluation["gates"]
    if not isinstance(gates, dict) or set(gates) != {
        "claims_evaluated_min",
        "supported_precision_min",
        "supported_recall_min",
        "balanced_accuracy_min",
        "false_support_rate_max",
    }:
        raise ValueError("registration gates are invalid")
    expected_claims = per_label * 2 * len({row["dataset"] for row in selected["test"]})
    if gates["claims_evaluated_min"] != expected_claims:
        raise ValueError("registered minimum claims does not match cohort")
    for mapping in (calibration, gates):
        for name, value in mapping.items():
            if name in {"selection", "test_unlock", "claims_evaluated_min"}:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError(f"registered metric {name} is invalid")
    return selected["dev"], selected["test"]


def _chunk_document(document: str, *, max_word_tokens: int = 550) -> list[str]:
    import nltk

    sentences = nltk.sent_tokenize(document)
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for sentence in sentences:
        token_count = len(nltk.word_tokenize(sentence))
        if current and current_size + token_count > max_word_tokens:
            chunks.append("\n".join(current))
            current = [sentence]
            current_size = token_count
        else:
            current.append(sentence)
            current_size += token_count
    if current:
        chunks.append("\n".join(current).strip())
    if not chunks:
        raise ValueError("FactCG document produced no chunks")
    return chunks


def _select_device(torch_module: Any) -> str:
    if torch_module.cuda.is_available():
        return "cuda"
    if torch_module.backends.mps.is_available():
        return "mps"
    return "cpu"


def _score_rows(
    rows: list[dict[str, Any]],
    *,
    model_spec: dict[str, Any],
    batch_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import torch
    import transformers
    from huggingface_hub import hf_hub_download
    from transformers import (
        AutoConfig,
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
    if _sha256_file(weight_path) != model_spec["weights_sha256"]:
        raise ValueError("downloaded FactCG weights do not match registration")

    config = AutoConfig.from_pretrained(
        model_spec["name"],
        revision=model_spec["revision"],
        num_labels=2,
        finetuning_task="text-classification",
    )
    config.problem_type = "single_label_classification"
    tokenizer = AutoTokenizer.from_pretrained(
        model_spec["name"], revision=model_spec["revision"], use_fast=True
    )
    model = AutoModelForSequenceClassification.from_pretrained(
        model_spec["name"],
        revision=model_spec["revision"],
        config=config,
    )
    device = _select_device(torch)
    model.to(device)
    model.eval()

    prompts: list[str] = []
    owners: list[int] = []
    chunk_counts: list[int] = []
    for index, row in enumerate(rows):
        chunks = _chunk_document(row["doc"])
        chunk_counts.append(len(chunks))
        for chunk in chunks:
            prompts.append(
                INSTRUCTION_TEMPLATE.format(document=chunk, claim=row["claim"])
            )
            owners.append(index)

    scores = [0.0] * len(rows)
    started = time.perf_counter()
    with torch.inference_mode():
        for offset in range(0, len(prompts), batch_size):
            encoded = tokenizer(
                prompts[offset : offset + batch_size],
                max_length=2048,
                truncation=True,
                padding="longest",
                return_tensors="pt",
            )
            encoded = {name: value.to(device) for name, value in encoded.items()}
            logits = model(**encoded).logits
            probabilities = torch.softmax(logits, dim=1)[
                :, model_spec["support_label_index"]
            ]
            for inner, probability in enumerate(probabilities.detach().cpu().tolist()):
                owner = owners[offset + inner]
                scores[owner] = max(scores[owner], float(probability))
    elapsed = time.perf_counter() - started

    samples = [
        {
            "id": row["contamination_identifier"],
            "dataset": row["dataset"],
            "label": row["label"],
            "support_probability": score,
            "source_chunks": chunk_count,
        }
        for row, score, chunk_count in zip(rows, scores, chunk_counts, strict=True)
    ]
    runtime = {
        "device": device,
        "seconds": elapsed,
        "pairs_scored": len(prompts),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    return samples, runtime


def _metrics(samples: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for sample in samples:
        predicted = sample["support_probability"] > threshold
        actual = sample["label"] == 1
        if predicted and actual:
            tp += 1
        elif predicted:
            fp += 1
        elif actual:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": (tp + tn) / len(samples) if samples else 0.0,
        "balanced_accuracy": (recall + specificity) / 2,
        "supported_precision": precision,
        "supported_recall": recall,
        "supported_f1": (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        ),
        "false_support_rate": fp / (fp + tn) if fp + tn else 0.0,
    }


def _calibrate_threshold(
    samples: list[dict[str, Any]],
    *,
    precision_min: float,
    recall_min: float,
) -> tuple[float | None, dict[str, Any] | None]:
    candidates = sorted(
        {0.0, 1.0, *(sample["support_probability"] for sample in samples)}
    )
    eligible = [
        metrics
        for threshold in candidates
        if (
            (metrics := _metrics(samples, threshold))["supported_precision"]
            >= precision_min
            and metrics["supported_recall"] >= recall_min
        )
    ]
    if not eligible:
        return None, None
    selected = max(
        eligible,
        key=lambda metrics: (
            metrics["supported_recall"],
            metrics["supported_precision"],
            -metrics["threshold"],
        ),
    )
    return selected["threshold"], selected


def _gate_results(metrics: dict[str, Any], gates: dict[str, Any]) -> dict[str, bool]:
    return {
        "claims_evaluated_min": sum(metrics[name] for name in ("tp", "fp", "tn", "fn"))
        >= gates["claims_evaluated_min"],
        "supported_precision_min": metrics["supported_precision"]
        >= gates["supported_precision_min"],
        "supported_recall_min": metrics["supported_recall"]
        >= gates["supported_recall_min"],
        "balanced_accuracy_min": metrics["balanced_accuracy"]
        >= gates["balanced_accuracy_min"],
        "false_support_rate_max": metrics["false_support_rate"]
        <= gates["false_support_rate_max"],
    }


def run(
    *,
    registration_path: Path,
    dev_path: Path,
    test_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    dev_rows, test_rows = _validate_registration(
        registration,
        project_root=project_root,
        dev_path=dev_path,
        test_path=test_path,
    )
    protocol = registration["protocol"]
    model_spec = registration["model"]
    dev_samples, dev_runtime = _score_rows(
        dev_rows, model_spec=model_spec, batch_size=protocol["batch_size"]
    )
    calibration = registration["evaluation"]["calibration"]
    threshold, calibration_metrics = _calibrate_threshold(
        dev_samples,
        precision_min=calibration["supported_precision_min"],
        recall_min=calibration["supported_recall_min"],
    )

    test_samples: list[dict[str, Any]] = []
    test_runtime: dict[str, Any] | None = None
    test_metrics: dict[str, Any] | None = None
    gate_results: dict[str, bool] = {"calibration": threshold is not None}
    if threshold is not None:
        test_samples, test_runtime = _score_rows(
            test_rows, model_spec=model_spec, batch_size=protocol["batch_size"]
        )
        test_metrics = _metrics(test_samples, threshold)
        test_gate_results = _gate_results(
            test_metrics, registration["evaluation"]["gates"]
        )
        gate_results.update(test_gate_results)

    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-factcg-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _sha256_file(registration_path),
        "dataset": registration["dataset"],
        "cohort": {
            **registration["cohort"],
            "dev_cases": len(dev_rows),
            "test_cases": len(test_rows),
        },
        "model": model_spec,
        "protocol": protocol,
        "calibration": {
            "requirements": calibration,
            "selected_threshold": threshold,
            "metrics": calibration_metrics,
            "runtime": dev_runtime,
            "samples": dev_samples,
        },
        "test": {
            "unlocked": threshold is not None,
            "metrics": test_metrics,
            "runtime": test_runtime,
            "samples": test_samples,
        },
        "gate_results": gate_results,
        "passed": bool(gate_results) and all(gate_results.values()),
    }
    result["result_sha256"] = _canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    result = run(
        registration_path=args.registration.resolve(),
        dev_path=args.dev.resolve(),
        test_path=args.test.resolve(),
        output_path=args.output.resolve(),
        project_root=project_root,
    )
    summary = {
        "passed": result["passed"],
        "calibration_threshold": result["calibration"]["selected_threshold"],
        "calibration_metrics": result["calibration"]["metrics"],
        "test_metrics": result["test"]["metrics"],
        "gate_results": result["gate_results"],
        "result_sha256": result["result_sha256"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
