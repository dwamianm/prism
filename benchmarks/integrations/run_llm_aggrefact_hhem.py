"""Register and run a development-only HHEM capacity trial on LLM-AggreFact."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import platform
import subprocess
import time
from typing import Any

from benchmarks.diagnostics import llm_aggrefact_partition_scoring as diagnostics
from benchmarks.diagnostics import llm_aggrefact_typed_tool_recovery as recovery
from benchmarks.integrations import run_llm_aggrefact_evidence_cascade as cascade
from benchmarks.integrations import run_llm_aggrefact_factcg as factcg
from benchmarks.integrations import run_llm_aggrefact_typed_references as typed


HHEM_REPOSITORY = "vectara/hallucination_evaluation_model"
HHEM_REVISION = "8e4a2e6e96c708cc76c2344f7e4757df2515292c"
HHEM_FILES = {
    "config.json": "773139fe764fe20e146ab14e627b188ccafae35f93cf6f5258dc6c237016b870",
    "configuration_hhem_v2.py": "ec57fe344e3104d0d4a99b13d893529aac1e2bd69e83c2814235baf37cdcacc7",
    "modeling_hhem_v2.py": "fcc9cfcee513cc08eb46eac21f1acb498b122572fb35a7dec4d85fae45cb9bba",
    "model.safetensors": "634de18a38cf1e991c1acd0f7a9e0d30f7ea187fba42bb4798f862d3edd31e72",
}
FOUNDATION_REPOSITORY = "google/flan-t5-base"
FOUNDATION_REVISION = "7bcac572ce56db69c1ea7c8af255c5d7c9672fc2"
FOUNDATION_FILES = {
    "config.json": "7c1853dbfa0e4aac093eb109a358b6ab25fe86b7c15185a91322f0ed26f0f940",
    "special_tokens_map.json": "5c87151ef0f72a99d1f766a4c418bd2a1f90aaa30a8e22fe5eca9641daebb64f",
    "spiece.model": "d60acb128cf7b7f2536e8f38a5b18a05535c9e14c7a355904270e15b0945ea86",
    "tokenizer.json": "fe2ebbbbde2985be723e0ce18217853e4020c5e9d35bd07be2c27ab9d3ead57a",
    "tokenizer_config.json": "4c55124402e4ce48c7125d04b9af152a125eda9e7c80829f8f99f2ec69f3f68d",
}
PROMPT = (
    "<pad> Determine if the hypothesis is true given the premise?\n\n"
    "Premise: {premise}\n\nHypothesis: {hypothesis}"
)
SELECTION_SEED = "prme-llm-aggrefact-hhem-v1"
MODEL_PARAMETER_COUNT = 109_630_082
RUNTIME_TRANSFORMERS = "5.3.0"


def _git_revision(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _select_fresh_balanced(
    rows: list[dict[str, Any]],
    *,
    observed_ids: frozenset[str],
    seed: str = SELECTION_SEED,
) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["contamination_identifier"] not in observed_ids:
            groups[int(row["label"])].append(row)
    if set(groups) != {0, 1}:
        raise ValueError("fresh cohort does not contain both labels")
    per_label = min(len(group) for group in groups.values())
    selected: list[dict[str, Any]] = []
    for label in (0, 1):
        candidates = sorted(
            groups[label],
            key=lambda row: (
                hashlib.sha256(
                    f"{seed}\0select\0{row['contamination_identifier']}".encode()
                ).digest(),
                row["contamination_identifier"],
            ),
        )
        selected.extend(candidates[:per_label])
    return sorted(
        selected,
        key=lambda row: (
            hashlib.sha256(
                f"{seed}\0order\0{row['contamination_identifier']}".encode()
            ).digest(),
            row["contamination_identifier"],
        ),
    )


def _counts_by_dataset_label(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        counts[row["dataset"]][str(row["label"])] += 1
    return {
        dataset: dict(sorted(values.items()))
        for dataset, values in sorted(counts.items())
    }


def _artifact_paths(
    *, model_spec: dict[str, Any], foundation_spec: dict[str, Any]
) -> tuple[Path, Path]:
    from huggingface_hub import snapshot_download

    model_path = Path(
        snapshot_download(
            model_spec["name"],
            revision=model_spec["revision"],
            allow_patterns=list(model_spec["files"]),
            local_files_only=True,
        )
    )
    foundation_path = Path(
        snapshot_download(
            foundation_spec["name"],
            revision=foundation_spec["revision"],
            allow_patterns=list(foundation_spec["files"]),
            local_files_only=True,
        )
    )
    for root, spec, label in (
        (model_path, model_spec, "HHEM"),
        (foundation_path, foundation_spec, "foundation"),
    ):
        for filename, expected in spec["files"].items():
            path = root / filename
            if not path.is_file() or factcg._sha256_file(path) != expected:
                raise ValueError(f"{label} artifact differs: {filename}")
    return model_path, foundation_path


def _base_inputs(
    *,
    typed_registration_path: Path,
    base_registration_path: Path,
    base_result_path: Path,
    prior_registration_path: Path,
    prior_result_path: Path,
    registered_result_path: Path,
    prior_state_path: Path,
    dev_path: Path,
    project_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    typed_registration = json.loads(typed_registration_path.read_text())
    selected, base_registration = typed._validate_registration(
        typed_registration,
        project_root=project_root,
        base_registration_path=base_registration_path,
        base_result_path=base_result_path,
        prior_registration_path=prior_registration_path,
        prior_result_path=prior_result_path,
        dev_path=dev_path,
    )
    _failures, registered_result, _prior_state = recovery._failure_ids(
        registration_path=typed_registration_path,
        result_path=registered_result_path,
        prior_state_path=prior_state_path,
    )
    observed = frozenset(registered_result["development"]["observed_ids"])
    fresh = _select_fresh_balanced(selected, observed_ids=observed)
    return fresh, base_registration, registered_result


def create_registration(
    *,
    typed_registration_path: Path,
    base_registration_path: Path,
    base_result_path: Path,
    prior_registration_path: Path,
    prior_result_path: Path,
    registered_result_path: Path,
    prior_state_path: Path,
    dev_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    selected, base_registration, registered_result = _base_inputs(
        typed_registration_path=typed_registration_path,
        base_registration_path=base_registration_path,
        base_result_path=base_result_path,
        prior_registration_path=prior_registration_path,
        prior_result_path=prior_result_path,
        registered_result_path=registered_result_path,
        prior_state_path=prior_state_path,
        dev_path=dev_path,
        project_root=project_root,
    )
    model = {
        "name": HHEM_REPOSITORY,
        "revision": HHEM_REVISION,
        "license": "apache-2.0",
        "architecture": "t5_token_classification_first_token",
        "support_label_index": 1,
        "parameter_count": MODEL_PARAMETER_COUNT,
        "files": HHEM_FILES,
        "remote_code_execution": False,
        "state_dict_transform": "strip_t5_prefix_then_tie_encoder_embedding",
    }
    foundation = {
        "name": FOUNDATION_REPOSITORY,
        "revision": FOUNDATION_REVISION,
        "license": "apache-2.0",
        "files": FOUNDATION_FILES,
    }
    _artifact_paths(model_spec=model, foundation_spec=foundation)
    label_counts = Counter(str(row["label"]) for row in selected)
    registration: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-hhem-capacity-registration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing would establish disjoint development evidence that pinned "
            "HHEM-2.1-Open over the registered top-two evidence meets the frozen "
            "high-precision factual-support gates. It would not validate the sealed "
            "external test split, retrieval quality, universal factuality, or "
            "production latency."
        ),
        "source": {
            "prme_revision": _git_revision(project_root),
            "files": {
                "runner_sha256": factcg._sha256_file(
                    Path(inspect.getfile(create_registration)).resolve()
                ),
                "typed_registration_sha256": factcg._sha256_file(
                    typed_registration_path
                ),
                "typed_result_sha256": factcg._sha256_file(registered_result_path),
                "typed_result_canonical_sha256": registered_result["result_sha256"],
            },
        },
        "dataset": {
            "name": "LLM-AggreFact",
            "repository": "https://huggingface.co/datasets/lytang/LLM-AggreFact",
            "revision": "981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4",
            "license": "cc-by-nd-4.0",
            "split": "development",
            "sha256": factcg._sha256_file(dev_path),
            "source_text_policy": (
                "Authenticated external development file only; public results may "
                "contain identifiers, labels, scores, counts and hashes but no "
                "document, evidence or claim text."
            ),
            "test_access": "forbidden_no_test_path",
        },
        "cohort": {
            "selection_seed": SELECTION_SEED,
            "parent_selected_identity_sha256": json.loads(
                typed_registration_path.read_text()
            )["cohort"]["selected_identity_sha256"],
            "observed_cases_excluded": registered_result["development"][
                "observed_cases"
            ],
            "observed_identity_sha256": registered_result["development"][
                "observed_identity_sha256"
            ],
            "sampling": (
                "exclude_all_typed-reference-v2-observed-identities; group the "
                "remaining parent cohort by label; take the full minority-label "
                "capacity and the same hash-ranked count from the majority label; "
                "hash-order the combined rows"
            ),
            "cases": len(selected),
            "counts_by_label": dict(sorted(label_counts.items())),
            "counts_by_dataset_label": _counts_by_dataset_label(selected),
            "selected_identity_sha256": factcg._canonical_sha256(
                [row["contamination_identifier"] for row in selected]
            ),
        },
        "ranker": {
            **base_registration["model"],
            "source_chunking": "nltk_sentence_chunks_max_550_word_tokens",
            "chunk_selection": "highest_support_probability_then_lowest_index",
            "top_k": 2,
        },
        "model": model,
        "foundation": foundation,
        "protocol": {
            "task": "whole_claim_factual_consistency_capacity",
            "evidence": "registered_top_two_segments_joined_in_source_order",
            "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "batch_size": 2,
            "ranker_batch_size": 2,
            "max_prompt_tokens": 4096,
            "truncation": False,
            "threshold_operator": "strictly_greater_than",
            "published_threshold": 0.5,
            "device": "auto",
            "transformers_version": RUNTIME_TRANSFORMERS,
            "emit_source_text": False,
            "provider_calls": False,
            "test_access": "forbidden_no_test_path",
        },
        "evaluation": {
            "calibration": {
                "supported_precision_min": 0.9,
                "supported_recall_min": 0.6,
                "selection": "maximum_recall_then_precision_then_lowest_threshold",
            },
            "gates": {
                "claims_evaluated_min": len(selected),
                "supported_precision_min": 0.9,
                "supported_recall_min": 0.6,
                "balanced_accuracy_min": 0.75,
                "false_support_rate_max": 0.1,
            },
            "decision_rule": (
                "Do not register or access an external test cohort unless every "
                "development gate passes."
            ),
        },
        "selection": {
            "rationale": (
                "HHEM-2.1-Open is a small Apache-2.0 factual-consistency model "
                "with published AggreFact and RAGTruth evidence. The loader uses "
                "audited standard T5 classes and pinned safetensors without "
                "executing repository code."
            ),
            "disclosure": (
                "The model card and aggregate published results were known. No "
                "HHEM output was observed for any selected identity before this "
                "registration."
            ),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
    base_registration: dict[str, Any],
    typed_registration_path: Path,
    registered_result_path: Path,
    registered_result: dict[str, Any],
    dev_path: Path,
    project_root: Path,
) -> None:
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "llm-aggrefact-hhem-capacity-registration"
    ):
        raise ValueError("HHEM registration kind is invalid")
    source = registration.get("source", {})
    revision = source.get("prme_revision")
    if not isinstance(revision, str) or not factcg._git_is_ancestor(
        revision, project_root
    ):
        raise ValueError("registered PRME revision is not an ancestor")
    expected_source = {
        "runner_sha256": factcg._sha256_file(Path(__file__).resolve()),
        "typed_registration_sha256": factcg._sha256_file(typed_registration_path),
        "typed_result_sha256": factcg._sha256_file(registered_result_path),
        "typed_result_canonical_sha256": registered_result["result_sha256"],
    }
    if source.get("files") != expected_source:
        raise ValueError("registered source artifacts differ")
    expected_dataset = {
        "name": "LLM-AggreFact",
        "repository": "https://huggingface.co/datasets/lytang/LLM-AggreFact",
        "revision": "981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4",
        "license": "cc-by-nd-4.0",
        "split": "development",
        "sha256": factcg._sha256_file(dev_path),
        "source_text_policy": (
            "Authenticated external development file only; public results may "
            "contain identifiers, labels, scores, counts and hashes but no "
            "document, evidence or claim text."
        ),
        "test_access": "forbidden_no_test_path",
    }
    if registration.get("dataset") != expected_dataset:
        raise ValueError("registered development dataset differs")
    cohort = registration.get("cohort", {})
    typed_cohort = json.loads(typed_registration_path.read_text())["cohort"]
    expected_cohort = {
        "selection_seed": SELECTION_SEED,
        "parent_selected_identity_sha256": typed_cohort["selected_identity_sha256"],
        "observed_cases_excluded": registered_result["development"]["observed_cases"],
        "observed_identity_sha256": registered_result["development"][
            "observed_identity_sha256"
        ],
        "sampling": (
            "exclude_all_typed-reference-v2-observed-identities; group the "
            "remaining parent cohort by label; take the full minority-label "
            "capacity and the same hash-ranked count from the majority label; "
            "hash-order the combined rows"
        ),
        "cases": len(selected),
        "counts_by_label": dict(
            sorted(Counter(str(row["label"]) for row in selected).items())
        ),
        "counts_by_dataset_label": _counts_by_dataset_label(selected),
        "selected_identity_sha256": factcg._canonical_sha256(
            [row["contamination_identifier"] for row in selected]
        ),
    }
    if cohort != expected_cohort:
        raise ValueError("registered fresh cohort differs")
    expected_model = {
        "name": HHEM_REPOSITORY,
        "revision": HHEM_REVISION,
        "license": "apache-2.0",
        "architecture": "t5_token_classification_first_token",
        "support_label_index": 1,
        "parameter_count": MODEL_PARAMETER_COUNT,
        "files": HHEM_FILES,
        "remote_code_execution": False,
        "state_dict_transform": "strip_t5_prefix_then_tie_encoder_embedding",
    }
    expected_foundation = {
        "name": FOUNDATION_REPOSITORY,
        "revision": FOUNDATION_REVISION,
        "license": "apache-2.0",
        "files": FOUNDATION_FILES,
    }
    if (
        registration.get("model") != expected_model
        or registration.get("foundation") != expected_foundation
    ):
        raise ValueError("registered HHEM artifacts differ")
    expected_ranker = {
        **base_registration["model"],
        "source_chunking": "nltk_sentence_chunks_max_550_word_tokens",
        "chunk_selection": "highest_support_probability_then_lowest_index",
        "top_k": 2,
    }
    if registration.get("ranker") != expected_ranker:
        raise ValueError("registered ranker differs")
    expected_protocol = {
        "task": "whole_claim_factual_consistency_capacity",
        "evidence": "registered_top_two_segments_joined_in_source_order",
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "batch_size": 2,
        "ranker_batch_size": 2,
        "max_prompt_tokens": 4096,
        "truncation": False,
        "threshold_operator": "strictly_greater_than",
        "published_threshold": 0.5,
        "device": "auto",
        "transformers_version": RUNTIME_TRANSFORMERS,
        "emit_source_text": False,
        "provider_calls": False,
        "test_access": "forbidden_no_test_path",
    }
    if registration.get("protocol") != expected_protocol:
        raise ValueError("registered HHEM protocol differs")
    expected_evaluation = {
        "calibration": {
            "supported_precision_min": 0.9,
            "supported_recall_min": 0.6,
            "selection": "maximum_recall_then_precision_then_lowest_threshold",
        },
        "gates": {
            "claims_evaluated_min": len(selected),
            "supported_precision_min": 0.9,
            "supported_recall_min": 0.6,
            "balanced_accuracy_min": 0.75,
            "false_support_rate_max": 0.1,
        },
        "decision_rule": (
            "Do not register or access an external test cohort unless every "
            "development gate passes."
        ),
    }
    if registration.get("evaluation") != expected_evaluation:
        raise ValueError("registered evaluation gates differ")


def _score_hhem(
    prepared: list[dict[str, Any]],
    *,
    model_spec: dict[str, Any],
    foundation_spec: dict[str, Any],
    batch_size: int,
    max_prompt_tokens: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import torch
    import transformers
    from safetensors.torch import load_file
    from transformers import AutoConfig, AutoTokenizer, T5ForTokenClassification

    if transformers.__version__ != RUNTIME_TRANSFORMERS:
        raise ValueError("Transformers runtime differs from registration")
    model_path, foundation_path = _artifact_paths(
        model_spec=model_spec, foundation_spec=foundation_spec
    )
    config = AutoConfig.from_pretrained(foundation_path, local_files_only=True)
    model = T5ForTokenClassification(config)
    state = {
        key.removeprefix("t5."): value
        for key, value in load_file(model_path / "model.safetensors").items()
    }
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing != ["transformer.encoder.embed_tokens.weight"] or unexpected:
        raise ValueError("HHEM state dictionary does not match audited T5 mapping")
    model.tie_weights()
    if (
        model.transformer.shared.weight.data_ptr()
        != model.transformer.encoder.embed_tokens.weight.data_ptr()
    ):
        raise ValueError("HHEM encoder embedding was not tied")
    if (
        sum(parameter.numel() for parameter in model.parameters())
        != model_spec["parameter_count"]
    ):
        raise ValueError("HHEM parameter count differs")
    tokenizer = AutoTokenizer.from_pretrained(
        foundation_path, local_files_only=True, use_fast=True
    )
    device = factcg._select_device(torch)
    model.to(device)
    model.eval()
    prompts = [
        PROMPT.format(
            premise="\n".join(text for _identifier, text in item["evidence_segments"]),
            hypothesis=item["claim"],
        )
        for item in prepared
    ]
    lengths = [
        len(tokenizer(prompt, add_special_tokens=True).input_ids) for prompt in prompts
    ]
    if max(lengths, default=0) > max_prompt_tokens:
        raise ValueError("HHEM prompt exceeds registered non-truncating limit")
    probabilities: list[float] = []
    started = time.perf_counter()
    with torch.inference_mode():
        for offset in range(0, len(prompts), batch_size):
            encoded = tokenizer(
                prompts[offset : offset + batch_size],
                padding="longest",
                truncation=False,
                return_tensors="pt",
            )
            encoded = {name: value.to(device) for name, value in encoded.items()}
            logits = model(**encoded).logits[:, 0, :]
            probabilities.extend(
                float(value)
                for value in torch.softmax(logits, dim=-1)[:, 1].detach().cpu()
            )
    elapsed = time.perf_counter() - started
    samples = [
        {
            "id": item["id"],
            "dataset": item["dataset"],
            "label": item["label"],
            "support_probability": probability,
            "prompt_tokens": length,
            "source_chunks": item["source_chunks"],
            "selected_chunk_indices": item["selected_chunk_indices"],
            "selected_chunk_scores": item["selected_chunk_scores"],
            "evidence_segments": len(item["evidence_segments"]),
        }
        for item, probability, length in zip(
            prepared, probabilities, lengths, strict=True
        )
    ]
    runtime = {
        "device": device,
        "seconds": elapsed,
        "pairs_scored": len(samples),
        "prompt_tokens_sum": sum(lengths),
        "prompt_tokens_max": max(lengths, default=0),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    return samples, runtime


def run(
    *,
    registration_path: Path,
    typed_registration_path: Path,
    base_registration_path: Path,
    base_result_path: Path,
    prior_registration_path: Path,
    prior_result_path: Path,
    registered_result_path: Path,
    prior_state_path: Path,
    dev_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    selected, base_registration, registered_result = _base_inputs(
        typed_registration_path=typed_registration_path,
        base_registration_path=base_registration_path,
        base_result_path=base_result_path,
        prior_registration_path=prior_registration_path,
        prior_result_path=prior_result_path,
        registered_result_path=registered_result_path,
        prior_state_path=prior_state_path,
        dev_path=dev_path,
        project_root=project_root,
    )
    registration = json.loads(registration_path.read_text())
    _validate_registration(
        registration,
        selected=selected,
        base_registration=base_registration,
        typed_registration_path=typed_registration_path,
        registered_result_path=registered_result_path,
        registered_result=registered_result,
        dev_path=dev_path,
        project_root=project_root,
    )
    protocol = registration["protocol"]
    prepared, ranker_runtime = cascade._prepare_evidence(
        selected,
        model_spec=base_registration["model"],
        top_k=registration["ranker"]["top_k"],
        batch_size=protocol["ranker_batch_size"],
    )
    samples, model_runtime = _score_hhem(
        prepared,
        model_spec=registration["model"],
        foundation_spec=registration["foundation"],
        batch_size=protocol["batch_size"],
        max_prompt_tokens=protocol["max_prompt_tokens"],
    )
    calibration = registration["evaluation"]["calibration"]
    threshold, metrics = factcg._calibrate_threshold(
        samples,
        precision_min=calibration["supported_precision_min"],
        recall_min=calibration["supported_recall_min"],
    )
    fixed_metrics = factcg._metrics(samples, protocol["published_threshold"])
    diagnostics_result = diagnostics._diagnostics(samples)
    gate_results = {"calibration": threshold is not None}
    if metrics is not None:
        gate_results.update(
            factcg._gate_results(metrics, registration["evaluation"]["gates"])
        )
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-hhem-capacity-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "test_accessed": False,
        "registration_sha256": factcg._sha256_file(registration_path),
        "cohort": registration["cohort"],
        "ranker": registration["ranker"],
        "model": registration["model"],
        "foundation": registration["foundation"],
        "protocol": registration["protocol"],
        "development": {
            "published_threshold_metrics": fixed_metrics,
            "selected_threshold": threshold,
            "selected_threshold_metrics": metrics,
            "diagnostics": diagnostics_result,
            "samples": samples,
        },
        "runtime": {"ranker": ranker_runtime, "hhem": model_runtime},
        "gate_results": gate_results,
        "passed": bool(gate_results) and all(gate_results.values()),
        "decision": (
            "eligible_for_external_registration"
            if bool(gate_results) and all(gate_results.values())
            else "development_capacity_rejected"
        ),
        "limitations": [
            "Development-only capacity trial on a disjoint but related source split.",
            "Evidence is selected by the pinned FactCG ranker before HHEM scoring.",
            "Passing would require a separately registered untouched external test.",
            "No external test split was accepted or accessed.",
        ],
    }
    result["result_sha256"] = factcg._canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--typed-registration", type=Path, required=True)
    parser.add_argument("--base-registration", type=Path, required=True)
    parser.add_argument("--base-result", type=Path, required=True)
    parser.add_argument("--prior-registration", type=Path, required=True)
    parser.add_argument("--prior-result", type=Path, required=True)
    parser.add_argument("--registered-result", type=Path, required=True)
    parser.add_argument("--prior-state", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_parser = subparsers.add_parser("register")
    _common_arguments(register_parser)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--registration", type=Path, required=True)
    _common_arguments(run_parser)
    return parser


def main() -> None:
    args = _parser().parse_args()
    common = {
        "typed_registration_path": args.typed_registration.resolve(),
        "base_registration_path": args.base_registration.resolve(),
        "base_result_path": args.base_result.resolve(),
        "prior_registration_path": args.prior_registration.resolve(),
        "prior_result_path": args.prior_result.resolve(),
        "registered_result_path": args.registered_result.resolve(),
        "prior_state_path": args.prior_state.resolve(),
        "dev_path": args.dev.resolve(),
        "output_path": args.output.resolve(),
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        result = create_registration(**common)
        summary = {
            "kind": result["kind"],
            "cases": result["cohort"]["cases"],
            "selected_identity_sha256": result["cohort"]["selected_identity_sha256"],
        }
    else:
        result = run(registration_path=args.registration.resolve(), **common)
        summary = {
            "passed": result["passed"],
            "published_threshold_metrics": result["development"][
                "published_threshold_metrics"
            ],
            "selected_threshold": result["development"]["selected_threshold"],
            "selected_threshold_metrics": result["development"][
                "selected_threshold_metrics"
            ],
            "result_sha256": result["result_sha256"],
        }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
