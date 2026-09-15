"""Verify a preregistered AgentMemBench run and emit aggregate-only evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any

from benchmarks.integrations.agentmembench import (
    ADAPTER_SCHEMA_VERSION,
    UPSTREAM_REVISION,
)
from benchmarks.integrations import install_agentmembench
from benchmarks.integrations.register_agentmembench import SUPPORTED_PHASES


SCHEMA_VERSION = 1
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
_MANIFEST_NAME = "agentmembench-prme-manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"could not inspect Git checkout at {root}") from error


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"could not read {label}: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _require_finite(value: Any, label: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeError(f"{label} contains a non-finite value")
        return
    if isinstance(value, list):
        for item in value:
            _require_finite(item, label)
        return
    if isinstance(value, dict):
        for item in value.values():
            _require_finite(item, label)
        return
    raise RuntimeError(f"{label} contains a non-JSON value")


def _normalize_collection(run_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", f"amb_kdd27_prme_{run_id}".lower())[:60]


def _expected_generations(arguments: dict[str, Any]) -> list[dict[str, int]]:
    expected: list[dict[str, int]] = []

    def add(adds: int, searches: int = 0, archives: int = 0) -> None:
        expected.append(
            {
                "add_calls": adds,
                "search_calls": searches,
                "archived_ids": archives,
            }
        )

    if arguments["warmup_writes"] > 0:
        add(arguments["warmup_writes"], 1)
        # Upstream resets once at the end of warmup and the next phase resets
        # again. Retain and verify that intervening empty generation as part of
        # the exact pinned harness behavior.
        add(0)
    phases = set(arguments["phases"])
    if "retrieval" in phases:
        add(arguments["retrieval_records"], arguments["retrieval_records"])
    if "conflict" in phases:
        add(arguments["conflict_pairs"] * 2, arguments["conflict_pairs"])
    if "isolation" in phases:
        add(
            arguments["isolation_users"] * arguments["isolation_facts"],
            arguments["isolation_users"],
        )
    if "deletion" in phases:
        add(
            arguments["deletion_records"],
            arguments["deletion_records"] * 2,
            arguments["deletion_records"],
        )
    if "concurrency" in phases:
        for _worker in arguments["workers"]:
            add(arguments["concurrency_records"])
    if "scale" in phases:
        for scale in arguments["scales"]:
            add(scale, min(arguments["scale_read_queries"], scale))
    return expected


def _validate_sources(
    registration: dict[str, Any], prme_root: Path, upstream_root: Path, data: Path
) -> None:
    source = registration.get("source")
    if not isinstance(source, dict):
        raise RuntimeError("registration lacks source identity")
    expected = {
        "prme_revision": _git(prme_root, "rev-parse", "HEAD"),
        "upstream_revision": _git(upstream_root, "rev-parse", "HEAD"),
        "data_sha256": _sha256(data),
        "adapter_sha256": _sha256(_HERE / "agentmembench.py"),
        "installer_sha256": _sha256(_HERE / "install_agentmembench.py"),
        "registrar_sha256": _sha256(_HERE / "register_agentmembench.py"),
        "verifier_sha256": _sha256(Path(__file__)),
        "installed_adapter_sha256": _sha256(
            upstream_root / "agentmembench" / "prme_adapter.py"
        ),
        "installed_harness_sha256": _sha256(
            upstream_root / "agentmembench" / "evaluation" / "unified_benchmark.py"
        ),
    }
    if expected["upstream_revision"] != UPSTREAM_REVISION:
        raise RuntimeError("AgentMemBench checkout moved from the pinned revision")
    for key, value in expected.items():
        if source.get(key) != value:
            raise RuntimeError(f"registration source mismatch: {key}")
    if _git(prme_root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("PRME tracked files changed after registration")
    harness = (
        upstream_root / "agentmembench" / "evaluation" / "unified_benchmark.py"
    ).read_text(encoding="utf-8")
    if (
        harness.count(install_agentmembench._ADAPTER_PATCH) != 1
        or harness.count(install_agentmembench._CHOICES_PATCH) != 1
    ):
        raise RuntimeError("installed AgentMemBench patch changed after registration")


def _validate_result_arguments(
    result: dict[str, Any], registration: dict[str, Any]
) -> None:
    if result.get("schema_version") != "unified-benchmark-v2":
        raise RuntimeError("unsupported AgentMemBench result schema")
    if result.get("system") != "prme" or registration.get("system") != "prme":
        raise RuntimeError("result is not a PRME run")
    if result.get("run_id") != registration.get("run_id"):
        raise RuntimeError("result run_id differs from registration")
    expected_collection = _normalize_collection(str(registration["run_id"]))
    if result.get("collection") != expected_collection:
        raise RuntimeError("result collection differs from registered run")
    actual = result.get("arguments")
    registered = registration.get("arguments")
    if not isinstance(actual, dict) or not isinstance(registered, dict):
        raise RuntimeError("result or registration lacks arguments")
    checks = (
        "retrieval_records",
        "group_size",
        "conflict_pairs",
        "isolation_users",
        "isolation_facts",
        "deletion_records",
        "concurrency_records",
        "scale_read_queries",
        "top_k",
        "seed",
        "warmup_writes",
    )
    for key in checks:
        if actual.get(key) != registered.get(key):
            raise RuntimeError(f"result argument differs from registration: {key}")
    actual_phases = [item.strip() for item in str(actual.get("phases", "")).split(",") if item.strip()]
    actual_workers = [int(item) for item in str(actual.get("workers", "")).split(",")]
    actual_scales = [int(item) for item in str(actual.get("scales", "")).split(",")]
    if actual_phases != registered.get("phases"):
        raise RuntimeError("result phases differ from registration")
    if actual_workers != registered.get("workers"):
        raise RuntimeError("result workers differ from registration")
    if actual_scales != registered.get("scales"):
        raise RuntimeError("result scales differ from registration")


def _validate_manifests(
    result: dict[str, Any], registration: dict[str, Any], history_dir: Path
) -> None:
    root = history_dir / "prme" / str(result["collection"])
    manifest = _load_object(root / _MANIFEST_NAME, "adapter manifest")
    source = registration["source"]
    expected = _expected_generations(registration["arguments"])
    if manifest.get("schema_version") != ADAPTER_SCHEMA_VERSION:
        raise RuntimeError("unsupported adapter manifest schema")
    if manifest.get("status") != "complete":
        raise RuntimeError("adapter manifest is incomplete")
    if manifest.get("generation_count") != len(expected):
        raise RuntimeError("adapter generation count does not match registered phases")
    if manifest.get("adapter_sha256") != source["adapter_sha256"]:
        raise RuntimeError("runtime adapter differs from registration")
    if manifest.get("upstream_revision") != UPSTREAM_REVISION:
        raise RuntimeError("runtime adapter reports another upstream revision")
    runtime = manifest.get("prme")
    if not isinstance(runtime, dict):
        raise RuntimeError("adapter manifest lacks PRME runtime identity")
    if runtime.get("revision") != source["prme_revision"]:
        raise RuntimeError("benchmark imported a different PRME revision")
    if runtime.get("tracked_files_dirty") is not False:
        raise RuntimeError("benchmark imported PRME with dirty tracked files")
    if manifest.get("delete_semantics") != "archive_retrieval_retirement":
        raise RuntimeError("adapter deletion semantics changed")
    generation_dirs = sorted(path for path in root.glob("generation-*") if path.is_dir())
    if len(generation_dirs) != len(expected):
        raise RuntimeError("retained generation directories are incomplete")
    for index, (path, counts) in enumerate(zip(generation_dirs, expected), start=1):
        item = _load_object(path / _MANIFEST_NAME, "generation manifest")
        if (
            item.get("status") != "complete"
            or item.get("generation") != index
            or item.get("operation_counts") != counts
        ):
            raise RuntimeError(f"generation {index} does not match registered workload")


def _safe_phases(result: dict[str, Any], registered: dict[str, Any]) -> dict[str, Any]:
    phases = result.get("phases")
    if not isinstance(phases, dict) or set(phases) != set(registered["phases"]):
        raise RuntimeError("result phases are incomplete or unexpected")
    safe: dict[str, Any] = {}
    for phase in SUPPORTED_PHASES:
        if phase not in phases:
            continue
        value = phases[phase]
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid {phase} result")
        if phase == "retrieval":
            safe[phase] = {key: item for key, item in value.items() if key != "details"}
        elif phase == "concurrency":
            safe[phase] = {
                workers: {
                    key: item
                    for key, item in metrics.items()
                    if key != "error_examples"
                }
                for workers, metrics in value.items()
                if isinstance(metrics, dict)
            }
            if len(safe[phase]) != len(value):
                raise RuntimeError("invalid concurrency result")
        else:
            safe[phase] = value
    _require_finite(safe, "aggregate result")
    return safe


def verify(
    *,
    prme_root: Path,
    upstream_root: Path,
    data: Path,
    history_dir: Path,
    registration_path: Path,
    result_path: Path,
) -> dict[str, Any]:
    root = prme_root.expanduser().resolve()
    upstream = upstream_root.expanduser().resolve()
    dataset = data.expanduser().resolve()
    history = history_dir.expanduser().resolve()
    registration = _load_object(registration_path, "registration")
    result = _load_object(result_path, "result")
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "agentmembench-prme-registration"
        or registration.get("status") != "registered"
    ):
        raise RuntimeError("unsupported AgentMemBench registration")
    _validate_sources(registration, root, upstream, dataset)
    _validate_result_arguments(result, registration)
    config = result.get("config")
    if not isinstance(config, dict) or Path(str(config.get("history_dir"))).resolve() != history:
        raise RuntimeError("result history directory differs from verification input")
    _validate_manifests(result, registration, history)
    phases = _safe_phases(result, registration["arguments"])
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "agentmembench-prme-verification",
        "status": "verified_complete",
        "run_id": registration["run_id"],
        "system": "prme",
        "arguments": registration["arguments"],
        "phases": phases,
        "source": {
            **registration["source"],
            "registration_sha256": _sha256(registration_path),
            "result_sha256": _sha256(result_path),
        },
        "limitations": [
            "These are development diagnostics on the named synthetic workloads, not a universal product ranking.",
            "AgentMemBench deletion measures post-delete retrieval absence; PRME implements that adapter operation by archival, not physical event erasure.",
            "Latency measurements are local-run observations and are not controlled cross-system comparisons.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prme-root", type=Path, default=_PROJECT_ROOT)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--registration", dest="registration_path", type=Path, required=True)
    parser.add_argument("--result", dest="result_path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = verify(
            **{key: value for key, value in vars(args).items() if key != "output"}
        )
    except (RuntimeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
