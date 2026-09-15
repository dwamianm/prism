"""Fail-closed checks for paired MemoryAgentBench comparisons."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.integrations import compare_memoryagentbench as comparator


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(comparator._canonical(value) + b"\n")


def task_fixture(
    root: Path,
    *,
    label: str,
    dataset: str,
    sub_dataset: str,
    prme_primary: list[int],
    bm25_primary: list[int],
) -> dict[str, str]:
    source_identity = {
        "prme_revision": "a" * 40,
        "upstream_revision": "b" * 40,
        "dataset_revision": "c" * 40,
    }
    reader = {
        "model": "reader",
        "temperature": 0.0,
        "reader_reasoning_effort": "none",
        "reader_seed": 42,
        "reader_output_contract": "upstream",
    }
    dataset_config = {"dataset": dataset, "sub_dataset": sub_dataset}
    preprocessing = {
        "datasets": {"version": "1"},
        "nltk": {"version": "1"},
        "tiktoken": {"version": "1"},
    }
    queries = [
        {
            "query_id": index,
            "query_sha256": str(index) * 64,
            "retrieval_query_sha256": "d" * 64,
            "answer_sha256": str(index + 1) * 64,
            "qa_pair_id_sha256": str(index + 2) * 64,
        }
        for index in range(len(prme_primary))
    ]
    chunks = [{"index": 0, "sha256": "f" * 64}]
    task = {
        "dataset": dataset,
        "sub_dataset": sub_dataset,
        "context_count": 1,
        "query_count": len(queries),
        "query_limit": len(queries),
        "contexts": [
            {"context_id": 0, "source_chunks": chunks, "queries": queries}
        ],
    }
    prme_registration = {
        "schema_version": 1,
        "kind": "memoryagentbench-prme-registration",
        "source": {**source_identity, "preprocessing": preprocessing},
        "configuration": {"dataset": dataset_config, "agent": reader},
        "task": {
            **task,
            "contexts": [
                {
                    "context_id": 0,
                    "source_chunks": [{**chunks[0], "piece_count": 1}],
                    "queries": queries,
                }
            ],
        },
    }
    bm25_registration = {
        "schema_version": 1,
        "kind": "memoryagentbench-bm25-registration",
        "source": {
            **source_identity,
            "dependencies": {**preprocessing, "rank-bm25": {"version": "1"}},
        },
        "configuration": {
            "dataset": dataset_config,
            "agent": reader,
            "memorize_template_sha256": "e" * 64,
        },
        "task": {
            **task,
            "contexts": [
                {
                    "context_id": 0,
                    "source_chunks": chunks,
                    "bm25_documents": chunks,
                    "queries": queries,
                }
            ],
        },
    }
    metric = comparator._PRIMARY_METRICS[dataset]
    alternate = (
        "exact_match" if metric == "substring_exact_match" else "substring_exact_match"
    )
    prme_result = {
        "metrics": {metric: prme_primary, alternate: [0] * len(queries)}
    }
    bm25_result = {
        "metrics": {metric: bm25_primary, alternate: [1] * len(queries)}
    }
    paths = {
        "prme_registration": root / f"{label}-prme-registration.json",
        "bm25_registration": root / f"{label}-bm25-registration.json",
        "prme_result": root / f"{label}-prme-result.json",
        "bm25_result": root / f"{label}-bm25-result.json",
    }
    for name, value in (
        ("prme_registration", prme_registration),
        ("bm25_registration", bm25_registration),
        ("prme_result", prme_result),
        ("bm25_result", bm25_result),
    ):
        write_json(paths[name], value)

    verification_task = {
        "dataset": dataset,
        "sub_dataset": sub_dataset,
        "queries": len(queries),
        "contexts": 1,
        "source_chunks": 1,
    }
    for arm in ("prme", "bm25"):
        verification = {
            "schema_version": 1,
            "kind": f"memoryagentbench-{arm}-verification",
            "status": "verified_complete",
            "source": {
                **source_identity,
                "registration_sha256": comparator._digest(
                    paths[f"{arm}_registration"]
                ),
                "result_sha256": comparator._digest(paths[f"{arm}_result"]),
            },
            "task": verification_task,
            "retrieval": {
                "context_tokens_mean": 100.0 if arm == "prme" else 200.0
            },
            "averaged_metrics": {"input_len": 120.0 if arm == "prme" else 220.0},
        }
        path = root / f"{label}-{arm}-verification.json"
        write_json(path, verification)
        paths[f"{arm}_verification"] = path
    return {name: path.name for name, path in paths.items()}


def comparison_fixture(tmp_path: Path) -> Path:
    tasks = [
        {
            "label": "conflict",
            **task_fixture(
                tmp_path,
                label="conflict",
                dataset="Conflict_Resolution",
                sub_dataset="factconsolidation_mh_6k",
                prme_primary=[1, 0],
                bm25_primary=[0, 0],
            ),
        },
        {
            "label": "detective",
            **task_fixture(
                tmp_path,
                label="detective",
                dataset="Long_Range_Understanding",
                sub_dataset="detective_qa",
                prme_primary=[0, 1],
                bm25_primary=[1, 0],
            ),
        },
    ]
    manifest = tmp_path / "manifest.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "kind": "memoryagentbench-paired-manifest",
            "tasks": tasks,
        },
    )
    return manifest


def test_comparator_uses_each_tasks_official_metric(tmp_path: Path) -> None:
    report = comparator.compare(comparison_fixture(tmp_path), samples=100)

    assert report["status"] == "verified_complete"
    assert report["tasks"]["conflict"]["official_primary_metric"] == (
        "substring_exact_match"
    )
    assert report["tasks"]["detective"]["official_primary_metric"] == "exact_match"
    assert report["overall"]["prme_correct"] == 2
    assert report["overall"]["bm25_correct"] == 1
    assert report["overall"]["paired_prme_minus_bm25"]["wins"] == 2
    assert report["overall"]["paired_prme_minus_bm25"]["losses"] == 1


def test_comparator_rejects_result_changed_after_verification(tmp_path: Path) -> None:
    manifest = comparison_fixture(tmp_path)
    result_path = tmp_path / "conflict-prme-result.json"
    result = json.loads(result_path.read_text())
    result["metrics"]["substring_exact_match"] = [0, 0]
    write_json(result_path, result)

    with pytest.raises(ValueError, match="does not bind the supplied result"):
        comparator.compare(manifest, samples=10)


def test_comparator_rejects_cross_arm_question_drift(tmp_path: Path) -> None:
    manifest = comparison_fixture(tmp_path)
    registration_path = tmp_path / "conflict-bm25-registration.json"
    registration = json.loads(registration_path.read_text())
    registration["task"]["contexts"][0]["queries"][0]["query_sha256"] = "9" * 64
    write_json(registration_path, registration)

    with pytest.raises(ValueError, match="different questions, retrieval queries"):
        comparator.compare(manifest, samples=10)


def test_comparator_rejects_cross_arm_retrieval_query_drift(
    tmp_path: Path,
) -> None:
    manifest = comparison_fixture(tmp_path)
    registration_path = tmp_path / "conflict-bm25-registration.json"
    registration = json.loads(registration_path.read_text())
    registration["task"]["contexts"][0]["queries"][0][
        "retrieval_query_sha256"
    ] = "9" * 64
    write_json(registration_path, registration)

    with pytest.raises(ValueError, match="different questions, retrieval queries"):
        comparator.compare(manifest, samples=10)


def test_comparator_rejects_cross_arm_reader_output_contract_drift(
    tmp_path: Path,
) -> None:
    manifest = comparison_fixture(tmp_path)
    registration_path = tmp_path / "conflict-bm25-registration.json"
    registration = json.loads(registration_path.read_text())
    registration["configuration"]["agent"]["reader_output_contract"] = (
        "numeric-label-v1"
    )
    write_json(registration_path, registration)

    with pytest.raises(ValueError, match="different reader settings"):
        comparator.compare(manifest, samples=10)


def test_comparator_rejects_nonbinary_primary_metric(tmp_path: Path) -> None:
    manifest = comparison_fixture(tmp_path)
    result_path = tmp_path / "conflict-prme-result.json"
    result = json.loads(result_path.read_text())
    result["metrics"]["substring_exact_match"] = [0.5, 0]
    write_json(result_path, result)
    verification_path = tmp_path / "conflict-prme-verification.json"
    verification = json.loads(verification_path.read_text())
    verification["source"]["result_sha256"] = comparator._digest(result_path)
    write_json(verification_path, verification)

    with pytest.raises(ValueError, match="is not binary"):
        comparator.compare(manifest, samples=10)
