"""Single-node lifecycle changes with locked validation and complete provenance."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from prme.models.nodes import MemoryNode
from prme.storage._threading import run_to_completion
from prme.storage import _snapshot_json
from prme.types import LifecycleState, validate_transition

Action = Literal["promote", "archive", "deprecate"]
TARGETS = {
    "promote": LifecycleState.STABLE,
    "archive": LifecycleState.ARCHIVED,
    "deprecate": LifecycleState.DEPRECATED,
}


class LifecycleRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    policy: Literal["lifecycle_transitions_v1"] = "lifecycle_transitions_v1"
    operation_id: UUID
    action: Action
    before: MemoryNode
    after: MemoryNode


def _validate(node, node_id, action):
    if action not in TARGETS:
        raise ValueError("Unsupported lifecycle action")
    if node is None:
        raise ValueError(f"Node {node_id} not found")
    target = TARGETS[action]
    if not validate_transition(node.lifecycle_state, target):
        raise ValueError(
            f"Cannot {action}: transition from {node.lifecycle_state.value} "
            f"to {target.value} is not allowed"
        )
    return target


def _payload(record):
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def read_record(payload):
    value = json.loads(payload) if isinstance(payload, str) else payload
    if hashlib.sha256(value["record"].encode()).hexdigest() != value["sha256"]:
        raise ValueError("Lifecycle journal checksum mismatch")
    record = LifecycleRecord.model_validate(_snapshot_json.loads(value["record"]))
    if (
        record.before.id != record.after.id
        or record.before.user_id != record.after.user_id
        or record.before.scope != record.after.scope
        or record.after.lifecycle_state != TARGETS[record.action]
    ):
        raise ValueError("Lifecycle journal identity mismatch")
    _validate(record.before, str(record.before.id), record.action)
    unchanged = {"lifecycle_state", "updated_at"}
    if _snapshot_json.dumps(
        record.before.model_dump(exclude=unchanged), sort_keys=True
    ) != _snapshot_json.dumps(
        record.after.model_dump(exclude=unchanged), sort_keys=True
    ):
        raise ValueError("Lifecycle journal changes unrelated node fields")
    return record


def _checkpoint(stage):
    """Native transaction fault-injection boundary; no external work."""


async def transition_duckdb(store, node_id, action):
    async with store._conn_lock:
        await run_to_completion(_transition_duckdb, store, node_id, action)


def _transition_duckdb(store, node_id, action):
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        before = store._get_node_sync(node_id, True)
        target = _validate(before, node_id, action)
        _checkpoint("validated")
        now = datetime.now(timezone.utc)
        conn.execute(
            "UPDATE nodes SET lifecycle_state=?, updated_at=? WHERE id=?",
            [target.value, now, node_id],
        )
        _checkpoint("updated")
        after = store._get_node_sync(node_id, True)
        record = LifecycleRecord(
            operation_id=uuid4(), action=action, before=before, after=after
        )
        conn.execute(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES (?,'LIFECYCLE_CHANGED',?,?,?,?,?)""",
            [
                str(record.operation_id),
                node_id,
                _payload(record),
                before.user_id,
                before.scope.value,
                now,
            ],
        )
        _checkpoint("journal")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def transition_postgres(store, node_id, action):
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    async with store._pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=$1 FOR UPDATE", node_id
        )
        before = store._record_to_node(row) if row is not None else None
        target = _validate(before, node_id, action)
        _checkpoint("validated")
        now = datetime.now(timezone.utc)
        row = await conn.fetchrow(
            f"UPDATE nodes SET lifecycle_state=$1, updated_at=$2 "
            f"WHERE id=$3 RETURNING {_NODE_COLUMNS}",
            target.value,
            now,
            node_id,
        )
        _checkpoint("updated")
        after = store._record_to_node(row)
        record = LifecycleRecord(
            operation_id=uuid4(), action=action, before=before, after=after
        )
        await conn.execute(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES ($1,'LIFECYCLE_CHANGED',$2,$3::jsonb,$4,$5,$6)""",
            str(record.operation_id),
            node_id,
            _payload(record),
            before.user_id,
            before.scope.value,
            now,
        )
        _checkpoint("journal")
