"""Expose PRME through MELT's B2 JSONL system-under-test contract.

This bridge intentionally advertises retrieval and lifecycle capabilities only;
it does not generate answers.  MELT owns the benchmark cases and scoring while
this process maps its public operations to :class:`prme.MemoryClient`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import prme
from prme import MemoryClient
from prme.client import config_from_directory
from prme.config import EmbeddingConfig
from prme.types import (
    DECAY_LAMBDAS,
    EpistemicType,
    LifecycleState,
    NodeType,
    RetrievalMode,
    SourceType,
)


_SUPPORTED = [
    "reset",
    "time_control",
    "consolidation",
    "query_as_of",
    "structured_memory_write",
]
_UNSUPPORTED = ["answer_generation"]
_ALL_STATES = list(LifecycleState)
_TOKENS = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class _StoredEntry:
    event_id: str
    source_id: str
    node_id: str
    timestamp: datetime
    key: str | None


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamps must include an offset")
    return parsed.astimezone(timezone.utc)


def _source_type(value: Any) -> SourceType:
    return {
        "user": SourceType.USER_STATED,
        "tool": SourceType.TOOL_OUTPUT,
        "external": SourceType.EXTERNAL_DOCUMENT,
    }.get(str(value or "user").casefold(), SourceType.EXTERNAL_DOCUMENT)


def _node_type(value: Any) -> NodeType:
    return {
        "fact": NodeType.FACT,
        "preference": NodeType.PREFERENCE,
        "instruction": NodeType.INSTRUCTION,
        "procedure": NodeType.INSTRUCTION,
        "decision": NodeType.DECISION,
        "task": NodeType.TASK,
        "summary": NodeType.SUMMARY,
    }.get(str(value or "note").casefold(), NodeType.NOTE)


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown-installed-build"
    return result.stdout.strip() or "unknown-installed-build"


class PrmeMeltSut:
    """Stateful MELT protocol implementation backed by one PRME pack per case."""

    def __init__(self) -> None:
        self._client: MemoryClient | None = None
        self._state_root: Path | None = None
        self._user_id = "melt-user"
        self._contract_version = "b2"
        self._reset_count = 0
        self._entries_by_event: dict[str, _StoredEntry] = {}
        self._entries_by_node: dict[str, _StoredEntry] = {}
        self._entries_by_key: dict[str, list[_StoredEntry]] = {}
        self._min_score = 0.05
        self._commit = _git_commit()
        self._accepted_overrides: dict[str, Any] = {}
        self._rejected_overrides: dict[str, Any] = {}

    @property
    def _engine(self) -> MemoryClient:
        if self._client is None:
            raise RuntimeError("reset must be called before memory operations")
        return self._client

    def handle(self, message: dict[str, Any]) -> dict[str, Any]:
        op = message.get("op")
        if op == "hello":
            return self._hello(message)
        if op == "metadata":
            return self._metadata()
        if op == "reset":
            return self._reset(message)
        if op == "ingest":
            return self._ingest(message)
        if op == "memory_write":
            return self._memory_write(message)
        if op == "consolidate":
            return self._consolidate(message)
        if op == "tick":
            return {"op": "tick_ack", "ok": True, "timestamp": message.get("timestamp") or message.get("to")}
        if op == "answer":
            return {
                "op": "answer_result",
                "ok": False,
                "query_id": str(message.get("query_id") or ""),
                "error": {
                    "code": "unsupported_capability",
                    "message": "answer_generation is unsupported",
                },
            }
        if op == "query":
            return self._query(message)
        if op == "shutdown":
            self.close()
            return {"op": "shutdown_ack", "ok": True}
        raise ValueError(f"unsupported operation: {op!r}")

    def _hello(self, message: dict[str, Any]) -> dict[str, Any]:
        contract = str(message.get("contract_version") or "")
        if contract != "b2":
            raise ValueError("PRME's MELT bridge currently supports contract b2")
        paths = message.get("paths")
        owner = message.get("owner")
        if not isinstance(paths, dict) or not str(paths.get("state_dir") or "").strip():
            raise ValueError("hello.paths.state_dir is required")
        if not isinstance(owner, dict) or not str(owner.get("user_id") or "").strip():
            raise ValueError("hello.owner.user_id is required")
        self._state_root = Path(str(paths["state_dir"])).resolve()
        self._state_root.mkdir(parents=True, exist_ok=True)
        self._user_id = str(owner["user_id"])
        overrides = message.get("config_overrides") or {}
        if not isinstance(overrides, dict):
            raise ValueError("hello.config_overrides must be an object")
        self._accepted_overrides = {}
        self._rejected_overrides = {}
        for key, value in overrides.items():
            if key == "min_score" and isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
                if not math.isfinite(numeric):
                    raise ValueError("min_score must be finite")
                self._min_score = numeric
                self._accepted_overrides[key] = value
            else:
                self._rejected_overrides[str(key)] = "unsupported override"
        requested = message.get("capabilities_requested") or []
        if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
            raise ValueError("hello.capabilities_requested must be a list of strings")
        return {
            "op": "hello_ack",
            "ok": True,
            "contract_version": self._contract_version,
            "identity": self._identity(),
            "capabilities_supported": [item for item in requested if item in _SUPPORTED],
            "capabilities_unsupported": [item for item in requested if item in _UNSUPPORTED],
            "envelope_metadata": self._envelope_metadata(),
            "config_overrides_accepted": dict(self._accepted_overrides),
            "config_overrides_rejected": dict(self._rejected_overrides),
        }

    def _metadata(self) -> dict[str, Any]:
        return {
            "op": "metadata",
            "ok": True,
            **self._identity(),
            "contract_version": self._contract_version,
            "capabilities": list(_SUPPORTED),
            "capabilities_unsupported": list(_UNSUPPORTED),
            "envelope_metadata": self._envelope_metadata(),
        }

    def _identity(self) -> dict[str, str]:
        return {"id": "prme", "version": prme.__version__, "commit": self._commit}

    @staticmethod
    def _envelope_metadata() -> dict[str, Any]:
        return {
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "llm_calls_per_op": {"ingest": 0, "memory_write": 0, "query": 0},
        }

    def _reset(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._state_root is None:
            raise RuntimeError("hello must be called before reset")
        run_id = str(message.get("run_id") or "")
        if not run_id:
            raise ValueError("reset.run_id is required")
        self.close()
        digest = hashlib.sha256(run_id.encode()).hexdigest()[:12]
        while True:
            self._reset_count += 1
            directory = self._state_root / f"case-{self._reset_count:04d}-{digest}"
            if not directory.exists():
                break
        config = config_from_directory(str(directory))
        config.database_url = None
        config.embedding = EmbeddingConfig(
            provider="fastembed",
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        self._client = MemoryClient(str(directory), config=config)
        self._entries_by_event.clear()
        self._entries_by_node.clear()
        self._entries_by_key.clear()
        return {"op": "reset_ack", "ok": True, "run_id": run_id}

    def _store(
        self,
        *,
        event_id: str,
        source_id: str,
        content: str,
        timestamp: datetime,
        session_id: str | None,
        node_type: NodeType,
        source_type: SourceType,
        key: str | None = None,
        predicate: str | None = None,
    ) -> _StoredEntry:
        metadata: dict[str, Any] = {
            "melt_event_id": event_id,
            "melt_source_id": source_id,
            "melt_timestamp": timestamp.isoformat(),
        }
        if key is not None:
            metadata["melt_key"] = key
        if predicate is not None:
            metadata["melt_predicate"] = predicate
        internal_event_id = self._engine.store(
            content,
            user_id=self._user_id,
            session_id=session_id,
            role="user",
            node_type=node_type,
            metadata=metadata,
            epistemic_type=EpistemicType.ASSERTED,
            source_type=source_type,
            event_time=timestamp,
            ttl_days=None,
        )
        nodes = self._engine.get_event_nodes(internal_event_id, user_id=self._user_id)
        matching = [node for node in nodes if (node.metadata or {}).get("melt_event_id") == event_id]
        if len(matching) != 1:
            raise RuntimeError("PRME did not materialize exactly one MELT memory node")
        entry = _StoredEntry(
            event_id=event_id,
            source_id=source_id,
            node_id=str(matching[0].id),
            timestamp=timestamp,
            key=key,
        )
        self._entries_by_event[event_id] = entry
        self._entries_by_node[entry.node_id] = entry
        if key is not None:
            self._entries_by_key.setdefault(key, []).append(entry)
        return entry

    def _ingest(self, message: dict[str, Any]) -> dict[str, Any]:
        event_id = str(message.get("event_id") or "")
        content = str(message.get("content") or "")
        if not event_id or not content:
            raise ValueError("ingest requires event_id and content")
        occurred_at = _timestamp(message.get("timestamp"))
        entry = self._store(
            event_id=event_id,
            source_id=event_id,
            content=content,
            timestamp=occurred_at,
            session_id=str(message["session_id"]) if message.get("session_id") else None,
            node_type=NodeType.NOTE,
            source_type=_source_type(message.get("source_type")),
        )
        return {
            "op": "ack",
            "ok": True,
            "event_id": event_id,
            "source_id": entry.source_id,
            "chunk_id": entry.node_id,
            "created_at": occurred_at.isoformat(),
        }

    def _memory_write(self, message: dict[str, Any]) -> dict[str, Any]:
        event_id = str(message.get("event_id") or "")
        content = str(message.get("value") or "")
        key = str(message.get("key") or "")
        if not event_id or not content or not key:
            raise ValueError("memory_write requires event_id, key, and value")
        source_id = str(message.get("source_id") or event_id)
        occurred_at = _timestamp(message.get("timestamp"))
        entry = self._store(
            event_id=event_id,
            source_id=source_id,
            content=content,
            timestamp=occurred_at,
            session_id=str(message["session_id"]) if message.get("session_id") else None,
            node_type=_node_type(message.get("entry_type")),
            source_type=SourceType.USER_STATED,
            key=key,
            predicate=str(message["predicate"]) if message.get("predicate") else None,
        )
        supersedes = str(message.get("supersedes") or "")
        if supersedes:
            old = self._entries_by_node.get(supersedes) or self._entries_by_event.get(supersedes)
            if old is None:
                raise ValueError("memory_write supersedes an unknown entry")
            self._engine.supersede(old.node_id, entry.node_id, user_id=self._user_id)
        elif _node_type(message.get("entry_type")) == NodeType.PREFERENCE:
            prior = [item for item in self._entries_by_key.get(key, []) if item != entry]
            if prior:
                self._engine.contradict(prior[-1].node_id, entry.node_id, user_id=self._user_id)
        return {
            "op": "memory_write_ack",
            "ok": True,
            "event_id": event_id,
            "entry_id": entry.node_id,
            "source_id": entry.source_id,
            "created_at": occurred_at.isoformat(),
        }

    def _consolidate(self, message: dict[str, Any]) -> dict[str, Any]:
        result = self._engine.organize(user_id=self._user_id, budget_ms=5_000)
        return {
            "op": "consolidate_ack",
            "ok": True,
            "jobs_run": list(result.jobs_run),
        }

    def _query(self, message: dict[str, Any]) -> dict[str, Any]:
        query_id = str(message.get("query_id") or "")
        query = str(message.get("query") or "")
        top_k = int(message.get("top_k") or 0)
        if not query_id or not query or top_k < 1:
            raise ValueError("query requires query_id, query, and positive top_k")
        query_time = _timestamp(message.get("timestamp"))
        as_of_value = message.get("as_of")
        if as_of_value is not None:
            nodes = self._historical_nodes(query, _timestamp(as_of_value), top_k)
            evidence = [self._node_evidence(node, rank, query_time) for rank, node in enumerate(nodes, 1)]
        else:
            response = self._engine.retrieve(
                query,
                user_id=self._user_id,
                reference_time=query_time,
                min_score=self._min_score,
                limit=top_k,
                include_cross_scope=False,
                retrieval_mode=RetrievalMode.EXPLICIT,
            )
            evidence = []
            seen: set[str] = set()
            for candidate in response.results:
                item = self._candidate_evidence(candidate, len(evidence) + 1, query_time)
                if item is None or item["source_id"] in seen:
                    continue
                seen.add(item["source_id"])
                evidence.append(item)
                if len(evidence) == top_k:
                    break
        return {"op": "query_result", "ok": True, "query_id": query_id, "answer": "", "evidence": evidence}

    def _historical_nodes(self, query: str, as_of: datetime, top_k: int) -> list[Any]:
        nodes = self._engine.query_nodes(
            user_id=self._user_id,
            lifecycle_states=_ALL_STATES,
            limit=max(100, len(self._entries_by_node) + 1),
        )
        query_tokens = set(_TOKENS.findall(query.casefold()))
        eligible: list[tuple[int, datetime, str, Any]] = []
        for node in nodes:
            entry = self._entries_by_node.get(str(node.id))
            if entry is None or entry.timestamp > as_of:
                continue
            if node.superseded_by is not None:
                successor = self._entries_by_node.get(str(node.superseded_by))
                if successor is not None and successor.timestamp <= as_of:
                    continue
            overlap = len(query_tokens & set(_TOKENS.findall(node.content.casefold())))
            if overlap:
                eligible.append((overlap, entry.timestamp, str(node.id), node))
        eligible.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[2]))
        return [item[3] for item in eligible[:top_k]]

    def _candidate_evidence(self, candidate: Any, rank: int, query_time: datetime) -> dict[str, Any] | None:
        item = self._node_evidence(candidate.node, rank, query_time)
        if item is None:
            return None
        metadata = item["metadata"]
        metadata["score"] = candidate.composite_score
        if candidate.contradicts_id is not None:
            counterpart = self._entries_by_node.get(str(candidate.contradicts_id))
            if counterpart is not None:
                metadata["conflict_source_ids"] = [counterpart.source_id]
        return item

    def _node_evidence(self, node: Any, rank: int, query_time: datetime) -> dict[str, Any] | None:
        entry = self._entries_by_node.get(str(node.id))
        if entry is None:
            return None
        age_days = max(0.0, (query_time - entry.timestamp).total_seconds() / 86_400)
        decay_score = math.exp(-DECAY_LAMBDAS[node.decay_profile] * age_days)
        return {
            "source_id": entry.source_id,
            "rank": rank,
            "text": node.content,
            "metadata": {
                "event_id": entry.event_id,
                "node_id": entry.node_id,
                "lifecycle_state": node.lifecycle_state.value,
                "decay_score": decay_score,
            },
        }

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()


def serve() -> int:
    # The JSONL protocol exclusively owns stdout. PRME's structured logs use a
    # stdout print logger by default, so redirect them before any engine starts.
    import structlog

    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    sut = PrmeMeltSut()
    try:
        for line in sys.stdin:
            request_op: Any = None
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("request must be a JSON object")
                request_op = message.get("op")
                response = sut.handle(message)
            except Exception as exc:
                response = {
                    "op": "error",
                    "ok": False,
                    "error": {"code": "prme_sut_error", "message": str(exc)},
                }
            print(json.dumps(response, sort_keys=True), flush=True)
            if request_op == "shutdown" and response.get("ok") is True:
                return 0
    finally:
        sut.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    raise SystemExit(serve())


if __name__ == "__main__":
    main()
