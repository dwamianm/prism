"""Registration freezes candidate-generation data, code, and decision gates."""

import csv
import json
from pathlib import Path

import pytest

from benchmarks.integrations.run_product_candidate_generation import (
    create_registration,
    run_trial,
)


PRODUCT_FIELDS = ("id", "title", "category", "brand", "modelno", "price")


def _write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _dataset(tmp_path):
    left = []
    right = []
    for index in range(25):
        left.append(
            {
                "id": f"a{index}",
                "title": f"Unique Left Product {index}",
                "category": "software",
                "brand": f"LeftBrand{index}",
                "modelno": f"L{index}",
                "price": str(index),
            }
        )
        right.append(
            {
                "id": f"b{index}",
                "title": f"Unrelated Right Item {index + 100}",
                "category": "software",
                "brand": f"RightBrand{index}",
                "modelno": f"R{index}",
                "price": str(index),
            }
        )
    left[0]["title"] = "Acme Ledger Professional 7"
    right[0]["title"] = "Acme Ledger Pro Version 7"
    left[1]["title"] = "Cobalt Planner Deluxe 2026"
    right[1]["title"] = "Cobalt Planning Deluxe 2026"
    _write(tmp_path / "tableA.csv", PRODUCT_FIELDS, left)
    _write(tmp_path / "tableB.csv", PRODUCT_FIELDS, right)
    pairs = [
        {"ltable_id": "a0", "rtable_id": "b0", "label": "1"},
        {"ltable_id": "a1", "rtable_id": "b1", "label": "1"},
        {"ltable_id": "a0", "rtable_id": "b1", "label": "0"},
    ]
    for split in ("train", "valid", "test"):
        _write(
            tmp_path / f"{split}.csv",
            ("ltable_id", "rtable_id", "label"),
            pairs,
        )


def test_registered_candidate_trial_passes_authored_gate(tmp_path):
    _dataset(tmp_path)
    root = Path(__file__).parents[1]
    registration_path = tmp_path / "registration.json"
    result_path = tmp_path / "result.json"
    registration = create_registration(
        dataset_root=tmp_path,
        project_root=root,
        output_path=registration_path,
    )
    result = run_trial(
        dataset_root=tmp_path,
        project_root=root,
        registration_path=registration_path,
        output_path=result_path,
    )

    assert registration["protocol"]["candidate_policy"] == (
        "product_tfidf_candidates_v1"
    )
    assert result["passed"] is True
    assert result["evaluation"]["positive_pair_recall"] == 1.0
    assert result["evaluation"]["cross_product_reduction"] >= 0.99
    assert result["evaluation"]["deterministic_replay"] is True
    assert len(result["result_sha256"]) == 64


def test_trial_rejects_changed_registration_or_dataset(tmp_path):
    _dataset(tmp_path)
    root = Path(__file__).parents[1]
    registration_path = tmp_path / "registration.json"
    create_registration(
        dataset_root=tmp_path,
        project_root=root,
        output_path=registration_path,
    )
    registration = json.loads(registration_path.read_text())
    registration["protocol"]["top_k_per_entity"] = 1
    registration_path.write_text(json.dumps(registration))
    with pytest.raises(ValueError, match="registration differs"):
        run_trial(
            dataset_root=tmp_path,
            project_root=root,
            registration_path=registration_path,
            output_path=tmp_path / "result.json",
        )
