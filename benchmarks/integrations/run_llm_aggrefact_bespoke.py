"""Evaluate pinned Bespoke MiniCheck sentence fusion on LLM-AggreFact."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import time
from typing import Any

from benchmarks.integrations import run_llm_aggrefact_factcg as factcg


SYSTEM_PROMPT = (
    "Determine whether the provided claim is consistent with the corresponding "
    "document. Consistency in this context implies that all information presented "
    "in the claim is substantiated by the document. If not, it should be considered "
    'inconsistent. Please assess the claim\'s consistency with the document by '
    'responding with either "Yes" or "No".'
)
USER_TEMPLATE = "Document: {document}\nClaim: {claim}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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


def _validate_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    base_registration_path: Path,
    dev_path: Path,
    test_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if registration.get("schema_version") != 1:
        raise ValueError("registration schema_version must be 1")
    if registration.get("kind") != "llm-aggrefact-bespoke-registration":
        raise ValueError("registration kind is invalid")

    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source is required")
    revision = source.get("prme_revision")
    if not isinstance(revision, str) or not _git_is_ancestor(revision, project_root):
        raise ValueError("registered PRME revision is not an ancestor of HEAD")
    expected_files = {
        "runner_sha256": factcg._sha256_file(Path(__file__).resolve()),
        "factcg_runner_sha256": factcg._sha256_file(Path(factcg.__file__).resolve()),
        "base_registration_sha256": factcg._sha256_file(base_registration_path),
    }
    if source.get("files") != expected_files:
        raise ValueError("registration source-file hashes do not match")

    base_registration = json.loads(base_registration_path.read_text())
    dev_rows, test_rows = factcg._validate_registration(
        base_registration,
        project_root=project_root,
        dev_path=dev_path,
        test_path=test_path,
    )
    if registration.get("dataset") != base_registration["dataset"]:
        raise ValueError("registration dataset does not match the sealed base trial")
    expected_cohort = {
        **base_registration["cohort"],
        "dev_cases": len(dev_rows),
        "test_cases": len(test_rows),
    }
    if registration.get("cohort") != expected_cohort:
        raise ValueError("registration cohort does not match the sealed base trial")

    model = registration.get("model")
    if not isinstance(model, dict) or set(model) != {
        "name",
        "revision",
        "license",
        "architecture",
        "parameter_count",
        "files",
        "yes_token_ids",
        "no_token_ids",
    }:
        raise ValueError("registration model is invalid")
    if model["architecture"] != "causal_lm" or model["license"] != "cc-by-nc-4.0":
        raise ValueError("registration model identity is invalid")
    if not isinstance(model["files"], dict) or not model["files"]:
        raise ValueError("registration model files are required")
    for label in ("yes_token_ids", "no_token_ids"):
        token_ids = model[label]
        if (
            not isinstance(token_ids, list)
            or not token_ids
            or any(isinstance(value, bool) or not isinstance(value, int) for value in token_ids)
        ):
            raise ValueError(f"registration {label} is invalid")
    if set(model["yes_token_ids"]) & set(model["no_token_ids"]):
        raise ValueError("registered Yes and No token identities overlap")

    expected_protocol = {
        "task": "sentence_fusion_document_grounding",
        "system_prompt_sha256": _sha256_text(SYSTEM_PROMPT),
        "user_template_sha256": _sha256_text(USER_TEMPLATE),
        "claim_segmentation": "nltk_sent_tokenize",
        "source_chunking": "nltk_sentence_chunks_by_model_tokens",
        "sentence_fusion": "minimum_over_claim_sentences_of_maximum_over_source_chunks",
        "max_model_length": 8192,
        "source_chunk_tokens": 7800,
        "batch_size": 2,
        "max_new_tokens": 1,
        "temperature": 0,
        "seed": 2024,
        "transformers_version": "4.43.3",
        "einops_version": "0.8.1",
        "sentencepiece_version": "0.2.1",
        "accelerate_version": "1.10.1",
        "support_probability": "sum_full_vocabulary_probability_of_registered_yes_tokens",
        "threshold_operator": "strictly_greater_than",
        "device": "auto",
        "emit_source_text": False,
    }
    if registration.get("protocol") != expected_protocol:
        raise ValueError("registration protocol is invalid")

    evaluation = registration.get("evaluation")
    if not isinstance(evaluation, dict) or set(evaluation) != {
        "calibration",
        "gates",
        "decision_rule",
    }:
        raise ValueError("registration evaluation is invalid")
    calibration = evaluation["calibration"]
    if not isinstance(calibration, dict) or set(calibration) != {
        "supported_precision_min",
        "supported_recall_min",
        "selection",
        "test_unlock",
    }:
        raise ValueError("registration calibration is invalid")
    if calibration["selection"] != "maximum_recall_then_precision_then_lowest_threshold":
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
    if gates["claims_evaluated_min"] != len(test_rows):
        raise ValueError("minimum test claims does not match the cohort")
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
                raise ValueError(f"registration metric {name} is invalid")
    return dev_rows, test_rows


def _sentence_chunks(
    document: str,
    *,
    tokenizer: Any,
    max_tokens: int,
) -> list[str]:
    import nltk

    sentences: list[str] = []
    for block in document.splitlines():
        sentences.extend(nltk.sent_tokenize(block))
    if not sentences and document.strip():
        sentences = [document.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        token_ids = tokenizer(sentence, add_special_tokens=False)["input_ids"]
        if len(token_ids) > max_tokens:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_tokens = 0
            for offset in range(0, len(token_ids), max_tokens):
                chunks.append(
                    tokenizer.decode(
                        token_ids[offset : offset + max_tokens],
                        skip_special_tokens=True,
                    ).strip()
                )
        elif current and current_tokens + len(token_ids) > max_tokens:
            chunks.append(" ".join(current))
            current = [sentence]
            current_tokens = len(token_ids)
        else:
            current.append(sentence)
            current_tokens += len(token_ids)
    if current:
        chunks.append(" ".join(current))
    chunks = [chunk for chunk in chunks if chunk]
    if not chunks:
        raise ValueError("document produced no source chunks")
    return chunks


def _fuse_sentence_probabilities(
    probabilities: list[tuple[int, int, float]],
    *,
    claim_sentence_count: int,
) -> tuple[float, list[float]]:
    by_sentence: dict[int, list[float]] = defaultdict(list)
    for _chunk_index, sentence_index, probability in probabilities:
        by_sentence[sentence_index].append(probability)
    if set(by_sentence) != set(range(claim_sentence_count)):
        raise ValueError("sentence fusion inputs are incomplete")
    sentence_scores = [max(by_sentence[index]) for index in range(claim_sentence_count)]
    return min(sentence_scores), sentence_scores


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
    protocol: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import accelerate
    import einops
    import nltk
    import sentencepiece
    import torch
    import transformers
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed

    observed_dependencies = {
        "transformers_version": transformers.__version__,
        "einops_version": einops.__version__,
        "sentencepiece_version": sentencepiece.__version__,
        "accelerate_version": accelerate.__version__,
    }
    for name, observed in observed_dependencies.items():
        if observed != protocol[name]:
            package = name.removesuffix("_version")
            raise ValueError(f"installed {package} version does not match registration")

    model_path = Path(
        snapshot_download(
            model_spec["name"],
            revision=model_spec["revision"],
            allow_patterns=list(model_spec["files"]),
        )
    )
    for filename, expected_sha256 in model_spec["files"].items():
        path = model_path / filename
        if not path.is_file() or factcg._sha256_file(path) != expected_sha256:
            raise ValueError(f"downloaded model file does not match: {filename}")
    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        trust_remote_code=True,
        local_files_only=True,
    )
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    for label, expected in (
        ("yes_token_ids", model_spec["yes_token_ids"]),
        ("no_token_ids", model_spec["no_token_ids"]),
    ):
        observed = _answer_token_ids(tokenizer, label.removesuffix("_token_ids"))
        if observed != expected:
            raise ValueError(f"registered {label} does not match the tokenizer")

    device = _select_device(torch)
    dtype = torch.bfloat16 if device in {"cuda", "mps"} else torch.float32
    set_seed(protocol["seed"])
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        trust_remote_code=True,
        local_files_only=True,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        attn_implementation="eager",
    )
    model.to(device)
    model.eval()

    tasks: list[tuple[int, int, int, str]] = []
    row_shapes: list[tuple[int, int]] = []
    for row_index, row in enumerate(rows):
        chunks = _sentence_chunks(
            row["doc"],
            tokenizer=tokenizer,
            max_tokens=protocol["source_chunk_tokens"],
        )
        claim_sentences = nltk.sent_tokenize(row["claim"]) or [row["claim"]]
        row_shapes.append((len(chunks), len(claim_sentences)))
        for chunk_index, chunk in enumerate(chunks):
            for sentence_index, claim_sentence in enumerate(claim_sentences):
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": USER_TEMPLATE.format(
                            document=chunk,
                            claim=claim_sentence,
                        ),
                    },
                ]
                prompt = tokenizer.apply_chat_template(
                    messages,
                    add_generation_prompt=True,
                    tokenize=False,
                )
                tasks.append((row_index, chunk_index, sentence_index, prompt))

    yes_ids = torch.tensor(model_spec["yes_token_ids"], device=device)
    observed: list[list[tuple[int, int, float]]] = [[] for _ in rows]
    started = time.perf_counter()
    batch_size = protocol["batch_size"]
    with torch.inference_mode():
        for offset in range(0, len(tasks), batch_size):
            batch = tasks[offset : offset + batch_size]
            encoded = tokenizer(
                [task[3] for task in batch],
                padding=True,
                return_tensors="pt",
            )
            if encoded["input_ids"].shape[1] > protocol["max_model_length"]:
                raise ValueError("Bespoke prompt exceeds registered model length")
            encoded = {name: value.to(device) for name, value in encoded.items()}
            generated = model.generate(
                **encoded,
                do_sample=False,
                max_new_tokens=protocol["max_new_tokens"],
                output_scores=True,
                return_dict_in_generate=True,
                pad_token_id=tokenizer.pad_token_id,
            )
            probabilities = torch.softmax(generated.scores[0].float(), dim=-1)
            support = probabilities.index_select(1, yes_ids).sum(dim=1)
            for task, probability in zip(batch, support.cpu().tolist(), strict=True):
                row_index, chunk_index, sentence_index, _prompt = task
                observed[row_index].append(
                    (chunk_index, sentence_index, float(probability))
                )
    elapsed = time.perf_counter() - started

    samples: list[dict[str, Any]] = []
    for row, shape, probabilities in zip(rows, row_shapes, observed, strict=True):
        score, sentence_scores = _fuse_sentence_probabilities(
            probabilities,
            claim_sentence_count=shape[1],
        )
        samples.append(
            {
                "id": row["contamination_identifier"],
                "dataset": row["dataset"],
                "label": row["label"],
                "support_probability": score,
                "source_chunks": shape[0],
                "claim_sentences": shape[1],
                "sentence_support_probabilities": sentence_scores,
            }
        )
    runtime = {
        "device": device,
        "dtype": str(dtype),
        "seconds": elapsed,
        "pairs_scored": len(tasks),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "einops": einops.__version__,
        "sentencepiece": sentencepiece.__version__,
        "accelerate": accelerate.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    return samples, runtime


def _answer_token_ids(tokenizer: Any, answer: str) -> list[int]:
    candidates: set[int] = set()
    for text in {answer, answer.title(), answer.upper(), f" {answer}", f" {answer.title()}"}:
        token_ids = tokenizer(text, add_special_tokens=False)["input_ids"]
        if len(token_ids) == 1:
            candidates.add(token_ids[0])
    if not candidates:
        raise ValueError(f"tokenizer has no single-token form for {answer!r}")
    return sorted(candidates)


def run(
    *,
    registration_path: Path,
    base_registration_path: Path,
    dev_path: Path,
    test_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    dev_rows, test_rows = _validate_registration(
        registration,
        project_root=project_root,
        base_registration_path=base_registration_path,
        dev_path=dev_path,
        test_path=test_path,
    )
    dev_samples, dev_runtime = _score_rows(
        dev_rows,
        model_spec=registration["model"],
        protocol=registration["protocol"],
    )
    calibration = registration["evaluation"]["calibration"]
    threshold, calibration_metrics = factcg._calibrate_threshold(
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
            test_rows,
            model_spec=registration["model"],
            protocol=registration["protocol"],
        )
        test_metrics = factcg._metrics(test_samples, threshold)
        gate_results.update(
            factcg._gate_results(test_metrics, registration["evaluation"]["gates"])
        )

    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-bespoke-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": factcg._sha256_file(registration_path),
        "dataset": registration["dataset"],
        "cohort": registration["cohort"],
        "model": registration["model"],
        "protocol": registration["protocol"],
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
    result["result_sha256"] = factcg._canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--base-registration", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        registration_path=args.registration.resolve(),
        base_registration_path=args.base_registration.resolve(),
        dev_path=args.dev.resolve(),
        test_path=args.test.resolve(),
        output_path=args.output.resolve(),
        project_root=Path(__file__).resolve().parents[2],
    )
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "calibration_threshold": result["calibration"]["selected_threshold"],
                "calibration_metrics": result["calibration"]["metrics"],
                "test_metrics": result["test"]["metrics"],
                "gate_results": result["gate_results"],
                "result_sha256": result["result_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
