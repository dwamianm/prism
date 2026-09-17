"""PRME backend for the official LongMemEval-V2 memory interface.

This file is also copyable into the upstream ``memory_modules`` package.  It
uses only the public PRME client and the trajectory fields released by the
benchmark; question IDs, categories, answers, and evaluator configuration never
enter the memory pack.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any, Literal, cast

from prme import (
    EpistemicType,
    LifecycleState,
    MemoryClient,
    NodeType,
    PRMEConfig,
    Scope,
    SourceType,
)
from prme.config import EmbeddingConfig
from prme.retrieval.config import PackingConfig

try:  # Copied into the official upstream ``memory_modules`` package.
    from .memory import Memory, MemoryConfig, MemoryContextItem, register_memory, require
except ImportError:  # Imported from PRME's own benchmark/test environment.
    try:
        from memory_modules.memory import (  # type: ignore[no-redef]
            Memory,
            MemoryConfig,
            MemoryContextItem,
            register_memory,
            require,
        )
    except ImportError:
        MemoryConfig = dict[str, Any]  # type: ignore[misc,assignment]
        MemoryContextItem = dict[str, str]  # type: ignore[misc,assignment]

        class Memory:  # type: ignore[no-redef]
            """Minimal local stand-in for integration tests without upstream."""

            memory_type = ""

            def __init__(self, memory_params: dict[str, object]) -> None:
                self.memory_params = dict(memory_params)

        def register_memory(cls):  # type: ignore[no-redef]
            return cls

        def require(condition: bool, message: str) -> None:  # type: ignore[no-redef]
            if not condition:
                raise RuntimeError(message)


UPSTREAM_REVISION = "2cc8c540bdb87fe6761629b585e727e1c4704520"
ADAPTER_SCHEMA_VERSION = 4
_READABLE_SCHEMA_VERSIONS = {2, 3, ADAPTER_SCHEMA_VERSION}
_MANIFEST_NAME = "longmemeval_v2_manifest.json"
_PACK_NAME = "prme_pack"
_ALLOWED_PARAMS = {
    "storage_path",
    "trajectories_root_dir",
    "user_id",
    "token_budget",
    "context_format",
    "result_limit",
    "include_images",
    "image_limit",
    "max_chunk_chars",
    "context_item_max_chars",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _trajectory_payload(trajectory: dict[str, object]) -> dict[str, object]:
    """Allowlist and validate the public trajectory fields used by memory."""
    trajectory_id = trajectory.get("id")
    domain = trajectory.get("domain")
    environment = trajectory.get("environment")
    goal = trajectory.get("goal")
    outcome = trajectory.get("outcome")
    start_url = trajectory.get("start_url")
    states = trajectory.get("states")
    require(isinstance(trajectory_id, str) and bool(trajectory_id.strip()), "trajectory id must be non-empty")
    require(isinstance(domain, str) and bool(domain.strip()), f"trajectory domain must be non-empty for {trajectory_id}")
    require(isinstance(environment, str) and bool(environment.strip()), f"trajectory environment must be non-empty for {trajectory_id}")
    require(isinstance(goal, str), f"trajectory goal must be a string for {trajectory_id}")
    require(outcome is None or isinstance(outcome, str), f"trajectory outcome must be a string or null for {trajectory_id}")
    require(isinstance(start_url, str) and bool(start_url.strip()), f"trajectory start_url must be non-empty for {trajectory_id}")
    require(isinstance(states, list) and bool(states), f"trajectory states must be non-empty for {trajectory_id}")

    normalized_states: list[dict[str, object]] = []
    for position, raw in enumerate(states):
        require(isinstance(raw, dict), f"trajectory state {position} must be an object for {trajectory_id}")
        state_index = raw.get("state_index", position)
        step = raw.get("step", state_index)
        url = raw.get("url")
        action = raw.get("action")
        thought = raw.get("thought", raw.get("thoughts"))
        text = raw.get("accessibility_tree", raw.get("text"))
        screenshot = raw.get("screenshot")
        require(type(state_index) is int and state_index >= 0, f"invalid state_index for {trajectory_id}:{position}")
        require(state_index == position, f"state_index must be contiguous and ordered for {trajectory_id}:{position}")
        require(type(step) is int and step >= 0, f"invalid step for {trajectory_id}:{position}")
        require(isinstance(url, str) and bool(url.strip()), f"state URL must be non-empty for {trajectory_id}:{position}")
        require(action is None or isinstance(action, str), f"state action must be a string or null for {trajectory_id}:{position}")
        require(thought is None or isinstance(thought, str), f"state thought must be a string or null for {trajectory_id}:{position}")
        require(isinstance(text, str), f"state accessibility text must be a string for {trajectory_id}:{position}")
        require(isinstance(screenshot, str) and bool(screenshot.strip()), f"state screenshot must be non-empty for {trajectory_id}:{position}")
        normalized_states.append(
            {
                "state_index": state_index,
                "step": step,
                "url": url,
                "action": action,
                "thought": thought,
                "text": text,
                "screenshot": screenshot,
            }
        )
    return {
        "id": trajectory_id,
        "domain": domain,
        "environment": environment,
        "goal": goal,
        "outcome": outcome,
        "start_url": start_url,
        "states": normalized_states,
    }


def _chunks(text: str, limit: int) -> list[str]:
    """Split without dropping text, preferring accessibility-tree line boundaries."""
    require(limit > 0, "chunk limit must be positive")
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True) or [text]:
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        if current and len(current) + len(line) > limit:
            pieces.append(current)
            current = ""
        current += line
    if current or not pieces:
        pieces.append(current)
    return pieces


def _prefixed_chunks(prefix_lines: list[str], body: str, limit: int) -> list[str]:
    """Split a long body while repeating the source identity on every chunk."""
    prefix = "\n".join(prefix_lines) + "\n"
    require(len(prefix) < limit, "trajectory identity exceeds max_chunk_chars")
    return [prefix + chunk for chunk in _chunks(body, limit - len(prefix))]


@register_memory
class PRMEMemory(Memory):
    """Text-first PRME trajectory memory with source screenshot returns."""

    memory_type = "prme"

    def __init__(self, memory_params: dict[str, object]) -> None:
        super().__init__(memory_params)
        unexpected = sorted(set(memory_params) - _ALLOWED_PARAMS)
        require(not unexpected, f"prme memory_params contains unexpected keys: {unexpected}")

        self.user_id = str(memory_params.get("user_id", "evaluation")).strip()
        self.token_budget = int(memory_params.get("token_budget", 32768))
        context_format = memory_params.get("context_format", "auditable")
        self.result_limit = int(memory_params.get("result_limit", 100))
        self.include_images = memory_params.get("include_images", True)
        self.image_limit = int(memory_params.get("image_limit", 8))
        self.max_chunk_chars = int(memory_params.get("max_chunk_chars", 8000))
        self.context_item_max_chars = int(
            memory_params.get("context_item_max_chars", 12000)
        )
        require(bool(self.user_id), "prme user_id must be non-empty")
        require(self.token_budget > 0, "prme token_budget must be positive")
        require(
            isinstance(context_format, str)
            and context_format in {"auditable", "compact"},
            "prme context_format must be 'auditable' or 'compact'",
        )
        self.context_format = cast(Literal["auditable", "compact"], context_format)
        require(self.result_limit > 0, "prme result_limit must be positive")
        require(type(self.include_images) is bool, "prme include_images must be a boolean")
        require(self.image_limit >= 0, "prme image_limit must be non-negative")
        require(self.max_chunk_chars >= 512, "prme max_chunk_chars must be at least 512")
        require(
            self.context_item_max_chars >= 512,
            "prme context_item_max_chars must be at least 512",
        )

        root_value = memory_params.get("trajectories_root_dir")
        if root_value is None:
            root_value = os.environ.get("DATA_ROOT")
        self.trajectories_root_dir = (
            Path(str(root_value)).expanduser().resolve() if root_value else None
        )

        storage_value = memory_params.get("storage_path")
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        if storage_value:
            self._root = Path(str(storage_value)).expanduser().resolve()
            self._root.mkdir(parents=True, exist_ok=True)
        else:
            self._temporary = tempfile.TemporaryDirectory(prefix="prme-lme-v2-")
            self._root = Path(self._temporary.name).resolve()
        self._lock = threading.RLock()
        self._client: MemoryClient | None = None
        self._manifest: dict[str, Any] = {}
        self._query_reference_time: datetime | None = None
        self._query_clock_source = "uninitialized"
        self._open(self._root)

    @property
    def memory_config(self) -> MemoryConfig:
        return {
            "memory_type": self.memory_type,
            "memory_params": dict(self.memory_params),
        }

    def _config(self, root: Path) -> PRMEConfig:
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
                token_budget=self.token_budget,
                context_format=self.context_format,
                # Each inserted trajectory already has a compact, ordered
                # procedure trace. Expanding arbitrary adjacent raw state
                # chunks duplicates long accessibility trees and can crowd
                # independently relevant candidates out of the result limit.
                session_context_window=0,
            ),
            enable_qa_pairing=False,
            enable_query_reformulation=False,
            enable_store_supersedence=False,
            enable_surprise_gating=False,
            enable_reranker=False,
            organizer={"opportunistic_enabled": False},
            _env_file=None,
        )

    def _open(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / _MANIFEST_NAME
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            schema_version = manifest.get("schema_version")
            require(
                schema_version in _READABLE_SCHEMA_VERSIONS
                and manifest.get("upstream_revision") == UPSTREAM_REVISION
                and isinstance(manifest.get("trajectories"), dict),
                "incompatible LongMemEval-V2 adapter manifest",
            )
            self._manifest = manifest
        else:
            require(not (root / "memory.duckdb").exists(), "PRME pack is missing its LongMemEval-V2 manifest")
            self._manifest = {
                "schema_version": ADAPTER_SCHEMA_VERSION,
                "upstream_revision": UPSTREAM_REVISION,
                "query_reference_time": None,
                "trajectories": {},
            }
            self._write_manifest()
        self._validate_manifest_attachments(root)
        self._root = root
        self._client = MemoryClient(config=self._config(root))
        if self._manifest["schema_version"] == 2:
            nodes = self._client.query_nodes(
                user_id=self.user_id,
                scope=Scope.PROJECT,
                lifecycle_states=list(LifecycleState),
                limit=1,
            )
            complete = any(
                record.get("status") == "complete"
                for record in self._manifest["trajectories"].values()
            )
            require(bool(nodes) or not complete, "legacy adapter pack has no query clock source")
            self._query_reference_time = nodes[0].created_at if nodes else None
            self._query_clock_source = "legacy_max_created_at"
        else:
            raw_reference_time = self._manifest.get("query_reference_time")
            if raw_reference_time is None:
                self._query_reference_time = None
            else:
                require(
                    isinstance(raw_reference_time, str),
                    "adapter query_reference_time must be an ISO timestamp",
                )
                parsed = datetime.fromisoformat(raw_reference_time.replace("Z", "+00:00"))
                require(
                    parsed.utcoffset() is not None,
                    "adapter query_reference_time must include an offset",
                )
                self._query_reference_time = parsed.astimezone(timezone.utc)
            complete = any(
                record.get("status") == "complete"
                for record in self._manifest["trajectories"].values()
            )
            require(
                self._query_reference_time is not None or not complete,
                "complete adapter pack is missing its query clock",
            )
            self._query_clock_source = "manifest"

    def _ensure_client(self) -> MemoryClient:
        if self._client is None:
            self._client = MemoryClient(config=self._config(self._root))
        return self._client

    def _write_manifest(self) -> None:
        path = self._root / _MANIFEST_NAME
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(_canonical(self._manifest) + b"\n")
        temporary.replace(path)

    def _resolve_screenshot(self, value: str) -> Path:
        supplied = Path(value).expanduser()
        candidates = [supplied]
        if self.trajectories_root_dir is not None and not supplied.is_absolute():
            candidates.extend(
                [
                    self.trajectories_root_dir / supplied,
                    self.trajectories_root_dir / "screenshots" / supplied,
                ]
            )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise RuntimeError(f"could not resolve trajectory screenshot: {value}")

    def _copy_screenshot(
        self,
        trajectory_id: str,
        state_index: int,
        original: Path,
    ) -> str | None:
        if not self.include_images:
            return None
        relative = self._attachment_relative(trajectory_id, state_index, original)
        destination = self._root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            require(
                hashlib.sha256(destination.read_bytes()).digest()
                == hashlib.sha256(original.read_bytes()).digest(),
                f"screenshot identity changed for {trajectory_id}:{state_index}",
            )
        else:
            shutil.copy2(original, destination)
        return relative.as_posix()

    @staticmethod
    def _attachment_relative(
        trajectory_id: str,
        state_index: int,
        original: Path,
    ) -> Path:
        trajectory_dir = hashlib.sha256(trajectory_id.encode("utf-8")).hexdigest()[:24]
        suffix = original.suffix.lower() or ".png"
        return Path("attachments") / trajectory_dir / f"{state_index:06d}{suffix}"

    def _validate_manifest_attachments(
        self,
        root: Path,
        trajectory_ids: set[str] | None = None,
    ) -> None:
        """Reject incomplete or altered schema-4 attachment artifacts."""
        if self._manifest.get("schema_version") != ADAPTER_SCHEMA_VERSION:
            return
        resolved_root = root.resolve()
        trajectories = self._manifest.get("trajectories")
        require(isinstance(trajectories, dict), "adapter manifest trajectories must be an object")
        selected_ids = sorted(trajectories) if trajectory_ids is None else sorted(trajectory_ids)
        for trajectory_id in selected_ids:
            require(trajectory_id in trajectories, f"unknown trajectory inventory: {trajectory_id}")
            record = trajectories[trajectory_id]
            require(isinstance(record, dict), f"invalid trajectory manifest record: {trajectory_id}")
            require(record.get("status") == "complete", f"trajectory insert is incomplete: {trajectory_id}")
            attachments = record.get("attachments")
            require(isinstance(attachments, list), f"trajectory attachment inventory is missing: {trajectory_id}")
            expected_count = record.get("state_count") if self.include_images else 0
            require(type(expected_count) is int and expected_count >= 0, f"invalid trajectory state count: {trajectory_id}")
            require(len(attachments) == expected_count, f"trajectory attachment inventory is incomplete: {trajectory_id}")
            seen_states: set[int] = set()
            seen_paths: set[str] = set()
            for attachment in attachments:
                require(isinstance(attachment, dict), f"invalid attachment inventory entry: {trajectory_id}")
                state_index = attachment.get("state_index")
                relative_value = attachment.get("path")
                expected_digest = attachment.get("sha256")
                require(type(state_index) is int and state_index >= 0, f"invalid attachment state index: {trajectory_id}")
                require(isinstance(relative_value, str) and bool(relative_value), f"invalid attachment path: {trajectory_id}:{state_index}")
                relative = Path(relative_value)
                require(not relative.is_absolute() and ".." not in relative.parts, f"attachment path escaped the PRME pack: {trajectory_id}:{state_index}")
                trajectory_dir = hashlib.sha256(trajectory_id.encode("utf-8")).hexdigest()[:24]
                require(
                    len(relative.parts) == 3
                    and relative.parts[:2] == ("attachments", trajectory_dir)
                    and relative.stem == f"{state_index:06d}",
                    f"attachment path does not match its trajectory state: {trajectory_id}:{state_index}",
                )
                resolved = (resolved_root / relative).resolve()
                require(resolved.is_relative_to(resolved_root), f"attachment path escaped the PRME pack: {trajectory_id}:{state_index}")
                require(isinstance(expected_digest, str) and len(expected_digest) == 64, f"invalid attachment digest: {trajectory_id}:{state_index}")
                require(state_index not in seen_states and relative_value not in seen_paths, f"duplicate attachment inventory entry: {trajectory_id}:{state_index}")
                require(resolved.is_file(), f"attachment is missing from the PRME pack: {trajectory_id}:{state_index}")
                actual_digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
                require(actual_digest == expected_digest, f"attachment digest mismatch: {trajectory_id}:{state_index}")
                seen_states.add(state_index)
                seen_paths.add(relative_value)
            require(seen_states == set(range(expected_count)), f"trajectory attachment states are incomplete: {trajectory_id}")

    def _record_insert_progress(self, trajectory_id: str, node_count: int) -> None:
        """Checkpoint and report bounded progress for long trajectories."""
        self._manifest["trajectories"][trajectory_id]["node_count"] = node_count
        if node_count % 100 == 0:
            self._write_manifest()
            print(
                f"[prme] trajectory={trajectory_id} indexed_nodes={node_count}",
                flush=True,
            )

    def insert(self, trajectory: dict[str, object]) -> None:
        require(
            self._manifest.get("schema_version") == ADAPTER_SCHEMA_VERSION,
            "legacy LongMemEval-V2 adapter packs are read-only; rebuild to insert trajectories",
        )
        payload = _trajectory_payload(trajectory)
        trajectory_id = str(payload["id"])
        states = list(payload["states"])
        screenshot_sources: dict[int, Path] = {}
        screenshot_digests: list[dict[str, object]] = []
        attachments: list[dict[str, object]] = []
        if self.include_images:
            for state in states:
                state_index = int(state["state_index"])
                source = self._resolve_screenshot(str(state["screenshot"]))
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                screenshot_sources[state_index] = source
                screenshot_digests.append(
                    {
                        "state_index": state_index,
                        "sha256": digest,
                    }
                )
                attachments.append(
                    {
                        "state_index": state_index,
                        "path": self._attachment_relative(trajectory_id, state_index, source).as_posix(),
                        "sha256": digest,
                    }
                )
        fingerprint = hashlib.sha256(
            _canonical(
                {
                    "trajectory": payload,
                    "screenshot_digests": screenshot_digests,
                }
            )
        ).hexdigest()
        with self._lock:
            saved = self._manifest["trajectories"].get(trajectory_id)
            if saved is not None:
                require(saved.get("fingerprint") == fingerprint, f"trajectory identity changed: {trajectory_id}")
                require(saved.get("status") == "complete", f"trajectory insert was interrupted: {trajectory_id}")
                return

            self._manifest["trajectories"][trajectory_id] = {
                "fingerprint": fingerprint,
                "status": "preparing",
                "state_count": len(states),
                "node_count": 0,
                "attachments": attachments,
            }
            self._write_manifest()
            client = self._ensure_client()
            node_count = 0
            last_event_id: str | None = None
            try:
                action_lines: list[str] = []
                for position, state in enumerate(states):
                    action = str(state["action"] or "").strip()
                    if not action:
                        continue
                    if position == 0:
                        action_lines.append(
                            f"Initial state {state['state_index']} recorded action: {action}"
                        )
                    else:
                        action_lines.append(
                            f"State {states[position - 1]['state_index']} -> "
                            f"state {state['state_index']}: {action}"
                        )
                summary_body = "\n".join(
                    [
                        f"Domain: {payload['domain']}",
                        f"Environment: {payload['environment']}",
                        f"Goal: {payload['goal']}",
                        f"Outcome: {payload['outcome'] or 'unknown'}",
                        f"Start URL: {payload['start_url']}",
                        "Transition actions (the dataset attaches actions to destination states):",
                        *action_lines,
                    ]
                )
                summary_chunks = _prefixed_chunks(
                    ["Agent trajectory summary", f"Trajectory: {trajectory_id}"],
                    summary_body,
                    self.max_chunk_chars,
                )
                for chunk_index, content in enumerate(summary_chunks):
                    last_event_id = client.store(
                        content,
                        user_id=self.user_id,
                        session_id=trajectory_id,
                        role="tool",
                        node_type=NodeType.SUMMARY,
                        scope=Scope.PROJECT,
                        epistemic_type=EpistemicType.OBSERVED,
                        source_type=SourceType.TOOL_OUTPUT,
                        metadata={
                            "benchmark": "longmemeval-v2",
                            "source_kind": "trajectory_summary",
                            "trajectory_id": trajectory_id,
                            "chunk_index": chunk_index,
                            "chunk_count": len(summary_chunks),
                        },
                    )
                    node_count += 1
                    self._record_insert_progress(trajectory_id, node_count)

                procedure_lines: list[str] = []
                for position, state in enumerate(states):
                    procedure_lines.extend(
                        [
                            f"State {state['state_index']} URL: {state['url']}",
                            "Recorded agent thought at this state (unverified): "
                            f"{state['thought'] or 'none'}",
                        ]
                    )
                    if position + 1 < len(states):
                        destination = states[position + 1]
                        procedure_lines.append(
                            f"Observed transition from state {state['state_index']} "
                            f"to state {destination['state_index']}: "
                            f"{destination['action'] or 'none recorded'}"
                        )
                procedure_chunks = _prefixed_chunks(
                    [
                        "Agent trajectory procedure trace",
                        f"Trajectory: {trajectory_id}",
                        f"Domain: {payload['domain']}",
                        f"Environment: {payload['environment']}",
                        f"Trajectory goal: {payload['goal']}",
                        f"Outcome: {payload['outcome'] or 'unknown'}",
                        "Actions are observed transitions into their destination states.",
                    ],
                    "\n".join(procedure_lines),
                    self.max_chunk_chars,
                )
                for chunk_index, content in enumerate(procedure_chunks):
                    last_event_id = client.store(
                        content,
                        user_id=self.user_id,
                        session_id=trajectory_id,
                        role="tool",
                        node_type=NodeType.SUMMARY,
                        scope=Scope.PROJECT,
                        epistemic_type=EpistemicType.OBSERVED,
                        source_type=SourceType.TOOL_OUTPUT,
                        metadata={
                            "benchmark": "longmemeval-v2",
                            "source_kind": "trajectory_procedure",
                            "trajectory_id": trajectory_id,
                            "chunk_index": chunk_index,
                            "chunk_count": len(procedure_chunks),
                        },
                    )
                    node_count += 1
                    self._record_insert_progress(trajectory_id, node_count)

                for position, state in enumerate(states):
                    state_index = int(state["state_index"])
                    screenshot = (
                        self._copy_screenshot(
                            trajectory_id,
                            state_index,
                            screenshot_sources[state_index],
                        )
                        if self.include_images
                        else None
                    )
                    if position == 0:
                        incoming_action = (
                            f"Initial-state action field: {state['action']}"
                            if state["action"]
                            else "Incoming action: none (initial state)"
                        )
                    else:
                        incoming_action = (
                            f"Incoming action from state {states[position - 1]['state_index']} "
                            f"to this state: {state['action'] or 'none recorded'}"
                        )
                    state_body = "\n".join(
                        [
                            incoming_action,
                            f"Recorded thought at this state: {state['thought'] or 'none'}",
                            "Accessibility tree:",
                            str(state["text"]),
                        ]
                    )
                    chunks = _prefixed_chunks(
                        [
                            "Agent trajectory state",
                            f"Trajectory: {trajectory_id}",
                            f"Domain: {payload['domain']}",
                            f"Environment: {payload['environment']}",
                            f"Trajectory goal: {payload['goal']}",
                            f"State index: {state_index}",
                            f"Step: {state['step']}",
                            f"URL: {state['url']}",
                        ],
                        state_body,
                        self.max_chunk_chars,
                    )
                    for chunk_index, content in enumerate(chunks):
                        last_event_id = client.store(
                            content,
                            user_id=self.user_id,
                            session_id=trajectory_id,
                            role="tool",
                            node_type=NodeType.NOTE,
                            scope=Scope.PROJECT,
                            epistemic_type=EpistemicType.OBSERVED,
                            source_type=SourceType.TOOL_OUTPUT,
                            metadata={
                                "benchmark": "longmemeval-v2",
                                "source_kind": "trajectory_state",
                                "trajectory_id": trajectory_id,
                                "state_index": state_index,
                                "step": state["step"],
                                "chunk_index": chunk_index,
                                "chunk_count": len(chunks),
                                "screenshot": screenshot,
                            },
                        )
                        node_count += 1
                        self._record_insert_progress(trajectory_id, node_count)
            except BaseException:
                self._manifest["trajectories"][trajectory_id]["node_count"] = node_count
                self._write_manifest()
                raise
            require(last_event_id is not None, "trajectory produced no memory events")
            last_event = client.get_event(last_event_id, user_id=self.user_id)
            require(last_event is not None, "trajectory query clock source is unavailable")
            require(
                last_event.created_at.utcoffset() is not None,
                "trajectory query clock source must include an offset",
            )
            query_reference_time = last_event.created_at.astimezone(timezone.utc)
            if self._query_reference_time is not None:
                query_reference_time = max(query_reference_time, self._query_reference_time)
            self._query_reference_time = query_reference_time
            self._query_clock_source = "manifest"
            self._manifest["query_reference_time"] = query_reference_time.isoformat()
            self._manifest["trajectories"][trajectory_id].update(
                status="complete", node_count=node_count
            )
            self._validate_manifest_attachments(self._root, {trajectory_id})
            self._write_manifest()

    def query(self, query: str, query_image: str | None = None) -> list[MemoryContextItem]:
        require(isinstance(query, str) and bool(query.strip()), "prme query must be non-empty")
        with self._lock:
            require(
                self._query_reference_time is not None,
                "cannot query PRME adapter before a completed trajectory insert",
            )
            response = self._ensure_client().retrieve(
                query,
                user_id=self.user_id,
                scope=Scope.PROJECT,
                reference_time=self._query_reference_time,
                token_budget=self.token_budget,
                limit=self.result_limit,
                include_cross_scope=False,
            )
            items: list[MemoryContextItem] = []
            rendered = response.bundle.render()
            if rendered.strip():
                # The upstream harness independently measures the final prompt
                # with the reader processor and truncates only at item
                # boundaries. Bounded items prevent a small tokenizer mismatch
                # from dropping one monolithic memory payload.
                items.extend(
                    {"type": "text", "value": chunk}
                    for chunk in _chunks(rendered, self.context_item_max_chars)
                    if chunk
                )
            if self.include_images and self.image_limit:
                included_ids = {
                    str(candidate.node.id)
                    for candidates in response.bundle.sections.values()
                    for candidate in candidates
                }
                seen: set[str] = set()
                for candidate in response.results:
                    if str(candidate.node.id) not in included_ids:
                        continue
                    metadata = candidate.node.metadata or {}
                    relative = metadata.get("screenshot")
                    if not isinstance(relative, str) or not relative or relative in seen:
                        continue
                    image = (self._root / relative).resolve()
                    require(image.is_file() and self._root in image.parents, "saved screenshot path escaped the PRME pack")
                    items.append({"type": "image", "value": str(image)})
                    seen.add(relative)
                    if len(seen) >= self.image_limit:
                        break
            return items

    def post_query_hook(
        self,
        *,
        query: str,
        query_image: str | None,
        memory_context: list[MemoryContextItem],
    ) -> dict[str, object]:
        return {
            "adapter": "prme",
            "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
            "upstream_revision": UPSTREAM_REVISION,
            "query_reference_time": (
                self._query_reference_time.isoformat()
                if self._query_reference_time is not None
                else None
            ),
            "query_clock_source": self._query_clock_source,
            "context_format": self.context_format,
            "query_image_used_for_retrieval": False,
            "returned_text_items": sum(item["type"] == "text" for item in memory_context),
            "returned_image_items": sum(item["type"] == "image" for item in memory_context),
        }

    def _save_backend(self, output_dir: Path) -> None:
        with self._lock:
            destination = output_dir / _PACK_NAME
            require(not destination.exists(), f"refusing to overwrite saved PRME pack: {destination}")
            self._validate_manifest_attachments(self._root)
            client = self._ensure_client()
            client.close()
            self._client = None
            try:
                self._validate_manifest_attachments(self._root)
                shutil.copytree(self._root, destination)
                self._validate_manifest_attachments(destination)
            except BaseException:
                self._client = MemoryClient(config=self._config(self._root))
                raise

    def _load_backend(self, input_dir: Path) -> None:
        with self._lock:
            source = input_dir / _PACK_NAME
            require(source.is_dir(), f"missing saved PRME pack: {source}")
            if self._client is not None:
                self._client.close()
                self._client = None
            if self._temporary is not None:
                self._temporary.cleanup()
                self._temporary = None
            self._open(source.resolve())

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            if self._temporary is not None:
                self._temporary.cleanup()
                self._temporary = None
