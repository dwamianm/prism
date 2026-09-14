"""Fail-closed verification for scored BM25 MemoryAgentBench runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from benchmarks.integrations import memoryagentbench as adapter
from benchmarks.integrations import register_memoryagentbench_bm25 as registrar
from benchmarks.integrations.verify_memoryagentbench import (
    _canonical,
    _digest,
    _git,
    _load_object,
    _load_yaml_object,
    _require_metric_number,
    _require_number,
)
from prme.retrieval.tokenization import count_tokens


_CAPTURE_NAME = re.compile(r"query_(\d+)_context_(\d+)\.json\Z")


def _rank_indices(documents: list[str], query: str, limit: int) -> list[int]:
    """Reproduce LangChain BM25Retriever's pinned default ranking path."""
    import numpy
    from rank_bm25 import BM25Okapi

    vectorizer = BM25Okapi([document.split() for document in documents])
    scores = vectorizer.get_scores(query.split())
    return [int(index) for index in numpy.argsort(scores)[::-1][:limit]]


def _validate_result(
    result: dict[str, Any],
    *,
    agent_config: dict[str, Any],
    dataset_config: dict[str, Any],
    registered_queries: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[float]]:
    expected_queries = len(registered_queries)
    if result.get("agent_config") != agent_config:
        raise ValueError("result agent configuration differs from the supplied file")
    if result.get("dataset_config") != dataset_config:
        raise ValueError("result dataset configuration differs from the supplied file")
    rows = result.get("data")
    if not isinstance(rows, list) or len(rows) != expected_queries:
        raise ValueError("result query count does not match the declared complete run")
    if [row.get("query_id") if isinstance(row, dict) else None for row in rows] != list(
        range(expected_queries)
    ):
        raise ValueError("result query IDs are incomplete, duplicated, or out of order")
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(
            row.get("query"), str
        ) or not isinstance(row.get("output"), str):
            raise ValueError(f"result row {index} has no query or output text")
        _require_number(row.get("input_len"), f"result row {index} input_len", positive=True)
        _require_number(row.get("output_len"), f"result row {index} output_len")
        _require_number(
            row.get("memory_construction_time", 0),
            f"result row {index} memory_construction_time",
        )
        _require_number(row.get("query_time_len"), f"result row {index} query_time_len")
        registered = registered_queries[index]
        if (
            registered.get("query_sha256")
            != hashlib.sha256(row["query"].encode("utf-8")).hexdigest()
            or registered.get("retrieval_query_sha256")
            != hashlib.sha256(
                registrar._extract_retrieval_query(row["query"]).encode("utf-8")
            ).hexdigest()
            or registered.get("answer_sha256")
            != hashlib.sha256(_canonical(row.get("answer"))).hexdigest()
            or registered.get("qa_pair_id_sha256")
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
            raise ValueError(f"averaged metric {name!r} does not match its query values")
    if set(averages) != set(metrics):
        raise ValueError("averaged metric names do not match per-query metrics")

    time_cost = result.get("time_cost")
    if not isinstance(time_cost, list) or not 1 <= len(time_cost) <= expected_queries:
        raise ValueError("result timing checkpoints are missing or over-complete")
    elapsed = [_require_number(value, "time checkpoint") for value in time_cost]
    if elapsed != sorted(elapsed):
        raise ValueError("result timing checkpoints are not monotonic")
    return rows, averages, elapsed


def verify(
    *,
    upstream_root: Path,
    prme_root: Path,
    registration_path: Path,
    result_path: Path,
    agent_config_path: Path,
    dataset_config_path: Path,
    chunks: list[list[str]] | None = None,
    query_groups: list[list[tuple[object, ...]]] | None = None,
    memorize_template: str | None = None,
    dependency_identity: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Verify one complete official-harness BM25 result and its exact rankings."""
    registration = _load_object(registration_path)
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "memoryagentbench-bm25-registration"
    ):
        raise ValueError("unsupported MemoryAgentBench BM25 registration")
    source = registration.get("source")
    configuration = registration.get("configuration")
    task = registration.get("task")
    if not all(isinstance(value, dict) for value in (source, configuration, task)):
        raise ValueError("registration is missing source, configuration, or task identity")
    expected_revision = source.get("prme_revision")
    if not isinstance(expected_revision, str) or re.fullmatch(
        r"[0-9a-f]{40}", expected_revision
    ) is None:
        raise ValueError("registration has an invalid PRME revision")
    actual_revision = _git(prme_root, "rev-parse", "HEAD").lower()
    if actual_revision != expected_revision:
        raise ValueError("PRME source revision does not match the declared revision")
    source_root = prme_root / "benchmarks" / "integrations"
    dirty = _git(
        prme_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *(str(Path("benchmarks/integrations") / name) for name in registrar.SOURCE_NAMES),
    )
    if dirty:
        raise ValueError("MemoryAgentBench BM25 verification sources are not committed")
    actual_upstream_revision = _git(upstream_root, "rev-parse", "HEAD").lower()
    if actual_upstream_revision != adapter.UPSTREAM_REVISION:
        raise ValueError("MemoryAgentBench source revision does not match the adapter")
    registered_prme_hashes = source.get("prme_files_sha256")
    registered_upstream_hashes = source.get("upstream_files_sha256")
    if (
        not isinstance(registered_prme_hashes, dict)
        or set(registered_prme_hashes) != set(registrar.SOURCE_NAMES)
        or not isinstance(registered_upstream_hashes, dict)
        or set(registered_upstream_hashes) != set(registrar.UPSTREAM_SOURCE_NAMES)
    ):
        raise ValueError("registration is missing BM25 benchmark source hashes")
    for name in registrar.SOURCE_NAMES:
        if registered_prme_hashes.get(name) != _digest(source_root / name):
            raise ValueError(f"registered PRME source {name} has changed")
    for name in registrar.UPSTREAM_SOURCE_NAMES:
        if registered_upstream_hashes.get(name) != _digest(upstream_root / name):
            raise ValueError(f"registered upstream source {name} has changed")
    if (
        source.get("upstream_revision") != actual_upstream_revision
        or source.get("dataset_revision") != adapter.DATASET_REVISION
    ):
        raise ValueError("installed benchmark source differs from the registration")
    actual_dependencies = dependency_identity or registrar._dependency_identity()
    if source.get("dependencies") != actual_dependencies:
        raise ValueError("BM25 ranking dependencies differ from the registration")

    agent_config = _load_yaml_object(agent_config_path)
    dataset_config = _load_yaml_object(dataset_config_path)
    if (
        configuration.get("agent") != agent_config
        or configuration.get("dataset") != dataset_config
        or configuration.get("agent_sha256") != _digest(agent_config_path)
        or configuration.get("dataset_sha256") != _digest(dataset_config_path)
    ):
        raise ValueError("benchmark configuration differs from the registration")
    if agent_config.get("agent_name") != "Simple_rag_bm25":
        raise ValueError("result does not identify the BM25 MemoryAgentBench baseline")

    if chunks is None or query_groups is None or memorize_template is None:
        with registrar._upstream_imports(upstream_root):
            from conversation_creator import ConversationCreator
            from utils.templates import get_template

            conversation = ConversationCreator(agent_config, dataset_config)
            if chunks is None:
                chunks = conversation.get_chunks()
            if query_groups is None:
                query_groups = conversation.get_query_and_answers()
            if memorize_template is None:
                memorize_template = get_template(
                    dataset_config.get("sub_dataset"), "memorize", "Simple_rag_bm25"
                )
    if configuration.get("memorize_template_sha256") != hashlib.sha256(
        memorize_template.encode("utf-8")
    ).hexdigest():
        raise ValueError("BM25 memorize template differs from the registration")

    run_id = agent_config.get("retrieval_run_id")
    memory_timestamp = agent_config.get("memory_timestamp")
    retrieve_num = agent_config.get("retrieve_num")
    chunk_size = dataset_config.get("chunk_size")
    generation_limit = dataset_config.get("generation_max_length")
    configured_input_limit = agent_config.get("input_length_limit")
    buffer_length = agent_config.get("buffer_length")
    if not isinstance(run_id, str) or registrar._RUN_ID.fullmatch(run_id) is None:
        raise ValueError("agent configuration has an invalid retrieval run ID")
    if not isinstance(memory_timestamp, str) or not memory_timestamp:
        raise ValueError("agent configuration requires a fixed memory_timestamp")
    settings = (
        retrieve_num,
        chunk_size,
        generation_limit,
        configured_input_limit,
        buffer_length,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in settings
    ):
        raise ValueError("BM25 integer configuration must be positive")
    effective_input_limit = configured_input_limit - buffer_length - generation_limit
    expected_contexts, expected_query_count = registrar._registered_task(
        chunks,
        query_groups,
        memorize_template=memorize_template,
        memory_timestamp=memory_timestamp,
        chunk_size=chunk_size,
        input_length_limit=effective_input_limit,
        max_queries=dataset_config.get("max_test_queries"),
    )
    expected_task = {
        "dataset": dataset_config.get("dataset"),
        "sub_dataset": dataset_config.get("sub_dataset"),
        "context_count": len(expected_contexts),
        "query_count": expected_query_count,
        "query_limit": dataset_config.get("max_test_queries"),
        "contexts": expected_contexts,
    }
    if task != expected_task:
        raise ValueError("registered BM25 task differs from the official prepared inputs")

    context_for_query: dict[int, int] = {}
    registered_queries: dict[int, dict[str, Any]] = {}
    documents_by_context: dict[int, list[str]] = {}
    for context, source_chunks in zip(expected_contexts, chunks):
        context_id = context["context_id"]
        documents_by_context[context_id] = registrar._prepare_documents(
            source_chunks,
            memorize_template=memorize_template,
            memory_timestamp=memory_timestamp,
            chunk_size=chunk_size,
            input_length_limit=effective_input_limit,
        )
        for query in context["queries"]:
            query_id = query["query_id"]
            context_for_query[query_id] = context_id
            registered_queries[query_id] = query
    result = _load_object(result_path)
    rows, averages, time_cost = _validate_result(
        result,
        agent_config=agent_config,
        dataset_config=dataset_config,
        registered_queries=registered_queries,
    )

    retrieval_root = (
        upstream_root
        / "outputs"
        / "rag_retrieved"
        / "Simple_rag_bm25"
        / f"run_{run_id}"
        / f"k_{retrieve_num}"
        / str(dataset_config.get("sub_dataset"))
        / f"chunksize_{chunk_size}"
    )
    captures: dict[int, tuple[int, Path]] = {}
    for path in retrieval_root.glob("query_*_context_*.json"):
        match = _CAPTURE_NAME.fullmatch(path.name)
        if match is None:
            continue
        query_id, context_id = (int(value) for value in match.groups())
        if query_id in captures:
            raise ValueError(f"query {query_id} has multiple BM25 retrieval captures")
        captures[query_id] = (context_id, path)
    if set(captures) != set(range(expected_query_count)):
        raise ValueError("BM25 retrieval captures do not cover exactly the completed queries")

    retrieved_counts: list[int] = []
    retrieved_tokens: list[int] = []
    aggregate_lines: list[str] = []
    for query_id in range(expected_query_count):
        context_id, capture_path = captures[query_id]
        if context_id != context_for_query[query_id]:
            raise ValueError(f"query {query_id} retrieval used the wrong source context")
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        if not isinstance(capture, list) or not all(
            isinstance(document, str) for document in capture
        ):
            raise ValueError(f"query {query_id} BM25 capture is not a document list")
        documents = documents_by_context[context_id]
        retrieval_query = registrar._extract_retrieval_query(rows[query_id]["query"])
        indices = _rank_indices(documents, retrieval_query, retrieve_num)
        expected_capture = [f"{documents[index]}\n" for index in indices]
        if capture != expected_capture:
            raise ValueError(f"query {query_id} BM25 ranking differs from registered inputs")
        reader_context = "\n".join(
            f"Memory {index + 1}:\n{text}" for index, text in enumerate(capture)
        )
        retrieved_counts.append(len(capture))
        retrieved_tokens.append(count_tokens(reader_context))
        aggregate_lines.append(
            f"{capture_path.relative_to(retrieval_root)}\0{_digest(capture_path)}"
        )

    return {
        "schema_version": 1,
        "kind": "memoryagentbench-bm25-verification",
        "status": "verified_complete",
        "source": {
            "prme_revision": actual_revision,
            "upstream_revision": actual_upstream_revision,
            "dataset_revision": adapter.DATASET_REVISION,
            "registration_sha256": _digest(registration_path),
            "registrar_sha256": _digest(
                source_root / "register_memoryagentbench_bm25.py"
            ),
            "verifier_sha256": _digest(
                source_root / "verify_memoryagentbench_bm25.py"
            ),
            "agent_config_sha256": _digest(agent_config_path),
            "dataset_config_sha256": _digest(dataset_config_path),
            "result_sha256": _digest(result_path),
            "retrieval_captures_sha256": hashlib.sha256(
                "\n".join(aggregate_lines).encode("utf-8")
            ).hexdigest(),
            "dependencies": actual_dependencies,
        },
        "task": {
            "dataset": dataset_config.get("dataset"),
            "sub_dataset": dataset_config.get("sub_dataset"),
            "queries": expected_query_count,
            "contexts": len(expected_contexts),
            "source_chunks": sum(
                len(context["source_chunks"]) for context in expected_contexts
            ),
            "bm25_documents": sum(len(value) for value in documents_by_context.values()),
        },
        "retrieval": {
            "retrieve_num": retrieve_num,
            "documents_total": sum(retrieved_counts),
            "documents_mean": math.fsum(retrieved_counts) / expected_query_count,
            "context_tokens_total": sum(retrieved_tokens),
            "context_tokens_mean": math.fsum(retrieved_tokens) / expected_query_count,
            "context_tokens_max": max(retrieved_tokens),
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
