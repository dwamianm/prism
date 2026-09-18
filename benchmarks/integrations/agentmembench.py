"""PRME adapter for the pinned AgentMemBench operational harness."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any

import prme
from prme import MemoryClient
from prme.types import EpistemicType, NodeType, Scope, SourceType


UPSTREAM_REVISION = "186c9a54edd47aae42d8b6990520f8e902b60303"
ADAPTER_SCHEMA_VERSION = 1
_MANIFEST_NAME = "agentmembench-prme-manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_identity() -> dict[str, Any]:
    package_path = Path(prme.__file__).resolve()
    identity: dict[str, Any] = {
        "package_version": importlib.metadata.version("prme"),
        "package_path": str(package_path),
        "revision": None,
        "tracked_files_dirty": None,
    }
    try:
        root = subprocess.run(
            ["git", "-C", str(package_path.parent), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        identity["revision"] = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        identity["tracked_files_dirty"] = bool(
            subprocess.run(
                ["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        pass
    return identity


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class PRMEAdapter:
    """Implement AgentMemBench's five-method adapter contract.

    Each reset opens a fresh generation while retaining earlier generation
    manifests and packs for verification. AgentMemBench calls reset once per
    phase, so phases cannot reuse state accidentally.
    """

    name = "prme"

    def __init__(self, config: Any, collection: str):
        self.config = config
        self.collection = collection
        self.root = Path(config.history_dir) / "prme" / collection
        self.client: MemoryClient | None = None
        self.owners: dict[str, str] = {}
        self._generation = 0
        self._generation_path: Path | None = None
        self._started = False
        self._lock = threading.Lock()
        self._counts = self._empty_counts()
        self._runtime = _runtime_identity()

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {"add_calls": 0, "search_calls": 0, "archived_ids": 0}

    def _root_manifest(self, *, status: str) -> dict[str, Any]:
        return {
            "schema_version": ADAPTER_SCHEMA_VERSION,
            "status": status,
            "collection": self.collection,
            "adapter_sha256": _sha256(Path(__file__)),
            "upstream_revision": UPSTREAM_REVISION,
            "prme": self._runtime,
            "generation_count": self._generation,
            "delete_semantics": "archive_retrieval_retirement",
        }

    def _write_root_manifest(self, *, status: str) -> None:
        _write_json(self.root / _MANIFEST_NAME, self._root_manifest(status=status))

    def _write_generation_manifest(self, *, status: str) -> None:
        if self._generation_path is None:
            return
        _write_json(
            self._generation_path / _MANIFEST_NAME,
            {
                "schema_version": ADAPTER_SCHEMA_VERSION,
                "status": status,
                "collection": self.collection,
                "generation": self._generation,
                "operation_counts": dict(self._counts),
                "delete_semantics": "archive_retrieval_retirement",
            },
        )

    def _open(self) -> MemoryClient:
        if self.client is None:
            if self._generation_path is None:
                self.reset()
            assert self._generation_path is not None
            self.client = MemoryClient(str(self._generation_path / "pack"))
            self._write_generation_manifest(status="active")
        return self.client

    def _close_generation(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        self._write_generation_manifest(status="complete")

    def reset(self) -> None:
        with self._lock:
            self._close_generation()
            if not self._started:
                shutil.rmtree(self.root, ignore_errors=True)
                self.root.mkdir(parents=True, exist_ok=True)
                self._started = True
            self._generation += 1
            self._generation_path = self.root / f"generation-{self._generation:04d}"
            self._generation_path.mkdir(parents=True, exist_ok=False)
            self.owners.clear()
            self._counts = self._empty_counts()
            self._write_root_manifest(status="active")
            self._open()

    def add(self, text: str, user_id: str) -> list[str]:
        receipt = self._open().store_with_receipt(
            text,
            user_id=user_id,
            node_type=NodeType.NOTE,
            scope=Scope.PERSONAL,
            metadata={
                "benchmark": "agentmembench",
                "collection": self.collection,
                "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
            },
            epistemic_type=EpistemicType.ASSERTED,
            source_type=SourceType.USER_STATED,
            ttl_days=None,
        )
        memory_id = str(receipt.node_id)
        with self._lock:
            self.owners[memory_id] = user_id
            self._counts["add_calls"] += 1
        return [memory_id]

    def search(self, query: str, user_id: str, limit: int) -> list[str]:
        response = self._open().retrieve(
            query,
            user_id=user_id,
            scope=Scope.PERSONAL,
            limit=limit,
            token_budget=8192,
            include_cross_scope=False,
        )
        with self._lock:
            self._counts["search_calls"] += 1
        return [candidate.node.content for candidate in response.results[:limit]]

    def delete(self, memory_ids: list[str]) -> None:
        client = self._open()
        archived = 0
        for memory_id in memory_ids:
            with self._lock:
                owner = self.owners.pop(memory_id, None)
            if owner is not None:
                client.archive(memory_id, user_id=owner)
                archived += 1
        with self._lock:
            self._counts["archived_ids"] += archived

    def close(self) -> None:
        with self._lock:
            self._close_generation()
            if self._started:
                self._write_root_manifest(status="complete")

