"""Atomic confidence penalties for detected supersedence oscillations."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import struct
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.oscillation import OscillationResult, analyze_oscillation_chain
from prme.storage import _snapshot_json
from prme.storage._threading import run_to_completion
from prme.types import ACTIVE_LIFECYCLE_STATES, EdgeType, LifecycleState

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore


_POLICY: Literal["oscillation_subtractive_v1"] = "oscillation_subtractive_v1"
_MAX_CHAIN_NODES = 11


class OscillationPenaltyConflict(ValueError):
    """A node's deterministic oscillation identity conflicts with durable state."""


class OscillationPenaltyRecord(BaseModel):
    """Complete locked inputs and output for one oscillation penalty."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    policy: Literal["oscillation_subtractive_v1"] = _POLICY
    operation_id: UUID
    topic: str
    cycle_count: int
    delta: float
    chain_before: tuple[MemoryNode, ...]
    chain_edges: tuple[MemoryEdge, ...]
    after: MemoryNode


def _float32(value: float) -> float:
    """Match the FLOAT/REAL precision used by both node tables."""
    return float(struct.unpack("!f", struct.pack("!f", value))[0])


def _request(
    node_id: str, chain_node_ids: list[str], user_id: str
) -> tuple[list[str], UUID, str]:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Oscillation penalty requires an owner")
    target = str(UUID(node_id))
    ids = [str(UUID(value)) for value in chain_node_ids]
    if (
        len(ids) < 3
        or len(ids) > _MAX_CHAIN_NODES
        or ids[0] != target
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Oscillation penalty requires one bounded supersedence chain")
    operation_id = uuid5(UUID(target), "prme:oscillation-penalty:v1")
    return ids, operation_id, user_id


def _payload(record: OscillationPenaltyRecord) -> str:
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def _chain_is_valid(
    nodes: list[MemoryNode], edges: list[MemoryEdge], user_id: str
) -> bool:
    if len(nodes) < 3 or len(edges) != len(nodes) - 1:
        return False
    owner_scope = (user_id, nodes[0].scope)
    if (
        nodes[0].user_id != user_id
        or nodes[0].lifecycle_state not in ACTIVE_LIFECYCLE_STATES
        or any(
            (node.user_id, node.scope) != owner_scope
            or node.lifecycle_state != LifecycleState.SUPERSEDED
            for node in nodes[1:]
        )
    ):
        return False
    return all(
        edge.source_id == nodes[index].id
        and edge.target_id == nodes[index + 1].id
        and edge.edge_type == EdgeType.SUPERSEDES
        and edge.user_id == user_id
        for index, edge in enumerate(edges)
    )


def _analysis(nodes: list[MemoryNode]) -> OscillationResult | None:
    result = analyze_oscillation_chain(nodes)
    if result is None or result.oscillating_node_ids != [str(node.id) for node in nodes]:
        return None
    return result


def read_record(
    payload: str | dict[str, Any],
) -> OscillationPenaltyRecord:
    """Validate and decode one checksummed oscillation penalty record."""
    value = json.loads(payload) if isinstance(payload, str) else payload
    try:
        raw = value["record"]
        if hashlib.sha256(raw.encode()).hexdigest() != value["sha256"]:
            raise ValueError("Oscillation penalty journal checksum mismatch")
        record = OscillationPenaltyRecord.model_validate(
            _snapshot_json.loads(raw)
        )
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("Malformed oscillation penalty journal") from exc

    nodes = list(record.chain_before)
    edges = list(record.chain_edges)
    result = _analysis(nodes)
    expected_operation = (
        uuid5(nodes[0].id, "prme:oscillation-penalty:v1") if nodes else None
    )
    expected_delta = (
        _float32(result.confidence_penalty) if result is not None else None
    )
    expected_confidence = (
        _float32(max(0.0, nodes[0].confidence_base - expected_delta))
        if nodes and expected_delta is not None
        else None
    )
    changed = {"confidence_base", "updated_at"}
    if (
        not nodes
        or not _chain_is_valid(nodes, edges, nodes[0].user_id)
        or result is None
        or record.operation_id != expected_operation
        or record.topic != result.topic
        or record.cycle_count != result.cycle_count
        or not math.isfinite(record.delta)
        or record.delta != expected_delta
        or record.after.id != nodes[0].id
        or record.after.user_id != nodes[0].user_id
        or record.after.scope != nodes[0].scope
        or record.after.confidence_base != expected_confidence
        or _snapshot_json.dumps(
            nodes[0].model_dump(exclude=changed), sort_keys=True
        )
        != _snapshot_json.dumps(
            record.after.model_dump(exclude=changed), sort_keys=True
        )
    ):
        raise ValueError("Oscillation penalty journal identity mismatch")
    return record


def _prepare(
    nodes: list[MemoryNode],
    edges: list[MemoryEdge],
    operation_id: UUID,
    user_id: str,
    changed_at: datetime,
) -> OscillationPenaltyRecord | None:
    if not _chain_is_valid(nodes, edges, user_id):
        return None
    result = _analysis(nodes)
    if result is None:
        return None
    delta = _float32(result.confidence_penalty)
    confidence = _float32(max(0.0, nodes[0].confidence_base - delta))
    if confidence == nodes[0].confidence_base:
        return None
    after = nodes[0].model_copy(
        deep=True,
        update={"confidence_base": confidence, "updated_at": changed_at},
    )
    return OscillationPenaltyRecord(
        operation_id=operation_id,
        topic=result.topic,
        cycle_count=result.cycle_count,
        delta=delta,
        chain_before=tuple(nodes),
        chain_edges=tuple(edges),
        after=after,
    )


def _replayed(
    row: tuple[Any, Any, Any, Any, Any] | None,
    *,
    ids: list[str],
    operation_id: UUID,
    user_id: str,
    nodes: list[MemoryNode],
    edges: list[MemoryEdge],
) -> bool:
    if row is None:
        return False
    op_type, target_id, payload, actor_id, namespace_id = row
    try:
        record = read_record(payload)
    except (TypeError, ValueError) as exc:
        raise OscillationPenaltyConflict(
            "Oscillation penalty identity is already used by another operation"
        ) from exc
    if (
        op_type != "PENALTY"
        or record.operation_id != operation_id
        or [str(node.id) for node in record.chain_before] != ids
        or str(target_id) != ids[0]
        or actor_id != user_id
        or namespace_id != record.chain_before[0].scope.value
        or not nodes
        or nodes[0] != record.after
        or tuple(nodes[1:]) != record.chain_before[1:]
        or tuple(edges) != record.chain_edges
    ):
        raise OscillationPenaltyConflict(
            "Oscillation penalty identity conflicts with current graph state"
        )
    return True


def _checkpoint(stage: str) -> None:
    """Native transaction fault-injection boundary; no external work."""


def _duckdb_inputs(
    store: "DuckPGQGraphStore", ids: list[str], user_id: str
) -> tuple[list[MemoryNode], list[MemoryEdge]]:
    nodes = [store._get_node_sync(node_id, True) for node_id in ids]
    if any(node is None for node in nodes):
        return [], []
    edges: list[MemoryEdge] = []
    for source_id, target_id in zip(ids, ids[1:]):
        row = store._conn.execute(
            "SELECT * FROM edges WHERE source_id=? AND target_id=? "
            "AND edge_type='supersedes' AND user_id=? ORDER BY id LIMIT 1",
            [source_id, target_id, user_id],
        ).fetchone()
        if row is None:
            return [node for node in nodes if node is not None], []
        edges.append(store._row_to_edge(row))
    return [node for node in nodes if node is not None], edges


async def apply_duckdb(
    store: "DuckPGQGraphStore",
    node_id: str,
    chain_node_ids: list[str],
    *,
    user_id: str,
) -> bool:
    request = _request(node_id, chain_node_ids, user_id)
    async with store._conn_lock:
        return await run_to_completion(_apply_duckdb, store, request)


def _apply_duckdb(
    store: "DuckPGQGraphStore", request: tuple[list[str], UUID, str]
) -> bool:
    ids, operation_id, user_id = request
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        nodes, edges = _duckdb_inputs(store, ids, user_id)
        saved = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=?",
            [str(operation_id)],
        ).fetchone()
        if _replayed(
            saved,
            ids=ids,
            operation_id=operation_id,
            user_id=user_id,
            nodes=nodes,
            edges=edges,
        ):
            conn.execute("COMMIT")
            return False
        changed_at = datetime.now(timezone.utc)
        record = _prepare(nodes, edges, operation_id, user_id, changed_at)
        if record is None:
            conn.execute("COMMIT")
            return False
        _checkpoint("validated")
        conn.execute(
            "UPDATE nodes SET confidence_base=?, updated_at=? WHERE id=?",
            [record.after.confidence_base, changed_at, ids[0]],
        )
        _checkpoint("updated")
        after = store._get_node_sync(ids[0], True)
        if after != record.after:
            raise RuntimeError("Oscillation penalty storage result changed")
        payload = _payload(record)
        read_record(payload)
        conn.execute(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES (?,'PENALTY',?,?,?,?,?)""",
            [
                str(operation_id),
                ids[0],
                payload,
                user_id,
                record.chain_before[0].scope.value,
                changed_at,
            ],
        )
        _checkpoint("journal")
        conn.execute("COMMIT")
        return True
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def _postgres_inputs(
    store: "PgGraphStore", conn: Any, ids: list[str], user_id: str
) -> tuple[list[MemoryNode], list[MemoryEdge]]:
    from prme.storage.pg.graph_store import _EDGE_COLUMNS, _NODE_COLUMNS

    rows = await conn.fetch(
        f"SELECT {_NODE_COLUMNS} FROM nodes "
        "WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
        ids,
    )
    by_id = {str(row["id"]): store._record_to_node(row) for row in rows}
    if any(node_id not in by_id for node_id in ids):
        return [], []
    edges: list[MemoryEdge] = []
    for source_id, target_id in zip(ids, ids[1:]):
        row = await conn.fetchrow(
            f"SELECT {_EDGE_COLUMNS} FROM edges "
            "WHERE source_id=$1::uuid AND target_id=$2::uuid "
            "AND edge_type='supersedes' AND user_id=$3 ORDER BY id LIMIT 1",
            source_id,
            target_id,
            user_id,
        )
        if row is None:
            return [by_id[node_id] for node_id in ids], []
        edges.append(store._record_to_edge(row))
    return [by_id[node_id] for node_id in ids], edges


async def apply_postgres(
    store: "PgGraphStore",
    node_id: str,
    chain_node_ids: list[str],
    *,
    user_id: str,
) -> bool:
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    ids, operation_id, user_id = _request(node_id, chain_node_ids, user_id)
    async with store._pool.acquire() as conn, conn.transaction():
        nodes, edges = await _postgres_inputs(store, conn, ids, user_id)
        saved = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=$1",
            str(operation_id),
        )
        if _replayed(
            tuple(saved) if saved is not None else None,
            ids=ids,
            operation_id=operation_id,
            user_id=user_id,
            nodes=nodes,
            edges=edges,
        ):
            return False
        changed_at = datetime.now(timezone.utc)
        record = _prepare(nodes, edges, operation_id, user_id, changed_at)
        if record is None:
            return False
        _checkpoint("validated")
        row = await conn.fetchrow(
            f"UPDATE nodes SET confidence_base=$1, updated_at=$2 "
            f"WHERE id=$3 RETURNING {_NODE_COLUMNS}",
            record.after.confidence_base,
            changed_at,
            ids[0],
        )
        _checkpoint("updated")
        after = store._record_to_node(row)
        if after != record.after:
            raise RuntimeError("Oscillation penalty storage result changed")
        payload = _payload(record)
        read_record(payload)
        inserted = await conn.fetchval(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES ($1,'PENALTY',$2,$3::jsonb,$4,$5,$6)
            ON CONFLICT (id) DO NOTHING RETURNING id""",
            str(operation_id),
            ids[0],
            payload,
            user_id,
            record.chain_before[0].scope.value,
            changed_at,
        )
        if inserted is None:
            raise OscillationPenaltyConflict(
                "Oscillation penalty identity is already used by another operation"
            )
        _checkpoint("journal")
        return True
