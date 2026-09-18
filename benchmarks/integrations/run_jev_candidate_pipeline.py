"""Register and evaluate candidate routing followed by Jev product advice.

The trial evaluates the complete Walmart-Amazon validation split.  Candidate
generation runs over both full catalogs; Jev is called only for routed labeled
pairs.  A non-routed pair is an end-to-end negative prediction.  No graph state
is mutated and no source product text is committed in the result artifact.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from benchmarks.integrations import run_jev_claim_verification as common
from benchmarks.integrations import run_jev_product_alignment as jev
from prme.integrations.product_candidates import (
    PRODUCT_CANDIDATE_POLICY,
    ProductCandidateEntity,
    rank_product_alignment_candidates,
)
from prme.integrations.typesafe import ProductEntity


DATASET = "DeepMatcher Structured Walmart-Amazon"
SOURCE = (
    "https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/"
    "Structured/Walmart-Amazon/exp_data/"
)
FILES = ("tableA.csv", "tableB.csv", "train.csv", "valid.csv", "test.csv")
SPLIT = "valid"
TOP_K = 5
MIN_SCORE = 0.1
CONCURRENCY = 6
TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 3
RESULT_ROOT = Path("benchmarks/results/research/2026-09-17")
UPSTREAM_ARTIFACTS = {
    "candidate_registration": RESULT_ROOT
    / "product-candidate-walmart-amazon-confirmation-v1-registration.json",
    "candidate_result": RESULT_ROOT
    / "product-candidate-walmart-amazon-confirmation-v1-result.json",
    "jev_registration": RESULT_ROOT
    / "jev-product-alignment-confirmation-v1-registration.json",
    "jev_result": RESULT_ROOT / "jev-product-alignment-confirmation-v1-result.json",
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


def _node_id(catalog: str, identity: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"prme:{DATASET}:{catalog}:{identity}")


def _product(row: dict[str, str]) -> dict[str, str]:
    return {
        "name": row["title"],
        "manufacturer": row["brand"],
        "price": row["price"],
    }


def _records(
    left: list[dict[str, str]], right: list[dict[str, str]]
) -> list[ProductCandidateEntity]:
    return [
        ProductCandidateEntity(
            node_id=_node_id(catalog, row["id"]),
            catalog=catalog,
            product=ProductEntity.model_validate(_product(row)),
        )
        for catalog, rows in (("walmart", left), ("amazon", right))
        for row in rows
    ]


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
            "entity_a": _product(left_by_id[row["ltable_id"]]),
            "entity_b": _product(right_by_id[row["rtable_id"]]),
        }
        for index, row in enumerate(rows)
    ]


def _candidate_set(root: Path) -> tuple[set[tuple[UUID, UUID]], dict[str, Any]]:
    left, right, _ = _inputs(root)
    candidates = rank_product_alignment_candidates(
        _records(left, right),
        top_k=TOP_K,
        min_score=MIN_SCORE,
        cross_catalog_only=True,
    )
    candidate_pairs = {
        (item.left.node_id, item.right.node_id) for item in candidates
    }
    candidate_identity = [
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
        "candidate_identity_sha256": common._canonical_sha256(candidate_identity),
    }


def _routed(pair: dict[str, Any], candidates: set[tuple[UUID, UUID]]) -> bool:
    identity = tuple(
        sorted(
            (
                _node_id("walmart", pair["left_id"]),
                _node_id("amazon", pair["right_id"]),
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


def _upstream(project_root: Path) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for name, relative in UPSTREAM_ARTIFACTS.items():
        path = project_root / relative
        value = json.loads(path.read_text())
        loaded[name] = {
            "path": str(relative),
            "file_sha256": common._sha256_file(path),
            "kind": value.get("kind"),
            "passed": value.get("passed"),
            "result_sha256": value.get("result_sha256"),
        }
    candidate_registration = json.loads(
        (project_root / UPSTREAM_ARTIFACTS["candidate_registration"]).read_text()
    )
    candidate_result = json.loads(
        (project_root / UPSTREAM_ARTIFACTS["candidate_result"]).read_text()
    )
    jev_registration = json.loads(
        (project_root / UPSTREAM_ARTIFACTS["jev_registration"]).read_text()
    )
    jev_result = json.loads(
        (project_root / UPSTREAM_ARTIFACTS["jev_result"]).read_text()
    )
    if (
        candidate_registration.get("kind")
        != "product-candidate-generation-registration"
        or candidate_result.get("kind") != "product-candidate-generation-result"
        or not candidate_result.get("passed")
        or candidate_result.get("registration_sha256")
        != loaded["candidate_registration"]["file_sha256"]
        or jev_registration.get("kind") != "jev-product-alignment-registration"
        or jev_result.get("kind") != "jev-product-alignment-result"
        or not jev_result.get("passed")
        or jev_result.get("registration_sha256")
        != loaded["jev_registration"]["file_sha256"]
    ):
        raise ValueError("upstream candidate or Jev evidence is invalid")
    return loaded


def _protocol() -> dict[str, Any]:
    return {
        "candidate_policy": PRODUCT_CANDIDATE_POLICY,
        "candidate_top_k_per_entity": TOP_K,
        "candidate_minimum_cosine_score": MIN_SCORE,
        "candidate_pair_union": "a pair survives when selected by either endpoint",
        "catalog_labels": ["walmart", "amazon"],
        "cross_catalog_only": True,
        "product_fields": {"name": "title", "manufacturer": "brand", "price": "price"},
        "jev_model": jev.MODEL,
        "jev_api_url": jev.API_URL,
        "jev_questions": jev.QUESTIONS,
        "jev_questions_sha256": common._canonical_sha256(jev.QUESTIONS),
        "proposal_rule": "link_state.score >= 1.5",
        "concurrency": CONCURRENCY,
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
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
            "false_positive_rate_max": 0.05,
            "recall_gain_over_exact_name_min": 0.20,
            "cross_product_reduction_min": 0.99,
            "p95_request_seconds_max": 1.0,
        },
        "decision_rule": (
            "Every gate must pass before candidate routing and Jev may ship as an "
            "optional proposal pipeline. Automatic identity merges remain forbidden."
        ),
    }


def _snapshot(root: Path) -> tuple[list[dict[str, Any]], set[tuple[UUID, UUID]], dict[str, Any]]:
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
        "kind": "jev-candidate-product-pipeline-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing confirms candidate routing followed by Jev as an optional "
            "unverified product-alias proposal pipeline on one held-out dataset. "
            "It does not authorize automatic identity merges."
        ),
        "source": {
            "prme_revision": common._git_revision(project_root),
            "runner_sha256": common._sha256_file(Path(__file__).resolve()),
            "candidate_source_sha256": common._sha256_file(
                project_root / "src/prme/integrations/product_candidates.py"
            ),
            "jev_runner_sha256": common._sha256_file(
                project_root / "benchmarks/integrations/run_jev_product_alignment.py"
            ),
            "typesafe_source_sha256": common._sha256_file(
                project_root / "src/prme/integrations/typesafe.py"
            ),
            "upstream_evidence": _upstream(project_root),
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
        or registration.get("kind") != "jev-candidate-product-pipeline-registration"
    ):
        raise ValueError("invalid Jev candidate pipeline registration")
    revision = registration.get("source", {}).get("prme_revision")
    pairs, candidates, snapshot = _snapshot(dataset_root)
    source = registration.get("source", {})
    if (
        not isinstance(revision, str)
        or not common._git_is_ancestor(revision, project_root)
        or source.get("runner_sha256")
        != common._sha256_file(Path(__file__).resolve())
        or source.get("candidate_source_sha256")
        != common._sha256_file(
            project_root / "src/prme/integrations/product_candidates.py"
        )
        or source.get("jev_runner_sha256")
        != common._sha256_file(
            project_root / "benchmarks/integrations/run_jev_product_alignment.py"
        )
        or source.get("typesafe_source_sha256")
        != common._sha256_file(project_root / "src/prme/integrations/typesafe.py")
        or source.get("upstream_evidence") != _upstream(project_root)
        or registration.get("dataset") != _dataset(dataset_root)
        or registration.get("candidate_snapshot") != snapshot
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation")
        != _evaluation(len(pairs), snapshot["routed_labeled_pairs"])
    ):
        raise ValueError("registered Jev candidate pipeline inputs differ")
    return pairs, candidates, snapshot


def _api_key(project_root: Path) -> str:
    for name in ("JEV_API_KEY", "TYPESAFE_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        from dotenv import dotenv_values

        values = dotenv_values(project_root / ".env")
    except Exception as exc:  # pragma: no cover - installation/environment failure
        raise RuntimeError("unable to read the project .env") from exc
    for name in ("JEV_API_KEY", "TYPESAFE_API_KEY"):
        value = values.get(name)
        if isinstance(value, str) and value:
            return value
    raise RuntimeError("set JEV_API_KEY or TYPESAFE_API_KEY")


async def _execute(
    routed: list[dict[str, Any]],
    *,
    project_root: Path,
    registration: dict[str, Any],
    registration_path: Path,
    state_path: Path,
) -> dict[str, Any]:
    identity = {
        "kind": "jev_candidate_product_pipeline_v1",
        "registration_sha256": common._sha256_file(registration_path),
        "routed_pair_identity_sha256": registration["candidate_snapshot"][
            "routed_pair_identity_sha256"
        ],
        "model": jev.MODEL,
        "questions_sha256": common._canonical_sha256(jev.QUESTIONS),
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("identity") != identity:
            raise ValueError("durable Jev pipeline state belongs to another run")
    else:
        state = {"identity": identity, "results": {}}
        common._atomic_write(state_path, state)
    key = _api_key(project_root)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    completed = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:

        async def execute(pair: dict[str, Any]) -> None:
            nonlocal completed
            identifier = pair["id"]
            if identifier in state["results"]:
                completed += 1
                return
            async with semaphore:
                value = await jev._request_one(client, key, pair)
            async with lock:
                state["results"][identifier] = value
                common._atomic_write(state_path, state)
                completed += 1
                if completed % 25 == 0 or completed == len(routed):
                    print(
                        f"[jev-candidate-pipeline] completed {completed}/{len(routed)}",
                        flush=True,
                    )

        await asyncio.gather(*(execute(pair) for pair in routed))
    return state


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _metrics(labels: list[bool], predictions: list[bool]) -> dict[str, Any]:
    tp = sum(prediction and label for prediction, label in zip(predictions, labels))
    fp = sum(prediction and not label for prediction, label in zip(predictions, labels))
    tn = sum(not prediction and not label for prediction, label in zip(predictions, labels))
    fn = sum(not prediction and label for prediction, label in zip(predictions, labels))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": (tp + tn) / len(labels),
        "proposal_precision": precision,
        "proposal_recall": recall,
        "f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
    }


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
        _normalize(pair["entity_a"]["name"])
        == _normalize(pair["entity_b"]["name"])
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
            decision = answers["link_state"]["score"] >= 1.5
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
    baseline = _metrics(labels, baseline_predictions)
    pipeline = _metrics(labels, predictions)
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
        "kind": "jev-candidate-product-pipeline-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": common._sha256_file(registration_path),
        "dataset": registration["dataset"],
        "candidate_snapshot": snapshot,
        "provider": registration["provider"],
        "protocol": registration["protocol"],
        "evaluation": {
            "candidate_positive_recall": candidate_positive_recall,
            "exact_name_baseline": baseline,
            "jev_candidate_pipeline": pipeline,
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
            "optional_candidate_jev_proposal_pipeline_confirmed"
            if passed
            else "candidate_jev_proposal_pipeline_rejected"
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
    state = await _execute(
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
                "metrics": result["evaluation"]["jev_candidate_pipeline"],
                "gate_results": result["evaluation"]["gate_results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
