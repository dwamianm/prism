"""Launch registered BEAM workflows from attested sources."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, cast
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener, urlopen

from benchmarks.integrations.beam_service import UPSTREAM_COMMIT
from benchmarks.integrations.run_melt import canonical_json, digest, git_identity
from benchmarks.integrations.validate_beam import QUESTION_TYPES


PROJECT_NAME = "prme-beam-100k-raw-v1"
RUN_ID = "prme-beam-100k-raw-v1"
DATASET_REVISION = "3205395e897e7318c7b094ef4e6047b9b82dbb03"
DATASET_FILENAME = "beam_100K.json"
MANIFEST_FILENAME = "execution-manifest.json"
PREDICTION_DIRECTORY = f"predicted_{PROJECT_NAME}"
PREDICT_REGISTRATION_KIND = "beam-raw-predict-only-registration"
SCORED_REGISTRATION_KIND = "beam-raw-scored-registration"
SCORED_REGISTRATION_KIND_V3 = "beam-scored-registration"
PREDICT_EXECUTION_KIND = "beam-raw-predict-only-execution"
SCORED_EXECUTION_KIND = "beam-raw-scored-execution"
SCORED_EXECUTION_KIND_V3 = "beam-scored-execution"


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


def _protocol(registration: dict[str, Any]) -> dict[str, Any]:
    """Validate and return either supported registered protocol."""
    schema_version = registration.get("schema_version")
    kind = registration.get("kind")
    value = registration.get("protocol")
    if schema_version == 1 and kind == PREDICT_REGISTRATION_KIND:
        if value != _expected_protocol():
            raise RuntimeError("BEAM protocol does not match the registered smoke")
        return cast(dict[str, Any], value)
    if (schema_version, kind) not in {
        (2, SCORED_REGISTRATION_KIND),
        (3, SCORED_REGISTRATION_KIND_V3),
        (4, SCORED_REGISTRATION_KIND_V3),
        (5, SCORED_REGISTRATION_KIND_V3),
    }:
        raise RuntimeError("unsupported BEAM registration schema or kind")
    if not isinstance(value, dict):
        raise RuntimeError("BEAM registration is missing its protocol")
    profile = value.get("profile")
    if schema_version == 2 and profile != "raw":
        raise RuntimeError("BEAM schema 2 scored protocol requires the raw profile")
    if schema_version in {3, 4, 5} and profile not in {"raw", "extracted"}:
        raise RuntimeError(
            f"BEAM schema {schema_version} scored protocol requires a supported profile"
        )
    expected_fixed = {
        "chat_sizes": ["100K"],
        "conversations": [0],
        "question_types": list(QUESTION_TYPES),
        "top_k": 50,
        "top_k_cutoffs": [50],
        "predict_only": False,
        "chunk_size": 2,
    }
    if any(value.get(key) != expected for key, expected in expected_fixed.items()):
        raise RuntimeError("BEAM scored protocol changes the registered selection")
    for key in ("project_name", "run_id"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise RuntimeError(f"BEAM scored protocol requires {key}")
    for key in ("max_workers", "rpm"):
        if (
            not isinstance(value.get(key), int)
            or isinstance(value[key], bool)
            or value[key] < 1
        ):
            raise RuntimeError(f"BEAM scored protocol requires a positive {key}")
    models = registration.get("models")
    if not isinstance(models, dict) or set(models) != {"answerer", "judge"}:
        raise RuntimeError("BEAM scored registration must bind answerer and judge")
    for role, model in models.items():
        if not isinstance(model, dict):
            raise RuntimeError(f"BEAM {role} identity must be an object")
        if model.get("provider") != "openai":
            raise RuntimeError(
                "The registered local BEAM run requires the OpenAI-compatible provider"
            )
        for key in ("model", "base_url", "model_digest"):
            if not isinstance(model.get(key), str) or not model[key].strip():
                raise RuntimeError(f"BEAM {role} identity requires {key}")
        if model["base_url"] != "http://127.0.0.1:11434/v1":
            raise RuntimeError(
                "The registered BEAM model endpoint must be loopback Ollama"
            )
    if models["answerer"]["model"] == models["judge"]["model"]:
        raise RuntimeError("BEAM answerer and judge models must be distinct")
    return value


def _ollama_model_digests(base_url: str) -> dict[str, str]:
    native_url = base_url.rstrip("/")
    if native_url.endswith("/v1"):
        native_url = native_url[:-3]
    opener = build_opener(ProxyHandler({}))
    with opener.open(native_url + "/api/tags", timeout=10) as response:
        inventory = json.load(response)
    return {
        item["name"]: item["digest"]
        for item in inventory.get("models", ())
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("digest"), str)
    }


def _verify_models(registration: dict[str, Any]) -> None:
    """Require the registered local model bytes before the first model call."""
    if registration.get("kind") not in {
        SCORED_REGISTRATION_KIND,
        SCORED_REGISTRATION_KIND_V3,
    }:
        return
    models = registration["models"]
    inventories: dict[str, dict[str, str]] = {}
    for role in ("answerer", "judge"):
        identity = models[role]
        base_url = identity["base_url"]
        inventory = inventories.get(base_url)
        if inventory is None:
            inventory = _ollama_model_digests(base_url)
            inventories[base_url] = inventory
        if inventory.get(identity["model"]) != identity["model_digest"]:
            raise RuntimeError(
                f"BEAM {role} Ollama model digest differs from registration"
            )
    if registration["protocol"]["profile"] == "extracted":
        extraction = registration.get("system", {}).get("extraction")
        if not isinstance(extraction, dict):
            raise RuntimeError("extracted BEAM registration must bind extraction")
        inventory = inventories.get(extraction.get("base_url"))
        if inventory is None:
            inventory = _ollama_model_digests(extraction.get("base_url", ""))
        if inventory.get(extraction.get("model")) != extraction.get("model_digest"):
            raise RuntimeError("BEAM extraction model digest differs from registration")


def validate_registration(
    registration: dict[str, Any],
    *,
    project_revision: str,
    upstream_revision: str,
    source_hashes: dict[str, str],
    dataset_sha256: str,
) -> None:
    protocol = _protocol(registration)
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
    system = registration.get("system")
    if (
        not isinstance(system, dict)
        or system.get("id") != "prme"
        or system.get("profile") != protocol["profile"]
        or not isinstance(system.get("version"), str)
    ):
        raise RuntimeError("BEAM registration does not identify the selected PRME system")
    extraction = system.get("extraction")
    if protocol["profile"] == "raw" and extraction is not None:
        raise RuntimeError("raw BEAM registration cannot configure extraction")
    if protocol["profile"] == "extracted":
        if not isinstance(extraction, dict):
            raise RuntimeError("extracted BEAM registration must configure extraction")
        required = {
            "provider",
            "model",
            "base_url",
            "model_digest",
            "reasoning_effort",
            "temperature",
            "timeout",
            "lease_seconds",
        }
        if registration["schema_version"] >= 4:
            required.add("max_retries")
        if set(extraction) != required or extraction.get("provider") != "ollama":
            raise RuntimeError("extracted BEAM registration has invalid extraction identity")
        if registration["schema_version"] >= 4 and (
            not isinstance(extraction.get("max_retries"), int)
            or isinstance(extraction["max_retries"], bool)
            or extraction["max_retries"] < 1
        ):
            raise RuntimeError("registered BEAM extraction retries must be positive")
        if extraction.get("base_url") != "http://127.0.0.1:11434/v1":
            raise RuntimeError("registered BEAM extraction endpoint must be loopback Ollama")
    if registration["schema_version"] in {3, 4, 5}:
        required_system = {
            "id",
            "version",
            "profile",
            "adapter_schema",
            "duckdb_threads",
            "embedding",
            "scoring_version",
            "packing",
            "extraction",
        }
        if registration["schema_version"] >= 5:
            required_system.update({"retrieval", "admission"})
        if set(system) != required_system:
            raise RuntimeError(
                f"BEAM schema {registration['schema_version']} must bind the complete adapter configuration"
            )
        if (
            not isinstance(system.get("adapter_schema"), int)
            or isinstance(system["adapter_schema"], bool)
            or not isinstance(system.get("duckdb_threads"), int)
            or isinstance(system["duckdb_threads"], bool)
            or not isinstance(system.get("embedding"), dict)
            or not isinstance(system.get("packing"), dict)
            or not isinstance(system.get("scoring_version"), str)
            or not system["scoring_version"]
        ):
            raise RuntimeError("BEAM schema 3 adapter configuration is incomplete")
        if registration["schema_version"] >= 5:
            if system["adapter_schema"] != 4:
                raise RuntimeError("BEAM schema 5 requires adapter schema 4")
            if system.get("retrieval") != {
                "max_per_source": 1,
                "passage_time": "latest_evidence_event",
            }:
                raise RuntimeError("BEAM schema 5 must bind source-diverse passage retrieval")
            if system.get("admission") != {
                "raw_materialization_before_ack": True,
                "extraction_before_ack": protocol["profile"] == "extracted",
            }:
                raise RuntimeError("BEAM schema 5 must bind complete admission work")
    _verify_models(registration)


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
) -> dict[str, Any]:
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
    protocol = _protocol(registration)
    scored = not protocol["predict_only"]

    output_root.mkdir(parents=True, exist_ok=True)
    scored_schema = registration["schema_version"] if scored else 1
    scored_kind = (
        SCORED_EXECUTION_KIND_V3
        if scored_schema >= 3
        else SCORED_EXECUTION_KIND
    )
    manifest = {
        "schema_version": scored_schema,
        "kind": scored_kind if scored else PREDICT_EXECUTION_KIND,
        "registration_sha256": digest(registration_path),
        "dataset_sha256": dataset_sha256,
        **({"protocol": protocol, "models": registration["models"]} if scored else {}),
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
        "prediction_dir": prediction_root / f"predicted_{protocol['project_name']}",
        "service_log": output_root / "service.log",
        "protocol": protocol,
        "models": registration.get("models"),
        "system": registration.get("system"),
    }


def _wait_for_service(
    port: int, process: subprocess.Popen[Any], *, profile: str
) -> None:
    deadline = time.monotonic() + 60
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("PRME BEAM service exited before becoming ready")
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310 - loopback only
                body = json.loads(response.read())
            if body.get("status") == "ok" and body.get("profile") == profile:
                return
        except (OSError, URLError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise RuntimeError("PRME BEAM service did not become ready within 60 seconds")


def launch(
    *, upstream_root: Path, paths: dict[str, Any], port: int, resume: bool
) -> Path:
    protocol = paths["protocol"]
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
        protocol["profile"],
        "--duckdb-threads",
        "1",
        "--port",
        str(port),
    ]
    if protocol["profile"] == "extracted":
        extraction = paths["system"]["extraction"]
        service_command.extend(
            [
                "--extraction-provider",
                extraction["provider"],
                "--extraction-model",
                extraction["model"],
                "--extraction-base-url",
                extraction["base_url"],
                "--extraction-reasoning-effort",
                extraction["reasoning_effort"],
                "--extraction-max-retries",
                str(extraction.get("max_retries", 3)),
                "--extraction-timeout",
                str(extraction["timeout"]),
                "--extraction-lease-seconds",
                str(extraction["lease_seconds"]),
            ]
        )
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
            _wait_for_service(port, service, profile=protocol["profile"])
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
                protocol["project_name"],
                "--backend",
                "oss",
                "--mem0-host",
                f"http://127.0.0.1:{port}",
                "--chat-sizes",
                ",".join(protocol["chat_sizes"]),
                "--conversations",
                ",".join(str(value) for value in protocol["conversations"]),
                "--top-k",
                str(protocol["top_k"]),
                "--top-k-cutoffs",
                ",".join(str(value) for value in protocol["top_k_cutoffs"]),
                "--question-types",
                ",".join(protocol["question_types"]),
                "--run-id",
                protocol["run_id"],
                "--dataset-cache-dir",
                str(paths["dataset_dir"]),
                "--output-dir",
                str(paths["prediction_root"]),
            ]
            environment = None
            if protocol["predict_only"]:
                command.append("--predict-only")
            else:
                models = paths["models"]
                command.extend(
                    [
                        "--answerer-model",
                        models["answerer"]["model"],
                        "--judge-model",
                        models["judge"]["model"],
                        "--provider",
                        models["answerer"]["provider"],
                        "--judge-provider",
                        models["judge"]["provider"],
                        "--max-workers",
                        str(protocol["max_workers"]),
                        "--rpm",
                        str(protocol["rpm"]),
                    ]
                )
                environment = os.environ.copy()
                environment["OPENAI_BASE_URL"] = models["answerer"]["base_url"]
                environment["OPENAI_API_KEY"] = "local-beam-evaluation"
            if resume:
                command.append("--resume")
            subprocess.run(command, cwd=upstream_root, env=environment, check=True)
        finally:
            service.terminate()
            try:
                service.wait(timeout=30)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=10)
    return cast(Path, paths["prediction_dir"])


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
