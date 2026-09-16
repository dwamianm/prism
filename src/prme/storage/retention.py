"""Atomic TTL archival with a complete, portable tombstone record."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from prme.models.nodes import MemoryNode
from prme.storage import _snapshot_json
from prme.storage._threading import run_to_completion
from prme.types import LifecycleState, validate_transition

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore


_POLICY: Literal["ttl_expiration_v1"] = "ttl_expiration_v1"
_ACTIVE = (
    LifecycleState.TENTATIVE,
    LifecycleState.STABLE,
    LifecycleState.CONTESTED,
)


class RetentionRecord(BaseModel):
    """Complete state transition and policy inputs for one TTL expiration."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    policy_ref: Literal["ttl_expiration_v1"] = _POLICY
    operation_id: UUID
    target_type: Literal["memory_object"] = "memory_object"
    reason: Literal["retention_policy_expiry"] = "retention_policy_expiry"
    evaluated_at: datetime
    expires_at: datetime
    content_hash_of_deleted: str
    before: MemoryNode
    after: MemoryNode


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _identity(
    node: MemoryNode | None, user_id: str, evaluated_at: datetime
) -> tuple[UUID, datetime, datetime] | None:
    if node is None or node.user_id != user_id:
        raise ValueError("Retention target is unavailable")
    if node.ttl_days is None:
        return None
    evaluated = _utc(evaluated_at, "evaluated_at")
    expires = _utc(node.created_at, "node.created_at") + timedelta(
        days=node.ttl_days
    )
    operation_id = uuid5(
        node.id, f"prme:ttl-expiration:v1:{expires.isoformat()}"
    )
    return operation_id, expires, evaluated


def _prepare(
    before: MemoryNode,
    operation_id: UUID,
    expires_at: datetime,
    evaluated_at: datetime,
    after: MemoryNode,
) -> RetentionRecord | None:
    if (
        before.pinned
        or before.lifecycle_state not in _ACTIVE
        or expires_at >= evaluated_at
        or not validate_transition(before.lifecycle_state, LifecycleState.ARCHIVED)
    ):
        return None
    return RetentionRecord(
        operation_id=operation_id,
        evaluated_at=evaluated_at,
        expires_at=expires_at,
        content_hash_of_deleted=hashlib.sha256(before.content.encode()).hexdigest(),
        before=before,
        after=after,
    )


def _payload(record: RetentionRecord) -> str:
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    # Keep the RFC-0007 tombstone fields available to older operation readers,
    # while the checksummed record carries the authoritative full transition.
    return json.dumps(
        {
            "version": record.version,
            "record": raw,
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "target_id": str(record.before.id),
            "target_type": record.target_type,
            "reason": record.reason,
            "policy_ref": record.policy_ref,
            "content_hash_of_deleted": record.content_hash_of_deleted,
            "ttl_days": record.before.ttl_days,
            "created_at": record.before.created_at.isoformat(),
            "expired_at": record.expires_at.isoformat(),
            "tombstone_ts": record.after.updated_at.isoformat(),
        }
    )


def read_record(payload: str | dict[str, Any]) -> RetentionRecord:
    """Validate and decode a checksummed TTL tombstone payload."""
    value = json.loads(payload) if isinstance(payload, str) else payload
    try:
        raw = value["record"]
        if hashlib.sha256(raw.encode()).hexdigest() != value["sha256"]:
            raise ValueError("Retention journal checksum mismatch")
        record = RetentionRecord.model_validate(_snapshot_json.loads(raw))
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("Malformed retention journal") from exc

    before, after = record.before, record.after
    identity = _identity(before, before.user_id, record.evaluated_at)
    if identity is None:
        raise ValueError("Retention journal target has no TTL")
    operation_id, expires_at, evaluated_at = identity
    unchanged = {"lifecycle_state", "updated_at"}
    if (
        record.operation_id != operation_id
        or record.expires_at != expires_at
        or record.evaluated_at != evaluated_at
        or expires_at >= evaluated_at
        or before.pinned
        or before.lifecycle_state not in _ACTIVE
        or after.lifecycle_state != LifecycleState.ARCHIVED
        or record.content_hash_of_deleted
        != hashlib.sha256(before.content.encode()).hexdigest()
        or _snapshot_json.dumps(
            before.model_dump(exclude=unchanged), sort_keys=True
        )
        != _snapshot_json.dumps(after.model_dump(exclude=unchanged), sort_keys=True)
    ):
        raise ValueError("Retention journal identity mismatch")

    expected_outer = {
        "version": record.version,
        "target_id": str(before.id),
        "target_type": record.target_type,
        "reason": record.reason,
        "policy_ref": record.policy_ref,
        "content_hash_of_deleted": record.content_hash_of_deleted,
        "ttl_days": before.ttl_days,
        "created_at": before.created_at.isoformat(),
        "expired_at": record.expires_at.isoformat(),
        "tombstone_ts": after.updated_at.isoformat(),
    }
    if any(value.get(key) != expected for key, expected in expected_outer.items()):
        raise ValueError("Retention journal summary mismatch")
    return record


def _replayed(
    row: tuple[Any, Any, Any, Any, Any] | None,
    *,
    operation_id: UUID,
    node_id: str,
    user_id: str,
) -> bool:
    if row is None:
        return False
    op_type, target_id, payload, actor_id, namespace_id = row
    try:
        record = read_record(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Retention identity is already used by another operation"
        ) from exc
    if (
        op_type != "TOMBSTONE_SWEEP"
        or record.operation_id != operation_id
        or str(record.before.id) != node_id
        or str(target_id) != node_id
        or actor_id != user_id
        or namespace_id != record.before.scope.value
    ):
        raise ValueError("Retention identity is already used with different inputs")
    return True


def _checkpoint(stage: str) -> None:
    """Native transaction fault-injection boundary; no external work."""


async def expire_duckdb(
    store: "DuckPGQGraphStore",
    node_id: str,
    *,
    user_id: str,
    evaluated_at: datetime,
) -> bool:
    node_id = str(UUID(node_id))
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("TTL expiration requires an owner")
    async with store._conn_lock:
        return await run_to_completion(
            _expire_duckdb, store, node_id, user_id, evaluated_at
        )


def _expire_duckdb(
    store: "DuckPGQGraphStore",
    node_id: str,
    user_id: str,
    evaluated_at: datetime,
) -> bool:
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        before = store._get_node_sync(node_id, True)
        identity = _identity(before, user_id, evaluated_at)
        if identity is None:
            conn.execute("COMMIT")
            return False
        if before is None:  # Narrowed by _identity; retained for static checkers.
            raise ValueError("Retention target is unavailable")
        operation_id, expires_at, evaluated = identity
        saved = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=?",
            [str(operation_id)],
        ).fetchone()
        if _replayed(
            saved,
            operation_id=operation_id,
            node_id=node_id,
            user_id=user_id,
        ):
            conn.execute("COMMIT")
            return False
        if (
            before.pinned
            or before.lifecycle_state not in _ACTIVE
            or expires_at >= evaluated
            or not validate_transition(
                before.lifecycle_state, LifecycleState.ARCHIVED
            )
        ):
            conn.execute("COMMIT")
            return False
        _checkpoint("validated")
        changed_at = datetime.now(timezone.utc)
        conn.execute(
            "UPDATE nodes SET lifecycle_state='archived', updated_at=? WHERE id=?",
            [changed_at, node_id],
        )
        _checkpoint("updated")
        after = store._get_node_sync(node_id, True)
        if after is None:
            raise RuntimeError("Archived retention target disappeared")
        record = _prepare(
            before, operation_id, expires_at, evaluated, after
        )
        if record is None:
            raise ValueError("Retention target changed during archival")
        payload = _payload(record)
        read_record(payload)
        conn.execute(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES (?,'TOMBSTONE_SWEEP',?,?,?,?,?)""",
            [
                str(operation_id),
                node_id,
                payload,
                user_id,
                before.scope.value,
                changed_at,
            ],
        )
        _checkpoint("journal")
        conn.execute("COMMIT")
        return True
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def expire_postgres(
    store: "PgGraphStore",
    node_id: str,
    *,
    user_id: str,
    evaluated_at: datetime,
) -> bool:
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    node_id = str(UUID(node_id))
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("TTL expiration requires an owner")
    async with store._pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=$1 FOR UPDATE", node_id
        )
        before = store._record_to_node(row) if row is not None else None
        identity = _identity(before, user_id, evaluated_at)
        if identity is None:
            return False
        if before is None:  # Narrowed by _identity; retained for static checkers.
            raise ValueError("Retention target is unavailable")
        operation_id, expires_at, evaluated = identity
        saved = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=$1",
            str(operation_id),
        )
        if _replayed(
            tuple(saved) if saved is not None else None,
            operation_id=operation_id,
            node_id=node_id,
            user_id=user_id,
        ):
            return False
        if (
            before.pinned
            or before.lifecycle_state not in _ACTIVE
            or expires_at >= evaluated
            or not validate_transition(
                before.lifecycle_state, LifecycleState.ARCHIVED
            )
        ):
            return False
        _checkpoint("validated")
        changed_at = datetime.now(timezone.utc)
        row = await conn.fetchrow(
            f"UPDATE nodes SET lifecycle_state='archived', updated_at=$1 "
            f"WHERE id=$2 RETURNING {_NODE_COLUMNS}",
            changed_at,
            node_id,
        )
        _checkpoint("updated")
        after = store._record_to_node(row)
        record = _prepare(
            before, operation_id, expires_at, evaluated, after
        )
        if record is None:
            raise ValueError("Retention target changed during archival")
        payload = _payload(record)
        read_record(payload)
        inserted = await conn.fetchval(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES ($1,'TOMBSTONE_SWEEP',$2,$3::jsonb,$4,$5,$6)
            ON CONFLICT (id) DO NOTHING RETURNING id""",
            str(operation_id),
            node_id,
            payload,
            user_id,
            before.scope.value,
            changed_at,
        )
        if inserted is None:
            raise ValueError("Retention identity is already used with different inputs")
        _checkpoint("journal")
        return True
