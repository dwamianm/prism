"""Preregister a pinned AgentMemBench operational run before execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from benchmarks.integrations.agentmembench import UPSTREAM_REVISION
from benchmarks.integrations import install_agentmembench


SCHEMA_VERSION = 2
SUPPORTED_PHASES = (
    "retrieval",
    "conflict",
    "isolation",
    "deletion",
    "concurrency",
    "scale",
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER = Path(__file__).with_name("agentmembench.py")
_INSTALLER = Path(__file__).with_name("install_agentmembench.py")
_VERIFIER = Path(__file__).with_name("verify_agentmembench.py")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_MODEL_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _parse_csv(value: str, *, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise RuntimeError(f"{label} must be comma-separated integers") from error
    if not parsed or any(item <= 0 for item in parsed) or len(parsed) != len(set(parsed)):
        raise RuntimeError(f"{label} must contain distinct positive integers")
    return parsed


def ollama_model_identity(base_url: str, model: str) -> dict[str, Any]:
    """Resolve an OpenAI-compatible Ollama model to immutable local weights."""
    parsed = urlparse.urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise RuntimeError(
            "retrieval judge base URL must be an Ollama OpenAI endpoint ending in /v1"
        )
    if not model or any(character.isspace() for character in model):
        raise RuntimeError("retrieval judge model must be a nonempty Ollama model name")
    normalized = urlparse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/v1", "", "")
    )
    tags_url = urlparse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/api/tags", "", "")
    )
    try:
        with urlrequest.urlopen(tags_url, timeout=10) as response:  # noqa: S310
            payload = json.loads(response.read())
    except (OSError, urlerror.URLError, json.JSONDecodeError) as error:
        raise RuntimeError("could not inspect the configured Ollama judge") from error
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Ollama judge returned invalid model metadata")
    matches = [
        item
        for item in models
        if isinstance(item, dict) and item.get("name") == model
    ]
    if len(matches) != 1 or not _MODEL_DIGEST_RE.fullmatch(
        str(matches[0].get("digest", ""))
    ):
        raise RuntimeError("configured Ollama judge model and digest are unavailable")
    return {
        "provider": "ollama",
        "base_url": normalized,
        "model": model,
        "model_digest": matches[0]["digest"],
        "temperature": 0,
        "reasoning_effort": "none",
        "max_tokens": 32,
        "response_format": "json_object",
        "concurrency": 32,
        "attempts": 3,
        "failure_policy": "abort",
    }


def _validate_install(upstream: Path) -> dict[str, str]:
    if _git(upstream, "rev-parse", "HEAD") != UPSTREAM_REVISION:
        raise RuntimeError("AgentMemBench checkout is not at the pinned revision")
    adapter = upstream / "agentmembench" / "prme_adapter.py"
    harness = upstream / "agentmembench" / "evaluation" / "unified_benchmark.py"
    if not adapter.is_file() or adapter.read_bytes() != _ADAPTER.read_bytes():
        raise RuntimeError("installed AgentMemBench adapter does not match PRME source")
    text = harness.read_text(encoding="utf-8")
    if (
        text.count(install_agentmembench._ADAPTER_PATCH) != 1
        or text.count(install_agentmembench._CHOICES_PATCH) != 1
        or text.count(install_agentmembench._JUDGE_FAILURE_PATCH) != 1
        or text.count(install_agentmembench._JUDGE_REASONING_PATCH) != 1
        or install_agentmembench._ADAPTER_ANCHOR in text
        or install_agentmembench._CHOICES_ANCHOR in text
        or install_agentmembench._JUDGE_FAILURE_ANCHOR in text
        or install_agentmembench._JUDGE_REASONING_ANCHOR in text
    ):
        raise RuntimeError("AgentMemBench harness does not contain the exact PRME patch")
    return {
        "installed_adapter_sha256": _sha256(adapter),
        "installed_harness_sha256": _sha256(harness),
    }


def register(
    *,
    prme_root: Path,
    upstream_root: Path,
    data: Path,
    run_id: str,
    phases: str,
    retrieval_records: int = 1000,
    group_size: int = 10,
    conflict_pairs: int = 250,
    isolation_users: int = 100,
    isolation_facts: int = 5,
    deletion_records: int = 200,
    concurrency_records: int = 200,
    workers: str = "1,4,8,16",
    scales: str = "100,1000",
    scale_read_queries: int = 200,
    top_k: int = 5,
    seed: int = 2027,
    warmup_writes: int = 5,
    llm_base_url: str = "http://127.0.0.1:18000/v1",
    llm_model: str = "qwen2.5-14b-instruct",
) -> dict[str, Any]:
    root = prme_root.expanduser().resolve()
    upstream = upstream_root.expanduser().resolve()
    dataset = data.expanduser().resolve()
    if not _RUN_ID_RE.fullmatch(run_id):
        raise RuntimeError("run_id must contain only letters, digits, dot, dash, or underscore")
    selected = [item.strip() for item in phases.split(",") if item.strip()]
    if not selected or len(selected) != len(set(selected)):
        raise RuntimeError("phases must be a nonempty list without duplicates")
    unknown = set(selected) - set(SUPPORTED_PHASES)
    if unknown:
        raise RuntimeError(f"unsupported phases: {sorted(unknown)}")
    counts = {
        "retrieval_records": retrieval_records,
        "group_size": group_size,
        "conflict_pairs": conflict_pairs,
        "isolation_users": isolation_users,
        "isolation_facts": isolation_facts,
        "deletion_records": deletion_records,
        "concurrency_records": concurrency_records,
        "scale_read_queries": scale_read_queries,
        "top_k": top_k,
        "warmup_writes": warmup_writes,
    }
    if any(isinstance(value, bool) or value < 0 for value in counts.values()):
        raise RuntimeError("benchmark counts must be nonnegative integers")
    if any(counts[key] == 0 for key in counts if key not in {"warmup_writes"}):
        raise RuntimeError("benchmark counts other than warmup_writes must be positive")
    worker_values = _parse_csv(workers, label="workers")
    scale_values = _parse_csv(scales, label="scales")
    judge = (
        ollama_model_identity(llm_base_url, llm_model)
        if "retrieval" in selected
        else None
    )
    if not dataset.is_file():
        raise RuntimeError(f"missing AgentMemBench data file: {dataset}")
    revision = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("PRME tracked files must be clean before registration")
    installed = _validate_install(upstream)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "agentmembench-prme-registration",
        "status": "registered",
        "run_id": run_id,
        "system": "prme",
        "arguments": {
            "phases": selected,
            **counts,
            "workers": worker_values,
            "scales": scale_values,
            "seed": seed,
            "llm_base_url": llm_base_url,
            "llm_model": llm_model,
        },
        "source": {
            "prme_revision": revision,
            "upstream_revision": UPSTREAM_REVISION,
            "data_sha256": _sha256(dataset),
            "adapter_sha256": _sha256(_ADAPTER),
            "installer_sha256": _sha256(_INSTALLER),
            "registrar_sha256": _sha256(Path(__file__)),
            "verifier_sha256": _sha256(_VERIFIER),
            **installed,
        },
        **({"judge": judge} if judge is not None else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prme-root", type=Path, default=_PROJECT_ROOT)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--phases", required=True)
    parser.add_argument("--retrieval-records", type=int, default=1000)
    parser.add_argument("--group-size", type=int, default=10)
    parser.add_argument("--conflict-pairs", type=int, default=250)
    parser.add_argument("--isolation-users", type=int, default=100)
    parser.add_argument("--isolation-facts", type=int, default=5)
    parser.add_argument("--deletion-records", type=int, default=200)
    parser.add_argument("--concurrency-records", type=int, default=200)
    parser.add_argument("--workers", default="1,4,8,16")
    parser.add_argument("--scales", default="100,1000")
    parser.add_argument("--scale-read-queries", type=int, default=200)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--seed", type=int, default=2027)
    parser.add_argument("--warmup-writes", type=int, default=5)
    parser.add_argument("--llm-base-url", default="http://127.0.0.1:18000/v1")
    parser.add_argument("--llm-model", default="qwen2.5-14b-instruct")
    args = parser.parse_args()
    try:
        payload = register(
            **{
                key: value
                for key, value in vars(args).items()
                if key != "output"
            }
        )
    except RuntimeError as error:
        parser.exit(2, f"error: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
