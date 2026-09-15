"""Freeze exact MemoryAgentBench BM25 inputs before a scored run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
from typing import Any

from benchmarks.integrations import memoryagentbench as adapter
from benchmarks.integrations import register_memoryagentbench as common_registrar
from benchmarks.integrations.register_memoryagentbench import (
    _canonical,
    _digest,
    _git,
    _load_yaml_object,
    _preprocessing_identity,
    _upstream_imports,
)


SOURCE_NAMES = (
    "memoryagentbench.py",
    "install_memoryagentbench.py",
    "register_memoryagentbench.py",
    "verify_memoryagentbench.py",
    "register_memoryagentbench_bm25.py",
    "verify_memoryagentbench_bm25.py",
)
UPSTREAM_SOURCE_NAMES = (
    "main.py",
    "agent.py",
    "conversation_creator.py",
    "initialization.py",
    "utils/eval_data_utils.py",
    "utils/eval_other_utils.py",
    "utils/templates.py",
)
_RUN_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


def _dependency_identity() -> dict[str, dict[str, str]]:
    """Bind the packages and adapter source that define BM25 retrieval."""
    import langchain_community.retrievers.bm25 as langchain_bm25
    import rank_bm25

    langchain_source = Path(langchain_bm25.__file__).resolve()
    rank_source = Path(rank_bm25.__file__).resolve()
    identity = _preprocessing_identity()
    identity.update(
        {
            "langchain-community": {
                "version": importlib.metadata.version("langchain-community"),
                "bm25_source_sha256": _digest(langchain_source),
            },
            "numpy": {
                "version": importlib.metadata.version("numpy"),
            },
            "rank-bm25": {
                "version": importlib.metadata.version("rank-bm25"),
                "source_sha256": _digest(rank_source),
            },
        }
    )
    return identity


def _extract_retrieval_query(message: str) -> str:
    upstream_query = message
    for pattern in (
        r"Now Answer the Question:\s*(.*)",
        r"Here is the conversation:\s*(.*)",
    ):
        match = re.search(pattern, message, re.DOTALL)
        if match:
            upstream_query = "".join(match.groups())
            break
    return adapter._retrieval_query(message, upstream_query=upstream_query)


def _prepare_documents(
    source_chunks: list[str],
    *,
    memorize_template: str,
    memory_timestamp: str,
    chunk_size: int,
    input_length_limit: int,
) -> list[str]:
    """Reproduce the pinned harness's RAG ingestion and coarse eviction."""
    if not source_chunks or not all(
        isinstance(chunk, str) and chunk for chunk in source_chunks
    ):
        raise ValueError("BM25 source chunks must be non-empty strings")
    if "{time_stamp}" in memorize_template and not memory_timestamp:
        raise ValueError("BM25 memory_timestamp is required by the memorize template")
    documents: list[str] = []
    context_length = 0
    for source_chunk in source_chunks:
        values = {"context": source_chunk}
        if "{time_stamp}" in memorize_template:
            values["time_stamp"] = memory_timestamp
        documents.append(memorize_template.format(**values))
        context_length += chunk_size
        if context_length > input_length_limit:
            documents = documents[1:]
            context_length -= chunk_size
    if not documents:
        raise ValueError("BM25 input limit evicted every source document")
    return documents


def _registered_task(
    chunks: list[list[str]],
    query_groups: list[list[tuple[object, ...]]],
    *,
    memorize_template: str,
    memory_timestamp: str,
    chunk_size: int,
    input_length_limit: int,
    max_queries: int | None,
) -> tuple[list[dict[str, Any]], int]:
    if len(chunks) != len(query_groups) or not chunks:
        raise ValueError("upstream contexts and query groups are empty or misaligned")
    if max_queries is not None and (
        isinstance(max_queries, bool)
        or not isinstance(max_queries, int)
        or max_queries <= 0
    ):
        raise ValueError("max_queries must be a positive integer when provided")
    contexts: list[dict[str, Any]] = []
    query_id = 0
    for context_id, (source_chunks, queries) in enumerate(zip(chunks, query_groups)):
        if max_queries is not None and query_id >= max_queries:
            break
        if not queries:
            raise ValueError(f"context {context_id} has no queries")
        documents = _prepare_documents(
            source_chunks,
            memorize_template=memorize_template,
            memory_timestamp=memory_timestamp,
            chunk_size=chunk_size,
            input_length_limit=input_length_limit,
        )
        registered_queries: list[dict[str, Any]] = []
        for query_data in queries:
            if max_queries is not None and query_id >= max_queries:
                break
            if not isinstance(query_data, (list, tuple)) or len(query_data) not in (
                2,
                3,
            ):
                raise ValueError(f"context {context_id} has an invalid query tuple")
            query, answer = query_data[:2]
            qa_pair_id = query_data[2] if len(query_data) == 3 else None
            if not isinstance(query, str) or not query:
                raise ValueError(f"query {query_id} is empty or non-text")
            retrieval_query = _extract_retrieval_query(query)
            registered_queries.append(
                {
                    "query_id": query_id,
                    "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                    "retrieval_query_sha256": hashlib.sha256(
                        retrieval_query.encode("utf-8")
                    ).hexdigest(),
                    "answer_sha256": hashlib.sha256(_canonical(answer)).hexdigest(),
                    "qa_pair_id_sha256": hashlib.sha256(
                        _canonical(qa_pair_id)
                    ).hexdigest(),
                }
            )
            query_id += 1
        contexts.append(
            {
                "context_id": context_id,
                "source_chunks": [
                    {
                        "index": index,
                        "sha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    }
                    for index, chunk in enumerate(source_chunks)
                ],
                "bm25_documents": [
                    {
                        "index": index,
                        "sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    }
                    for index, document in enumerate(documents)
                ],
                "queries": registered_queries,
            }
        )
    if query_id == 0:
        raise ValueError("upstream task contains no queries")
    return contexts, query_id


def register(
    *,
    upstream_root: Path,
    prme_root: Path,
    agent_config_path: Path,
    dataset_config_path: Path,
    expected_prme_revision: str,
    chunks: list[list[str]] | None = None,
    query_groups: list[list[tuple[object, ...]]] | None = None,
    memorize_template: str | None = None,
) -> dict[str, Any]:
    """Load official prepared inputs and return their outcome-free identity."""
    expected_prme_revision = expected_prme_revision.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", expected_prme_revision) is None:
        raise ValueError("expected PRME revision must be a full 40-character commit")
    if _git(prme_root, "rev-parse", "HEAD").lower() != expected_prme_revision:
        raise ValueError("PRME source revision does not match the declared revision")
    source_root = prme_root / "benchmarks" / "integrations"
    common_registrar._require_executing_source(
        adapter.__file__, source_root / "memoryagentbench.py", "adapter"
    )
    common_registrar._require_executing_source(
        common_registrar.__file__,
        source_root / "register_memoryagentbench.py",
        "PRME registrar",
    )
    common_registrar._require_executing_source(
        __file__,
        source_root / "register_memoryagentbench_bm25.py",
        "BM25 registrar",
    )
    dirty = _git(
        prme_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *(str(Path("benchmarks/integrations") / name) for name in SOURCE_NAMES),
    )
    if dirty:
        raise ValueError("MemoryAgentBench BM25 benchmark sources are not committed")
    upstream_revision = _git(upstream_root, "rev-parse", "HEAD").lower()
    if upstream_revision != adapter.UPSTREAM_REVISION:
        raise ValueError("MemoryAgentBench source revision does not match the adapter")

    agent_config = _load_yaml_object(agent_config_path)
    dataset_config = _load_yaml_object(dataset_config_path)
    if agent_config.get("agent_name") != "Simple_rag_bm25":
        raise ValueError("agent configuration does not identify the BM25 baseline")
    run_id = agent_config.get("retrieval_run_id")
    memory_timestamp = agent_config.get("memory_timestamp")
    retrieve_num = agent_config.get("retrieve_num")
    chunk_size = dataset_config.get("chunk_size")
    generation_limit = dataset_config.get("generation_max_length")
    configured_input_limit = agent_config.get("input_length_limit")
    buffer_length = agent_config.get("buffer_length")
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("agent configuration has an invalid retrieval run ID")
    if not isinstance(memory_timestamp, str) or not memory_timestamp:
        raise ValueError("agent configuration requires a fixed memory_timestamp")
    integer_settings = {
        "retrieve_num": retrieve_num,
        "chunk_size": chunk_size,
        "generation_max_length": generation_limit,
        "input_length_limit": configured_input_limit,
        "buffer_length": buffer_length,
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in integer_settings.values()
    ):
        raise ValueError("BM25 integer configuration must be positive")
    effective_input_limit = configured_input_limit - buffer_length - generation_limit
    if effective_input_limit <= 0:
        raise ValueError("BM25 effective input limit must be positive")
    sub_dataset = dataset_config.get("sub_dataset")
    if not isinstance(sub_dataset, str) or not sub_dataset.strip():
        raise ValueError("dataset configuration has an invalid sub_dataset")
    adapter.validate_reader_output_contract(
        sub_dataset=sub_dataset,
        contract=agent_config.get("reader_output_contract", "upstream"),
    )
    max_queries = dataset_config.get("max_test_queries")

    if chunks is None or query_groups is None or memorize_template is None:
        with _upstream_imports(upstream_root):
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
    contexts, query_count = _registered_task(
        chunks,
        query_groups,
        memorize_template=memorize_template,
        memory_timestamp=memory_timestamp,
        chunk_size=chunk_size,
        input_length_limit=effective_input_limit,
        max_queries=max_queries,
    )
    return {
        "schema_version": 1,
        "kind": "memoryagentbench-bm25-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "prme_revision": expected_prme_revision,
            "upstream_revision": upstream_revision,
            "dataset_revision": adapter.DATASET_REVISION,
            "prme_files_sha256": {
                name: _digest(source_root / name) for name in SOURCE_NAMES
            },
            "upstream_files_sha256": {
                name: _digest(upstream_root / name) for name in UPSTREAM_SOURCE_NAMES
            },
            "dependencies": _dependency_identity(),
        },
        "configuration": {
            "agent": agent_config,
            "agent_sha256": _digest(agent_config_path),
            "dataset": dataset_config,
            "dataset_sha256": _digest(dataset_config_path),
            "memorize_template_sha256": hashlib.sha256(
                memorize_template.encode("utf-8")
            ).hexdigest(),
        },
        "task": {
            "dataset": dataset_config.get("dataset"),
            "sub_dataset": dataset_config.get("sub_dataset"),
            "context_count": len(contexts),
            "query_count": query_count,
            "query_limit": max_queries,
            "contexts": contexts,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", required=True, type=Path)
    parser.add_argument(
        "--prme-root", default=Path(__file__).resolve().parents[2], type=Path
    )
    parser.add_argument("--agent-config", required=True, type=Path)
    parser.add_argument("--dataset-config", required=True, type=Path)
    parser.add_argument("--expected-prme-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("registration output already exists; choose a new path")
    registration = register(
        upstream_root=args.upstream_root.resolve(),
        prme_root=args.prme_root.resolve(),
        agent_config_path=args.agent_config.resolve(),
        dataset_config_path=args.dataset_config.resolve(),
        expected_prme_revision=args.expected_prme_revision,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(registration) + b"\n")
    print(json.dumps(registration, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
