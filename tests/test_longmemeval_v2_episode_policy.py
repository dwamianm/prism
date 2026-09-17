"""Fail-closed contracts for registered LongMemEval-V2 episode trials."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.integrations import compare_longmemeval_v2_episode_policy as subject
from tests.test_longmemeval_v2_curve import _fixture as _curve_fixture


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(root: Path) -> tuple[dict[str, Path], Path]:
    curve_runs, registration_path, _ = _curve_fixture(root)
    runs = {"flat": curve_runs["4k"], "episode": curve_runs["8k"]}
    registration = json.loads(registration_path.read_text())
    systems = {}
    policies = {
        "flat": {
            "episode_context_top_k": 0,
            "episode_context_local_k": 8,
            "episode_context_score_decay": 0.95,
        },
        "episode": {
            "episode_context_top_k": 2,
            "episode_context_local_k": 8,
            "episode_context_score_decay": 0.95,
        },
    }
    old_labels = {"flat": "4k", "episode": "8k"}
    for label, run in runs.items():
        system = registration["systems"][old_labels[label]]
        selected = Path(system["memory_config"])
        memory = Path(json.loads((run / "run_args.json").read_text())["load_memory_dir"])
        saved = memory / "memory_config.json"
        config = json.loads(selected.read_text())
        config["memory_params"].update(token_budget=4096, **policies[label])
        payload = json.dumps(config, sort_keys=True)
        selected.write_text(payload)
        saved.write_text(payload)
        system.update(
            internal_context_budget_cl100k_tokens=4096,
            memory_config_sha256=_digest(selected),
            saved_memory_config_sha256=_digest(saved),
            **policies[label],
        )
        systems[label] = system

        rows = [
            json.loads(line)
            for line in (run / "prompt_rows.jsonl").read_text().splitlines()
        ]
        for row in rows:
            row["memory_post_query_metadata"] = dict(policies[label])
        (run / "prompt_rows.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )

    registration.update(
        kind=subject.REGISTRATION_KIND,
        protocol=subject._protocol_specification(),
        systems=systems,
    )
    registration_path.write_text(json.dumps(registration, sort_keys=True))
    registration_sha256 = _digest(registration_path)
    for label, run in runs.items():
        path = run / "execution_manifest.json"
        manifest = json.loads(path.read_text())
        system = systems[label]
        manifest["registration_sha256"] = registration_sha256
        manifest["invocation"]["memory_config_sha256"] = system[
            "memory_config_sha256"
        ]
        manifest["invocation"]["memory_artifact"] = {
            "sha256": system["memory_artifact_sha256"],
            "file_count": system["memory_artifact_file_count"],
            "bytes": system["memory_artifact_bytes"],
        }
        path.write_text(json.dumps(manifest))
    return runs, registration_path


def test_episode_comparison_verifies_trial_and_reports_pairs(tmp_path: Path) -> None:
    runs, registration = _fixture(tmp_path)

    result = subject.compare_episode_policies(
        runs, registration_path=registration, samples=100
    )

    assert result["arms"]["flat"]["correct"] == 1
    assert result["arms"]["episode"]["correct"] == 2
    assert result["overall"]["paired_left_minus_right"]["wins"] == 1
    assert result["bindings"]["flat"]["episode_context_top_k"] == 0
    assert result["bindings"]["episode"]["episode_context_top_k"] == 2
    assert "Question 0" not in json.dumps(result)


def test_episode_comparison_rejects_prompt_policy_drift(tmp_path: Path) -> None:
    runs, registration = _fixture(tmp_path)
    path = runs["episode"] / "prompt_rows.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["memory_post_query_metadata"]["episode_context_top_k"] = 3
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match="prompt row episode policy"):
        subject.compare_episode_policies(
            runs, registration_path=registration, samples=10
        )


def test_episode_comparison_rejects_other_policy_drift(tmp_path: Path) -> None:
    runs, registration_path = _fixture(tmp_path)
    registration = json.loads(registration_path.read_text())
    system = registration["systems"]["episode"]
    selected = Path(system["memory_config"])
    memory = Path(
        json.loads((runs["episode"] / "run_args.json").read_text())["load_memory_dir"]
    )
    config = json.loads(selected.read_text())
    config["memory_params"]["result_limit"] = 99
    payload = json.dumps(config, sort_keys=True)
    selected.write_text(payload)
    (memory / "memory_config.json").write_text(payload)
    system["memory_config_sha256"] = _digest(selected)
    system["saved_memory_config_sha256"] = _digest(memory / "memory_config.json")
    registration_path.write_text(json.dumps(registration, sort_keys=True))
    registration_sha256 = _digest(registration_path)
    for label, run in runs.items():
        path = run / "execution_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["registration_sha256"] = registration_sha256
        if label == "episode":
            manifest["invocation"]["memory_config_sha256"] = _digest(selected)
        path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="differ outside episode policy"):
        subject.compare_episode_policies(
            runs, registration_path=registration_path, samples=10
        )
