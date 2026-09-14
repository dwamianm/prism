"""Paired comparison contracts for complete LongMemEval-V2 runs."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.integrations.compare_longmemeval_v2 import compare


def _write_run(root: Path, scores: list[bool], *, model: str = "reader") -> Path:
    root.mkdir()
    inputs = root.parent / "inputs"
    inputs.mkdir(exist_ok=True)
    for name in ("questions.json", "haystack.json", "trajectories.jsonl"):
        (inputs / name).write_text(name, encoding="utf-8")
    args = {
        "domain": "web",
        "model": model,
        "base_url": "http://reader.test/v1",
        "max_completion_tokens": 1024,
        "memory_context_max_tokens": 4096,
        "reader_max_concurrent_requests": 1,
        "prompt_build_max_workers": 1,
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": 20,
        "presence_penalty": None,
        "repetition_penalty": None,
        "reasoning_effort": "none",
        "reader_enable_thinking": False,
        "shuffle_questions_seed": None,
        "questions_path": str(inputs / "questions.json"),
        "haystack_path": str(inputs / "haystack.json"),
        "trajectories_path": str(inputs / "trajectories.jsonl"),
        "started_at_utc": "2026-09-14T00:00:00+00:00",
    }
    (root / "run_args.json").write_text(json.dumps(args), encoding="utf-8")
    rows = []
    for index, score in enumerate(scores):
        rows.append(
            {
                "question_id": f"q{index}",
                "question_type": "static-environment",
                "category": "static" if index < 2 else "dynamic",
                "is_abstention_problem": False,
                "eval_function": "exact",
                "question_text": f"Question {index}",
                "answer_gold": f"Answer {index}",
                "response_raw": "answer",
                "score_bool": score,
                "is_unknown": False,
                "usage": {
                    "prompt_tokens": 10 + index,
                    "completion_tokens": 2,
                    "total_tokens": 12 + index,
                },
                "memory_context_token_count": 5,
                "memory_query_duration_seconds": 0.1 + index,
            }
        )
    (root / "per_question.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    question_rows = "".join(
        json.dumps({"question_id": row["question_id"]}) + "\n" for row in rows
    )
    (root / "prompt_build_summary.json").write_text(json.dumps({
        "prompt_row_count": len(rows),
        "question_ids": [row["question_id"] for row in rows],
    }), encoding="utf-8")
    for name in ("prompt_rows.jsonl", "reader_outputs.checkpoint.jsonl"):
        (root / name).write_text(question_rows, encoding="utf-8")
    aggregate = {
        "overall": {
            "count_all_questions": len(rows),
            "overall_full_set": sum(scores) / len(scores),
        },
        "completed_at_utc": "2026-09-14T00:01:00+00:00",
    }
    (root / "aggregated_metrics.json").write_text(
        json.dumps(aggregate), encoding="utf-8"
    )
    return root


def _write_registration(
    path: Path,
    run: Path,
    *,
    source: dict | None = None,
    systems: dict | None = None,
    schema_version: int = 1,
) -> Path:
    args = json.loads((run / "run_args.json").read_text())
    rows = [json.loads(line) for line in (run / "per_question.jsonl").read_text().splitlines()]
    ids = [row["question_id"] for row in rows]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["question_type"]] = counts.get(row["question_type"], 0) + 1
    def digest(value: str) -> str:
        return hashlib.sha256(Path(value).read_bytes()).hexdigest()
    value = {
        "schema_version": schema_version,
        "selection": {
            "question_count": len(ids),
            "ordered_question_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
            "counts_by_type": counts,
            "questions_file_sha256": digest(args["questions_path"]),
            "haystack_file_sha256": digest(args["haystack_path"]),
        },
        "reader": {
            "model": args["model"],
            "reasoning_effort": args["reasoning_effort"],
            "reader_enable_thinking": args["reader_enable_thinking"],
            "temperature": args["temperature"],
            "top_p": args["top_p"],
            "top_k": args["top_k"],
            "max_completion_tokens": args["max_completion_tokens"],
            "max_concurrent_requests": args["reader_max_concurrent_requests"],
        },
    }
    if source is not None:
        value["source"] = source
    if systems is not None:
        value["systems"] = systems
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _write_execution_manifests(
    left: Path,
    right: Path,
    registration: Path,
    source: dict,
    systems: dict,
) -> None:
    digest = hashlib.sha256(registration.read_bytes()).hexdigest()
    execution_source = {
        "prme_revision": source["prme_revision"],
        "prme_worktree_changes": [],
        "upstream_revision": source["upstream_revision"],
        "upstream_worktree_changes": [
            "evaluation/memory_configs/prme.json",
            "evaluation/memory_configs/prme_compact.json",
            "memory_modules/__init__.py",
            "memory_modules/prme.py",
        ],
        "launcher_sha256": "1" * 64,
        "installer_sha256": "2" * 64,
        "adapter_source_sha256": "3" * 64,
        "adapter_installed_sha256": "3" * 64,
        "config_source_sha256": "4" * 64,
        "config_installed_sha256": "4" * 64,
        "compact_config_source_sha256": "6" * 64,
        "compact_config_installed_sha256": "6" * 64,
        "upstream_harness_sha256": "5" * 64,
    }
    for directory, system_name in ((left, "prme"), (right, "baseline")):
        system = systems[system_name]
        manifest = {
            "schema_version": 2,
            "kind": "longmemeval-v2-execution",
            "registration_sha256": digest,
            "source": execution_source,
            "invocation": {
                "memory_config_path": system["memory_config"],
                "memory_config_sha256": system["memory_config_sha256"],
                "load_memory_dir": json.loads(
                    (directory / "run_args.json").read_text()
                )["load_memory_dir"],
                "memory_artifact": (
                    {
                        "sha256": system["memory_artifact_sha256"],
                        "file_count": system["memory_artifact_file_count"],
                        "bytes": system["memory_artifact_bytes"],
                    }
                    if system_name == "prme"
                    else None
                ),
            },
        }
        (directory / "execution_manifest.json").write_text(json.dumps(manifest))


def _bind_registered_systems(
    left: Path, right: Path, root: Path
) -> tuple[dict, dict]:
    memory = root / "saved-memory"
    pack = memory / "prme_pack"
    pack.mkdir(parents=True)
    saved_config = {
        "memory_type": "prme",
        "memory_params": {"token_budget": 32_768, "image_limit": 8},
    }
    manifest = {"schema_version": 2, "upstream_revision": "upstream-revision"}
    config_path = memory / "memory_config.json"
    manifest_path = pack / "longmemeval_v2_manifest.json"
    config_path.write_text(json.dumps(saved_config), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def update_args(directory: Path, *, prme: bool) -> None:
        path = directory / "run_args.json"
        args = json.loads(path.read_text())
        args.update(
            memory_config_path=(
                "evaluation/memory_configs/prme.json"
                if prme
                else "evaluation/memory_configs/no_retrieval.json"
            ),
            memory_context_max_tokens=65_536,
            load_memory_dir=str(memory) if prme else None,
            save_memory=False,
            skip_evaluation=False,
        )
        path.write_text(json.dumps(args), encoding="utf-8")

    update_args(left, prme=True)
    update_args(right, prme=False)
    rows_path = right / "per_question.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    for row in rows:
        row["memory_context_token_count"] = 0
    rows_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    source = {
        "upstream_revision": manifest["upstream_revision"],
        "prme_adapter_schema_version": manifest["schema_version"],
        "prme_pack_manifest_sha256": digest(manifest_path),
        "prme_memory_config_sha256": digest(config_path),
    }
    systems = {
        "prme": {
            "memory_config": "evaluation/memory_configs/prme.json",
            "memory_config_sha256": digest(config_path),
            "memory_artifact_sha256": "c" * 64,
            "memory_artifact_file_count": 3,
            "memory_artifact_bytes": 123,
            "internal_context_budget_cl100k_tokens": 32_768,
            "upstream_context_budget_tokens": 65_536,
            "max_source_screenshots": 8,
            "context_format": "auditable",
        },
        "baseline": {
            "memory_config": "evaluation/memory_configs/no_retrieval.json",
            "memory_config_sha256": "b" * 64,
        },
    }
    return source, systems


def test_comparison_preserves_paired_direction_and_efficiency(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True, False, True])
    right = _write_run(tmp_path / "right", [False, True, False])
    result = compare(
        left, right, left_label="prme", right_label="no_memory", samples=100
    )
    assert result["overall"]["left_correct"] == 2
    assert result["overall"]["right_correct"] == 1
    paired = result["overall"]["paired_left_minus_right"]
    assert (paired["wins"], paired["losses"], paired["ties"]) == (2, 1, 0)
    assert paired["delta"] == pytest.approx(1 / 3)
    assert paired["exact_mcnemar_p_two_sided"] == 1.0
    assert result["efficiency"]["left"]["total_tokens"] == 39
    assert set(result["categories"]) == {"dynamic", "static"}


def test_comparison_rejects_reader_or_question_drift(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True])
    right = _write_run(tmp_path / "right", [True])
    args_path = right / "run_args.json"
    args = json.loads(args_path.read_text())
    args["model"] = "different-reader"
    args_path.write_text(json.dumps(args))
    with pytest.raises(ValueError, match="reader settings differ"):
        compare(left, right, left_label="left", right_label="right")

    args["model"] = "reader"
    args_path.write_text(json.dumps(args))
    rows_path = right / "per_question.jsonl"
    row = json.loads(rows_path.read_text())
    changed = deepcopy(row)
    changed["answer_gold"] = "changed"
    rows_path.write_text(json.dumps(changed) + "\n")
    with pytest.raises(ValueError, match="question inputs differ"):
        compare(left, right, left_label="left", right_label="right")


@pytest.mark.parametrize(
    ("artifact", "error"),
    [
        ("prompt_rows.jsonl", "artifact question identities are incomplete"),
        ("reader_outputs.checkpoint.jsonl", "artifact question identities are incomplete"),
    ],
)
def test_comparison_rejects_incomplete_intermediate_artifacts(
    tmp_path: Path, artifact: str, error: str
) -> None:
    left = _write_run(tmp_path / "left", [True, False])
    right = _write_run(tmp_path / "right", [True, False])
    (left / artifact).write_text('{"question_id": "q0"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        compare(left, right, left_label="left", right_label="right")


def test_comparison_rejects_aggregate_score_drift(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True, False])
    right = _write_run(tmp_path / "right", [True, False])
    aggregate_path = left / "aggregated_metrics.json"
    aggregate = json.loads(aggregate_path.read_text())
    aggregate["overall"]["overall_full_set"] = 1.0
    aggregate_path.write_text(json.dumps(aggregate))
    with pytest.raises(ValueError, match="aggregate score does not match rows"):
        compare(left, right, left_label="left", right_label="right")


def test_comparison_verifies_registered_cohort_and_reader(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True, False])
    right = _write_run(tmp_path / "right", [True, False])
    source, systems = _bind_registered_systems(left, right, tmp_path)
    registration = _write_registration(
        tmp_path / "registration.json", left, source=source, systems=systems
    )
    result = compare(
        left, right, left_label="left", right_label="right",
        registration=registration, samples=10,
    )
    assert result["registration_sha256"] == hashlib.sha256(registration.read_bytes()).hexdigest()
    assert result["registration_schema_version"] == 1
    assert result["system_binding"]["baseline_memory_context_tokens_zero"] is True
    assert result["system_binding"]["prme_adapter_schema_version"] == 2
    assert result["system_binding"]["prme_context_format"] == "auditable"

    registered = json.loads(registration.read_text())
    registered["selection"]["question_count"] = 1
    registration.write_text(json.dumps(registered))
    with pytest.raises(ValueError, match="cohort does not match"):
        compare(left, right, left_label="left", right_label="right",
                registration=registration, samples=10)


def test_schema_two_registration_requires_matching_execution_sources(
    tmp_path: Path,
) -> None:
    left = _write_run(tmp_path / "left", [True, False])
    right = _write_run(tmp_path / "right", [False, False])
    source, systems = _bind_registered_systems(left, right, tmp_path)
    source["prme_revision"] = "a" * 40
    registration = _write_registration(
        tmp_path / "registration.json",
        left,
        source=source,
        systems=systems,
        schema_version=2,
    )

    with pytest.raises(ValueError, match="require execution manifests"):
        compare(
            left,
            right,
            left_label="prme",
            right_label="no_memory",
            registration=registration,
            samples=10,
        )

    _write_execution_manifests(left, right, registration, source, systems)
    result = compare(
        left,
        right,
        left_label="prme",
        right_label="no_memory",
        registration=registration,
        samples=10,
    )
    assert result["registration_schema_version"] == 2
    assert result["execution_source"]["prme_revision"] == "a" * 40
    assert result["execution_source"]["invocations"]["left"] == {
        "memory_config_path": systems["prme"]["memory_config"],
        "memory_config_sha256": systems["prme"]["memory_config_sha256"],
        "load_memory_dir": str(tmp_path / "saved-memory"),
        "memory_artifact_sha256": systems["prme"]["memory_artifact_sha256"],
    }
    assert "execution_manifest.json" in result["artifacts"]["left"]

    manifest_path = right / "execution_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["invocation"]["memory_config_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="configuration hash does not match"):
        compare(
            left,
            right,
            left_label="prme",
            right_label="no_memory",
            registration=registration,
            samples=10,
        )

    _write_execution_manifests(left, right, registration, source, systems)
    memory_manifest_path = left / "execution_manifest.json"
    manifest = json.loads(memory_manifest_path.read_text())
    manifest["invocation"]["memory_artifact"]["bytes"] += 1
    memory_manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="saved-memory identity does not match"):
        compare(
            left,
            right,
            left_label="prme",
            right_label="no_memory",
            registration=registration,
            samples=10,
        )

    _write_execution_manifests(left, right, registration, source, systems)
    manifest = json.loads(manifest_path.read_text())
    manifest["source"]["launcher_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="do not share one execution source"):
        compare(
            left,
            right,
            left_label="prme",
            right_label="no_memory",
            registration=registration,
            samples=10,
        )


def test_comparison_rejects_registered_context_format_drift(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True])
    right = _write_run(tmp_path / "right", [False])
    source, systems = _bind_registered_systems(left, right, tmp_path)
    systems["prme"]["context_format"] = "compact"
    registration = _write_registration(
        tmp_path / "registration.json", left, source=source, systems=systems
    )

    with pytest.raises(ValueError, match="adapter settings do not match"):
        compare(
            left,
            right,
            left_label="prme",
            right_label="no_memory",
            registration=registration,
            samples=10,
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("left_config", "left arm does not use the registered"),
        ("right_config", "right arm does not use the registered"),
        ("loaded_config", "loaded PRME memory configuration"),
        ("pack_manifest", "loaded PRME pack manifest"),
        ("baseline_context", "no-memory baseline returned memory context"),
    ],
)
def test_comparison_rejects_registered_system_drift(
    tmp_path: Path, mutation: str, error: str
) -> None:
    left = _write_run(tmp_path / "left", [True])
    right = _write_run(tmp_path / "right", [False])
    source, systems = _bind_registered_systems(left, right, tmp_path)
    registration = _write_registration(
        tmp_path / "registration.json", left, source=source, systems=systems
    )

    if mutation in {"left_config", "right_config"}:
        directory = left if mutation == "left_config" else right
        path = directory / "run_args.json"
        args = json.loads(path.read_text())
        args["memory_config_path"] = "evaluation/memory_configs/changed.json"
        path.write_text(json.dumps(args))
    elif mutation == "loaded_config":
        (tmp_path / "saved-memory" / "memory_config.json").write_text("{}")
    elif mutation == "pack_manifest":
        (tmp_path / "saved-memory" / "prme_pack" / "longmemeval_v2_manifest.json").write_text("{}")
    else:
        path = right / "per_question.jsonl"
        row = json.loads(path.read_text())
        row["memory_context_token_count"] = 1
        path.write_text(json.dumps(row) + "\n")

    with pytest.raises(ValueError, match=error):
        compare(
            left,
            right,
            left_label="prme",
            right_label="no_memory",
            registration=registration,
            samples=10,
        )
