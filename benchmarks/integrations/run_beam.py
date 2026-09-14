"""Launch the registered retrieval-only BEAM workflow from attested sources."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from benchmarks.integrations.beam_service import UPSTREAM_COMMIT
from benchmarks.integrations.run_melt import canonical_json, digest, git_identity
from benchmarks.integrations.validate_beam import QUESTION_TYPES


PROJECT_NAME = "prme-beam-100k-raw-v1"
RUN_ID = "prme-beam-100k-raw-v1"
DATASET_REVISION = "3205395e897e7318c7b094ef4e6047b9b82dbb03"
DATASET_FILENAME = "beam_100K.json"
MANIFEST_FILENAME = "execution-manifest.json"
PREDICTION_DIRECTORY = f"predicted_{PROJECT_NAME}"


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return value


def _source_hashes(project_root: Path, upstream_root: Path) -> dict[str, str]:
    return {
        "launcher_sha256": digest(Path(__file__).resolve()),
        "service_sha256": digest(
            project_root / "benchmarks/integrations/beam_service.py"
        ),
        "validator_sha256": digest(
            project_root / "benchmarks/integrations/validate_beam.py"
        ),
        "upstream_runner_sha256": digest(upstream_root / "benchmarks/beam/run.py"),
        "upstream_mem0_client_sha256": digest(
            upstream_root / "benchmarks/common/mem0_client.py"
        ),
        "upstream_utils_sha256": digest(upstream_root / "benchmarks/common/utils.py"),
        "upstream_requirements_sha256": digest(upstream_root / "requirements.txt"),
    }


def _expected_protocol() -> dict[str, Any]:
    return {
        "profile": "raw",
        "chat_sizes": ["100K"],
        "conversations": [0],
        "question_types": list(QUESTION_TYPES),
        "top_k": 50,
        "top_k_cutoffs": [50],
        "predict_only": True,
        "project_name": PROJECT_NAME,
        "run_id": RUN_ID,
        "chunk_size": 2,
    }


def validate_registration(
    registration: dict[str, Any],
    *,
    project_revision: str,
    upstream_revision: str,
    source_hashes: dict[str, str],
    dataset_sha256: str,
) -> None:
    if registration.get("schema_version") != 1:
        raise RuntimeError("BEAM registration schema version must equal 1")
    if registration.get("kind") != "beam-raw-predict-only-registration":
        raise RuntimeError("unexpected BEAM registration kind")
    source = registration.get("source")
    if not isinstance(source, dict):
        raise RuntimeError("BEAM registration is missing source identity")
    if source.get("prme_revision") != project_revision:
        raise RuntimeError("PRME revision does not match the BEAM registration")
    if source.get("upstream_revision") != upstream_revision:
        raise RuntimeError("BEAM revision does not match the registration")
    if source.get("files") != source_hashes:
        raise RuntimeError("registered BEAM source hashes do not match the launch")
    dataset = registration.get("dataset")
    if not isinstance(dataset, dict):
        raise RuntimeError("BEAM registration is missing dataset identity")
    if dataset.get("revision") != DATASET_REVISION:
        raise RuntimeError("BEAM dataset revision differs from registration")
    if dataset.get("cache_sha256") != dataset_sha256:
        raise RuntimeError("BEAM dataset cache differs from registration")
    if registration.get("protocol") != _expected_protocol():
        raise RuntimeError("BEAM protocol does not match the registered smoke")
    system = registration.get("system")
    if (
        not isinstance(system, dict)
        or system.get("id") != "prme"
        or system.get("profile") != "raw"
        or not isinstance(system.get("version"), str)
    ):
        raise RuntimeError("BEAM registration does not identify the raw PRME system")


def _write_exact(path: Path, payload: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise RuntimeError(f"existing launch artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
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
    *,
    upstream_root: Path,
    output_root: Path,
    registration_path: Path,
    dataset_path: Path,
    resume: bool,
) -> dict[str, Path]:
    project_root = Path(__file__).resolve().parents[2]
    project_revision, project_changes = git_identity(project_root)
    upstream_revision, upstream_changes = git_identity(upstream_root)
    if project_changes:
        raise RuntimeError("registered BEAM launch requires a clean PRME worktree")
    if upstream_changes:
        raise RuntimeError("registered BEAM launch requires a clean upstream worktree")
    if upstream_revision != UPSTREAM_COMMIT:
        raise RuntimeError("BEAM checkout is not at the supported pinned revision")
    if not dataset_path.is_file():
        raise RuntimeError("registered BEAM dataset cache does not exist")

    files = _source_hashes(project_root, upstream_root)
    dataset_sha256 = digest(dataset_path)
    registration_path = registration_path.resolve()
    registration = _read_object(registration_path)
    validate_registration(
        registration,
        project_revision=project_revision,
        upstream_revision=upstream_revision,
        source_hashes=files,
        dataset_sha256=dataset_sha256,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "kind": "beam-raw-predict-only-execution",
        "registration_sha256": digest(registration_path),
        "dataset_sha256": dataset_sha256,
        "source": {
            "prme_revision": project_revision,
            "prme_worktree_changes": project_changes,
            "upstream_revision": upstream_revision,
            "upstream_worktree_changes": upstream_changes,
            "files": files,
        },
    }
    manifest_path = output_root / MANIFEST_FILENAME
    manifest_payload = canonical_json(manifest) + b"\n"
    allowed = {
        MANIFEST_FILENAME,
        "dataset",
        "prme-pack",
        "service.log",
        "upstream-results",
    }
    unexpected = sorted(
        path.name for path in output_root.iterdir() if path.name not in allowed
    )
    if unexpected:
        raise RuntimeError(
            "cannot attribute an existing BEAM output directory: "
            + ", ".join(unexpected)
        )
    if not resume and any(
        (output_root / name).exists()
        for name in ("prme-pack", "service.log", "upstream-results")
    ):
        raise RuntimeError("new BEAM launch requires an unused output directory")
    _write_exact(manifest_path, manifest_payload)
    frozen_dataset = output_root / "dataset" / DATASET_FILENAME
    if frozen_dataset.parent.exists():
        dataset_entries = sorted(path.name for path in frozen_dataset.parent.iterdir())
        if dataset_entries not in ([], [DATASET_FILENAME]):
            raise RuntimeError(
                "frozen BEAM dataset directory contains unexpected files"
            )
    if frozen_dataset.exists():
        if digest(frozen_dataset) != dataset_sha256:
            raise RuntimeError("frozen BEAM dataset differs from registration")
    else:
        frozen_dataset.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dataset_path, frozen_dataset)
    pack_dir = output_root / "prme-pack"
    prediction_root = output_root / "upstream-results"
    if resume and (not pack_dir.is_dir() or not prediction_root.is_dir()):
        raise RuntimeError(
            "BEAM resume requires both pack and upstream result directories"
        )
    return {
        "project_root": project_root,
        "manifest": manifest_path,
        "dataset_dir": frozen_dataset.parent,
        "pack_dir": pack_dir,
        "prediction_root": prediction_root,
        "prediction_dir": prediction_root / PREDICTION_DIRECTORY,
        "service_log": output_root / "service.log",
    }


def _wait_for_service(port: int, process: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 60
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("PRME BEAM service exited before becoming ready")
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310 - loopback only
                body = json.loads(response.read())
            if body.get("status") == "ok" and body.get("profile") == "raw":
                return
        except (OSError, URLError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise RuntimeError("PRME BEAM service did not become ready within 60 seconds")


def launch(
    *, upstream_root: Path, paths: dict[str, Path], port: int, resume: bool
) -> Path:
    service_command = [
        "uv",
        "--directory",
        str(paths["project_root"]),
        "run",
        "python",
        "-m",
        "benchmarks.integrations.beam_service",
        "--directory",
        str(paths["pack_dir"]),
        "--profile",
        "raw",
        "--duckdb-threads",
        "1",
        "--port",
        str(port),
    ]
    if resume:
        service_command.append("--resume")
    paths["prediction_root"].mkdir(parents=True, exist_ok=True)
    with paths["service_log"].open("a" if resume else "w", encoding="utf-8") as log:
        service = subprocess.Popen(
            service_command,
            cwd=paths["project_root"],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_service(port, service)
            command = [
                "uv",
                "run",
                "--with-requirements",
                str(upstream_root / "requirements.txt"),
                "--with",
                "datasets>=3,<5",
                "python",
                "-m",
                "benchmarks.beam.run",
                "--project-name",
                PROJECT_NAME,
                "--backend",
                "oss",
                "--mem0-host",
                f"http://127.0.0.1:{port}",
                "--chat-sizes",
                "100K",
                "--conversations",
                "0",
                "--top-k",
                "50",
                "--top-k-cutoffs",
                "50",
                "--predict-only",
                "--run-id",
                RUN_ID,
                "--dataset-cache-dir",
                str(paths["dataset_dir"]),
                "--output-dir",
                str(paths["prediction_root"]),
            ]
            if resume:
                command.append("--resume")
            subprocess.run(command, cwd=upstream_root, check=True)
        finally:
            service.terminate()
            try:
                service.wait(timeout=30)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=10)
    return paths["prediction_dir"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8889)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be 1-65535")
    paths = prepare_launch(
        upstream_root=args.upstream_root.resolve(),
        output_root=args.output_root.resolve(),
        registration_path=args.registration,
        dataset_path=args.dataset.resolve(),
        resume=args.resume,
    )
    prediction_dir = launch(
        upstream_root=args.upstream_root.resolve(),
        paths=paths,
        port=args.port,
        resume=args.resume,
    )
    print(f"Registered BEAM predictions: {prediction_dir}")


if __name__ == "__main__":
    main()
