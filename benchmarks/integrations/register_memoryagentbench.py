"""Freeze exact PRME MemoryAgentBench inputs before a scored run."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterator

import yaml

from benchmarks.integrations import memoryagentbench as adapter


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


def _require_executing_source(
    module_file: str | Path | None, expected_path: Path, label: str
) -> None:
    """Require the imported module bytes to match the declared frozen source."""
    if module_file is None or _digest(Path(module_file).resolve()) != _digest(
        expected_path.resolve()
    ):
        raise ValueError(f"executing {label} differs from the declared PRME source")


def _tree_digest(root: Path) -> str:
    hasher = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"preprocessing resource is empty: {root}")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        hasher.update(len(relative).to_bytes(4, "big"))
        hasher.update(relative)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
    return hasher.hexdigest()


def _preprocessing_identity() -> dict[str, dict[str, str]]:
    """Bind packages and data that define official prepared input chunks."""
    import nltk
    import tiktoken

    punkt_root = Path(str(nltk.data.find("tokenizers/punkt_tab/english/")))
    if not punkt_root.is_dir():
        raise ValueError("NLTK punkt_tab English data is not a filesystem directory")
    encoding = tiktoken.encoding_for_model("gpt-4o-mini")
    mergeable_ranks = getattr(encoding, "_mergeable_ranks", None)
    special_tokens = getattr(encoding, "_special_tokens", None)
    if not isinstance(mergeable_ranks, dict) or not isinstance(special_tokens, dict):
        raise ValueError("tiktoken encoding does not expose auditable token tables")
    encoding_hasher = hashlib.sha256()
    for token, rank in sorted(mergeable_ranks.items(), key=lambda item: item[1]):
        if not isinstance(token, bytes) or not isinstance(rank, int):
            raise ValueError("tiktoken mergeable ranks are malformed")
        encoding_hasher.update(rank.to_bytes(4, "big"))
        encoding_hasher.update(len(token).to_bytes(4, "big"))
        encoding_hasher.update(token)
    for token, rank in sorted(special_tokens.items()):
        token_bytes = token.encode("utf-8")
        encoding_hasher.update(rank.to_bytes(4, "big"))
        encoding_hasher.update(len(token_bytes).to_bytes(4, "big"))
        encoding_hasher.update(token_bytes)
    return {
        "datasets": {"version": importlib.metadata.version("datasets")},
        "nltk": {
            "version": importlib.metadata.version("nltk"),
            "punkt_tab_english_sha256": _tree_digest(punkt_root),
        },
        "tiktoken": {
            "version": importlib.metadata.version("tiktoken"),
            "encoding": encoding.name,
            "encoding_sha256": encoding_hasher.hexdigest(),
        },
    }


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_yaml_object(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return value


@contextmanager
def _upstream_imports(root: Path) -> Iterator[None]:
    old_cwd = Path.cwd()
    old_path = list(sys.path)
    os.chdir(root)
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path


def _registered_contexts(
    chunks: list[list[str]],
    query_groups: list[list[tuple[object, ...]]],
    *,
    max_chunk_chars: int,
    sub_dataset: str,
    max_queries: int | None = None,
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
        if not source_chunks or not all(
            isinstance(chunk, str) and chunk for chunk in source_chunks
        ):
            raise ValueError(f"context {context_id} has invalid source chunks")
        if not queries:
            raise ValueError(f"context {context_id} has no queries")
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
            retrieval_query = adapter._retrieval_query(query)
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
        _, _, piece_counts = adapter._split_source_chunks(
            source_chunks, max_chunk_chars, sub_dataset=sub_dataset
        )
        contexts.append(
            {
                "context_id": context_id,
                "source_chunks": [
                    {
                        "index": index,
                        "sha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                        "piece_count": piece_counts[index],
                    }
                    for index, chunk in enumerate(source_chunks)
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
) -> dict[str, Any]:
    """Load official prepared inputs and return their outcome-free identity."""
    expected_prme_revision = expected_prme_revision.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", expected_prme_revision) is None:
        raise ValueError("expected PRME revision must be a full 40-character commit")
    if _git(prme_root, "rev-parse", "HEAD").lower() != expected_prme_revision:
        raise ValueError("PRME source revision does not match the declared revision")
    source_names = (
        "memoryagentbench.py",
        "install_memoryagentbench.py",
        "register_memoryagentbench.py",
        "verify_memoryagentbench.py",
    )
    source_root = prme_root / "benchmarks" / "integrations"
    _require_executing_source(
        adapter.__file__, source_root / "memoryagentbench.py", "adapter"
    )
    _require_executing_source(
        __file__, source_root / "register_memoryagentbench.py", "registrar"
    )
    dirty = _git(
        prme_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *(str(Path("benchmarks/integrations") / name) for name in source_names),
    )
    if dirty:
        raise ValueError("MemoryAgentBench benchmark sources are not committed")
    upstream_revision = _git(upstream_root, "rev-parse", "HEAD").lower()
    if upstream_revision != adapter.UPSTREAM_REVISION:
        raise ValueError("MemoryAgentBench source revision does not match the adapter")
    installed_adapter = upstream_root / "methods" / "prme.py"
    if _digest(installed_adapter) != _digest(source_root / "memoryagentbench.py"):
        raise ValueError("installed MemoryAgentBench adapter differs from PRME source")

    agent_config = _load_yaml_object(agent_config_path)
    dataset_config = _load_yaml_object(dataset_config_path)
    if agent_config.get("agent_name") != "Agentic_memory_prme_rag":
        raise ValueError("agent configuration does not identify PRME")
    max_chunk_chars = agent_config.get("prme_max_chunk_chars")
    if (
        isinstance(max_chunk_chars, bool)
        or not isinstance(max_chunk_chars, int)
        or max_chunk_chars < 512
    ):
        raise ValueError("agent configuration has an invalid PRME chunk limit")
    max_queries = dataset_config.get("max_test_queries")
    sub_dataset = dataset_config.get("sub_dataset")
    if not isinstance(sub_dataset, str) or not sub_dataset.strip():
        raise ValueError("dataset configuration has an invalid sub_dataset")
    adapter.validate_reader_output_contract(
        sub_dataset=sub_dataset,
        contract=agent_config.get("reader_output_contract", "upstream"),
    )
    episode_context_top_k = agent_config.get("prme_episode_context_top_k", 0)
    episode_context_local_k = agent_config.get("prme_episode_context_local_k", 8)
    episode_context_score_decay = agent_config.get(
        "prme_episode_context_score_decay", 0.95
    )
    if (
        isinstance(episode_context_top_k, bool)
        or not isinstance(episode_context_top_k, int)
        or episode_context_top_k < 0
        or isinstance(episode_context_local_k, bool)
        or not isinstance(episode_context_local_k, int)
        or episode_context_local_k <= 0
        or isinstance(episode_context_score_decay, bool)
        or not isinstance(episode_context_score_decay, (int, float))
        or not 0 < float(episode_context_score_decay) <= 1
    ):
        raise ValueError("agent configuration has invalid PRME episode settings")
    if max_queries is not None and (
        isinstance(max_queries, bool)
        or not isinstance(max_queries, int)
        or max_queries <= 0
    ):
        raise ValueError("dataset configuration has an invalid max_test_queries")
    if chunks is None or query_groups is None:
        with _upstream_imports(upstream_root):
            from conversation_creator import ConversationCreator

            conversation = ConversationCreator(agent_config, dataset_config)
            chunks = conversation.get_chunks()
            query_groups = conversation.get_query_and_answers()
    contexts, query_count = _registered_contexts(
        chunks,
        query_groups,
        max_chunk_chars=max_chunk_chars,
        sub_dataset=sub_dataset,
        max_queries=max_queries,
    )

    upstream_sources = {
        name: _digest(upstream_root / name)
        for name in (
            "main.py",
            "agent.py",
            "conversation_creator.py",
            "initialization.py",
            "utils/eval_data_utils.py",
            "utils/eval_other_utils.py",
        )
    }
    return {
        "schema_version": 1,
        "kind": "memoryagentbench-prme-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "prme_revision": expected_prme_revision,
            "upstream_revision": upstream_revision,
            "dataset_revision": adapter.DATASET_REVISION,
            "preprocessing": _preprocessing_identity(),
            "prme_files_sha256": {
                name: _digest(source_root / name) for name in source_names
            },
            "upstream_files_sha256": upstream_sources,
            "installed_adapter_sha256": _digest(installed_adapter),
        },
        "configuration": {
            "agent": agent_config,
            "agent_sha256": _digest(agent_config_path),
            "dataset": dataset_config,
            "dataset_sha256": _digest(dataset_config_path),
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
