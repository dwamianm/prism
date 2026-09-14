"""Validate a registered MELT lifecycle report and its execution evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

from benchmarks.integrations.run_melt import (
    CONFIG_FILENAME,
    MANIFEST_FILENAME,
    SEEDS,
    UPSTREAM_COMMIT,
    digest,
)


_EXPECTED_PROTOCOL = {
    "suite": "lifecycle",
    "suite_version": "lifecycle-v5",
    "fixture": "stress",
    "split": "held_out",
    "score_profile": "lifecycle-v5-core",
    "top_k": 12,
    "runs": 5,
    "seed_schedule": SEEDS,
    "answer_mode": "retrieval_only",
    "store_case_io": True,
    "min_score": 0.05,
}


def _read(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: invalid JSON ({type(exc).__name__})")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path.name}: root must be an object")
        return None
    return value


def _check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _upstream_load(upstream_root: Path, summary_path: Path) -> tuple[bool, str | None]:
    program = (
        "from pathlib import Path; from melt.reports import load_report; "
        "import sys; load_report(Path(sys.argv[1]))"
    )
    result = subprocess.run(
        [
            "uv",
            "--directory",
            str(upstream_root),
            "run",
            "python",
            "-c",
            program,
            str(summary_path),
        ],
        cwd=upstream_root,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, None
    detail = (result.stderr or result.stdout).strip().splitlines()
    return False, detail[-1] if detail else "MELT report loader failed"


def validate_run(
    summary_path: Path,
    *,
    registration_path: Path,
    upstream_root: Path,
    run_upstream_loader: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    summary_path = summary_path.resolve()
    run_dir = summary_path.parent
    output_dir = run_dir.parent
    registration = _read(registration_path.resolve(), errors)
    report = _read(summary_path, errors)
    manifest_path = output_dir / MANIFEST_FILENAME
    copied_manifest_path = run_dir / MANIFEST_FILENAME
    config_toml_path = output_dir / CONFIG_FILENAME
    manifest = _read(manifest_path, errors)
    copied_manifest = _read(copied_manifest_path, errors)
    resolved_config_path = run_dir / "config.json"
    envelope_path = run_dir / "envelope.json"
    config = _read(resolved_config_path, errors)
    envelope = _read(envelope_path, errors)
    expected_cases: list[Any] | None = None

    if registration is not None:
        _check(
            registration.get("schema_version") == 1,
            "unsupported registration schema",
            errors,
        )
        _check(
            registration.get("kind") == "melt-lifecycle-v5-core-registration",
            "unexpected registration kind",
            errors,
        )
        _check(
            registration.get("protocol") == _EXPECTED_PROTOCOL,
            "registration does not describe the final MELT core protocol",
            errors,
        )
    if registration is not None and manifest is not None:
        _check(
            manifest.get("schema_version") == 1,
            "unsupported execution manifest",
            errors,
        )
        _check(
            manifest.get("kind") == "melt-lifecycle-v5-core-execution",
            "unexpected execution manifest kind",
            errors,
        )
        _check(
            manifest.get("registration_sha256") == digest(registration_path.resolve()),
            "execution manifest does not match the registration",
            errors,
        )
        source = manifest.get("source")
        registered_source = registration.get("source")
        _check(
            isinstance(source, dict),
            "execution manifest has no source identity",
            errors,
        )
        _check(
            isinstance(registered_source, dict),
            "registration has no source identity",
            errors,
        )
        if isinstance(source, dict) and isinstance(registered_source, dict):
            _check(
                source.get("prme_revision") == registered_source.get("prme_revision"),
                "executed PRME revision differs from registration",
                errors,
            )
            _check(
                source.get("upstream_revision")
                == registered_source.get("upstream_revision"),
                "executed MELT revision differs from registration",
                errors,
            )
            _check(
                source.get("prme_worktree_changes") == [],
                "PRME worktree was modified",
                errors,
            )
            _check(
                source.get("upstream_worktree_changes") == [],
                "MELT worktree was modified",
                errors,
            )
            _check(
                source.get("files") == registered_source.get("files"),
                "executed source hashes differ from registration",
                errors,
            )
    if manifest is not None and copied_manifest is not None:
        _check(manifest == copied_manifest, "copied execution manifest differs", errors)
    if manifest is not None and config_toml_path.is_file():
        _check(
            manifest.get("config_sha256") == digest(config_toml_path),
            "executed TOML configuration hash differs",
            errors,
        )
    elif manifest is not None:
        errors.append("registered TOML configuration is missing")

    protocol = registration.get("protocol") if registration is not None else None
    system = registration.get("system") if registration is not None else None
    if report is not None and isinstance(protocol, dict) and isinstance(system, dict):
        _check(
            report.get("report_kind") == "summary", "report is not a summary", errors
        )
        _check(
            report.get("report_schema_version") == "5",
            "unexpected report schema",
            errors,
        )
        _check(
            report.get("melt_schema_version") == "2", "unexpected MELT schema", errors
        )
        _check(
            report.get("status") == "final", "registered report is not final", errors
        )
        _check(
            "error_detail" not in report, "report contains an execution error", errors
        )
        _check(
            "not_supported_detail" not in report, "profile was not supported", errors
        )
        suite = report.get("suite")
        _check(isinstance(suite, dict), "report has no suite identity", errors)
        if isinstance(suite, dict):
            _check(
                suite.get("id") == protocol.get("suite"),
                "suite differs from registration",
                errors,
            )
            _check(
                suite.get("fixture") == protocol.get("fixture"),
                "fixture differs from registration",
                errors,
            )
            _check(
                suite.get("split") == protocol.get("split"),
                "split differs from registration",
                errors,
            )
        sut = report.get("sut")
        _check(isinstance(sut, dict), "report has no SUT identity", errors)
        if isinstance(sut, dict):
            _check(
                sut.get("id") == system.get("id"),
                "SUT differs from registration",
                errors,
            )
            _check(
                sut.get("version") == system.get("version"),
                "SUT version differs",
                errors,
            )
            registered_source = registration.get("source")
            _check(
                isinstance(registered_source, dict)
                and sut.get("commit") == registered_source.get("prme_revision"),
                "SUT commit differs from registered PRME",
                errors,
            )
            _check(
                sut.get("contract_version") == system.get("contract_version"),
                "SUT contract differs",
                errors,
            )
        identity = report.get("score_identity")
        _check(isinstance(identity, dict), "report has no score identity", errors)
        if isinstance(identity, dict):
            _check(
                identity.get("suite_version") == protocol.get("suite_version"),
                "suite version differs",
                errors,
            )
            _check(
                identity.get("score_profile_id") == protocol.get("score_profile"),
                "score profile differs",
                errors,
            )
            profile = identity.get("score_profile_manifest")
            run_protocol = identity.get("protocol_manifest")
            _check(
                isinstance(profile, dict), "score profile manifest is missing", errors
            )
            _check(
                isinstance(run_protocol, dict),
                "run protocol manifest is missing",
                errors,
            )
            if isinstance(run_protocol, dict):
                _check(
                    run_protocol.get("run")
                    == {
                        "restart_sut_per_case": False,
                        "runs": 5,
                        "seed_schedule": SEEDS,
                    },
                    "run count or seed schedule differs",
                    errors,
                )
                suite_protocol = run_protocol.get("suite")
                if isinstance(suite_protocol, dict):
                    for report_key, registered_key in (
                        ("name", "suite"),
                        ("version", "suite_version"),
                        ("fixture", "fixture"),
                        ("split", "split"),
                        ("top_k", "top_k"),
                    ):
                        _check(
                            suite_protocol.get(report_key)
                            == protocol.get(registered_key),
                            f"protocol {report_key} differs from registration",
                            errors,
                        )
                answer = run_protocol.get("answer")
                _check(
                    isinstance(answer, dict)
                    and answer.get("mode") == "retrieval_only"
                    and answer.get("enabled") is False,
                    "answer protocol is not retrieval only",
                    errors,
                )
            expected_cases = (
                profile.get("case_ids") if isinstance(profile, dict) else None
            )
        else:
            expected_cases = None
        runs = report.get("runs")
        _check(
            isinstance(runs, list) and len(runs) == 5,
            "report does not contain five runs",
            errors,
        )
        if isinstance(runs, list):
            _check(
                [row.get("run_id") for row in runs if isinstance(row, dict)]
                == [f"run-{index:03d}" for index in range(1, 6)],
                "run IDs are incomplete or out of order",
                errors,
            )
            if isinstance(expected_cases, list):
                expected_set = set(expected_cases)
                for row in runs:
                    cases = row.get("cases") if isinstance(row, dict) else None
                    actual = {
                        case.get("case_id")
                        for case in cases or []
                        if isinstance(case, dict)
                    }
                    _check(
                        actual == expected_set,
                        f"{row.get('run_id')}: case coverage differs",
                        errors,
                    )
        guards = report.get("validity_guards")
        _check(
            isinstance(guards, list)
            and all(
                isinstance(item, dict) and item.get("passed") is True for item in guards
            ),
            "a report validity guard failed",
            errors,
        )
    if report is not None and envelope is not None:
        _check(
            report.get("envelope") == envelope, "standalone envelope differs", errors
        )
        _check(
            envelope.get("runner_commit") == UPSTREAM_COMMIT[:7],
            "upstream runner commit differs",
            errors,
        )
    if report is not None and config is not None:
        _check(
            report.get("config_hash") == envelope.get("config_hash")
            if envelope
            else False,
            "config hash differs",
            errors,
        )
        run_config = config.get("run")
        suite_config = config.get("suite")
        sut_config = config.get("sut")
        _check(
            isinstance(run_config, dict) and run_config.get("store_case_io") is True,
            "case I/O was not retained",
            errors,
        )
        _check(
            isinstance(suite_config, dict) and suite_config.get("split") == "held_out",
            "resolved split differs",
            errors,
        )
        _check(
            isinstance(sut_config, dict)
            and sut_config.get("overrides") == {"min_score": 0.05},
            "SUT override differs",
            errors,
        )

    per_run_paths = sorted(run_dir.glob("runs/run-*/report.json"))
    _check(len(per_run_paths) == 5, "five per-run reports were not retained", errors)
    checkpoints = sorted((output_dir / "checkpoints").glob("*/*/*.json"))
    expected_checkpoint_count = (
        len(expected_cases) * 5 if isinstance(expected_cases, list) else 20
    )
    _check(
        len(checkpoints) == expected_checkpoint_count,
        f"expected {expected_checkpoint_count} complete case checkpoints",
        errors,
    )
    for path in [*per_run_paths, *checkpoints]:
        _read(path, errors)

    upstream_valid = False
    upstream_error = None
    if run_upstream_loader and summary_path.is_file():
        upstream_valid, upstream_error = _upstream_load(upstream_root, summary_path)
        if not upstream_valid:
            errors.append(f"upstream MELT loader rejected the report: {upstream_error}")

    artifacts = {}
    for path in [
        summary_path,
        resolved_config_path,
        envelope_path,
        manifest_path,
        copied_manifest_path,
        config_toml_path,
        *per_run_paths,
        *checkpoints,
    ]:
        if path.is_file():
            artifacts[str(path.relative_to(output_dir))] = digest(path)
    return {
        "schema_version": 1,
        "complete": not errors,
        "registration_sha256": digest(registration_path.resolve())
        if registration_path.is_file()
        else None,
        "upstream_loader_valid": upstream_valid if run_upstream_loader else None,
        "report_path": str(summary_path),
        "coverage": {
            "runs": len(report.get("runs", [])) if report is not None else 0,
            "per_run_reports": len(per_run_paths),
            "case_checkpoints": len(checkpoints),
        },
        "errors": errors,
        "artifact_sha256": artifacts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_run(
        args.summary,
        registration_path=args.registration,
        upstream_root=args.upstream_root.resolve(),
    )
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
