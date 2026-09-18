"""Confirm a development-calibrated candidate plus Jev proposal pipeline.

The calibration rule was selected only after the original rule failed on the
Walmart-Amazon validation split. This runner freezes that rule and evaluates it
on the test split, whose Jev outputs were not used during calibration.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
from typing import Any
from uuid import UUID

from benchmarks.integrations import run_jev_candidate_pipeline as base
from benchmarks.integrations import run_jev_claim_verification as common
from benchmarks.integrations import run_jev_product_alignment as jev
from prme.integrations.product_candidates import (
    PRODUCT_CANDIDATE_POLICY,
    rank_product_alignment_candidates,
)


DATASET = base.DATASET
SOURCE = base.SOURCE
FILES = base.FILES
SPLIT = "test"
LINK_SCORE_MIN = 1.6
SAME_NAME_MIN = 0.8
COMPATIBLE_PRICE_MIN = 0.5
RESULT_ROOT = Path("benchmarks/results/research/2026-09-17")
UPSTREAM_ARTIFACTS = {
    "candidate_registration": RESULT_ROOT
    / "product-candidate-walmart-amazon-confirmation-v1-registration.json",
    "candidate_result": RESULT_ROOT
    / "product-candidate-walmart-amazon-confirmation-v1-result.json",
    "advisor_registration": RESULT_ROOT
    / "jev-product-alignment-confirmation-v1-registration.json",
    "advisor_result": RESULT_ROOT
    / "jev-product-alignment-confirmation-v1-result.json",
    "calibration_registration": RESULT_ROOT
    / "jev-candidate-walmart-amazon-confirmation-v1-registration.json",
    "calibration_result": RESULT_ROOT
    / "jev-candidate-walmart-amazon-confirmation-v1-result.json",
}


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _inputs(
    root: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    left = _read(root / "tableA.csv")
    right = _read(root / "tableB.csv")
    pairs = _read(root / f"{SPLIT}.csv")
    product_fields = {"id", "title", "category", "brand", "modelno", "price"}
    if (
        not left
        or not right
        or set(left[0]) != product_fields
        or set(right[0]) != product_fields
        or not pairs
        or set(pairs[0]) != {"ltable_id", "rtable_id", "label"}
    ):
        raise ValueError("Walmart-Amazon dataset schema differs")
    left_ids = {row["id"] for row in left}
    right_ids = {row["id"] for row in right}
    if (
        len(left_ids) != len(left)
        or len(right_ids) != len(right)
        or any(
            row["ltable_id"] not in left_ids
            or row["rtable_id"] not in right_ids
            or row["label"] not in {"0", "1"}
            for row in pairs
        )
    ):
        raise ValueError("Walmart-Amazon dataset identities differ")
    return left, right, pairs


def _pairs(root: Path) -> list[dict[str, Any]]:
    left, right, rows = _inputs(root)
    left_by_id = {row["id"]: row for row in left}
    right_by_id = {row["id"]: row for row in right}
    return [
        {
            "id": f"{SPLIT}-{index:05d}-{row['ltable_id']}-{row['rtable_id']}",
            "left_id": row["ltable_id"],
            "right_id": row["rtable_id"],
            "label": int(row["label"]),
            "entity_a": base._product(left_by_id[row["ltable_id"]]),
            "entity_b": base._product(right_by_id[row["rtable_id"]]),
        }
        for index, row in enumerate(rows)
    ]


def _candidate_set(root: Path) -> tuple[set[tuple[UUID, UUID]], dict[str, Any]]:
    left, right, _ = _inputs(root)
    candidates = rank_product_alignment_candidates(
        base._records(left, right),
        top_k=base.TOP_K,
        min_score=base.MIN_SCORE,
        cross_catalog_only=True,
    )
    candidate_pairs = {
        (item.left.node_id, item.right.node_id) for item in candidates
    }
    identity = [
        [
            str(item.left.node_id),
            str(item.right.node_id),
            item.score,
            item.shared_feature_count,
            [str(node_id) for node_id in item.selected_by],
        ]
        for item in candidates
    ]
    cross_product = len(left) * len(right)
    return candidate_pairs, {
        "candidate_pairs": len(candidates),
        "cross_product_pairs": cross_product,
        "cross_product_reduction": 1 - len(candidates) / cross_product,
        "candidate_identity_sha256": common._canonical_sha256(identity),
    }


def _routed(pair: dict[str, Any], candidates: set[tuple[UUID, UUID]]) -> bool:
    identity = tuple(
        sorted(
            (
                base._node_id("walmart", pair["left_id"]),
                base._node_id("amazon", pair["right_id"]),
            ),
            key=str,
        )
    )
    return identity in candidates


def _dataset(root: Path) -> dict[str, Any]:
    left, right, rows = _inputs(root)
    return {
        "name": DATASET,
        "source": SOURCE,
        "split": SPLIT,
        "files": {
            name: {
                "sha256": common._sha256_file(root / name),
                "bytes": (root / name).stat().st_size,
            }
            for name in FILES
        },
        "left_entities": len(left),
        "right_entities": len(right),
        "labeled_pairs": len(rows),
        "labels": dict(sorted(Counter(row["label"] for row in rows).items())),
        "pair_identity_sha256": common._canonical_sha256(
            [[row["ltable_id"], row["rtable_id"], row["label"]] for row in rows]
        ),
    }


def _decision(answers: dict[str, Any]) -> bool:
    return bool(
        answers["link_state"]["score"] >= LINK_SCORE_MIN
        and answers["same_name"]["noul"] >= SAME_NAME_MIN
        and answers["compatible_price"]["noul"] >= COMPATIBLE_PRICE_MIN
    )


def _artifact(project_root: Path, relative: Path) -> tuple[dict[str, Any], str]:
    path = project_root / relative
    return json.loads(path.read_text()), common._sha256_file(path)


def _development_evidence(project_root: Path) -> dict[str, Any]:
    registration, registration_hash = _artifact(
        project_root, UPSTREAM_ARTIFACTS["calibration_registration"]
    )
    result, result_hash = _artifact(
        project_root, UPSTREAM_ARTIFACTS["calibration_result"]
    )
    if (
        registration.get("kind")
        != "jev-candidate-product-pipeline-registration"
        or result.get("kind") != "jev-candidate-product-pipeline-result"
        or result.get("registration_sha256") != registration_hash
        or result.get("passed") is not False
    ):
        raise ValueError("calibration development evidence is invalid")
    samples = result.get("evaluation", {}).get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("calibration development samples are unavailable")
    labels: list[bool] = []
    predictions: list[bool] = []
    for sample in samples:
        labels.append(sample["label"] == 1)
        predictions.append(
            _decision(
                {
                    "link_state": {"score": sample.get("link_score", 0.0)},
                    "same_name": {"noul": sample.get("same_name_noul", 0.0)},
                    "compatible_price": {
                        "noul": sample.get("compatible_price_noul", 0.0)
                    },
                }
            )
            if sample["candidate_routed"]
            else False
        )
    metrics = base._metrics(labels, predictions)
    if metrics["proposal_precision"] < 0.90 or metrics["proposal_recall"] < 0.50:
        raise ValueError("calibrated development policy does not clear its gates")
    return {
        "registration_file_sha256": registration_hash,
        "result_file_sha256": result_hash,
        "result_sha256": result["result_sha256"],
        "original_pipeline_passed": result["passed"],
        "calibrated_metrics": metrics,
    }


def _upstream(project_root: Path) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    values: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for name, relative in UPSTREAM_ARTIFACTS.items():
        values[name], hashes[name] = _artifact(project_root, relative)
        evidence[name] = {
            "path": str(relative),
            "file_sha256": hashes[name],
            "kind": values[name].get("kind"),
            "passed": values[name].get("passed"),
            "result_sha256": values[name].get("result_sha256"),
        }
    for stem in ("candidate", "advisor"):
        registration = values[f"{stem}_registration"]
        result = values[f"{stem}_result"]
        if not result.get("passed") or result.get("registration_sha256") != hashes[
            f"{stem}_registration"
        ]:
            raise ValueError(f"upstream {stem} evidence is invalid")
        if not str(registration.get("kind", "")).endswith("registration"):
            raise ValueError(f"upstream {stem} registration is invalid")
    _development_evidence(project_root)
    return evidence


def _protocol() -> dict[str, Any]:
    return {
        "candidate_policy": PRODUCT_CANDIDATE_POLICY,
        "candidate_top_k_per_entity": base.TOP_K,
        "candidate_minimum_cosine_score": base.MIN_SCORE,
        "candidate_pair_union": "a pair survives when selected by either endpoint",
        "catalog_labels": ["walmart", "amazon"],
        "cross_catalog_only": True,
        "product_fields": {"name": "title", "manufacturer": "brand", "price": "price"},
        "jev_model": jev.MODEL,
        "jev_api_url": jev.API_URL,
        "jev_questions": jev.QUESTIONS,
        "jev_questions_sha256": common._canonical_sha256(jev.QUESTIONS),
        "proposal_rule": {
            "link_state_score_min": LINK_SCORE_MIN,
            "same_name_noul_min": SAME_NAME_MIN,
            "compatible_price_noul_min": COMPATIBLE_PRICE_MIN,
            "operator": "all",
        },
        "concurrency": base.CONCURRENCY,
        "timeout_seconds": base.TIMEOUT_SECONDS,
        "max_attempts": base.MAX_ATTEMPTS,
        "mutation_authorized": False,
        "automatic_merge_authorized": False,
    }


def _evaluation(pair_count: int, routed_count: int) -> dict[str, Any]:
    return {
        "gates": {
            "pairs_evaluated_min": pair_count,
            "valid_provider_responses_min": routed_count,
            "candidate_positive_recall_min": 0.95,
            "proposal_precision_min": 0.90,
            "proposal_recall_min": 0.50,
            "false_positive_rate_max": 0.01,
            "recall_gain_over_exact_name_min": 0.20,
            "cross_product_reduction_min": 0.99,
            "p95_request_seconds_max": 1.0,
        },
        "decision_rule": (
            "Every gate must pass on the untouched Jev outputs before the calibrated "
            "pipeline may ship as an optional unverified proposal workflow."
        ),
    }


def _snapshot(
    root: Path,
) -> tuple[list[dict[str, Any]], set[tuple[UUID, UUID]], dict[str, Any]]:
    pairs = _pairs(root)
    candidates, values = _candidate_set(root)
    routed = [pair for pair in pairs if _routed(pair, candidates)]
    values.update(
        {
            "routed_labeled_pairs": len(routed),
            "routed_labels": dict(
                sorted(Counter(str(pair["label"]) for pair in routed).items())
            ),
            "routed_pair_identity_sha256": common._canonical_sha256(
                [pair["id"] for pair in routed]
            ),
        }
    )
    return pairs, candidates, values


def create_registration(
    *, dataset_root: Path, project_root: Path, output_path: Path
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("Registration output already exists")
    pairs, _, snapshot = _snapshot(dataset_root)
    registration = {
        "schema_version": 1,
        "kind": "jev-calibrated-product-pipeline-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing confirms the development-calibrated candidate plus Jev rule "
            "as an optional unverified product-alias proposal pipeline. It does not "
            "authorize automatic identity merges."
        ),
        "source": {
            "prme_revision": common._git_revision(project_root),
            "runner_sha256": common._sha256_file(Path(__file__).resolve()),
            "base_runner_sha256": common._sha256_file(
                project_root / "benchmarks/integrations/run_jev_candidate_pipeline.py"
            ),
            "candidate_source_sha256": common._sha256_file(
                project_root / "src/prme/integrations/product_candidates.py"
            ),
            "jev_runner_sha256": common._sha256_file(
                project_root / "benchmarks/integrations/run_jev_product_alignment.py"
            ),
            "upstream_evidence": _upstream(project_root),
            "calibration_development": _development_evidence(project_root),
        },
        "dataset": _dataset(dataset_root),
        "candidate_snapshot": snapshot,
        "provider": {
            "name": "TypeSafe AI",
            "model": jev.MODEL,
            "endpoint": jev.API_URL,
            "credential_environment": ["JEV_API_KEY", "TYPESAFE_API_KEY"],
        },
        "protocol": _protocol(),
        "evaluation": _evaluation(len(pairs), snapshot["routed_labeled_pairs"]),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any], *, dataset_root: Path, project_root: Path
) -> tuple[list[dict[str, Any]], set[tuple[UUID, UUID]], dict[str, Any]]:
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "jev-calibrated-product-pipeline-registration"
    ):
        raise ValueError("invalid calibrated Jev pipeline registration")
    revision = registration.get("source", {}).get("prme_revision")
    pairs, candidates, snapshot = _snapshot(dataset_root)
    source = registration.get("source", {})
    if (
        not isinstance(revision, str)
        or not common._git_is_ancestor(revision, project_root)
        or source.get("runner_sha256")
        != common._sha256_file(Path(__file__).resolve())
        or source.get("base_runner_sha256")
        != common._sha256_file(
            project_root / "benchmarks/integrations/run_jev_candidate_pipeline.py"
        )
        or source.get("candidate_source_sha256")
        != common._sha256_file(
            project_root / "src/prme/integrations/product_candidates.py"
        )
        or source.get("jev_runner_sha256")
        != common._sha256_file(
            project_root / "benchmarks/integrations/run_jev_product_alignment.py"
        )
        or source.get("upstream_evidence") != _upstream(project_root)
        or source.get("calibration_development")
        != _development_evidence(project_root)
        or registration.get("dataset") != _dataset(dataset_root)
        or registration.get("candidate_snapshot") != snapshot
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation")
        != _evaluation(len(pairs), snapshot["routed_labeled_pairs"])
    ):
        raise ValueError("registered calibrated Jev pipeline inputs differ")
    return pairs, candidates, snapshot


def _gate(
    *,
    candidate_recall: float,
    pipeline: dict[str, Any],
    baseline: dict[str, Any],
    response_count: int,
    pair_count: int,
    reduction: float,
    p95: float,
    registration: dict[str, Any],
) -> dict[str, bool]:
    gates = registration["evaluation"]["gates"]
    return {
        "pairs_evaluated_min": pair_count >= gates["pairs_evaluated_min"],
        "valid_provider_responses_min": response_count
        >= gates["valid_provider_responses_min"],
        "candidate_positive_recall_min": candidate_recall
        >= gates["candidate_positive_recall_min"],
        "proposal_precision_min": pipeline["proposal_precision"]
        >= gates["proposal_precision_min"],
        "proposal_recall_min": pipeline["proposal_recall"]
        >= gates["proposal_recall_min"],
        "false_positive_rate_max": pipeline["false_positive_rate"]
        <= gates["false_positive_rate_max"],
        "recall_gain_over_exact_name_min": pipeline["proposal_recall"]
        - baseline["proposal_recall"]
        >= gates["recall_gain_over_exact_name_min"],
        "cross_product_reduction_min": reduction
        >= gates["cross_product_reduction_min"],
        "p95_request_seconds_max": p95 <= gates["p95_request_seconds_max"],
    }


def _result(
    *,
    pairs: list[dict[str, Any]],
    candidates: set[tuple[UUID, UUID]],
    snapshot: dict[str, Any],
    state: dict[str, Any],
    registration: dict[str, Any],
    registration_path: Path,
) -> dict[str, Any]:
    labels = [pair["label"] == 1 for pair in pairs]
    routed_flags = [_routed(pair, candidates) for pair in pairs]
    baseline_predictions = [
        base._normalize(pair["entity_a"]["name"])
        == base._normalize(pair["entity_b"]["name"])
        for pair in pairs
    ]
    predictions: list[bool] = []
    samples: list[dict[str, Any]] = []
    elapsed: list[float] = []
    input_tokens = output_tokens = attempts = valid = 0
    for pair, routed in zip(pairs, routed_flags):
        sample: dict[str, Any] = {
            "id": pair["id"],
            "label": pair["label"],
            "candidate_routed": routed,
        }
        if routed:
            item = state["results"].get(pair["id"])
            if not isinstance(item, dict):
                raise ValueError(f"missing durable response for {pair['id']}")
            response = jev._validate_response(item["response"])
            answers = response["answers"]
            decision = _decision(answers)
            usage = response["usage"]
            valid += 1
            attempts += item["attempts"]
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            elapsed.append(item["elapsed_seconds"])
            sample.update(
                {
                    "link_score": answers["link_state"]["score"],
                    "link_confidence": answers["link_state"]["confidence"],
                    "link_probabilities": answers["link_state"]["probabilities"],
                    "same_name_noul": answers["same_name"]["noul"],
                    "same_manufacturer_noul": answers["same_manufacturer"]["noul"],
                    "compatible_price_noul": answers["compatible_price"]["noul"],
                    "proposal_recommended": decision,
                    "attempts": item["attempts"],
                    "elapsed_seconds": item["elapsed_seconds"],
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                }
            )
        else:
            decision = False
            sample["proposal_recommended"] = False
        predictions.append(decision)
        samples.append(sample)
    positives = sum(labels)
    candidate_positive_recall = (
        sum(label and routed for label, routed in zip(labels, routed_flags)) / positives
        if positives
        else 0.0
    )
    baseline = base._metrics(labels, baseline_predictions)
    pipeline = base._metrics(labels, predictions)
    p95 = sorted(elapsed)[math.ceil(len(elapsed) * 0.95) - 1]
    gate_results = _gate(
        candidate_recall=candidate_positive_recall,
        pipeline=pipeline,
        baseline=baseline,
        response_count=valid,
        pair_count=len(pairs),
        reduction=snapshot["cross_product_reduction"],
        p95=p95,
        registration=registration,
    )
    passed = all(gate_results.values())
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "jev-calibrated-product-pipeline-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": common._sha256_file(registration_path),
        "dataset": registration["dataset"],
        "candidate_snapshot": snapshot,
        "provider": registration["provider"],
        "protocol": registration["protocol"],
        "evaluation": {
            "candidate_positive_recall": candidate_positive_recall,
            "exact_name_baseline": baseline,
            "calibrated_jev_candidate_pipeline": pipeline,
            "gate_results": gate_results,
            "samples": samples,
        },
        "runtime": {
            "requests": valid,
            "attempts": attempts,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "median_request_seconds": sorted(elapsed)[len(elapsed) // 2],
            "p95_request_seconds": p95,
            "max_request_seconds": max(elapsed),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "passed": passed,
        "decision": (
            "optional_calibrated_jev_proposal_pipeline_confirmed"
            if passed
            else "calibrated_jev_proposal_pipeline_rejected"
        ),
        "automatic_merge_authorized": False,
        "limitations": [
            "The confirmed output is an unverified proposal requiring explicit review.",
            "One structured retail-product dataset does not establish other domains.",
            "The external service receives the three supplied product fields.",
            "The result does not authorize node merging or source retirement.",
        ],
    }
    result["result_sha256"] = common._canonical_sha256(result)
    return result


async def run(
    *,
    registration_path: Path,
    dataset_root: Path,
    state_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("Result output already exists")
    registration = json.loads(registration_path.read_text())
    pairs, candidates, snapshot = _validate_registration(
        registration, dataset_root=dataset_root, project_root=project_root
    )
    routed = [pair for pair in pairs if _routed(pair, candidates)]
    state = await base._execute(
        routed,
        project_root=project_root,
        registration=registration,
        registration_path=registration_path,
        state_path=state_path,
    )
    result = _result(
        pairs=pairs,
        candidates=candidates,
        snapshot=snapshot,
        state=state,
        registration=registration,
        registration_path=registration_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--dataset-root", type=Path, required=True)
    register.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--output", type=Path, required=True)
    execute = subparsers.add_parser("run")
    execute.add_argument("--dataset-root", type=Path, required=True)
    execute.add_argument("--project-root", type=Path, default=Path.cwd())
    execute.add_argument("--registration", type=Path, required=True)
    execute.add_argument("--state", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "register":
        result = create_registration(
            dataset_root=args.dataset_root.resolve(),
            project_root=args.project_root.resolve(),
            output_path=args.output.resolve(),
        )
        print(
            json.dumps(
                {
                    "dataset": result["dataset"],
                    "candidate_snapshot": result["candidate_snapshot"],
                    "calibration_development": result["source"][
                        "calibration_development"
                    ],
                    "evaluation": result["evaluation"],
                },
                indent=2,
            )
        )
        return
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            dataset_root=args.dataset_root.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            project_root=args.project_root.resolve(),
        )
    )
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "decision": result["decision"],
                "metrics": result["evaluation"][
                    "calibrated_jev_candidate_pipeline"
                ],
                "gate_results": result["evaluation"]["gate_results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
