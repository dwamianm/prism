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
ADAPTER_SCHEMA_VERSION = 1
_MANIFEST_NAME = "memoryagentbench_prme_manifest.json"
_DEFAULT_CHUNK_CHARS = 6000
_DEFAULT_TOKEN_BUDGET = 4096
_DEFAULT_RESULT_LIMIT = 100


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
        "packing_policy": "balanced",
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


def _split_units(text: str, limit: int) -> list[str]:
    """Preserve all text while keeping each stored record packable at 4K."""
    if limit < 512:
        raise ValueError("prme_max_chunk_chars must be at least 512")
    chunks: list[str] = []
    current = ""
    for unit in text.splitlines(keepends=True) or [text]:
        while len(unit) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:limit])
            unit = unit[limit:]
        if current and len(current) + len(unit) > limit:
            chunks.append(current)
            current = ""
        current += unit
    if current:
        chunks.append(current)
    return chunks or [text]


def initialize_prme_agent(agent: Any, agent_config: dict[str, object] | None = None) -> None:
    """Initialize a lazy, persistent PRME pack for one upstream context."""
    config = agent_config or {}
    agent.retrieve_num = int(config.get("retrieve_num", _DEFAULT_RESULT_LIMIT))
    agent.prme_result_limit = int(
        config.get("prme_result_limit", agent.retrieve_num)
    )
    agent.prme_token_budget = int(
        config.get("prme_token_budget", _DEFAULT_TOKEN_BUDGET)
    )
    agent.prme_max_chunk_chars = int(
        config.get("prme_max_chunk_chars", _DEFAULT_CHUNK_CHARS)
    )
    agent.prme_user_id = str(config.get("prme_user_id", "memoryagentbench")).strip()
    if not agent.prme_user_id:
        raise ValueError("prme_user_id must be non-empty")
    if agent.prme_result_limit <= 0:
        raise ValueError("prme_result_limit must be positive")
    if agent.prme_token_budget <= 0:
        raise ValueError("prme_token_budget must be positive")
    if agent.prme_max_chunk_chars < 512:
        raise ValueError("prme_max_chunk_chars must be at least 512")

    agent.prme_pack_path = Path(agent.agent_save_to_folder) / "prme_pack"
    agent.prme_client = None
    agent.prme_finalizer = None
    agent.prme_manifest = None
    agent.prme_reference_time = None
    agent.prme_ingest_started = None
    agent.prme_ingest_seconds = 0.0
    agent.prme_report_ingest = False
    agent.prme_closed = False


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

    source_index = len(agent.prme_manifest["source_chunks"])
    source_digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
    pieces = _split_units(message, agent.prme_max_chunk_chars)
    last_event_id: str | None = None
    client = _open_client(agent)
    try:
        for piece_index, piece in enumerate(pieces):
            last_event_id = client.store(
                piece,
                user_id=agent.prme_user_id,
                session_id=f"{agent.sub_dataset}:context:{context_id}",
                role="tool",
                node_type=NodeType.NOTE,
                scope=Scope.PROJECT,
                epistemic_type=EpistemicType.OBSERVED,
                source_type=SourceType.TOOL_OUTPUT,
                metadata={
                    "benchmark": "memoryagentbench",
                    "dataset_revision": DATASET_REVISION,
                    "sub_dataset": agent.sub_dataset,
                    "context_id": context_id,
                    "source_chunk_index": source_index,
                    "piece_index": piece_index,
                    "piece_count": len(pieces),
                },
            )
    except BaseException:
        _write_manifest(agent)
        raise
    if last_event_id is None:
        raise RuntimeError("MemoryAgentBench source chunk produced no PRME event")
    event = client.get_event(last_event_id, user_id=agent.prme_user_id)
    if event is None or event.created_at.utcoffset() is None:
        raise RuntimeError("PRME benchmark query clock source is unavailable")
    reference_time = event.created_at.astimezone(timezone.utc)
    if agent.prme_reference_time is not None:
        reference_time = max(reference_time, agent.prme_reference_time)
    agent.prme_reference_time = reference_time
    agent.prme_manifest["source_chunks"].append(
        {
            "index": source_index,
            "sha256": source_digest,
            "piece_count": len(pieces),
        }
    )
    agent.prme_manifest["stored_nodes"] += len(pieces)
    agent.prme_manifest["query_reference_time"] = reference_time.isoformat()
    _write_manifest(agent)


def save_prme_agent(agent: Any) -> None:
    """Fence a complete ingestion before the upstream harness starts querying."""
    if agent.prme_manifest is None or not agent.prme_manifest["source_chunks"]:
        raise RuntimeError("cannot save an empty PRME MemoryAgentBench pack")
    if agent.prme_manifest["status"] != "preparing":
        raise RuntimeError("PRME MemoryAgentBench pack was already completed")
    agent.prme_ingest_seconds = time.monotonic() - agent.prme_ingest_started
    agent.prme_manifest["status"] = "complete"
    agent.prme_manifest["ingest_seconds"] = agent.prme_ingest_seconds
    _write_manifest(agent)
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
        raise RuntimeError("saved PRME MemoryAgentBench pack is incompatible or incomplete")
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
    retrieval_context: str,
    request_id: str,
) -> None:
    root = Path(agent.output_dir) / "prme_retrievals" / agent.sub_dataset
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"query_{query_id}_context_{context_id}.json"
    payload = {
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "upstream_revision": UPSTREAM_REVISION,
        "dataset_revision": DATASET_REVISION,
        "request_id": request_id,
        "context_sha256": hashlib.sha256(
            retrieval_context.encode("utf-8")
        ).hexdigest(),
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
    retrieval_query = agent._extract_retrieval_query(message)
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
        message=retrieval_context + "\n" + message,
        system_message=system_message,
    )
    completion = _reader_client(agent).chat.completions.create(
        model=agent.model,
        messages=messages,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
    )
    query_seconds = time.monotonic() - started - retrieval_seconds
    memory_seconds = agent.prme_ingest_seconds if agent.prme_report_ingest else 0.0
    agent.prme_report_ingest = False
    _save_retrieval(
        agent,
        query_id=query_id,
        context_id=context_id,
        retrieval_context=retrieval_context,
        request_id=str(response.metadata.request_id),
    )
    return agent._create_standard_response(
        completion.choices[0].message.content,
        completion.usage.prompt_tokens,
        completion.usage.completion_tokens,
        memory_seconds,
        retrieval_seconds + query_seconds,
    )
