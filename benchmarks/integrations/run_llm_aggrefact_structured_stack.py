"""Cross-fit FactCG with deterministic claim-to-evidence alignment features."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import subprocess
from typing import Any, Iterable

from benchmarks.integrations import run_llm_aggrefact_factcg as factcg


FEATURE_NAMES = (
    "factcg_logit",
    "alignment_min",
    "alignment_mean",
    "numeric_anchor_coverage",
    "capitalized_anchor_coverage",
    "negation_alignment_min",
    "claim_sentence_count_log1p",
    "claim_content_token_count_log1p",
    "factcg_alignment_interaction",
)
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "hers",
        "him",
        "his",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "ours",
        "she",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "will",
        "with",
        "you",
        "your",
        "yours",
    }
)
NEGATIONS = frozenset({"no", "not", "never", "neither", "nor", "without"})
TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
NUMBER_RE = re.compile(r"(?<!\w)[€£$]?\d[\d,.:/%-]*", re.UNICODE)
CAPITALIZED_RE = re.compile(r"\b[A-Z][^\W_]*(?:['’][^\W_]*)?\b", re.UNICODE)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n+")


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


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_BOUNDARY_RE.split(text.strip()) if part.strip()]


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(text)]


def _content_tokens(text: str) -> set[str]:
    return {
        token
        for token in _tokens(text)
        if token not in STOPWORDS and (len(token) > 1 or token.isdigit())
    }


def _coverage(anchors: set[str], evidence_tokens: set[str]) -> float:
    if not anchors:
        return 1.0
    return len(anchors & evidence_tokens) / len(anchors)


def _logit(probability: float) -> float:
    clipped = min(max(probability, 1e-6), 1 - 1e-6)
    return math.log(clipped / (1 - clipped))


def _alignment_features(doc: str, claim: str, factcg_probability: float) -> list[float]:
    doc_sentences = _split_sentences(doc) or [doc]
    claim_sentences = _split_sentences(claim) or [claim]
    doc_sentence_tokens = [_content_tokens(sentence) for sentence in doc_sentences]
    doc_tokens = set(_tokens(doc))

    alignments: list[float] = []
    negation_alignments: list[float] = []
    claim_content_count = 0
    for claim_sentence in claim_sentences:
        claim_tokens = _content_tokens(claim_sentence)
        claim_content_count += len(claim_tokens)
        scored = [(_coverage(claim_tokens, tokens), index) for index, tokens in enumerate(doc_sentence_tokens)]
        alignment, best_index = max(scored, key=lambda item: (item[0], -item[1]))
        alignments.append(alignment)
        claim_negated = bool(set(_tokens(claim_sentence)) & NEGATIONS)
        evidence_negated = bool(set(_tokens(doc_sentences[best_index])) & NEGATIONS)
        negation_alignments.append(float(claim_negated == evidence_negated))

    numeric_anchors = {match.group(0).casefold() for match in NUMBER_RE.finditer(claim)}
    capitalized_anchors = {
        match.group(0).casefold()
        for match in CAPITALIZED_RE.finditer(claim)
        if match.group(0).casefold() not in STOPWORDS
    }
    factcg_logit = _logit(factcg_probability)
    alignment_min = min(alignments)
    return [
        factcg_logit,
        alignment_min,
        sum(alignments) / len(alignments),
        _coverage(numeric_anchors, doc_tokens),
        _coverage(capitalized_anchors, doc_tokens),
        min(negation_alignments),
        math.log1p(len(claim_sentences)),
        math.log1p(claim_content_count),
        factcg_logit * alignment_min,
    ]


def _group_identity(dataset: str, doc: str) -> str:
    doc_sha256 = hashlib.sha256(doc.encode()).hexdigest()
    return hashlib.sha256(f"{dataset}\0{doc_sha256}".encode()).hexdigest()


def _fold_for_group(group: str, *, seed: str, folds: int) -> int:
    digest = hashlib.sha256(f"{seed}\0{group}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % folds


def _decision_metrics(labels: Iterable[int], decisions: Iterable[bool]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for label, decision in zip(labels, decisions, strict=True):
        actual = label == 1
        if decision and actual:
            tp += 1
        elif decision:
            fp += 1
        elif actual:
            fn += 1
        else:
            tn += 1
    total = tp + fp + tn + fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": (tp + tn) / total if total else 0.0,
        "balanced_accuracy": (recall + specificity) / 2,
        "supported_precision": precision,
        "supported_recall": recall,
        "supported_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "false_support_rate": fp / (fp + tn) if fp + tn else 0.0,
    }


def _model_specification() -> dict[str, Any]:
    return {
        "type": "standard_scaler_logistic_regression",
        "features": list(FEATURE_NAMES),
        "C": 0.1,
        "penalty": "l2",
        "solver": "lbfgs",
        "fit_intercept": True,
        "class_weight": None,
        "max_iter": 1000,
        "tol": 1e-8,
    }


def _protocol_specification() -> dict[str, Any]:
    return {
        "task": "cross_fitted_structured_claim_verification",
        "base_scorer": "pinned_factcg_deberta_v1_scores",
        "sentence_splitter": "regex_terminal_punctuation_v1",
        "tokenizer": "unicode_word_casefold_v1",
        "content_alignment": "minimum_and_mean_of_maximum_claim_sentence_token_coverage",
        "anchors": "exact_numeric_and_capitalized_token_coverage",
        "negation": "best_aligned_sentence_presence_agreement",
        "outer_folds": 5,
        "outer_seed": "prme-llm-aggrefact-structured-stack-v1-outer",
        "inner_folds": 4,
        "inner_seed": "prme-llm-aggrefact-structured-stack-v1-inner",
        "grouping": "exact_dataset_and_document_sha256",
        "inner_threshold_selection": "maximum_recall_then_precision_then_lowest_threshold",
        "threshold_operator": "strictly_greater_than",
        "emit_source_text": False,
        "test_access": "none_in_stage_one",
    }


def _gates_specification() -> dict[str, Any]:
    return {
        "claims_evaluated_min": 1100,
        "supported_precision_min": 0.9,
        "supported_recall_min": 0.6,
        "balanced_accuracy_min": 0.75,
        "false_support_rate_max": 0.1,
        "all_outer_thresholds_available": True,
    }


def _validate_registration(
    registration: dict[str, Any],
    *,
    project_root: Path,
    factcg_result_path: Path,
    dev_path: Path,
) -> None:
    if registration.get("schema_version") != 1 or registration.get("kind") != "llm-aggrefact-structured-stack-registration":
        raise ValueError("registration identity is invalid")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise ValueError("registration source is required")
    revision = source.get("prme_revision")
    if not isinstance(revision, str) or not _git_is_ancestor(revision, project_root):
        raise ValueError("registered PRME revision is not an ancestor of HEAD")
    expected_files = {
        "runner_sha256": factcg._sha256_file(Path(__file__).resolve()),
        "factcg_result_sha256": factcg._sha256_file(factcg_result_path),
    }
    if source.get("files") != expected_files:
        raise ValueError("registration source-file hashes do not match")
    dataset = registration.get("dataset")
    if dataset != {
        "name": "LLM-AggreFact",
        "split": "development",
        "sha256": factcg._sha256_file(dev_path),
        "selected_identity_sha256": "8f40221503bc677f2ac3345302cfb363a2b5584620423c3eeb21bc0ca6dfd9cf",
        "cases": 1100,
        "test_access": "forbidden",
    }:
        raise ValueError("registration dataset does not match")
    if registration.get("model") != _model_specification():
        raise ValueError("registration model does not match")
    if registration.get("protocol") != _protocol_specification():
        raise ValueError("registration protocol does not match")
    if registration.get("gates") != _gates_specification():
        raise ValueError("registration gates do not match")
    runtime = registration.get("runtime")
    if runtime != {"numpy": "2.4.2", "scikit_learn": "1.8.0"}:
        raise ValueError("registration runtime does not match")


def _fit_model(features: list[list[float]], labels: list[int], spec: dict[str, Any]) -> Any:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=spec["C"],
            penalty=spec["penalty"],
            solver=spec["solver"],
            fit_intercept=spec["fit_intercept"],
            class_weight=spec["class_weight"],
            max_iter=spec["max_iter"],
            tol=spec["tol"],
        ),
    )
    model.fit(features, labels)
    return model


def _serialized_model(model: Any) -> dict[str, Any]:
    scaler = model.named_steps["standardscaler"]
    classifier = model.named_steps["logisticregression"]
    return {
        "feature_names": list(FEATURE_NAMES),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coefficient": classifier.coef_[0].tolist(),
        "intercept": float(classifier.intercept_[0]),
        "iterations": int(classifier.n_iter_[0]),
    }


def _score_cross_fitted(
    rows: list[dict[str, Any]],
    *,
    model_spec: dict[str, Any],
    protocol: dict[str, Any],
    precision_min: float,
    recall_min: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outer_folds = protocol["outer_folds"]
    samples: list[dict[str, Any] | None] = [None] * len(rows)
    fold_results: list[dict[str, Any]] = []
    for outer_fold in range(outer_folds):
        train_indices = [index for index, row in enumerate(rows) if row["outer_fold"] != outer_fold]
        validation_indices = [index for index, row in enumerate(rows) if row["outer_fold"] == outer_fold]
        inner_probabilities: dict[int, float] = {}
        for inner_fold in range(protocol["inner_folds"]):
            inner_train = [index for index in train_indices if rows[index]["inner_fold"] != inner_fold]
            inner_validation = [index for index in train_indices if rows[index]["inner_fold"] == inner_fold]
            model = _fit_model(
                [rows[index]["features"] for index in inner_train],
                [rows[index]["label"] for index in inner_train],
                model_spec,
            )
            probabilities = model.predict_proba(
                [rows[index]["features"] for index in inner_validation]
            )[:, 1]
            inner_probabilities.update(
                zip(inner_validation, (float(value) for value in probabilities), strict=True)
            )
        if set(inner_probabilities) != set(train_indices):
            raise ValueError("inner cross-fitting did not score every training case")
        threshold_samples = [
            {"label": rows[index]["label"], "support_probability": inner_probabilities[index]}
            for index in train_indices
        ]
        threshold, threshold_metrics = factcg._calibrate_threshold(
            threshold_samples,
            precision_min=precision_min,
            recall_min=recall_min,
        )
        outer_model = _fit_model(
            [rows[index]["features"] for index in train_indices],
            [rows[index]["label"] for index in train_indices],
            model_spec,
        )
        probabilities = outer_model.predict_proba(
            [rows[index]["features"] for index in validation_indices]
        )[:, 1]
        for index, probability in zip(validation_indices, probabilities, strict=True):
            samples[index] = {
                "id": rows[index]["id"],
                "dataset": rows[index]["dataset"],
                "label": rows[index]["label"],
                "outer_fold": outer_fold,
                "support_probability": float(probability),
                "threshold": threshold,
                "supported": threshold is not None and float(probability) > threshold,
                "features": dict(zip(FEATURE_NAMES, rows[index]["features"], strict=True)),
            }
        fold_samples = [samples[index] for index in validation_indices]
        fold_results.append(
            {
                "fold": outer_fold,
                "train_cases": len(train_indices),
                "validation_cases": len(validation_indices),
                "train_labels": dict(sorted(Counter(str(rows[index]["label"]) for index in train_indices).items())),
                "validation_labels": dict(sorted(Counter(str(rows[index]["label"]) for index in validation_indices).items())),
                "threshold": threshold,
                "threshold_metrics": threshold_metrics,
                "metrics": _decision_metrics(
                    (sample["label"] for sample in fold_samples if sample is not None),
                    (sample["supported"] for sample in fold_samples if sample is not None),
                ),
                "model": _serialized_model(outer_model),
            }
        )
    if any(sample is None for sample in samples):
        raise ValueError("outer cross-fitting did not score every case")
    return [sample for sample in samples if sample is not None], fold_results


def run(
    *,
    registration_path: Path,
    factcg_result_path: Path,
    dev_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    import numpy
    import pyarrow
    import sklearn

    registration = json.loads(registration_path.read_text())
    _validate_registration(
        registration,
        project_root=project_root,
        factcg_result_path=factcg_result_path,
        dev_path=dev_path,
    )
    if numpy.__version__ != registration["runtime"]["numpy"] or sklearn.__version__ != registration["runtime"]["scikit_learn"]:
        raise ValueError("installed numerical runtime does not match registration")

    factcg_result = json.loads(factcg_result_path.read_text())
    factcg_samples = factcg_result.get("calibration", {}).get("samples")
    if not isinstance(factcg_samples, list) or len(factcg_samples) != 1100:
        raise ValueError("FactCG result does not contain the registered development scores")
    factcg_by_id = {sample["id"]: sample for sample in factcg_samples}
    selected_rows = factcg._select_cohort(
        factcg._load_rows(dev_path),
        split="dev",
        seed="prme-llm-aggrefact-factcg-v1",
        per_label_per_dataset=50,
    )
    if factcg._identity_sha256(selected_rows) != registration["dataset"]["selected_identity_sha256"]:
        raise ValueError("selected development identities do not match registration")

    rows: list[dict[str, Any]] = []
    for row in selected_rows:
        row_id = row["contamination_identifier"]
        prior = factcg_by_id.get(row_id)
        if prior is None or prior["dataset"] != row["dataset"] or prior["label"] != row["label"]:
            raise ValueError("FactCG sample identity does not match source row")
        group = _group_identity(row["dataset"], row["doc"])
        rows.append(
            {
                "id": row_id,
                "dataset": row["dataset"],
                "label": row["label"],
                "group": group,
                "outer_fold": _fold_for_group(group, seed=registration["protocol"]["outer_seed"], folds=registration["protocol"]["outer_folds"]),
                "inner_fold": _fold_for_group(group, seed=registration["protocol"]["inner_seed"], folds=registration["protocol"]["inner_folds"]),
                "features": _alignment_features(row["doc"], row["claim"], prior["support_probability"]),
            }
        )

    samples, folds = _score_cross_fitted(
        rows,
        model_spec=registration["model"],
        protocol=registration["protocol"],
        precision_min=registration["gates"]["supported_precision_min"],
        recall_min=registration["gates"]["supported_recall_min"],
    )
    metrics = _decision_metrics(
        (sample["label"] for sample in samples),
        (sample["supported"] for sample in samples),
    )
    gates = registration["gates"]
    gate_results = {
        "claims_evaluated_min": len(samples) >= gates["claims_evaluated_min"],
        "supported_precision_min": metrics["supported_precision"] >= gates["supported_precision_min"],
        "supported_recall_min": metrics["supported_recall"] >= gates["supported_recall_min"],
        "balanced_accuracy_min": metrics["balanced_accuracy"] >= gates["balanced_accuracy_min"],
        "false_support_rate_max": metrics["false_support_rate"] <= gates["false_support_rate_max"],
        "all_outer_thresholds_available": all(fold["threshold"] is not None for fold in folds),
    }
    by_dataset: dict[str, dict[str, Any]] = {}
    for dataset in sorted({sample["dataset"] for sample in samples}):
        subset = [sample for sample in samples if sample["dataset"] == dataset]
        by_dataset[dataset] = _decision_metrics(
            (sample["label"] for sample in subset),
            (sample["supported"] for sample in subset),
        )
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-structured-stack-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": factcg._sha256_file(registration_path),
        "source": registration["source"],
        "dataset": registration["dataset"],
        "model": registration["model"],
        "protocol": registration["protocol"],
        "gates": gates,
        "execution": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": numpy.__version__,
            "scikit_learn": sklearn.__version__,
            "pyarrow": pyarrow.__version__,
        },
        "metrics": metrics,
        "metrics_by_dataset": by_dataset,
        "folds": folds,
        "samples": samples,
        "gate_results": gate_results,
        "passed": all(gate_results.values()),
        "test": {"accessed": False, "reason": "stage_one_development_cross_fit_only"},
    }
    result["result_sha256"] = factcg._canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--factcg-result", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    result = run(
        registration_path=args.registration.resolve(),
        factcg_result_path=args.factcg_result.resolve(),
        dev_path=args.dev.resolve(),
        output_path=args.output.resolve(),
        project_root=project_root,
    )
    print(
        json.dumps(
            {
                "gate_results": result["gate_results"],
                "metrics": result["metrics"],
                "passed": result["passed"],
                "result_sha256": result["result_sha256"],
                "test_accessed": result["test"]["accessed"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
