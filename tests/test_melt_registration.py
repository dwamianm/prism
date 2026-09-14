"""Registered MELT runs bind the final protocol to complete source evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib

import pytest

from benchmarks.integrations import run_melt, validate_melt


PRME_REVISION = "1" * 40
FILES = {"launcher_sha256": "2" * 64}
CASES = [
    "lifecycle-v5-core-write_quality",
    "lifecycle-v5-core-correction",
    "lifecycle-v5-core-contradiction",
    "lifecycle-v5-core-maintenance",
]


def _protocol() -> dict:
    return {
        "suite": "lifecycle",
        "suite_version": "lifecycle-v5",
        "fixture": "stress",
        "split": "held_out",
        "score_profile": "lifecycle-v5-core",
        "top_k": 12,
        "runs": 5,
        "seed_schedule": run_melt.SEEDS,
        "answer_mode": "retrieval_only",
        "store_case_io": True,
        "min_score": 0.05,
    }


def _registration() -> dict:
    return {
        "schema_version": 1,
        "kind": "melt-lifecycle-v5-core-registration",
        "source": {
            "prme_revision": PRME_REVISION,
            "upstream_revision": run_melt.UPSTREAM_COMMIT,
            "files": FILES,
        },
        "protocol": _protocol(),
        "system": {"id": "prme", "version": "0.11.0", "contract_version": "b2"},
    }


def test_rendered_config_pins_the_upstream_final_profile(tmp_path: Path) -> None:
    parsed = tomllib.loads(
        run_melt.render_config(tmp_path / "prme", tmp_path / "results").decode()
    )
    assert parsed["run"]["runs"] == 5
    assert parsed["run"]["seed_schedule"] == run_melt.SEEDS
    assert parsed["run"]["store_case_io"] is True
    assert parsed["suite"] == {
        "name": "lifecycle",
        "version": "lifecycle-v5",
        "fixture": "stress",
        "split": "held_out",
        "profile": "lifecycle-v5-core",
        "top_k": 12,
    }
    assert parsed["sut"]["overrides"] == {"min_score": 0.05}
    assert parsed["answer"]["mode"] == "retrieval_only"


def test_registration_rejects_protocol_drift() -> None:
    registration = _registration()
    registration["protocol"]["split"] = "dev"
    with pytest.raises(RuntimeError, match="final profile"):
        run_melt.validate_registration(
            registration,
            project_revision=PRME_REVISION,
            upstream_revision=run_melt.UPSTREAM_COMMIT,
            source_hashes=FILES,
        )


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _complete_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    output_dir = tmp_path / "output"
    run_dir = output_dir / "lifecycle_prme_registered"
    summary_path = run_dir / "summary.json"
    registration_path = tmp_path / "registration.json"
    registration = _registration()
    _write_json(registration_path, registration)

    config_toml = run_melt.render_config(tmp_path / "prme", output_dir)
    config_path = output_dir / run_melt.CONFIG_FILENAME
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(config_toml)
    manifest = {
        "schema_version": 1,
        "kind": "melt-lifecycle-v5-core-execution",
        "registration_sha256": run_melt.digest(registration_path),
        "config_sha256": hashlib.sha256(config_toml).hexdigest(),
        "source": {
            "prme_revision": PRME_REVISION,
            "prme_worktree_changes": [],
            "upstream_revision": run_melt.UPSTREAM_COMMIT,
            "upstream_worktree_changes": [],
            "files": FILES,
        },
    }
    _write_json(output_dir / run_melt.MANIFEST_FILENAME, manifest)
    _write_json(run_dir / run_melt.MANIFEST_FILENAME, manifest)

    envelope = {
        "runner_commit": run_melt.UPSTREAM_COMMIT[:7],
        "config_hash": "config-hash",
    }
    score_identity = {
        "suite_version": "lifecycle-v5",
        "score_profile_id": "lifecycle-v5-core",
        "score_profile_manifest": {"case_ids": CASES},
        "protocol_manifest": {
            "run": {
                "restart_sut_per_case": False,
                "runs": 5,
                "seed_schedule": run_melt.SEEDS,
            },
            "suite": {
                "case_granularity": "auto",
                "fixture": "stress",
                "include_category_5": None,
                "name": "lifecycle",
                "split": "held_out",
                "top_k": 12,
                "variant": None,
                "version": "lifecycle-v5",
            },
            "answer": {"enabled": False, "mode": "retrieval_only"},
        },
    }
    runs = [
        {
            "run_id": f"run-{run_number:03d}",
            "cases": [{"case_id": case_id} for case_id in CASES],
        }
        for run_number in range(1, 6)
    ]
    report = {
        "report_kind": "summary",
        "report_schema_version": "5",
        "melt_schema_version": "2",
        "status": "final",
        "config_hash": "config-hash",
        "envelope": envelope,
        "suite": {"id": "lifecycle", "fixture": "stress", "split": "held_out"},
        "sut": {
            "id": "prme",
            "version": "0.11.0",
            "commit": PRME_REVISION,
            "contract_version": "b2",
        },
        "score_identity": score_identity,
        "runs": runs,
        "validity_guards": [],
    }
    _write_json(summary_path, report)
    _write_json(run_dir / "envelope.json", envelope)
    _write_json(
        run_dir / "config.json",
        {
            "run": {"store_case_io": True},
            "suite": {"split": "held_out"},
            "sut": {"overrides": {"min_score": 0.05}},
        },
    )
    for run_number, row in enumerate(runs, start=1):
        _write_json(run_dir / f"runs/run-{run_number:03d}/report.json", row)
        for case_id in CASES:
            _write_json(
                output_dir
                / f"checkpoints/invocation/run-{run_number:03d}/{case_id}.json",
                {"case_id": case_id},
            )
    return summary_path, registration_path


def test_validator_accepts_complete_registered_evidence(tmp_path: Path) -> None:
    summary, registration = _complete_artifacts(tmp_path)
    result = validate_melt.validate_run(
        summary,
        registration_path=registration,
        upstream_root=tmp_path,
        run_upstream_loader=False,
    )
    assert result["complete"] is True
    assert result["coverage"] == {
        "runs": 5,
        "per_run_reports": 5,
        "case_checkpoints": 20,
    }


def test_validator_rejects_nonfinal_or_partial_run(tmp_path: Path) -> None:
    summary, registration = _complete_artifacts(tmp_path)
    report = json.loads(summary.read_text())
    report["status"] = "preliminary"
    _write_json(summary, report)
    next((summary.parent.parent / "checkpoints").glob("*/*/*.json")).unlink()
    result = validate_melt.validate_run(
        summary,
        registration_path=registration,
        upstream_root=tmp_path,
        run_upstream_loader=False,
    )
    assert result["complete"] is False
    assert "registered report is not final" in result["errors"]
    assert any("complete case checkpoints" in error for error in result["errors"])
