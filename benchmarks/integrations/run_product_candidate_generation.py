"""Register and evaluate deterministic product-pair candidate generation."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from benchmarks.integrations import run_jev_claim_verification as common
from prme.integrations.product_candidates import (
    PRODUCT_CANDIDATE_POLICY,
    ProductCandidateEntity,
    rank_product_alignment_candidates,
)


DATASET = "DeepMatcher Structured Walmart-Amazon"
SOURCE = (
    "https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/"
    "Structured/Walmart-Amazon/exp_data/"
)
FILES = ("tableA.csv", "tableB.csv", "train.csv", "valid.csv", "test.csv")
SPLIT = "test"
TOP_K = 5
MIN_SCORE = 0.1


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _inputs(root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    left = _read(root / "tableA.csv")
    right = _read(root / "tableB.csv")
    pairs = _read(root / f"{SPLIT}.csv")
    expected_product = {"id", "title", "category", "brand", "modelno", "price"}
    if (
        not left
        or not right
        or set(left[0]) != expected_product
        or set(right[0]) != expected_product
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


def _dataset(root: Path) -> dict[str, Any]:
    left, right, pairs = _inputs(root)
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
        "labeled_pairs": len(pairs),
        "labels": dict(sorted(Counter(row["label"] for row in pairs).items())),
        "pair_identity_sha256": common._canonical_sha256(
            [
                [row["ltable_id"], row["rtable_id"], row["label"]]
                for row in pairs
            ]
        ),
    }


def _protocol() -> dict[str, Any]:
    return {
        "candidate_policy": PRODUCT_CANDIDATE_POLICY,
        "entity_fields": {
            "name": "title",
            "manufacturer": "brand",
            "price": "price",
        },
        "catalog_labels": ["walmart", "amazon"],
        "cross_catalog_only": True,
        "top_k_per_entity": TOP_K,
        "minimum_cosine_score": MIN_SCORE,
        "pair_union": "a pair survives when selected by either endpoint",
        "mutation_authorized": False,
        "external_model_calls": False,
    }


def _evaluation() -> dict[str, Any]:
    return {
        "gates": {
            "positive_pair_recall_min": 0.95,
            "cross_product_reduction_min": 0.99,
            "deterministic_replay": True,
            "candidate_count_max": "top_k * total_entities",
        },
        "decision_rule": (
            "Every gate must pass before the candidate generator may be composed "
            "with the separately confirmed Jev product-pair advisor. Passing does "
            "not validate Jev on this dataset or authorize automatic merging."
        ),
    }


def create_registration(
    *, dataset_root: Path, project_root: Path, output_path: Path
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("Registration output already exists")
    registration = {
        "schema_version": 1,
        "kind": "product-candidate-generation-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing validates deterministic broad-recall product candidate "
            "generation on an untouched product-linkage dataset. It does not "
            "validate an end-to-end Jev pipeline or an identity merge."
        ),
        "source": {
            "prme_revision": common._git_revision(project_root),
            "runner_sha256": common._sha256_file(Path(__file__).resolve()),
            "candidate_source_sha256": common._sha256_file(
                project_root / "src/prme/integrations/product_candidates.py"
            ),
        },
        "dataset": _dataset(dataset_root),
        "protocol": _protocol(),
        "evaluation": _evaluation(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any], *, dataset_root: Path, project_root: Path
) -> None:
    revision = registration.get("source", {}).get("prme_revision")
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "product-candidate-generation-registration"
        or not isinstance(revision, str)
        or not common._git_is_ancestor(revision, project_root)
        or registration.get("source", {}).get("runner_sha256")
        != common._sha256_file(Path(__file__).resolve())
        or registration.get("source", {}).get("candidate_source_sha256")
        != common._sha256_file(
            project_root / "src/prme/integrations/product_candidates.py"
        )
        or registration.get("dataset") != _dataset(dataset_root)
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _evaluation()
    ):
        raise ValueError("Product candidate registration differs")


def _node_id(catalog: str, identity: str):
    return uuid5(NAMESPACE_URL, f"prme:{DATASET}:{catalog}:{identity}")


def _records(
    left: list[dict[str, str]], right: list[dict[str, str]]
) -> list[ProductCandidateEntity]:
    return [
        ProductCandidateEntity(
            node_id=_node_id(catalog, row["id"]),
            catalog=catalog,
            product={
                "name": row["title"],
                "manufacturer": row["brand"],
                "price": row["price"],
            },
        )
        for catalog, rows in (("walmart", left), ("amazon", right))
        for row in rows
    ]


def run_trial(
    *,
    dataset_root: Path,
    project_root: Path,
    registration_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("Result output already exists")
    registration = json.loads(registration_path.read_text())
    _validate_registration(
        registration, dataset_root=dataset_root, project_root=project_root
    )
    left, right, pairs = _inputs(dataset_root)
    records = _records(left, right)
    candidates = rank_product_alignment_candidates(
        records,
        top_k=TOP_K,
        min_score=MIN_SCORE,
        cross_catalog_only=True,
    )
    replay = rank_product_alignment_candidates(
        reversed(records),
        top_k=TOP_K,
        min_score=MIN_SCORE,
        cross_catalog_only=True,
    )
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
    replay_identity = [
        [
            str(item.left.node_id),
            str(item.right.node_id),
            item.score,
            item.shared_feature_count,
            [str(node_id) for node_id in item.selected_by],
        ]
        for item in replay
    ]
    candidate_pairs = {
        (item.left.node_id, item.right.node_id) for item in candidates
    }

    def routed(row: dict[str, str]) -> bool:
        pair = tuple(
            sorted(
                (
                    _node_id("walmart", row["ltable_id"]),
                    _node_id("amazon", row["rtable_id"]),
                ),
                key=str,
            )
        )
        return pair in candidate_pairs

    positives = [row for row in pairs if row["label"] == "1"]
    negatives = [row for row in pairs if row["label"] == "0"]
    positive_routed = sum(routed(row) for row in positives)
    negative_routed = sum(routed(row) for row in negatives)
    cross_product = len(left) * len(right)
    recall = positive_routed / len(positives) if positives else 0.0
    reduction = 1 - len(candidates) / cross_product if cross_product else 0.0
    deterministic = candidate_identity == replay_identity
    gates = {
        "positive_pair_recall_min": recall >= 0.95,
        "cross_product_reduction_min": reduction >= 0.99,
        "deterministic_replay": deterministic,
        "candidate_count_max": len(candidates) <= TOP_K * len(records),
    }
    result = {
        "schema_version": 1,
        "kind": "product-candidate-generation-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": common._sha256_file(registration_path),
        "dataset": registration["dataset"],
        "protocol": registration["protocol"],
        "evaluation": {
            "positive_pairs_routed": positive_routed,
            "positive_pairs_total": len(positives),
            "positive_pair_recall": recall,
            "negative_labeled_pairs_routed": negative_routed,
            "negative_labeled_pairs_total": len(negatives),
            "candidate_pairs": len(candidates),
            "cross_product_pairs": cross_product,
            "cross_product_reduction": reduction,
            "mean_candidates_per_entity": len(candidates) / len(records),
            "candidate_identity_sha256": common._canonical_sha256(
                candidate_identity
            ),
            "deterministic_replay": deterministic,
            "gate_results": gates,
        },
        "passed": all(gates.values()),
        "decision": (
            "candidate_generation_confirmed"
            if all(gates.values())
            else "candidate_generation_rejected"
        ),
        "limitations": [
            "Candidate recall is not Jev classification precision or recall.",
            "The ranker only routes pairs; every identity decision remains reviewed.",
            "Walmart-Amazon is one structured retail-product dataset.",
        ],
    }
    result["result_sha256"] = common._canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--dataset-root", type=Path, required=True)
        command.add_argument("--project-root", type=Path, default=Path.cwd())
        command.add_argument("--output", type=Path, required=True)
        if name == "run":
            command.add_argument("--registration", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "register":
        result = create_registration(
            dataset_root=args.dataset_root.resolve(),
            project_root=args.project_root.resolve(),
            output_path=args.output.resolve(),
        )
    else:
        result = run_trial(
            dataset_root=args.dataset_root.resolve(),
            project_root=args.project_root.resolve(),
            registration_path=args.registration.resolve(),
            output_path=args.output.resolve(),
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
