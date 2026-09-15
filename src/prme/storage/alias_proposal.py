"""Atomic, journaled publication of unverified entity-alias proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import struct
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.merge_policy import alias_pair_allowed
from prme.storage import _snapshot_json
from prme.storage._threading import run_to_completion
from prme.types import EdgeType, LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore


AliasType = Literal["abbreviation", "case_variation", "semantic"]
_ACTIVE = (LifecycleState.TENTATIVE, LifecycleState.STABLE)
_POLICY: Literal["unverified_alias_proposals_v1"] = (
    "unverified_alias_proposals_v1"
)


class AliasProposalConflict(ValueError):
    """A deterministic alias identity conflicts with durable state."""


class AliasProposalRecord(BaseModel):
    """Complete inputs and output for one accepted unverified alias proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    policy: Literal["unverified_alias_proposals_v1"] = _POLICY
    operation_id: UUID
    alias_type: AliasType
    score: float
    left_before: MemoryNode
    right_before: MemoryNode
    edge: MemoryEdge


@dataclass(frozen=True)
class AliasProposalResult:
    operation_id: str | None
    edge_id: str
    applied: bool


def _float32(value: float) -> float:
    """Match the REAL/FLOAT precision used by both edge tables."""
    return float(struct.unpack("!f", struct.pack("!f", value))[0])


def _request(
    a: str, b: str, user_id: str, alias_type: str, score: float
) -> tuple[list[str], UUID, str, AliasType, float]:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Alias proposal requires an owner")
    if alias_type not in {"abbreviation", "case_variation", "semantic"}:
        raise ValueError("Alias proposal requires a supported alias type")
    if (
        type(score) not in (int, float)
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        raise ValueError("Alias proposal requires a finite score in [0, 1]")
    ids = sorted((str(UUID(a)), str(UUID(b))))
    if ids[0] == ids[1]:
        raise ValueError("Alias proposal requires two distinct nodes")
    operation_id = uuid5(
        UUID(ids[0]), f"prme:unverified-alias-proposal:v1:{ids[1]}"
    )
    return (
        ids,
        operation_id,
        user_id,
        cast(AliasType, alias_type),
        _float32(float(score)),
    )


def _payload(record: AliasProposalRecord) -> str:
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def read_record(payload: str | dict[str, Any]) -> AliasProposalRecord:
    """Validate and decode a checksummed alias proposal journal payload."""
    value = json.loads(payload) if isinstance(payload, str) else payload
    try:
        raw = value["record"]
        if hashlib.sha256(raw.encode()).hexdigest() != value["sha256"]:
            raise ValueError("Alias proposal journal checksum mismatch")
        record = AliasProposalRecord.model_validate(_snapshot_json.loads(raw))
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("Malformed alias proposal journal") from exc

    left, right, edge = record.left_before, record.right_before, record.edge
    ids = sorted((str(left.id), str(right.id)))
    expected_operation = uuid5(
        UUID(ids[0]), f"prme:unverified-alias-proposal:v1:{ids[1]}"
    )
    expected_metadata = {
        "relation": "alias",
        "alias_type": record.alias_type,
        "identity_verified": False,
        "alias_operation_id": str(record.operation_id),
    }
    if (
        str(left.id) != ids[0]
        or str(right.id) != ids[1]
        or left.id == right.id
        or record.operation_id != expected_operation
        or left.node_type != NodeType.ENTITY
        or right.node_type != NodeType.ENTITY
        or left.user_id != right.user_id
        or left.scope != right.scope
        or left.lifecycle_state not in _ACTIVE
        or right.lifecycle_state not in _ACTIVE
        or edge.id != uuid5(record.operation_id, "relates-to")
        or edge.source_id != left.id
        or edge.target_id != right.id
        or edge.edge_type != EdgeType.RELATES_TO
        or edge.user_id != left.user_id
        or edge.confidence != record.score
        or edge.valid_to is not None
        or edge.provenance_event_id is not None
        or edge.metadata != expected_metadata
        or edge.valid_from != edge.created_at
    ):
        raise ValueError("Alias proposal journal identity mismatch")
    return record


def _replayed(
    row: tuple[Any, Any, Any, Any, Any] | None,
    *,
    ids: list[str],
    operation_id: UUID,
    user_id: str,
) -> AliasProposalResult | None:
    if row is None:
        return None
    op_type, target_id, payload, actor_id, namespace_id = row
    try:
        record = read_record(payload)
    except (ValueError, TypeError) as exc:
        raise AliasProposalConflict(
            "Alias proposal identity is already used by another operation"
        ) from exc
    if (
        op_type != "ALIAS_PROPOSED"
        or record.operation_id != operation_id
        or [str(record.left_before.id), str(record.right_before.id)] != ids
        or str(target_id) != ids[1]
        or actor_id != user_id
        or namespace_id != record.left_before.scope.value
    ):
        raise AliasProposalConflict(
            "Alias proposal identity is already used with different inputs"
        )
    return AliasProposalResult(str(record.operation_id), str(record.edge.id), False)


def _legacy_alias(
    edges: list[MemoryEdge], ids: list[str], user_id: str
) -> MemoryEdge | None:
    for edge in edges:
        metadata = edge.metadata or {}
        if (
            sorted((str(edge.source_id), str(edge.target_id))) == ids
            and edge.edge_type == EdgeType.RELATES_TO
            and edge.user_id == user_id
            and metadata.get("relation") == "alias"
            and metadata.get("identity_verified") is False
        ):
            return edge
    return None


def _prepare(
    nodes: dict[str, MemoryNode],
    edges: list[MemoryEdge],
    ids: list[str],
    operation_id: UUID,
    user_id: str,
    alias_type: AliasType,
    score: float,
) -> AliasProposalRecord | AliasProposalResult | None:
    if any(node_id not in nodes for node_id in ids):
        return None
    left, right = (nodes[node_id] for node_id in ids)
    if (
        left.user_id != user_id
        or right.user_id != user_id
        or left.lifecycle_state not in _ACTIVE
        or right.lifecycle_state not in _ACTIVE
        or not alias_pair_allowed(left, right)
    ):
        return None

    existing = _legacy_alias(edges, ids, user_id)
    expected_edge_id = uuid5(operation_id, "relates-to")
    if existing is not None:
        if existing.id == expected_edge_id:
            raise AliasProposalConflict(
                "Deterministic alias edge exists without its journal record"
            )
        # Older versions used random edge IDs and no journal. Preserve that
        # state without claiming it was atomically produced or duplicating it.
        return AliasProposalResult(None, str(existing.id), False)

    now = datetime.now(timezone.utc)
    edge = MemoryEdge(
        id=expected_edge_id,
        source_id=left.id,
        target_id=right.id,
        edge_type=EdgeType.RELATES_TO,
        user_id=user_id,
        confidence=score,
        valid_from=now,
        metadata={
            "relation": "alias",
            "alias_type": alias_type,
            "identity_verified": False,
            "alias_operation_id": str(operation_id),
        },
        created_at=now,
    )
    return AliasProposalRecord(
        operation_id=operation_id,
        alias_type=alias_type,
        score=score,
        left_before=left,
        right_before=right,
        edge=edge,
    )


def _checkpoint(stage: str) -> None:
    """Native transaction fault-injection boundary; no external work."""


async def propose_duckdb(
    store: "DuckPGQGraphStore",
    a: str,
    b: str,
    *,
    user_id: str,
    alias_type: str,
    score: float,
) -> AliasProposalResult | None:
    request = _request(a, b, user_id, alias_type, score)
    async with store._conn_lock:
        return await run_to_completion(_propose_duckdb, store, request)


def _propose_duckdb(
    store: "DuckPGQGraphStore",
    request: tuple[list[str], UUID, str, AliasType, float],
) -> AliasProposalResult | None:
    ids, operation_id, user_id, alias_type, score = request
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        saved = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=?",
            [str(operation_id)],
        ).fetchone()
        replayed = _replayed(
            saved, ids=ids, operation_id=operation_id, user_id=user_id
        )
        if replayed is not None:
            conn.execute("COMMIT")
            return replayed
        nodes = {node_id: store._get_node_sync(node_id, True) for node_id in ids}
        present_nodes = {
            node_id: node for node_id, node in nodes.items() if node is not None
        }
        edges = store._get_edges_sync(
            None, None, ids, EdgeType.RELATES_TO, None, None
        )
        prepared = _prepare(
            present_nodes, edges, ids, operation_id, user_id, alias_type, score
        )
        if prepared is None or isinstance(prepared, AliasProposalResult):
            conn.execute("COMMIT")
            return prepared
        _checkpoint("validated")
        store._create_edge_sync(prepared.edge)
        _checkpoint("edge")
        conn.execute(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES (?,'ALIAS_PROPOSED',?,?,?,?,?)""",
            [
                str(prepared.operation_id),
                ids[1],
                _payload(prepared),
                user_id,
                prepared.left_before.scope.value,
                prepared.edge.created_at,
            ],
        )
        _checkpoint("journal")
        conn.execute("COMMIT")
        return AliasProposalResult(
            str(prepared.operation_id), str(prepared.edge.id), True
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def propose_postgres(
    store: "PgGraphStore",
    a: str,
    b: str,
    *,
    user_id: str,
    alias_type: str,
    score: float,
) -> AliasProposalResult | None:
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    ids, operation_id, user_id, alias_type, score = _request(
        a, b, user_id, alias_type, score
    )
    async with store._pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            f"SELECT {_NODE_COLUMNS} FROM nodes "
            "WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
            ids,
        )
        saved = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=$1",
            str(operation_id),
        )
        replayed = _replayed(
            tuple(saved) if saved is not None else None,
            ids=ids,
            operation_id=operation_id,
            user_id=user_id,
        )
        if replayed is not None:
            return replayed
        nodes = {str(row["id"]): store._record_to_node(row) for row in rows}
        edge_rows = await conn.fetch(
            """SELECT * FROM edges
            WHERE edge_type='relates_to'
              AND ((source_id=$1::uuid AND target_id=$2::uuid)
                OR (source_id=$2::uuid AND target_id=$1::uuid))
            ORDER BY id FOR UPDATE""",
            ids[0],
            ids[1],
        )
        edges = [store._record_to_edge(row) for row in edge_rows]
        prepared = _prepare(
            nodes, edges, ids, operation_id, user_id, alias_type, score
        )
        if prepared is None or isinstance(prepared, AliasProposalResult):
            return prepared
        _checkpoint("validated")
        await store._create_edge_on_connection(conn, prepared.edge)
        _checkpoint("edge")
        inserted = await conn.fetchval(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES ($1,'ALIAS_PROPOSED',$2,$3::jsonb,$4,$5,$6)
            ON CONFLICT (id) DO NOTHING RETURNING id""",
            str(prepared.operation_id),
            ids[1],
            _payload(prepared),
            user_id,
            prepared.left_before.scope.value,
            prepared.edge.created_at,
        )
        if inserted is None:
            raise AliasProposalConflict(
                "Alias proposal identity is already used with different inputs"
            )
        _checkpoint("journal")
        return AliasProposalResult(
            str(prepared.operation_id), str(prepared.edge.id), True
        )
