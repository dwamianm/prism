"""PRME adapter for the official MemoryAgentBench incremental harness.

The installer copies this module to ``methods/prme.py`` in the pinned upstream
checkout.  It deliberately receives only the context chunks and formatted
questions exposed to every memory method; answer fields and evaluation labels
never enter the memory pack.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
import weakref

from prme import (
    EpistemicType,
    MemoryClient,
    NodeType,
    PRMEConfig,
    Scope,
    SourceType,
)
from prme.config import EmbeddingConfig
from prme.retrieval.config import PackingConfig


UPSTREAM_REVISION = "fe1735de8cf8b9908e1e3d3b5612afc815698062"
DATASET_REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"
ADAPTER_SCHEMA_VERSION = 8
_MANIFEST_NAME = "memoryagentbench_prme_manifest.json"
_DEFAULT_CHUNK_CHARS = 6000
_DEFAULT_TOKEN_BUDGET = 4096
_DEFAULT_RESULT_LIMIT = 100
_SEGMENTATION_POLICY = "task-aware-semantic-boundaries-v1"
_RETRIEVAL_QUERY_POLICY = "upstream-plus-terminal-label-question-v1"
_EPISODE_PARTITION_POLICY = "source-chunk-session-v1"
_READER_OUTPUT_CONTRACTS = frozenset(
    {"upstream", "numeric-label-v1", "answer-only-v1", "choice-only-v1"}
)
_NUMERIC_LABEL_INSTRUCTION = (
    "For scoring, return the numeric label alone. Your entire response must contain "
    "only ASCII digits. Do not include 'label:', punctuation, reasoning, or explanation."
)
_ANSWER_ONLY_INSTRUCTION = (
    "For scoring, follow the task's requested output format and return only the "
    "answer. Do not include reasoning, explanations, prefaces, an 'Answer:' label, "
    "or Markdown."
)
_CHOICE_ONLY_INSTRUCTION = (
    "For scoring, ignore any request to return JSON, reasoning, or an 'Output:' "
    "wrapper. Select one listed choice and return exactly its choice label and "
    "text in the form 'A. choice text'. Your entire response must be one line and "
    "contain nothing else."
)
_BLANK_LINE = re.compile(r"\r?\n(?:[ \t]*\r?\n)+")
_NUMBERED_ITEM = re.compile(r"(?<!\S)\d+\.[ \t]+")
_TERMINAL_LABEL_QUESTION = re.compile(
    r"(?:\A|\r?\n[ \t]*\r?\n)Question:[ \t]*(?P<query>.*?)"
    r"\r?\n[ \t]*\r?\n[ \t]*label:[ \t]*\Z",
    re.DOTALL,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _config_identity(agent: Any) -> dict[str, object]:
    return {
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "upstream_revision": UPSTREAM_REVISION,
        "dataset_revision": DATASET_REVISION,
        "sub_dataset": agent.sub_dataset,
        "user_id": agent.prme_user_id,
        "token_budget": agent.prme_token_budget,
        "result_limit": agent.prme_result_limit,
        "max_chunk_chars": agent.prme_max_chunk_chars,
        "embedding_provider": "fastembed",
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "embedding_dimension": 384,
        "segmentation_policy": _SEGMENTATION_POLICY,
        "retrieval_query_policy": _RETRIEVAL_QUERY_POLICY,
        "episode_partition_policy": (
            _EPISODE_PARTITION_POLICY
            if agent.prme_episode_context_top_k > 0
            else "context-session-v1"
        ),
        "episode_context_top_k": agent.prme_episode_context_top_k,
        "episode_context_local_k": agent.prme_episode_context_local_k,
        "episode_context_score_decay": agent.prme_episode_context_score_decay,
        "packing_policy": "balanced",
        "context_format": agent.prme_context_format,
        "reader_reasoning_effort": agent.reader_reasoning_effort,
        "reader_seed": agent.reader_seed,
        "reader_output_contract": agent.reader_output_contract,
        "run_id": agent.prme_run_id,
    }


def _config(root: Path, agent: Any) -> PRMEConfig:
    lexical = root / "lexical_index"
    lexical.mkdir(parents=True, exist_ok=True)
    return PRMEConfig(
        database_url=None,
        db_path=str(root / "memory.duckdb"),
        vector_path=str(root / "vectors.usearch"),
        lexical_path=str(lexical),
        embedding=EmbeddingConfig(
            provider="fastembed",
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
        ),
        packing=PackingConfig(
            token_budget=agent.prme_token_budget,
            multipath_ordering="balanced",
            context_format=agent.prme_context_format,
            episode_context_top_k=agent.prme_episode_context_top_k,
            episode_context_local_k=agent.prme_episode_context_local_k,
            episode_context_score_decay=agent.prme_episode_context_score_decay,
        ),
        enable_qa_pairing=False,
        enable_query_reformulation=False,
        enable_store_supersedence=False,
        enable_surprise_gating=False,
        enable_reranker=False,
        organizer={"opportunistic_enabled": False},
        _env_file=None,
    )


def _write_manifest(agent: Any) -> None:
    path = agent.prme_pack_path / _MANIFEST_NAME
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical(agent.prme_manifest) + b"\n")
    temporary.replace(path)


def _open_client(agent: Any) -> MemoryClient:
    if agent.prme_client is None:
        agent.prme_pack_path.mkdir(parents=True, exist_ok=True)
        agent.prme_client = MemoryClient(config=_config(agent.prme_pack_path, agent))
        agent.prme_finalizer = weakref.finalize(agent, agent.prme_client.close)
    return agent.prme_client


def _close_client(agent: Any) -> None:
    finalizer = getattr(agent, "prme_finalizer", None)
    if finalizer is not None and finalizer.alive:
        finalizer()
    elif getattr(agent, "prme_client", None) is not None:
        agent.prme_client.close()
    agent.prme_finalizer = None
    agent.prme_client = None


def _split_units(
    text: str, limit: int, *, numbered_items: bool = False
) -> list[str]:
    """Preserve source bytes and semantic boundaries under a hard limit."""
    if limit < 512:
        raise ValueError("prme_max_chunk_chars must be at least 512")
    boundaries = {match.end() for match in _BLANK_LINE.finditer(text)}
    numbered_starts = (
        [match.start() for match in _NUMBERED_ITEM.finditer(text)]
        if numbered_items
        else []
    )
    for position in numbered_starts:
        if position <= 0:
            continue
        prefix = text[:position].strip()
        if position == numbered_starts[0] and len(prefix) <= 200 and prefix.endswith(":"):
            continue
        boundaries.add(position)

    units: list[str] = []
    start = 0
    for boundary in sorted(boundaries):
        if boundary > start:
            units.append(text[start:boundary])
        start = boundary
    if start < len(text):
        units.append(text[start:])
    if not units:
        units.append(text)

    chunks: list[str] = []
    for unit in units:
        while len(unit) > limit:
            chunks.append(unit[:limit])
            unit = unit[limit:]
        if unit:
            chunks.append(unit)
    return chunks or [text]


def _split_source_chunks(
    source_chunks: list[str], limit: int, *, sub_dataset: str
) -> tuple[list[str], list[int], list[int]]:
    """Segment one ordered source stream and attribute records by ending chunk."""
    if not source_chunks or any(not chunk for chunk in source_chunks):
        raise ValueError("source chunks must be non-empty")
    source_ends: list[int] = []
    total = 0
    for source_chunk in source_chunks:
        total += len(source_chunk)
        source_ends.append(total)

    pieces = _split_units(
        "".join(source_chunks),
        limit,
        numbered_items=sub_dataset.strip().startswith("factconsolidation_"),
    )
    source_indices: list[int] = []
    piece_counts = [0] * len(source_chunks)
    piece_end = 0
    source_index = 0
    for piece in pieces:
        piece_end += len(piece)
        while (
            source_index < len(source_ends) - 1
            and piece_end > source_ends[source_index]
        ):
            source_index += 1
        source_indices.append(source_index)
        piece_counts[source_index] += 1
    return pieces, source_indices, piece_counts


def _retrieval_query(message: str, upstream_query: str | None = None) -> str:
    """Apply the pinned upstream extraction and isolate terminal label questions."""
    match = _TERMINAL_LABEL_QUESTION.search(message)
    if match is not None and (question := match.group("query").strip()):
        return question
    if upstream_query is not None:
        return upstream_query
    for pattern in (
        r"Now Answer the Question:\s*(.*)",
        r"Here is the conversation:\s*(.*)",
    ):
        match = re.search(pattern, message, re.DOTALL)
        if match:
            return "".join(match.groups())
    return message


def validate_reader_output_contract(*, sub_dataset: str, contract: object) -> str:
    """Validate a reader contract and its benchmark-task boundary."""
    if not isinstance(contract, str) or contract not in _READER_OUTPUT_CONTRACTS:
        raise ValueError(f"unsupported reader_output_contract: {contract}")
    if contract == "numeric-label-v1" and not sub_dataset.strip().startswith("icl_"):
        raise ValueError("numeric-label-v1 is only valid for ICL tasks")
    if contract == "choice-only-v1" and not sub_dataset.strip().startswith(
        "detective_"
    ):
        raise ValueError("choice-only-v1 is only valid for DetectiveQA tasks")
    return contract


def reader_message(message: str, *, sub_dataset: str, contract: str) -> str:
    """Apply an explicit reader-only output contract without changing retrieval."""
    contract = validate_reader_output_contract(
        sub_dataset=sub_dataset, contract=contract
    )
    if contract == "upstream":
        return message
    if contract == "answer-only-v1":
        return f"{message}\n\n{_ANSWER_ONLY_INSTRUCTION}"
    if contract == "choice-only-v1":
        return f"{message}\n\n{_CHOICE_ONLY_INSTRUCTION}"
    return f"{message}\n\n{_NUMERIC_LABEL_INSTRUCTION}"


def initialize_prme_agent(
    agent: Any, agent_config: dict[str, object] | None = None
) -> None:
    """Initialize a lazy, persistent PRME pack for one upstream context."""
    config = agent_config or {}
    agent.retrieve_num = int(config.get("retrieve_num", _DEFAULT_RESULT_LIMIT))
    agent.prme_result_limit = int(config.get("prme_result_limit", agent.retrieve_num))
    agent.prme_token_budget = int(
        config.get("prme_token_budget", _DEFAULT_TOKEN_BUDGET)
    )
    agent.prme_max_chunk_chars = int(
        config.get("prme_max_chunk_chars", _DEFAULT_CHUNK_CHARS)
    )
    agent.prme_user_id = str(config.get("prme_user_id", "memoryagentbench")).strip()
    agent.prme_context_format = str(
        config.get("prme_context_format", "auditable")
    ).strip()
    agent.prme_episode_context_top_k = config.get("prme_episode_context_top_k", 0)
    agent.prme_episode_context_local_k = config.get("prme_episode_context_local_k", 8)
    agent.prme_episode_context_score_decay = config.get(
        "prme_episode_context_score_decay", 0.95
    )
    agent.reader_reasoning_effort = config.get("reader_reasoning_effort")
    agent.reader_seed = config.get("reader_seed")
    agent.reader_output_contract = str(
        config.get("reader_output_contract", "upstream")
    ).strip()
    agent.prme_run_id = str(config.get("prme_run_id", "default")).strip()
    if not agent.prme_user_id:
        raise ValueError("prme_user_id must be non-empty")
    if agent.prme_result_limit <= 0:
        raise ValueError("prme_result_limit must be positive")
    if agent.prme_token_budget <= 0:
        raise ValueError("prme_token_budget must be positive")
    if agent.prme_max_chunk_chars < 512:
        raise ValueError("prme_max_chunk_chars must be at least 512")
    if agent.prme_context_format not in {"auditable", "compact"}:
        raise ValueError("prme_context_format must be 'auditable' or 'compact'")
    if (
        isinstance(agent.prme_episode_context_top_k, bool)
        or not isinstance(agent.prme_episode_context_top_k, int)
        or agent.prme_episode_context_top_k < 0
    ):
        raise ValueError("prme_episode_context_top_k must be a non-negative integer")
    if (
        isinstance(agent.prme_episode_context_local_k, bool)
        or not isinstance(agent.prme_episode_context_local_k, int)
        or agent.prme_episode_context_local_k <= 0
    ):
        raise ValueError("prme_episode_context_local_k must be a positive integer")
    if (
        isinstance(agent.prme_episode_context_score_decay, bool)
        or not isinstance(agent.prme_episode_context_score_decay, (int, float))
        or not 0 < float(agent.prme_episode_context_score_decay) <= 1
    ):
        raise ValueError(
            "prme_episode_context_score_decay must be greater than zero and at most one"
        )
    agent.prme_episode_context_score_decay = float(
        agent.prme_episode_context_score_decay
    )
    if agent.reader_reasoning_effort not in {None, "none", "low", "medium", "high"}:
        raise ValueError(
            "reader_reasoning_effort must be none, low, medium, high, or omitted"
        )
    if (
        isinstance(agent.reader_seed, bool)
        or agent.reader_seed is not None
        and not isinstance(agent.reader_seed, int)
    ):
        raise ValueError("reader_seed must be an integer or omitted")
    validate_reader_output_contract(
        sub_dataset=agent.sub_dataset, contract=agent.reader_output_contract
    )
    if (
        not agent.prme_run_id
        or len(agent.prme_run_id) > 64
        or any(
            not char.isalnum() and char not in "._-"
            for char in agent.prme_run_id
        )
    ):
        raise ValueError(
            "prme_run_id must contain 1-64 letters, digits, dots, underscores, or hyphens"
        )

    agent.prme_pack_path = Path(agent.agent_save_to_folder) / "prme_pack"
    agent.prme_client = None
    agent.prme_finalizer = None
    agent.prme_manifest = None
    agent.prme_reference_time = None
    agent.prme_ingest_started = None
    agent.prme_ingest_seconds = 0.0
    agent.prme_report_ingest = False
    agent.prme_source_chunks = []
    agent.prme_context_id = None


def _begin_ingestion(agent: Any) -> None:
    if agent.prme_manifest is not None:
        return
    if agent.prme_pack_path.exists():
        raise RuntimeError(
            "PRME benchmark pack exists before ingestion; load it through the "
            "upstream saved-agent path or remove the incomplete experiment"
        )
    agent.prme_pack_path.mkdir(parents=True)
    identity = _config_identity(agent)
    agent.prme_manifest = {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "config": identity,
        "config_sha256": hashlib.sha256(_canonical(identity)).hexdigest(),
        "status": "preparing",
        "source_chunks": [],
        "stored_nodes": 0,
        "query_reference_time": None,
    }
    _write_manifest(agent)
    agent.prme_ingest_started = time.monotonic()
    _open_client(agent)


def _store_source_chunk(agent: Any, message: str, context_id: int | None) -> None:
    if not isinstance(message, str) or not message:
        raise ValueError("MemoryAgentBench context chunks must be non-empty strings")
    _begin_ingestion(agent)
    if agent.prme_manifest["status"] != "preparing":
        raise RuntimeError("cannot append to a completed PRME benchmark pack")
    if agent.prme_source_chunks and context_id != agent.prme_context_id:
        raise RuntimeError("one PRME benchmark pack cannot mix source contexts")
    agent.prme_context_id = context_id

    source_index = len(agent.prme_manifest["source_chunks"])
    agent.prme_source_chunks.append(message)
    agent.prme_manifest["source_chunks"].append(
        {
            "index": source_index,
            "sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
            "piece_count": 0,
        }
    )
    _write_manifest(agent)


def save_prme_agent(agent: Any) -> None:
    """Fence a complete ingestion before the upstream harness starts querying."""
    if agent.prme_manifest is None or not agent.prme_manifest["source_chunks"]:
        raise RuntimeError("cannot save an empty PRME MemoryAgentBench pack")
    if agent.prme_manifest["status"] != "preparing":
        raise RuntimeError("PRME MemoryAgentBench pack was already completed")
    pieces, source_indices, piece_counts = _split_source_chunks(
        agent.prme_source_chunks,
        agent.prme_max_chunk_chars,
        sub_dataset=agent.sub_dataset,
    )
    for source, piece_count in zip(
        agent.prme_manifest["source_chunks"], piece_counts
    ):
        source["piece_count"] = piece_count
    agent.prme_manifest["stored_nodes"] = len(pieces)
    last_event_id: str | None = None
    client = _open_client(agent)
    try:
        for piece_index, (piece, source_index) in enumerate(
            zip(pieces, source_indices)
        ):
            session_id = f"{agent.sub_dataset}:context:{agent.prme_context_id}"
            if agent.prme_episode_context_top_k > 0:
                session_id += f":source:{source_index}"
            last_event_id = client.store(
                piece,
                user_id=agent.prme_user_id,
                session_id=session_id,
                role="tool",
                node_type=NodeType.NOTE,
                scope=Scope.PROJECT,
                epistemic_type=EpistemicType.OBSERVED,
                source_type=SourceType.TOOL_OUTPUT,
                metadata={
                    "benchmark": "memoryagentbench",
                    "dataset_revision": DATASET_REVISION,
                    "sub_dataset": agent.sub_dataset,
                    "context_id": agent.prme_context_id,
                    "source_chunk_index": source_index,
                    "piece_index": piece_index,
                    "piece_count": len(pieces),
                },
            )
    except BaseException:
        _write_manifest(agent)
        raise
    if last_event_id is None:
        raise RuntimeError("MemoryAgentBench source chunks produced no PRME event")
    event = client.get_event(last_event_id, user_id=agent.prme_user_id)
    if event is None or event.created_at.utcoffset() is None:
        raise RuntimeError("PRME benchmark query clock source is unavailable")
    agent.prme_reference_time = event.created_at.astimezone(timezone.utc)
    agent.prme_manifest["query_reference_time"] = agent.prme_reference_time.isoformat()
    agent.prme_ingest_seconds = time.monotonic() - agent.prme_ingest_started
    agent.prme_manifest["status"] = "complete"
    agent.prme_manifest["ingest_seconds"] = agent.prme_ingest_seconds
    _write_manifest(agent)
    agent.prme_source_chunks = []
    agent.prme_report_ingest = True
    print(
        f"[prme] indexed {agent.prme_manifest['stored_nodes']} records in "
        f"{agent.prme_ingest_seconds:.3f}s",
        flush=True,
    )


def load_prme_agent(agent: Any) -> None:
    """Open an exact completed pack on a resumed upstream run."""
    manifest_path = agent.prme_pack_path / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError("saved PRME MemoryAgentBench pack is missing its manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = _config_identity(agent)
    if (
        manifest.get("schema_version") != ADAPTER_SCHEMA_VERSION
        or manifest.get("config") != identity
        or manifest.get("config_sha256")
        != hashlib.sha256(_canonical(identity)).hexdigest()
        or manifest.get("status") != "complete"
    ):
        raise RuntimeError(
            "saved PRME MemoryAgentBench pack is incompatible or incomplete"
        )
    raw_time = manifest.get("query_reference_time")
    if not isinstance(raw_time, str):
        raise RuntimeError("saved PRME MemoryAgentBench pack has no query clock")
    parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise RuntimeError("saved PRME MemoryAgentBench query clock lacks an offset")
    agent.prme_manifest = manifest
    agent.prme_reference_time = parsed.astimezone(timezone.utc)
    agent.prme_ingest_seconds = float(manifest.get("ingest_seconds", 0.0))
    agent.prme_report_ingest = False
    _open_client(agent)


def _reader_client(agent: Any) -> Any:
    base_url = os.environ.get("PRME_MAB_OPENAI_BASE_URL")
    if not base_url:
        return agent._create_oai_client()
    from openai import OpenAI

    return OpenAI(
        base_url=base_url,
        api_key=os.environ.get("PRME_MAB_OPENAI_API_KEY", "ollama"),
    )


def _save_retrieval(
    agent: Any,
    *,
    query_id: int | None,
    context_id: int | None,
    query: str,
    retrieval_query: str,
    retrieval_context: str,
    request_id: str,
    context_token_count: int,
    included_count: int,
    receipt_persisted: bool,
) -> None:
    root = Path(agent.output_dir) / "prme_retrievals" / agent.sub_dataset
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"query_{query_id}_context_{context_id}.json"
    manifest_path = agent.prme_pack_path / _MANIFEST_NAME
    payload = {
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "upstream_revision": UPSTREAM_REVISION,
        "dataset_revision": DATASET_REVISION,
        "sub_dataset": agent.sub_dataset,
        "query_id": query_id,
        "context_id": context_id,
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "retrieval_query_sha256": hashlib.sha256(
            retrieval_query.encode("utf-8")
        ).hexdigest(),
        "request_id": request_id,
        "receipt_persisted": receipt_persisted,
        "token_budget": agent.prme_token_budget,
        "context_format": agent.prme_context_format,
        "episode_context_top_k": agent.prme_episode_context_top_k,
        "episode_context_local_k": agent.prme_episode_context_local_k,
        "episode_context_score_decay": agent.prme_episode_context_score_decay,
        "reader_reasoning_effort": agent.reader_reasoning_effort,
        "reader_seed": agent.reader_seed,
        "reader_output_contract": agent.reader_output_contract,
        "run_id": agent.prme_run_id,
        "context_token_count": context_token_count,
        "included_count": included_count,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "context_sha256": hashlib.sha256(retrieval_context.encode("utf-8")).hexdigest(),
        "context": retrieval_context,
    }
    path.write_bytes(_canonical(payload) + b"\n")


def handle_prme_agent(
    agent: Any,
    message: str,
    memorizing: bool,
    query_id: int | None,
    context_id: int | None,
) -> dict[str, object] | str:
    """Store an increment or retrieve and answer with the upstream reader layout."""
    if memorizing:
        _store_source_chunk(agent, message, context_id)
        return "Memorized"

    if agent.prme_manifest is None:
        load_prme_agent(agent)
    if agent.prme_manifest["status"] != "complete" or agent.prme_reference_time is None:
        raise RuntimeError("cannot query an incomplete PRME MemoryAgentBench pack")

    started = time.monotonic()
    retrieval_query = _retrieval_query(
        message, upstream_query=agent._extract_retrieval_query(message)
    )
    response = _open_client(agent).retrieve(
        retrieval_query,
        user_id=agent.prme_user_id,
        scope=Scope.PROJECT,
        reference_time=agent.prme_reference_time,
        token_budget=agent.prme_token_budget,
        limit=agent.prme_result_limit,
        include_cross_scope=False,
    )
    retrieval_context = response.bundle.render()
    retrieval_seconds = time.monotonic() - started

    from utils.eval_data_utils import format_chat
    from utils.templates import get_template

    system_message = get_template(agent.sub_dataset, "system", agent.agent_name)
    messages = format_chat(
        message=reader_message(
            retrieval_context + "\n" + message,
            sub_dataset=agent.sub_dataset,
            contract=agent.reader_output_contract,
        ),
        system_message=system_message,
    )
    completion_options = {
        "model": agent.model,
        "messages": messages,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
    }
    if agent.reader_reasoning_effort is not None:
        completion_options["reasoning_effort"] = agent.reader_reasoning_effort
    if agent.reader_seed is not None:
        completion_options["seed"] = agent.reader_seed
    completion = _reader_client(agent).chat.completions.create(**completion_options)
    query_seconds = time.monotonic() - started - retrieval_seconds
    memory_seconds = agent.prme_ingest_seconds if agent.prme_report_ingest else 0.0
    agent.prme_report_ingest = False
    _save_retrieval(
        agent,
        query_id=query_id,
        context_id=context_id,
        query=message,
        retrieval_query=retrieval_query,
        retrieval_context=retrieval_context,
        request_id=str(response.metadata.request_id),
        context_token_count=response.bundle.tokens_used,
        included_count=response.bundle.included_count,
        receipt_persisted=response.metadata.receipt_persisted,
    )
    return agent._create_standard_response(
        completion.choices[0].message.content,
        completion.usage.prompt_tokens,
        completion.usage.completion_tokens,
        memory_seconds,
        retrieval_seconds + query_seconds,
    )
