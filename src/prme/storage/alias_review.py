"""Owner-scoped review of durable unverified alias proposals."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from prme.models.derivation import node_checksum
from prme.models.edges import MemoryEdge
from prme.organizer.merge_policy import alias_pair_allowed
from prme.storage import _snapshot_json
from prme.storage._threading import run_to_completion
from prme.storage.alias_proposal import (
    AliasProposalJournalRecord,
    read_record as read_proposal_record,
)
from prme.types import EdgeType, LifecycleState, Scope

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore


AliasProposalDecision = Literal["accepted", "rejected"]
AliasProposalStatus = Literal["pending", "accepted", "rejected"]
_ACTIVE = (LifecycleState.TENTATIVE, LifecycleState.STABLE)
_ACCEPTED = "ALIAS_PROPOSAL_ACCEPTED"
_REJECTED = "ALIAS_PROPOSAL_REJECTED"
_REVIEW_TYPES = (_ACCEPTED, _REJECTED)


class AliasProposalReviewConflict(ValueError):
    """A proposal or deterministic review identity conflicts with durable state."""


class StaleAliasProposal(ValueError):
    """An accepted proposal no longer matches its assessed node snapshots."""


class AliasProposalReviewRecord(BaseModel):
    """Complete original proposal and one explicit reviewer decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    policy: Literal["explicit_alias_proposal_review_v1"] = (
        "explicit_alias_proposal_review_v1"
    )
    operation_id: UUID
    proposal_operation_id: UUID
    proposal_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_payload: str
    user_id: str
    scope: Scope
    decision: AliasProposalDecision
    reviewer_id: str
    reason: str | None = None
    decided_at: datetime
    verified_edge: MemoryEdge | None = None


class AliasProposalInboxItem(BaseModel):
    """One decoded proposal and its current review state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AliasProposalStatus
    proposal: AliasProposalJournalRecord
    review: AliasProposalReviewRecord | None = None


class AliasProposalReviewResult(BaseModel):
    """Outcome of an idempotent proposal decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_id: str
    proposal_operation_id: str
    decision: AliasProposalDecision
    applied: bool
    verified_edge_id: str | None


def _proposal_payload_text(payload: str | dict[str, Any]) -> str:
    value = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(value, dict):
        raise AliasProposalReviewConflict("Alias proposal journal is malformed")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _operation_id(proposal_operation_id: UUID) -> UUID:
    return uuid5(proposal_operation_id, "prme:alias-proposal-review:v1")


def _request(
    proposal_operation_id: str,
    *,
    user_id: str,
    decision: str,
    reviewer_id: str,
    reason: str | None,
) -> tuple[UUID, UUID, str, AliasProposalDecision, str, str | None]:
    proposal_id = UUID(proposal_operation_id)
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Alias proposal review requires an owner")
    if decision not in {"accepted", "rejected"}:
        raise ValueError("Alias proposal decision must be accepted or rejected")
    if (
        not isinstance(reviewer_id, str)
        or not reviewer_id.strip()
        or len(reviewer_id) > 256
    ):
        raise ValueError("Alias proposal review requires a bounded reviewer ID")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 2048):
        raise ValueError("Alias proposal review reason must be at most 2048 characters")
    if decision == "rejected" and (reason is None or not reason.strip()):
        raise ValueError("Rejected alias proposals require a reason")
    return (
        proposal_id,
        _operation_id(proposal_id),
        user_id,
        cast(AliasProposalDecision, decision),
        reviewer_id,
        reason,
    )


def _review_payload(record: AliasProposalReviewRecord) -> str:
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def _expected_edge(record: AliasProposalReviewRecord) -> MemoryEdge | None:
    if record.decision == "rejected":
        return None
    proposal = read_proposal_record(record.proposal_payload)
    return MemoryEdge(
        id=uuid5(record.operation_id, "verified-relates-to"),
        source_id=proposal.edge.source_id,
        target_id=proposal.edge.target_id,
        edge_type=EdgeType.RELATES_TO,
        user_id=record.user_id,
        confidence=proposal.score,
        valid_from=record.decided_at,
        metadata={
            "relation": "alias",
            "alias_type": proposal.alias_type,
            "identity_verified": True,
            "proposal_operation_id": str(record.proposal_operation_id),
            "review_operation_id": str(record.operation_id),
            "reviewer_id": record.reviewer_id,
        },
        created_at=record.decided_at,
    )


def read_review_record(
    payload: str | dict[str, Any],
) -> AliasProposalReviewRecord:
    """Validate and decode a checksummed alias review journal payload."""
    value = json.loads(payload) if isinstance(payload, str) else payload
    try:
        raw = value["record"]
        if hashlib.sha256(raw.encode()).hexdigest() != value["sha256"]:
            raise ValueError("Alias proposal review journal checksum mismatch")
        record = AliasProposalReviewRecord.model_validate(_snapshot_json.loads(raw))
        proposal = read_proposal_record(record.proposal_payload)
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("Malformed alias proposal review journal") from exc

    expected_operation = _operation_id(record.proposal_operation_id)
    proposal_payload_sha256 = hashlib.sha256(
        record.proposal_payload.encode()
    ).hexdigest()
    expected_edge = _expected_edge(record)
    if (
        record.operation_id != expected_operation
        or proposal.operation_id != record.proposal_operation_id
        or record.proposal_payload_sha256 != proposal_payload_sha256
        or record.user_id != proposal.left_before.user_id
        or record.user_id != proposal.right_before.user_id
        or record.scope != proposal.left_before.scope
        or record.scope != proposal.right_before.scope
        or not record.reviewer_id.strip()
        or len(record.reviewer_id) > 256
        or (record.reason is not None and len(record.reason) > 2048)
        or (
            record.decision == "rejected"
            and (record.reason is None or not record.reason.strip())
        )
        or record.decided_at.tzinfo is None
        or record.verified_edge != expected_edge
    ):
        raise ValueError("Alias proposal review journal identity mismatch")
    return record


def _result(
    record: AliasProposalReviewRecord, *, applied: bool
) -> AliasProposalReviewResult:
    return AliasProposalReviewResult(
        operation_id=str(record.operation_id),
        proposal_operation_id=str(record.proposal_operation_id),
        decision=record.decision,
        applied=applied,
        verified_edge_id=(
            str(record.verified_edge.id) if record.verified_edge is not None else None
        ),
    )


def _replayed(
    row: tuple[Any, Any, Any, Any, Any] | None,
    request: tuple[UUID, UUID, str, AliasProposalDecision, str, str | None],
) -> AliasProposalReviewResult | None:
    if row is None:
        return None
    proposal_id, operation_id, user_id, decision, reviewer_id, reason = request
    op_type, target_id, payload, actor_id, namespace_id = row
    try:
        record = read_review_record(payload)
    except (TypeError, ValueError) as exc:
        raise AliasProposalReviewConflict(
            "Alias proposal review identity is already used by another operation"
        ) from exc
    if (
        op_type != (_ACCEPTED if decision == "accepted" else _REJECTED)
        or record.operation_id != operation_id
        or record.proposal_operation_id != proposal_id
        or str(target_id) != str(proposal_id)
        or record.user_id != user_id
        or actor_id != user_id
        or namespace_id != record.scope.value
        or record.decision != decision
        or record.reviewer_id != reviewer_id
        or record.reason != reason
    ):
        raise AliasProposalReviewConflict(
            "Alias proposal already has a different review decision"
        )
    return _result(record, applied=False)


def _prepare(
    proposal_row: tuple[Any, Any, Any, Any, Any] | None,
    nodes: dict[str, Any],
    edges: list[MemoryEdge],
    request: tuple[UUID, UUID, str, AliasProposalDecision, str, str | None],
) -> AliasProposalReviewRecord:
    proposal_id, operation_id, user_id, decision, reviewer_id, reason = request
    if proposal_row is None:
        raise ValueError("Alias proposal is unavailable for this owner")
    op_type, target_id, proposal_payload, actor_id, namespace_id = proposal_row
    try:
        proposal_payload_text = _proposal_payload_text(proposal_payload)
        proposal = read_proposal_record(proposal_payload_text)
    except (TypeError, ValueError) as exc:
        raise AliasProposalReviewConflict("Alias proposal journal is invalid") from exc
    if (
        op_type != "ALIAS_PROPOSED"
        or proposal.operation_id != proposal_id
        or str(target_id) != str(proposal.right_before.id)
        or actor_id != user_id
        or namespace_id != proposal.left_before.scope.value
        or proposal.left_before.user_id != user_id
        or proposal.right_before.user_id != user_id
    ):
        raise ValueError("Alias proposal is unavailable for this owner")
    if proposal.edge not in edges:
        raise AliasProposalReviewConflict("Alias proposal edge differs from its journal")

    left_id = str(proposal.left_before.id)
    right_id = str(proposal.right_before.id)
    if decision == "accepted":
        if left_id not in nodes or right_id not in nodes:
            raise StaleAliasProposal("Alias proposal nodes are no longer available")
        left, right = nodes[left_id], nodes[right_id]
        if (
            left.lifecycle_state not in _ACTIVE
            or right.lifecycle_state not in _ACTIVE
            or node_checksum(left) != node_checksum(proposal.left_before)
            or node_checksum(right) != node_checksum(proposal.right_before)
            or not alias_pair_allowed(left, right)
        ):
            raise StaleAliasProposal(
                "Alias proposal nodes changed after assessment; assess the pair again"
            )

    now = datetime.now(timezone.utc)
    record = AliasProposalReviewRecord(
        operation_id=operation_id,
        proposal_operation_id=proposal_id,
        proposal_payload_sha256=hashlib.sha256(
            proposal_payload_text.encode()
        ).hexdigest(),
        proposal_payload=proposal_payload_text,
        user_id=user_id,
        scope=proposal.left_before.scope,
        decision=decision,
        reviewer_id=reviewer_id,
        reason=reason,
        decided_at=now,
    )
    return record.model_copy(update={"verified_edge": _expected_edge(record)})


def _checkpoint(stage: str) -> None:
    """Native transaction fault-injection boundary; no external work."""


def _inbox_item(
    proposal_payload: str | dict[str, Any],
    review_payload: str | dict[str, Any] | None,
) -> AliasProposalInboxItem:
    proposal = read_proposal_record(proposal_payload)
    if review_payload is None:
        return AliasProposalInboxItem(status="pending", proposal=proposal)
    review = read_review_record(review_payload)
    if review.proposal_operation_id != proposal.operation_id:
        raise AliasProposalReviewConflict("Alias proposal review targets another proposal")
    return AliasProposalInboxItem(
        status=review.decision,
        proposal=proposal,
        review=review,
    )


def _list_args(
    user_id: str,
    scope: Scope | str | None,
    status: AliasProposalStatus | str | None,
    limit: int,
) -> tuple[str, Scope | None, AliasProposalStatus | None, int]:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Alias proposal listing requires an owner")
    parsed_scope = Scope(scope) if scope is not None else None
    if status is not None and status not in {"pending", "accepted", "rejected"}:
        raise ValueError("Alias proposal status is invalid")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Alias proposal limit must be between 1 and 1000")
    return user_id, parsed_scope, cast(AliasProposalStatus | None, status), limit


async def review_duckdb(
    store: "DuckPGQGraphStore",
    proposal_operation_id: str,
    *,
    user_id: str,
    decision: str,
    reviewer_id: str,
    reason: str | None = None,
) -> AliasProposalReviewResult:
    request = _request(
        proposal_operation_id,
        user_id=user_id,
        decision=decision,
        reviewer_id=reviewer_id,
        reason=reason,
    )
    async with store._conn_lock:
        return await run_to_completion(_review_duckdb, store, request)


def _review_duckdb(
    store: "DuckPGQGraphStore",
    request: tuple[UUID, UUID, str, AliasProposalDecision, str, str | None],
) -> AliasProposalReviewResult:
    proposal_id, operation_id, user_id, decision, _, _ = request
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        saved = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=?",
            [str(operation_id)],
        ).fetchone()
        replayed = _replayed(saved, request)
        if replayed is not None:
            conn.execute("COMMIT")
            return replayed
        proposal_row = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=?",
            [str(proposal_id)],
        ).fetchone()
        proposal = (
            read_proposal_record(proposal_row[2]) if proposal_row is not None else None
        )
        ids = (
            [str(proposal.left_before.id), str(proposal.right_before.id)]
            if proposal is not None
            else []
        )
        nodes = {
            node_id: node
            for node_id in ids
            if (node := store._get_node_sync(node_id, True)) is not None
        }
        edges = store._get_edges_sync(
            None, None, ids or None, EdgeType.RELATES_TO, None, None
        )
        record = _prepare(proposal_row, nodes, edges, request)
        _checkpoint("validated")
        if record.verified_edge is not None:
            store._create_edge_sync(record.verified_edge)
        _checkpoint("edge")
        conn.execute(
            "INSERT INTO operations "
            "(id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                str(record.operation_id),
                _ACCEPTED if decision == "accepted" else _REJECTED,
                str(proposal_id),
                _review_payload(record),
                user_id,
                record.scope.value,
                record.decided_at,
            ],
        )
        _checkpoint("journal")
        conn.execute("COMMIT")
        return _result(record, applied=True)
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def list_duckdb(
    store: "DuckPGQGraphStore",
    *,
    user_id: str,
    scope: Scope | str | None = None,
    status: AliasProposalStatus | str | None = None,
    limit: int = 100,
) -> list[AliasProposalInboxItem]:
    args = _list_args(user_id, scope, status, limit)
    async with store._conn_lock:
        return await run_to_completion(_list_duckdb, store, args)


def _list_duckdb(store: "DuckPGQGraphStore", args) -> list[AliasProposalInboxItem]:
    user_id, scope, status, limit = args
    conditions = ["p.op_type='ALIAS_PROPOSED'", "p.actor_id=?"]
    values: list[Any] = [user_id]
    if scope is not None:
        conditions.append("p.namespace_id=?")
        values.append(scope.value)
    if status == "pending":
        conditions.append("r.id IS NULL")
    elif status == "accepted":
        conditions.append(f"r.op_type='{_ACCEPTED}'")
    elif status == "rejected":
        conditions.append(f"r.op_type='{_REJECTED}'")
    values.append(limit)
    rows = store._conn.execute(
        "SELECT p.id,p.payload AS proposal_payload,"
        "r.payload AS review_payload FROM operations p "
        "LEFT JOIN operations r ON r.target_id=p.id "
        f"AND r.op_type IN ('{_ACCEPTED}','{_REJECTED}') "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY p.created_at DESC,p.id DESC LIMIT ?",
        values,
    ).fetchall()
    if len({row[0] for row in rows}) != len(rows):
        raise AliasProposalReviewConflict("Alias proposal has multiple review records")
    return [_inbox_item(row[1], row[2]) for row in rows]


async def review_postgres(
    store: "PgGraphStore",
    proposal_operation_id: str,
    *,
    user_id: str,
    decision: str,
    reviewer_id: str,
    reason: str | None = None,
) -> AliasProposalReviewResult:
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    request = _request(
        proposal_operation_id,
        user_id=user_id,
        decision=decision,
        reviewer_id=reviewer_id,
        reason=reason,
    )
    proposal_id, operation_id, _, requested_decision, _, _ = request
    async with store._pool.acquire() as conn, conn.transaction():
        proposal_row = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=$1 FOR UPDATE",
            str(proposal_id),
        )
        saved = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE id=$1",
            str(operation_id),
        )
        replayed = _replayed(tuple(saved) if saved is not None else None, request)
        if replayed is not None:
            return replayed
        proposal = (
            read_proposal_record(proposal_row["payload"])
            if proposal_row is not None
            else None
        )
        ids = (
            sorted((str(proposal.left_before.id), str(proposal.right_before.id)))
            if proposal is not None
            else []
        )
        node_rows = await conn.fetch(
            f"SELECT {_NODE_COLUMNS} FROM nodes "
            "WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
            ids,
        )
        nodes = {str(row["id"]): store._record_to_node(row) for row in node_rows}
        edge_rows = await conn.fetch(
            "SELECT * FROM edges WHERE edge_type='relates_to' "
            "AND (source_id=ANY($1::uuid[]) OR target_id=ANY($1::uuid[])) "
            "ORDER BY id FOR UPDATE",
            ids,
        )
        edges = [store._record_to_edge(row) for row in edge_rows]
        record = _prepare(
            tuple(proposal_row) if proposal_row is not None else None,
            nodes,
            edges,
            request,
        )
        _checkpoint("validated")
        if record.verified_edge is not None:
            await store._create_edge_on_connection(conn, record.verified_edge)
        _checkpoint("edge")
        inserted = await conn.fetchval(
            "INSERT INTO operations "
            "(id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
            "VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            str(record.operation_id),
            _ACCEPTED if requested_decision == "accepted" else _REJECTED,
            str(proposal_id),
            _review_payload(record),
            user_id,
            record.scope.value,
            record.decided_at,
        )
        if inserted is None:
            raise AliasProposalReviewConflict(
                "Alias proposal review identity was committed concurrently"
            )
        _checkpoint("journal")
        return _result(record, applied=True)


async def list_postgres(
    store: "PgGraphStore",
    *,
    user_id: str,
    scope: Scope | str | None = None,
    status: AliasProposalStatus | str | None = None,
    limit: int = 100,
) -> list[AliasProposalInboxItem]:
    user_id, scope, status, limit = _list_args(user_id, scope, status, limit)
    conditions = ["p.op_type='ALIAS_PROPOSED'", "p.actor_id=$1"]
    values: list[Any] = [user_id]
    if scope is not None:
        values.append(scope.value)
        conditions.append(f"p.namespace_id=${len(values)}")
    if status == "pending":
        conditions.append("r.id IS NULL")
    elif status == "accepted":
        conditions.append(f"r.op_type='{_ACCEPTED}'")
    elif status == "rejected":
        conditions.append(f"r.op_type='{_REJECTED}'")
    values.append(limit)
    query = (
        "SELECT p.id,p.payload AS proposal_payload,"
        "r.payload AS review_payload FROM operations p "
        "LEFT JOIN operations r ON r.target_id=p.id "
        f"AND r.op_type IN ('{_ACCEPTED}','{_REJECTED}') "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY p.created_at DESC,p.id DESC LIMIT ${len(values)}"
    )
    async with store._pool.acquire() as conn:
        rows = await conn.fetch(query, *values)
    if len({row["id"] for row in rows}) != len(rows):
        raise AliasProposalReviewConflict("Alias proposal has multiple review records")
    return [
        _inbox_item(row["proposal_payload"], row["review_payload"])
        for row in rows
    ]


__all__ = [
    "AliasProposalDecision",
    "AliasProposalInboxItem",
    "AliasProposalReviewConflict",
    "AliasProposalReviewRecord",
    "AliasProposalReviewResult",
    "AliasProposalStatus",
    "StaleAliasProposal",
    "list_duckdb",
    "list_postgres",
    "read_review_record",
    "review_duckdb",
    "review_postgres",
]
