"""Atomic reinforcement and complete mutation records for both graph backends."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict

from prme.models.nodes import MemoryNode
from prme.storage._threading import run_to_completion
from prme.storage import _snapshot_json

EVIDENCE_ERROR = "Evidence event not found in the node's owner and scope"


class ReinforcementRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1, 2] = 2
    policy: Literal["additive_caps_v1"] = "additive_caps_v1"
    operation_id: UUID
    request_id: UUID | None = None
    evidence_id: UUID | None
    before: MemoryNode
    after: MemoryNode


def _validate_node(node, node_id, user_id):
    if node is None or (user_id is not None and node.user_id != user_id):
        raise ValueError(f"Node {node_id!r} not found")


def _evidence_id(evidence_id):
    if evidence_id is None:
        return None
    try:
        return UUID(evidence_id)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(EVIDENCE_ERROR) from None


class ReinforcementConflict(ValueError):
    """A caller's retry identity is already bound to another confirmation."""


def _request_id(value):
    if value is None:
        return None
    if not isinstance(value, (str, UUID)):
        raise ValueError("request_id must be a UUID")
    try:
        return UUID(str(value))
    except ValueError:
        raise ValueError("request_id must be a UUID") from None


def _operation_id(owner, request_id):
    if request_id is None:
        return uuid4()
    return uuid5(
        NAMESPACE_URL,
        json.dumps(
            ["prme:reinforcement:v2", owner, str(request_id)], separators=(",", ":")
        ),
    )


def _replayed(row, operation_id, request_id, node, evidence):
    if row is None:
        return False
    kind, payload = row
    if kind != "REINFORCE":
        raise ReinforcementConflict("request_id is already bound to another operation")
    record = read_record(payload)
    if (
        record.operation_id != operation_id
        or record.request_id != request_id
        or record.before.id != node.id
        or record.before.user_id != node.user_id
        or record.before.scope != node.scope
        or record.evidence_id != evidence
    ):
        raise ReinforcementConflict(
            "request_id is already bound to a different reinforcement"
        )
    return True


def _values(node, evidence):
    refs = list(node.evidence_refs)
    if evidence is not None:
        refs.append(evidence)
    return (
        max(node.reinforcement_boost, min(node.reinforcement_boost + 0.15, 0.5)),
        max(node.confidence_base, min(node.confidence_base + 0.05, 0.95)),
        datetime.now(timezone.utc),
        json.dumps([str(ref) for ref in refs]),
    )


def _payload(record):
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def read_record(payload):
    """Validate retained record bytes; does not apply a historical graph replay."""
    value = json.loads(payload) if isinstance(payload, str) else payload
    if hashlib.sha256(value["record"].encode()).hexdigest() != value["sha256"]:
        raise ValueError("Reinforcement journal checksum mismatch")
    record = ReinforcementRecord.model_validate(_snapshot_json.loads(value["record"]))
    if (
        record.before.id != record.after.id
        or record.before.user_id != record.after.user_id
        or record.before.scope != record.after.scope
    ):
        raise ValueError("Reinforcement journal identity mismatch")
    if record.version == 1 and record.request_id is not None:
        raise ValueError("Legacy reinforcement journal cannot contain a request_id")
    if record.request_id is not None and record.operation_id != _operation_id(
        record.before.user_id, record.request_id
    ):
        raise ValueError("Reinforcement journal request identity mismatch")
    return record


def _checkpoint(stage):
    """Transaction fault-injection point, with no external work."""


async def reinforce_duckdb(store, node_id, *, user_id, evidence_id, request_id=None):
    request_id = _request_id(request_id)
    async with store._conn_lock:
        await run_to_completion(
            _reinforce_duckdb, store, node_id, user_id, evidence_id, request_id
        )


def _reinforce_duckdb(store, node_id, user_id, evidence_id, request_id):
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        before = store._get_node_sync(node_id, True)
        _validate_node(before, node_id, user_id)
        evidence = _evidence_id(evidence_id)
        operation = _operation_id(before.user_id, request_id)
        row = conn.execute(
            "SELECT op_type,payload FROM operations WHERE id=?", [str(operation)]
        ).fetchone()
        if _replayed(row, operation, request_id, before, evidence):
            conn.execute("COMMIT")
            return
        if (
            evidence is not None
            and conn.execute(
                "SELECT id FROM events WHERE id=? AND user_id=? AND scope=?",
                [str(evidence), before.user_id, before.scope.value],
            ).fetchone()
            is None
        ):
            raise ValueError(EVIDENCE_ERROR)
        _checkpoint("validated")
        boost, confidence, now, refs = _values(before, evidence)
        conn.execute(
            """UPDATE nodes SET reinforcement_boost=?, confidence_base=?,
            last_reinforced_at=?, evidence_refs=?, updated_at=? WHERE id=?""",
            [boost, confidence, now, refs, now, node_id],
        )
        _checkpoint("updated")
        after = store._get_node_sync(node_id, True)
        record = ReinforcementRecord(
            operation_id=operation,
            request_id=request_id,
            evidence_id=evidence,
            before=before,
            after=after,
        )
        conn.execute(
            """INSERT INTO operations
            (id, op_type, target_id, payload, actor_id, namespace_id, created_at)
            VALUES (?, 'REINFORCE', ?, ?, ?, ?, ?)""",
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


async def reinforce_postgres(store, node_id, *, user_id, evidence_id, request_id=None):
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    request_id = _request_id(request_id)
    async with store._pool.acquire() as conn, conn.transaction():
        # Read after acquiring the row lock: independent callers cannot compute
        # increments or evidence lists from the same obsolete snapshot.
        row = await conn.fetchrow(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=$1 "
            "AND ($2::text IS NULL OR user_id=$2) FOR UPDATE",
            node_id,
            user_id,
        )
        before = store._record_to_node(row) if row is not None else None
        _validate_node(before, node_id, user_id)
        evidence = _evidence_id(evidence_id)
        operation = _operation_id(before.user_id, request_id)
        existing = await conn.fetchrow(
            "SELECT op_type,payload FROM operations WHERE id=$1", str(operation)
        )
        if _replayed(
            tuple(existing) if existing else None,
            operation,
            request_id,
            before,
            evidence,
        ):
            return
        if (
            evidence is not None
            and await conn.fetchval(
                "SELECT id FROM events WHERE id=$1 AND user_id=$2 AND scope=$3",
                str(evidence),
                before.user_id,
                before.scope.value,
            )
            is None
        ):
            raise ValueError(EVIDENCE_ERROR)
        _checkpoint("validated")
        boost, confidence, now, refs = _values(before, evidence)
        row = await conn.fetchrow(
            f"""UPDATE nodes SET reinforcement_boost=$1,
            confidence_base=$2, last_reinforced_at=$3, evidence_refs=$4::jsonb,
            updated_at=$3 WHERE id=$5 RETURNING {_NODE_COLUMNS}""",
            boost,
            confidence,
            now,
            refs,
            node_id,
        )
        _checkpoint("updated")
        after = store._record_to_node(row)
        record = ReinforcementRecord(
            operation_id=operation,
            request_id=request_id,
            evidence_id=evidence,
            before=before,
            after=after,
        )
        inserted = await conn.fetchval(
            """INSERT INTO operations
            (id, op_type, target_id, payload, actor_id, namespace_id, created_at)
            VALUES ($1, 'REINFORCE', $2, $3::jsonb, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING RETURNING id""",
            str(record.operation_id),
            node_id,
            _payload(record),
            before.user_id,
            before.scope.value,
            now,
        )
        if inserted is None:
            # A simultaneous changed-node request may have won the same key
            # while holding a different row lock. Roll back this entire change.
            raise ReinforcementConflict(
                "request_id is already bound to a different reinforcement"
            )
        _checkpoint("journal")
