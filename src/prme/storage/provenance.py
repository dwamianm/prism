"""Portable operation-log paging for public node provenance queries."""

from __future__ import annotations

import base64
from datetime import datetime
import json

from prme.models.provenance import OperationAuditRecord
from prme.storage._threading import run_to_completion


def _cursor(created_at: datetime, operation_id: str) -> str:
    raw = json.dumps(
        [created_at.isoformat(), operation_id], separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("operation_cursor must be a non-empty provenance cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(decoded, list) or len(decoded) != 2:
            raise ValueError
        created_at = datetime.fromisoformat(decoded[0])
        operation_id = decoded[1]
        if created_at.tzinfo is None or not isinstance(operation_id, str) or not operation_id:
            raise ValueError
        return created_at, operation_id
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("Invalid operation_cursor") from None


def _payload(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _records(rows, limit: int):
    records = tuple(OperationAuditRecord(
        id=str(row[0]), op_type=row[1], target_id=row[2],
        payload=_payload(row[3]), actor_id=row[4], namespace_id=row[5],
        created_at=row[6],
    ) for row in rows[:limit])
    next_cursor = None
    if len(rows) > limit and records:
        last = records[-1]
        next_cursor = _cursor(last.created_at, last.id)
    return records, next_cursor


async def node_operations(store, node_id: str, *, cursor: str | None, limit: int):
    """Read one stable chronological page of operations targeting a node."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("operation_limit must be an integer between 1 and 1000")
    position = _decode_cursor(cursor)
    if hasattr(store, "_pool"):
        conditions = ["target_id=$1"]
        params = [node_id]
        if position is not None:
            params.extend(position)
            conditions.append("(created_at > $2 OR (created_at = $2 AND id > $3))")
        params.append(limit + 1)
        query = (
            "SELECT id,op_type,target_id,payload,actor_id,namespace_id,created_at "
            "FROM operations WHERE " + " AND ".join(conditions)
            + f" ORDER BY created_at,id LIMIT ${len(params)}"
        )
        async with store._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return _records([tuple(row) for row in rows], limit)

    async with store._conn_lock:
        return await run_to_completion(
            _node_operations_duckdb, store, node_id, position, limit
        )


def _node_operations_duckdb(store, node_id, position, limit):
    conditions = ["target_id=?"]
    params = [node_id]
    if position is not None:
        conditions.append("(created_at > ? OR (created_at = ? AND id > ?))")
        params.extend([position[0], position[0], position[1]])
    params.append(limit + 1)
    rows = store._conn.execute(
        "SELECT id,op_type,target_id,payload,actor_id,namespace_id,created_at "
        "FROM operations WHERE " + " AND ".join(conditions)
        + " ORDER BY created_at,id LIMIT ?",
        params,
    ).fetchall()
    return _records(rows, limit)
