"""Fail-closed verification for scored PRME MemoryAgentBench runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any
from uuid import UUID

import duckdb
import yaml  # type: ignore[import-untyped]

from benchmarks.integrations import memoryagentbench as adapter
from benchmarks.integrations import register_memoryagentbench as registrar
from prme.models.relevance import RetrievalReceipt
from prme.retrieval.tokenization import count_tokens
from prme.types import Scope


_CAPTURE_NAME = re.compile(r"query_(\d+)_context_(\d+)\.json\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_yaml_object(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return value


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _require_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        raise ValueError(
            f"{label} must be finite and {'positive' if positive else 'non-negative'}"
        )
    return number


def _require_metric_number(value: object, label: str) -> float:
    """Accept upstream boolean match metrics while retaining finite-number checks."""
    if isinstance(value, bool):
        return float(value)
    return _require_number(value, label)


def _verify_manifest(
    path: Path,
    *,
    expected_sub_dataset: str,
    expected_user_id: str,
    expected_budget: int,
    expected_result_limit: int,
    expected_max_chunk_chars: int,
    expected_context_format: str,
    expected_reasoning_effort: str | None,
    expected_reader_seed: int | None,
    expected_run_id: str,
) -> dict[str, Any]:
    manifest = _load_object(path)
    identity = manifest.get("config")
    expected_identity = {
        "adapter_schema_version": adapter.ADAPTER_SCHEMA_VERSION,
        "upstream_revision": adapter.UPSTREAM_REVISION,
        "dataset_revision": adapter.DATASET_REVISION,
        "sub_dataset": expected_sub_dataset,
        "user_id": expected_user_id,
        "token_budget": expected_budget,
        "result_limit": expected_result_limit,
        "max_chunk_chars": expected_max_chunk_chars,
        "embedding_provider": "fastembed",
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "embedding_dimension": 384,
        "segmentation_policy": adapter._SEGMENTATION_POLICY,
        "retrieval_query_policy": adapter._RETRIEVAL_QUERY_POLICY,
        "packing_policy": "balanced",
        "context_format": expected_context_format,
        "reader_reasoning_effort": expected_reasoning_effort,
        "reader_seed": expected_reader_seed,
        "run_id": expected_run_id,
    }
    if (
        manifest.get("schema_version") != adapter.ADAPTER_SCHEMA_VERSION
        or manifest.get("status") != "complete"
        or identity != expected_identity
        or manifest.get("config_sha256")
        != hashlib.sha256(_canonical(identity)).hexdigest()
    ):
        raise ValueError(f"{path} has an incompatible or incomplete manifest")

    chunks = manifest.get("source_chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError(f"{path} has no source chunks")
    pieces = 0
    for index, chunk in enumerate(chunks):
        if (
            not isinstance(chunk, dict)
            or chunk.get("index") != index
            or not isinstance(chunk.get("sha256"), str)
            or _HEX64.fullmatch(chunk["sha256"]) is None
            or isinstance(chunk.get("piece_count"), bool)
            or not isinstance(chunk.get("piece_count"), int)
            or chunk["piece_count"] < 0
        ):
            raise ValueError(f"{path} has an invalid source chunk at index {index}")
        pieces += chunk["piece_count"]
    if pieces <= 0:
        raise ValueError(f"{path} has no stored source records")
    if manifest.get("stored_nodes") != pieces:
        raise ValueError(f"{path} stored-node count does not match its source chunks")
    reference_time = manifest.get("query_reference_time")
    if not isinstance(reference_time, str) or not reference_time.endswith(
        ("+00:00", "Z")
    ):
        raise ValueError(f"{path} does not contain a UTC query reference time")
    _require_number(manifest.get("ingest_seconds"), f"{path} ingest_seconds")
    return manifest


def _load_receipt(
    pack_root: Path, *, request_id: str, user_id: str
) -> RetrievalReceipt:
    """Read and authenticate one durable receipt without mutating the pack."""
    database = pack_root / "memory.duckdb"
    if not database.is_file():
        raise ValueError(f"{pack_root} has no memory.duckdb")
    try:
        connection = duckdb.connect(str(database), read_only=True)
        try:
            rows = connection.execute(
                "SELECT payload FROM operations "
                "WHERE op_type='RETRIEVAL_REQUEST' AND target_id=? AND actor_id=? "
                "LIMIT 2",
                [request_id, user_id],
            ).fetchall()
        finally:
            connection.close()
    except duckdb.Error as error:
        raise ValueError(f"retrieval {request_id} receipt store is unreadable") from error
    if len(rows) != 1:
        raise ValueError(f"retrieval {request_id} has no unique durable receipt")
    payload = rows[0][0]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(payload, dict) or not isinstance(payload.get("receipt"), str):
        raise ValueError(f"retrieval {request_id} has an invalid durable receipt")
    receipt = RetrievalReceipt.model_validate_json(payload["receipt"])
    if (
        str(receipt.request_id) != request_id
        or receipt.user_id != user_id
        or receipt.checksum != payload.get("receipt_checksum")
    ):
        raise ValueError(f"retrieval {request_id} has a mismatched durable receipt")
    return receipt


def verify(
    *,
    upstream_root: Path,
    prme_root: Path,
    registration_path: Path,
    result_path: Path,
    agent_config_path: Path,
    dataset_config_path: Path,
    preprocessing_identity: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Verify and summarize one completed official-harness result."""
    registration = _load_object(registration_path)
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "memoryagentbench-prme-registration"
    ):
        raise ValueError("unsupported MemoryAgentBench registration")
    registered_source = registration.get("source")
    registered_configuration = registration.get("configuration")
    registered_task = registration.get("task")
    if (
        not isinstance(registered_source, dict)
        or not isinstance(registered_configuration, dict)
        or not isinstance(registered_task, dict)
    ):
        raise ValueError(
            "registration is missing source, configuration, or task identity"
        )
    expected_prme_revision = registered_source.get("prme_revision")
    expected_queries = registered_task.get("query_count")
    if (
        not isinstance(expected_prme_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", expected_prme_revision) is None
        or isinstance(expected_queries, bool)
        or not isinstance(expected_queries, int)
        or expected_queries <= 0
    ):
        raise ValueError("registration has an invalid PRME revision or query count")
    actual_prme_revision = _git(prme_root, "rev-parse", "HEAD").lower()
    if actual_prme_revision != expected_prme_revision:
        raise ValueError("PRME source revision does not match the declared revision")
    source_paths = [
        Path("benchmarks/integrations/memoryagentbench.py"),
        Path("benchmarks/integrations/install_memoryagentbench.py"),
        Path("benchmarks/integrations/register_memoryagentbench.py"),
        Path("benchmarks/integrations/verify_memoryagentbench.py"),
    ]
    dirty = _git(
        prme_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *(str(path) for path in source_paths),
    )
    if dirty:
        raise ValueError("MemoryAgentBench verification sources are not committed")
    actual_upstream_revision = _git(upstream_root, "rev-parse", "HEAD").lower()
    if actual_upstream_revision != adapter.UPSTREAM_REVISION:
        raise ValueError("MemoryAgentBench source revision does not match the adapter")

    registered_prme_hashes = registered_source.get("prme_files_sha256")
    registered_upstream_hashes = registered_source.get("upstream_files_sha256")
    expected_upstream_names = {
        "main.py",
        "agent.py",
        "conversation_creator.py",
        "initialization.py",
        "utils/eval_data_utils.py",
        "utils/eval_other_utils.py",
    }
    if (
        not isinstance(registered_prme_hashes, dict)
        or not isinstance(registered_upstream_hashes, dict)
        or set(registered_upstream_hashes) != expected_upstream_names
    ):
        raise ValueError("registration is missing benchmark source hashes")
    for source_path in source_paths:
        if registered_prme_hashes.get(source_path.name) != _digest(
            prme_root / source_path
        ):
            raise ValueError(f"registered PRME source {source_path.name} has changed")
    for name, expected_digest in registered_upstream_hashes.items():
        path = upstream_root / name
        if (
            not isinstance(name, str)
            or not isinstance(expected_digest, str)
            or not path.is_file()
            or _digest(path) != expected_digest
        ):
            raise ValueError(f"registered upstream source {name!r} has changed")

    installed_adapter = upstream_root / "methods" / "prme.py"
    source_adapter = prme_root / source_paths[0]
    if _digest(installed_adapter) != _digest(source_adapter):
        raise ValueError("installed MemoryAgentBench adapter differs from PRME source")
    if (
        registered_source.get("upstream_revision") != actual_upstream_revision
        or registered_source.get("dataset_revision") != adapter.DATASET_REVISION
        or registered_source.get("installed_adapter_sha256")
        != _digest(installed_adapter)
    ):
        raise ValueError("installed benchmark source differs from the registration")
    actual_preprocessing = preprocessing_identity or registrar._preprocessing_identity()
    if registered_source.get("preprocessing") != actual_preprocessing:
        raise ValueError(
            "MemoryAgentBench preprocessing dependencies differ from the registration"
        )

    agent_config = _load_yaml_object(agent_config_path)
    dataset_config = _load_yaml_object(dataset_config_path)
    if (
        registered_configuration.get("agent") != agent_config
        or registered_configuration.get("dataset") != dataset_config
        or registered_configuration.get("agent_sha256") != _digest(agent_config_path)
        or registered_configuration.get("dataset_sha256")
        != _digest(dataset_config_path)
    ):
        raise ValueError("benchmark configuration differs from the registration")
    result = _load_object(result_path)
    if result.get("agent_config") != agent_config:
        raise ValueError("result agent configuration differs from the supplied file")
    if result.get("dataset_config") != dataset_config:
        raise ValueError("result dataset configuration differs from the supplied file")
    if agent_config.get("agent_name") != "Agentic_memory_prme_rag":
        raise ValueError("result does not identify the PRME MemoryAgentBench agent")
    sub_dataset = dataset_config.get("sub_dataset")
    model = agent_config.get("model")
    output_dir = agent_config.get("output_dir")
    token_budget = agent_config.get("prme_token_budget")
    result_limit = agent_config.get("prme_result_limit", 100)
    max_chunk_chars = agent_config.get("prme_max_chunk_chars", 6000)
    user_id = agent_config.get("prme_user_id", "memoryagentbench")
    context_format = agent_config.get("prme_context_format", "auditable")
    reasoning_effort = agent_config.get("reader_reasoning_effort")
    reader_seed = agent_config.get("reader_seed")
    run_id = agent_config.get("prme_run_id", "default")
    if (
        not isinstance(sub_dataset, str)
        or not sub_dataset
        or not isinstance(model, str)
        or not model
        or not isinstance(output_dir, str)
        or not output_dir
    ):
        raise ValueError(
            "configuration is missing sub-dataset, model, or output directory"
        )
    if (
        isinstance(token_budget, bool)
        or not isinstance(token_budget, int)
        or token_budget <= 0
    ):
        raise ValueError("configuration has an invalid PRME token budget")
    if (
        isinstance(result_limit, bool)
        or not isinstance(result_limit, int)
        or result_limit <= 0
    ):
        raise ValueError("configuration has an invalid PRME result limit")
    if (
        isinstance(max_chunk_chars, bool)
        or not isinstance(max_chunk_chars, int)
        or max_chunk_chars < 512
    ):
        raise ValueError("configuration has an invalid PRME chunk limit")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("configuration has an invalid PRME user ID")
    user_id = user_id.strip()
    if context_format not in {"auditable", "compact"}:
        raise ValueError("configuration has an invalid PRME context format")
    if reasoning_effort not in {None, "none", "low", "medium", "high"}:
        raise ValueError("configuration has an invalid reader reasoning effort")
    if isinstance(reader_seed, bool) or (
        reader_seed is not None and not isinstance(reader_seed, int)
    ):
        raise ValueError("configuration has an invalid reader seed")
    if (
        not isinstance(run_id, str)
        or not run_id
        or len(run_id) > 64
        or any(not char.isalnum() and char not in "._-" for char in run_id)
    ):
        raise ValueError("configuration has an invalid PRME run ID")

    registered_contexts = registered_task.get("contexts")
    if (
        registered_task.get("dataset") != dataset_config.get("dataset")
        or registered_task.get("sub_dataset") != sub_dataset
        or registered_task.get("query_limit") != dataset_config.get("max_test_queries")
        or not isinstance(registered_contexts, list)
        or registered_task.get("context_count") != len(registered_contexts)
    ):
        raise ValueError("registered task identity is inconsistent")
    expected_context_for_query: dict[int, int] = {}
    expected_query_rows: dict[int, dict[str, Any]] = {}
    expected_chunks: dict[int, list[dict[str, Any]]] = {}
    for expected_context_id, registered_context in enumerate(registered_contexts):
        if (
            not isinstance(registered_context, dict)
            or registered_context.get("context_id") != expected_context_id
            or not isinstance(registered_context.get("source_chunks"), list)
            or not registered_context["source_chunks"]
            or not isinstance(registered_context.get("queries"), list)
        ):
            raise ValueError(f"registration context {expected_context_id} is invalid")
        expected_chunks[expected_context_id] = registered_context["source_chunks"]
        for registered_query in registered_context["queries"]:
            query_id = (
                registered_query.get("query_id")
                if isinstance(registered_query, dict)
                else None
            )
            if (
                isinstance(query_id, bool)
                or not isinstance(query_id, int)
                or query_id in expected_context_for_query
            ):
                raise ValueError("registration query IDs are invalid or duplicated")
            expected_context_for_query[query_id] = expected_context_id
            expected_query_rows[query_id] = registered_query
    if set(expected_context_for_query) != set(range(expected_queries)):
        raise ValueError("registration query IDs do not cover the declared task")

    rows = result.get("data")
    if not isinstance(rows, list) or len(rows) != expected_queries:
        raise ValueError("result query count does not match the declared complete run")
    if [row.get("query_id") if isinstance(row, dict) else None for row in rows] != list(
        range(expected_queries)
    ):
        raise ValueError("result query IDs are incomplete, duplicated, or out of order")
    for index, row in enumerate(rows):
        if not isinstance(row.get("query"), str) or not isinstance(
            row.get("output"), str
        ):
            raise ValueError(f"result row {index} has no query or output text")
        _require_number(
            row.get("input_len"), f"result row {index} input_len", positive=True
        )
        _require_number(row.get("output_len"), f"result row {index} output_len")
        _require_number(
            row.get("memory_construction_time", 0),
            f"result row {index} memory_construction_time",
        )
        _require_number(row.get("query_time_len"), f"result row {index} query_time_len")
        registered_query = expected_query_rows[index]
        retrieval_query_sha256 = hashlib.sha256(
            adapter._retrieval_query(row["query"]).encode("utf-8")
        ).hexdigest()
        if (
            registered_query.get("query_sha256")
            != hashlib.sha256(row["query"].encode("utf-8")).hexdigest()
            or registered_query.get("retrieval_query_sha256")
            != retrieval_query_sha256
            or registered_query.get("answer_sha256")
            != hashlib.sha256(_canonical(row.get("answer"))).hexdigest()
            or registered_query.get("qa_pair_id_sha256")
            != hashlib.sha256(_canonical(row.get("qa_pair_id"))).hexdigest()
        ):
            raise ValueError(f"result row {index} differs from registered task inputs")

    metrics = result.get("metrics")
    averages = result.get("averaged_metrics")
    if not isinstance(metrics, dict) or not metrics or not isinstance(averages, dict):
        raise ValueError("result metrics are missing")
    for name, values in metrics.items():
        if (
            not isinstance(name, str)
            or not isinstance(values, list)
            or len(values) != expected_queries
        ):
            raise ValueError(f"metric {name!r} does not cover every query")
        numbers = [_require_metric_number(value, f"metric {name}") for value in values]
        expected_average = math.fsum(numbers) / expected_queries
        if "_len" not in name and "_time" not in name:
            expected_average *= 100
        actual_average = _require_number(averages.get(name), f"averaged metric {name}")
        if not math.isclose(
            actual_average, expected_average, rel_tol=1e-10, abs_tol=1e-10
        ):
            raise ValueError(
                f"averaged metric {name!r} does not match its query values"
            )
    if set(averages) != set(metrics):
        raise ValueError("averaged metric names do not match per-query metrics")

    time_cost = result.get("time_cost")
    if not isinstance(time_cost, list) or not 1 <= len(time_cost) <= expected_queries:
        raise ValueError("result timing checkpoints are missing or over-complete")
    elapsed = [_require_number(value, "time checkpoint") for value in time_cost]
    if elapsed != sorted(elapsed):
        raise ValueError("result timing checkpoints are not monotonic")

    retrieval_root = (
        (upstream_root / output_dir).resolve() / "prme_retrievals" / sub_dataset
    )
    captures: dict[int, tuple[int, Path]] = {}
    for path in retrieval_root.glob("query_*_context_*.json"):
        match = _CAPTURE_NAME.fullmatch(path.name)
        if match is None:
            continue
        query_id, context_id = (int(value) for value in match.groups())
        if query_id in captures:
            raise ValueError(f"query {query_id} has multiple retrieval captures")
        captures[query_id] = (context_id, path)
    if set(captures) != set(range(expected_queries)):
        raise ValueError(
            "retrieval captures do not cover exactly the completed queries"
        )

    agent_root = (
        upstream_root / "agents" / f"prme_{sub_dataset}_model{model}_run{run_id}"
    )
    manifests: dict[int, tuple[Path, dict[str, Any]]] = {}
    context_token_counts: list[int] = []
    included_counts: list[int] = []
    retrieval_seconds: list[float] = []
    aggregate_lines: list[str] = []
    receipt_lines: list[str] = []
    for query_id in range(expected_queries):
        context_id, capture_path = captures[query_id]
        if context_id != expected_context_for_query[query_id]:
            raise ValueError(
                f"query {query_id} retrieval used the wrong source context"
            )
        capture = _load_object(capture_path)
        context = capture.get("context")
        if (
            capture.get("adapter_schema_version") != adapter.ADAPTER_SCHEMA_VERSION
            or capture.get("upstream_revision") != adapter.UPSTREAM_REVISION
            or capture.get("dataset_revision") != adapter.DATASET_REVISION
            or capture.get("sub_dataset") != sub_dataset
            or capture.get("query_id") != query_id
            or capture.get("context_id") != context_id
            or not isinstance(context, str)
            or capture.get("query_sha256")
            != hashlib.sha256(rows[query_id]["query"].encode("utf-8")).hexdigest()
            or capture.get("retrieval_query_sha256")
            != expected_query_rows[query_id]["retrieval_query_sha256"]
            or capture.get("context_sha256")
            != hashlib.sha256(context.encode("utf-8")).hexdigest()
            or capture.get("receipt_persisted") is not True
            or capture.get("token_budget") != token_budget
            or capture.get("context_format") != context_format
            or capture.get("reader_reasoning_effort") != reasoning_effort
            or capture.get("reader_seed") != reader_seed
            or capture.get("run_id") != run_id
        ):
            raise ValueError(f"query {query_id} retrieval capture is inconsistent")
        try:
            UUID(str(capture.get("request_id")))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(
                f"query {query_id} retrieval request ID is invalid"
            ) from exc
        context_tokens = capture.get("context_token_count")
        included_count = capture.get("included_count")
        if (
            isinstance(context_tokens, bool)
            or not isinstance(context_tokens, int)
            or not 0 <= context_tokens <= token_budget
            or context_tokens != count_tokens(context)
            or isinstance(included_count, bool)
            or not isinstance(included_count, int)
            or included_count < 0
        ):
            raise ValueError(f"query {query_id} retrieval accounting is invalid")
        if context_id not in manifests:
            manifest_path = (
                agent_root / f"exp_{context_id}" / "prme_pack" / adapter._MANIFEST_NAME
            )
            manifest = _verify_manifest(
                manifest_path,
                expected_sub_dataset=sub_dataset,
                expected_user_id=user_id,
                expected_budget=token_budget,
                expected_result_limit=result_limit,
                expected_max_chunk_chars=max_chunk_chars,
                expected_context_format=context_format,
                expected_reasoning_effort=reasoning_effort,
                expected_reader_seed=reader_seed,
                expected_run_id=run_id,
            )
            if manifest["source_chunks"] != expected_chunks[context_id]:
                raise ValueError(
                    f"context {context_id} memory inputs differ from the registration"
                )
            manifests[context_id] = (manifest_path, manifest)
        manifest_path, manifest = manifests[context_id]
        if capture.get("manifest_sha256") != _digest(manifest_path):
            raise ValueError(
                f"query {query_id} does not bind its completed memory manifest"
            )
        request_id = str(capture["request_id"])
        receipt = _load_receipt(
            manifest_path.parent,
            request_id=request_id,
            user_id=user_id,
        )
        if (
            hashlib.sha256(receipt.query.encode("utf-8")).hexdigest()
            != capture["retrieval_query_sha256"]
            or receipt.context_sha256 != capture["context_sha256"]
            or receipt.reference_time.isoformat() != manifest["query_reference_time"]
            or receipt.packing.token_budget != token_budget
            or receipt.packing.multipath_ordering != "balanced"
            or receipt.packing.context_format != context_format
            or receipt.result_limit != result_limit
            or receipt.scopes != (Scope.PROJECT,)
            or sum(candidate.in_context for candidate in receipt.candidates)
            != included_count
        ):
            raise ValueError(
                f"query {query_id} durable receipt differs from its retrieval capture"
            )
        context_token_counts.append(context_tokens)
        included_counts.append(included_count)
        retrieval_seconds.append(
            _require_number(rows[query_id].get("query_time_len"), "query latency")
        )
        aggregate_lines.append(
            f"{capture_path.relative_to(retrieval_root)}\0{_digest(capture_path)}"
        )
        receipt_lines.append(f"{query_id}\0{receipt.checksum}")

    return {
        "schema_version": 1,
        "kind": "memoryagentbench-prme-verification",
        "status": "verified_complete",
        "source": {
            "prme_revision": actual_prme_revision,
            "upstream_revision": actual_upstream_revision,
            "dataset_revision": adapter.DATASET_REVISION,
            "preprocessing": actual_preprocessing,
            "registration_sha256": _digest(registration_path),
            "adapter_sha256": _digest(source_adapter),
            "installer_sha256": _digest(prme_root / source_paths[1]),
            "registrar_sha256": _digest(prme_root / source_paths[2]),
            "verifier_sha256": _digest(prme_root / source_paths[3]),
            "agent_config_sha256": _digest(agent_config_path),
            "dataset_config_sha256": _digest(dataset_config_path),
            "result_sha256": _digest(result_path),
            "retrieval_captures_sha256": hashlib.sha256(
                "\n".join(aggregate_lines).encode("utf-8")
            ).hexdigest(),
            "retrieval_receipts_sha256": hashlib.sha256(
                "\n".join(receipt_lines).encode("utf-8")
            ).hexdigest(),
        },
        "task": {
            "dataset": dataset_config.get("dataset"),
            "sub_dataset": sub_dataset,
            "queries": expected_queries,
            "contexts": len(manifests),
            "source_chunks": sum(
                len(manifest["source_chunks"]) for _, manifest in manifests.values()
            ),
            "stored_nodes": sum(
                manifest["stored_nodes"] for _, manifest in manifests.values()
            ),
        },
        "retrieval": {
            "token_budget": token_budget,
            "context_tokens_total": sum(context_token_counts),
            "context_tokens_mean": math.fsum(context_token_counts) / expected_queries,
            "context_tokens_max": max(context_token_counts),
            "included_total": sum(included_counts),
            "query_time_seconds_mean": math.fsum(retrieval_seconds) / expected_queries,
        },
        "averaged_metrics": averages,
        "timing_checkpoints": len(time_cost),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", required=True, type=Path)
    parser.add_argument(
        "--prme-root", default=Path(__file__).resolve().parents[2], type=Path
    )
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--agent-config", required=True, type=Path)
    parser.add_argument("--dataset-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = verify(
        upstream_root=args.upstream_root.resolve(),
        prme_root=args.prme_root.resolve(),
        registration_path=args.registration.resolve(),
        result_path=args.result.resolve(),
        agent_config_path=args.agent_config.resolve(),
        dataset_config_path=args.dataset_config.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(report) + b"\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
