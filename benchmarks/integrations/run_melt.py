"""Launch PRME's registered MELT lifecycle evaluation from attested sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


UPSTREAM_COMMIT = "47c819f417b0d81a57f781ec54d8a5cf84e0c833"
SEEDS = [1103, 2207, 3301, 4409, 5501]
CAPABILITIES = [
    "reset",
    "time_control",
    "consolidation",
    "query_as_of",
    "structured_memory_write",
    "answer_generation",
]
CONFIG_FILENAME = "registered-config.toml"
MANIFEST_FILENAME = "execution-manifest.json"


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def git_identity(root: Path) -> tuple[str, list[str]]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tracked = subprocess.run(
            ["git", "diff", "HEAD", "--name-only", "--"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"could not identify Git source at {root}") from exc
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise RuntimeError(f"invalid Git revision at {root}: {revision!r}")
    return revision, sorted(set(tracked) | set(untracked))


def load_registration(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read MELT registration: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("MELT registration must be a JSON object")
    return value


def validate_registration(
    registration: dict[str, Any],
    *,
    project_revision: str,
    upstream_revision: str,
    source_hashes: dict[str, str],
) -> None:
    expected_protocol = {
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
    if registration.get("schema_version") != 1:
        raise RuntimeError("MELT registration schema version must equal 1")
    if registration.get("kind") != "melt-lifecycle-v5-core-registration":
        raise RuntimeError("unexpected MELT registration kind")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise RuntimeError("MELT registration is missing source identity")
    if source.get("prme_revision") != project_revision:
        raise RuntimeError("PRME revision does not match the MELT registration")
    if source.get("upstream_revision") != upstream_revision:
        raise RuntimeError("MELT revision does not match the registration")
    if source.get("files") != source_hashes:
        raise RuntimeError("registered MELT source hashes do not match the launch")
    if registration.get("protocol") != expected_protocol:
        raise RuntimeError("MELT protocol does not match the registered final profile")
    system = registration.get("system")
    if not isinstance(system, dict) or system.get("id") != "prme":
        raise RuntimeError("MELT registration does not identify PRME")
    if system.get("contract_version") != "b2":
        raise RuntimeError("registered MELT contract must be b2")


def render_config(project_root: Path, output_dir: Path) -> bytes:
    command = [
        "uv",
        "--directory",
        str(project_root),
        "run",
        "python",
        "-m",
        "benchmarks.integrations.melt_sut",
    ]
    values = [
        "[run]",
        f"output_dir = {json.dumps(str(output_dir))}",
        "store_case_io = true",
        "runs = 5",
        f"seed_schedule = {json.dumps(SEEDS)}",
        "",
        "[sut]",
        'adapter = "shisad"',
        'contract_version = "b2"',
        f"command = {json.dumps(command)}",
        f"capabilities = {json.dumps(CAPABILITIES)}",
        "timeout_seconds = 60",
        "",
        "[sut.overrides]",
        "min_score = 0.05",
        "",
        "[suite]",
        'name = "lifecycle"',
        'version = "lifecycle-v5"',
        'fixture = "stress"',
        'split = "held_out"',
        'profile = "lifecycle-v5-core"',
        "top_k = 12",
        "",
        "[answer]",
        'mode = "retrieval_only"',
        "",
    ]
    return "\n".join(values).encode("utf-8")


def _write_exact(path: Path, payload: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise RuntimeError(f"existing launch artifact differs: {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_launch(
    *, upstream_root: Path, output_dir: Path, registration_path: Path
) -> tuple[Path, Path]:
    project_root = Path(__file__).resolve().parents[2]
    project_revision, project_changes = git_identity(project_root)
    upstream_revision, upstream_changes = git_identity(upstream_root)
    if project_changes:
        raise RuntimeError("registered MELT launch requires a clean PRME worktree")
    if upstream_changes:
        raise RuntimeError("registered MELT launch requires a clean upstream worktree")
    if upstream_revision != UPSTREAM_COMMIT:
        raise RuntimeError("MELT checkout is not at the supported pinned revision")

    source_files = {
        "launcher_sha256": digest(Path(__file__).resolve()),
        "bridge_sha256": digest(project_root / "benchmarks/integrations/melt_sut.py"),
        "upstream_lock_sha256": digest(upstream_root / "uv.lock"),
        "upstream_runner_sha256": digest(upstream_root / "src/melt/runner.py"),
        "upstream_reports_sha256": digest(upstream_root / "src/melt/reports.py"),
        "upstream_suite_manifest_sha256": digest(
            upstream_root / "src/melt/suites/manifests/lifecycle-v5.json"
        ),
    }
    registration_path = registration_path.resolve()
    registration = load_registration(registration_path)
    validate_registration(
        registration,
        project_revision=project_revision,
        upstream_revision=upstream_revision,
        source_hashes=source_files,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    allowed = {CONFIG_FILENAME, MANIFEST_FILENAME}
    unexpected = sorted(
        path.name for path in output_dir.iterdir() if path.name not in allowed
    )
    if unexpected:
        raise RuntimeError(
            "cannot attribute an existing MELT output directory: "
            + ", ".join(unexpected)
        )
    config_path = output_dir / CONFIG_FILENAME
    config_payload = render_config(project_root, output_dir)
    manifest = {
        "schema_version": 1,
        "kind": "melt-lifecycle-v5-core-execution",
        "registration_sha256": digest(registration_path),
        "config_sha256": hashlib.sha256(config_payload).hexdigest(),
        "source": {
            "prme_revision": project_revision,
            "prme_worktree_changes": project_changes,
            "upstream_revision": upstream_revision,
            "upstream_worktree_changes": upstream_changes,
            "files": source_files,
        },
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    _write_exact(manifest_path, canonical_json(manifest) + b"\n")
    _write_exact(config_path, config_payload)
    return config_path, manifest_path


def launch(upstream_root: Path, config_path: Path, manifest_path: Path) -> Path:
    output_dir = config_path.parent
    subprocess.run(
        [
            "uv",
            "--directory",
            str(upstream_root),
            "run",
            "melt",
            "run",
            "--config",
            str(config_path),
        ],
        cwd=upstream_root,
        check=True,
    )
    summaries = sorted(output_dir.glob("*/summary.json"))
    if len(summaries) != 1:
        raise RuntimeError(f"expected exactly one MELT summary, found {len(summaries)}")
    copied_manifest = summaries[0].parent / MANIFEST_FILENAME
    shutil.copyfile(manifest_path, copied_manifest)
    return summaries[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--registration", type=Path, required=True)
    args = parser.parse_args()
    config, manifest = prepare_launch(
        upstream_root=args.upstream_root.resolve(),
        output_dir=args.output_dir.resolve(),
        registration_path=args.registration,
    )
    summary = launch(args.upstream_root.resolve(), config, manifest)
    print(f"Registered MELT report: {summary}")


if __name__ == "__main__":
    main()
