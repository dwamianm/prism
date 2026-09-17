"""Fail-closed contracts for registered LongMemEval-V2 budget curves."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.integrations import compare_longmemeval_v2_curve as subject
from tests.test_longmemeval_v2_comparison import _write_run


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_identity() -> dict[str, object]:
    return {
        "provider": "ollama",
        "api_base_url": "http://127.0.0.1:11434",
        "server_version": "0.34.1",
        "model": "reader-model",
        "resolved_model": "reader-model",
        "model_digest_sha256": "d" * 64,
        "model_size_bytes": 1024,
        "details": {
            "format": "gguf",
            "family": "reader",
            "parameter_size": "1B",
            "quantization_level": "Q4_K_M",
        },
        "capabilities": ["completion"],
        "requires": "0.17.1",
    }


def _fixture(
    root: Path,
) -> tuple[dict[str, Path], Path, dict[str, dict[str, object]]]:
    scores = {
        "4k": [True, False, False],
        "8k": [True, True, False],
        "16k": [True, True, True],
    }
    budgets = {"4k": 4096, "8k": 8192, "16k": 16_384}
    runs: dict[str, Path] = {}
    systems: dict[str, dict[str, object]] = {}
    for offset, (label, outcomes) in enumerate(scores.items(), start=1):
        run = _write_run(root / f"run-{label}", outcomes)
        memory = root / f"memory-{label}"
        pack = memory / "prme_pack"
        pack.mkdir(parents=True)
        config = {
            "memory_type": "prme",
            "memory_params": {
                "token_budget": budgets[label],
                "context_format": "auditable",
                "image_limit": 8,
            },
        }
        selected_config = root / f"config-{label}.json"
        saved_config = memory / "memory_config.json"
        payload = json.dumps(config, sort_keys=True)
        selected_config.write_text(payload)
        saved_config.write_text(payload)
        pack_manifest = pack / "longmemeval_v2_manifest.json"
        pack_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "upstream_revision": subject.installer.UPSTREAM_REVISION,
                }
            )
        )
        args_path = run / "run_args.json"
        args = json.loads(args_path.read_text())
        args.update(
            memory_config_path=str(selected_config),
            memory_context_max_tokens=65_536,
            load_memory_dir=str(memory),
            save_memory=False,
            skip_evaluation=False,
        )
        args_path.write_text(json.dumps(args))
        rows_path = run / "per_question.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        for row in rows:
            row["memory_context_token_count"] = budgets[label] + offset
        rows_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        systems[label] = {
            "context_format": "auditable",
            "internal_context_budget_cl100k_tokens": budgets[label],
            "max_source_screenshots": 8,
            "memory_artifact_bytes": 1000 + offset,
            "memory_artifact_file_count": 3,
            "memory_artifact_sha256": str(offset) * 64,
            "memory_config": str(selected_config),
            "memory_config_sha256": _digest(selected_config),
            "memory_payload_artifact_bytes": 999,
            "memory_payload_artifact_file_count": 2,
            "memory_payload_artifact_sha256": "e" * 64,
            "pack_manifest_sha256": _digest(pack_manifest),
            "saved_memory_config_sha256": _digest(saved_config),
            "upstream_context_budget_tokens": 65_536,
        }
        runs[label] = run

    first = runs["4k"]
    args = json.loads((first / "run_args.json").read_text())
    rows = [
        json.loads(line)
        for line in (first / "per_question.jsonl").read_text().splitlines()
    ]
    question_ids = [row["question_id"] for row in rows]
    counts: dict[str, int] = {}
    for row in rows:
        value = row["question_type"]
        counts[value] = counts.get(value, 0) + 1
    registration = {
        "schema_version": 2,
        "kind": subject.REGISTRATION_KIND,
        "protocol": subject._protocol_specification(),
        "source": {
            "dataset_revision": subject.DATASET_REVISION,
            "files": subject._expected_source_files(),
            "prme_revision": "a" * 40,
            "upstream_revision": subject.installer.UPSTREAM_REVISION,
        },
        "selection": {
            "question_count": len(rows),
            "ordered_question_ids_sha256": hashlib.sha256(
                "\n".join(question_ids).encode()
            ).hexdigest(),
            "counts_by_type": counts,
            "questions_file_sha256": _digest(Path(args["questions_path"])),
            "haystack_file_sha256": _digest(Path(args["haystack_path"])),
            "trajectories_file_sha256": _digest(Path(args["trajectories_path"])),
        },
        "reader": {
            "model": args["model"],
            "base_url": args["base_url"],
            "reasoning_effort": args["reasoning_effort"],
            "reader_enable_thinking": args["reader_enable_thinking"],
            "temperature": args["temperature"],
            "top_p": args["top_p"],
            "top_k": args["top_k"],
            "max_completion_tokens": args["max_completion_tokens"],
            "max_concurrent_requests": args["reader_max_concurrent_requests"],
            "prompt_build_max_workers": args["prompt_build_max_workers"],
            "runtime_identity": _runtime_identity(),
        },
        "systems": systems,
    }
    registration_path = root / "registration.json"
    registration_path.write_text(json.dumps(registration, sort_keys=True))
    registration_sha256 = _digest(registration_path)
    files = subject._expected_source_files()
    execution_source = {
        "prme_revision": registration["source"]["prme_revision"],
        "prme_worktree_changes": [],
        "upstream_revision": subject.installer.UPSTREAM_REVISION,
        "upstream_worktree_changes": [
            "evaluation/memory_configs/prme.json",
            "evaluation/memory_configs/prme_compact.json",
            "memory_modules/__init__.py",
            "memory_modules/prme.py",
        ],
        "launcher_sha256": files["launcher_sha256"],
        "installer_sha256": files["installer_sha256"],
        "adapter_source_sha256": files["adapter_sha256"],
        "adapter_installed_sha256": files["adapter_sha256"],
        "config_source_sha256": files["config_sha256"],
        "config_installed_sha256": files["config_sha256"],
        "compact_config_source_sha256": files["compact_config_sha256"],
        "compact_config_installed_sha256": files["compact_config_sha256"],
        "upstream_harness_sha256": "f" * 64,
    }
    for label, run in runs.items():
        system = systems[label]
        manifest = {
            "schema_version": 3,
            "kind": "longmemeval-v2-execution",
            "registration_sha256": registration_sha256,
            "reader_runtime": _runtime_identity(),
            "source": execution_source,
            "invocation": {
                "memory_config_path": system["memory_config"],
                "memory_config_sha256": system["memory_config_sha256"],
                "load_memory_dir": json.loads((run / "run_args.json").read_text())[
                    "load_memory_dir"
                ],
                "memory_artifact": {
                    "sha256": system["memory_artifact_sha256"],
                    "file_count": system["memory_artifact_file_count"],
                    "bytes": system["memory_artifact_bytes"],
                },
                "memory_payload_artifact": {
                    "sha256": system["memory_payload_artifact_sha256"],
                    "file_count": system["memory_payload_artifact_file_count"],
                    "bytes": system["memory_payload_artifact_bytes"],
                },
            },
        }
        (run / "execution_manifest.json").write_text(json.dumps(manifest))
    return runs, registration_path, systems


def test_curve_verifies_every_arm_and_reports_all_pairs(tmp_path: Path) -> None:
    runs, registration, _ = _fixture(tmp_path)

    result = subject.compare_curve(runs, registration_path=registration, samples=100)

    assert result["ordered_labels"] == ["4k", "8k", "16k"]
    assert {label: row["correct"] for label, row in result["arms"].items()} == {
        "4k": 1,
        "8k": 2,
        "16k": 3,
    }
    assert set(result["pairwise"]) == {
        "8k_minus_4k",
        "16k_minus_4k",
        "16k_minus_8k",
    }
    largest = result["pairwise"]["16k_minus_4k"]["overall"]
    assert largest["paired_left_minus_right"]["wins"] == 2
    assert "Question 0" not in json.dumps(result)
    assert "Answer 0" not in json.dumps(result)


def test_curve_accepts_registered_holdout_protocol(tmp_path: Path) -> None:
    runs, registration_path, _ = _fixture(tmp_path)
    registration = json.loads(registration_path.read_text())
    registration["protocol"] = subject._holdout_protocol_specification()
    registration_path.write_text(json.dumps(registration, sort_keys=True))
    registration_sha256 = _digest(registration_path)
    for run in runs.values():
        path = run / "execution_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["registration_sha256"] = registration_sha256
        path.write_text(json.dumps(manifest))

    result = subject.compare_curve(
        runs, registration_path=registration_path, samples=10
    )

    assert result["limitations"][0] == (
        "This is a registered cross-domain confirmation cohort, not a competitor comparison."
    )


def test_curve_rejects_unregistered_protocol(tmp_path: Path) -> None:
    runs, registration_path, _ = _fixture(tmp_path)
    registration = json.loads(registration_path.read_text())
    registration["protocol"]["claim_boundary"] = "changed after registration"
    registration_path.write_text(json.dumps(registration, sort_keys=True))

    with pytest.raises(ValueError, match="registration identity is invalid"):
        subject.compare_curve(runs, registration_path=registration_path, samples=10)


def test_curve_rejects_changed_saved_configuration(tmp_path: Path) -> None:
    runs, registration, _ = _fixture(tmp_path)
    memory = Path(
        json.loads((runs["8k"] / "run_args.json").read_text())["load_memory_dir"]
    )
    (memory / "memory_config.json").write_text("{}")

    with pytest.raises(ValueError, match="system files changed"):
        subject.compare_curve(runs, registration_path=registration, samples=10)


def test_curve_rejects_execution_source_drift(tmp_path: Path) -> None:
    runs, registration, _ = _fixture(tmp_path)
    path = runs["16k"] / "execution_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["source"]["upstream_harness_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="do not share one execution source"):
        subject.compare_curve(runs, registration_path=registration, samples=10)


def test_curve_rejects_reader_runtime_drift(tmp_path: Path) -> None:
    runs, registration, _ = _fixture(tmp_path)
    path = runs["16k"] / "execution_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["reader_runtime"]["model_digest_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="do not share one reader runtime"):
        subject.compare_curve(runs, registration_path=registration, samples=10)


def test_curve_rejects_non_budget_policy_drift(tmp_path: Path) -> None:
    runs, registration_path, _ = _fixture(tmp_path)
    registration = json.loads(registration_path.read_text())
    system = registration["systems"]["8k"]
    selected = Path(system["memory_config"])
    saved = (
        Path(json.loads((runs["8k"] / "run_args.json").read_text())["load_memory_dir"])
        / "memory_config.json"
    )
    config = json.loads(selected.read_text())
    config["memory_params"]["result_limit"] = 99
    payload = json.dumps(config, sort_keys=True)
    selected.write_text(payload)
    saved.write_text(payload)
    system["memory_config_sha256"] = _digest(selected)
    system["saved_memory_config_sha256"] = _digest(saved)
    registration_path.write_text(json.dumps(registration, sort_keys=True))
    registration_sha256 = _digest(registration_path)
    for label, run in runs.items():
        path = run / "execution_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["registration_sha256"] = registration_sha256
        if label == "8k":
            manifest["invocation"]["memory_config_sha256"] = _digest(selected)
        path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="differ outside token budget"):
        subject.compare_curve(runs, registration_path=registration_path, samples=10)


def test_curve_rejects_unregistered_arm_labels(tmp_path: Path) -> None:
    runs, registration, _ = _fixture(tmp_path)
    runs["extra"] = runs["4k"]

    with pytest.raises(ValueError, match="labels do not match"):
        subject.compare_curve(runs, registration_path=registration, samples=10)
