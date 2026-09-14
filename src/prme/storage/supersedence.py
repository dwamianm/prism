"""Atomic, auditable, retry-safe explicit memory replacement."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.storage import _snapshot_json
from prme.types import EdgeType, LifecycleState, validate_transition


class SupersedenceRecord(BaseModel):
    """Complete inputs and outputs for one explicit correction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    policy: Literal["explicit_supersedence_v1"] = "explicit_supersedence_v1"
    operation_id: UUID
    actor_id: str
    evidence_id: UUID | None
    before: MemoryNode
    replacement: MemoryNode
    after: MemoryNode
    edge: MemoryEdge


def _request(old_id, new_id, evidence_id, actor_id):
    old_id, new_id = str(UUID(old_id)), str(UUID(new_id))
    if old_id == new_id:
        raise ValueError("A node cannot supersede itself")
    from prme.storage.transition_evidence import _identity

    evidence = _identity(evidence_id)
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("actor_id must be a non-empty string")
    actor = actor_id.strip()
    operation_id = uuid5(UUID(old_id), f"prme:explicit-supersedence:v1:{new_id}")
    return old_id, new_id, evidence, actor, operation_id


def _payload(record: SupersedenceRecord) -> str:
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps({"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()})


def read_record(value) -> SupersedenceRecord:
    """Validate a saved record before treating it as retry evidence."""
    value = json.loads(value) if isinstance(value, str) else value
    raw = value["record"]
    if hashlib.sha256(raw.encode()).hexdigest() != value["sha256"]:
        raise ValueError("Supersedence journal checksum mismatch")
    record = SupersedenceRecord.model_validate(_snapshot_json.loads(raw))
    before, replacement, after, edge = (
        record.before,
        record.replacement,
        record.after,
        record.edge,
    )
    if (
        before.id == replacement.id
        or before.id != after.id
        or (before.user_id, before.scope) != (replacement.user_id, replacement.scope)
        or (before.user_id, before.scope) != (after.user_id, after.scope)
        or before.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE)
        or replacement.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE)
        or after.lifecycle_state != LifecycleState.SUPERSEDED
        or after.superseded_by != replacement.id
        or edge.source_id != replacement.id
        or edge.target_id != before.id
        or edge.edge_type != EdgeType.SUPERSEDES
        or edge.user_id != before.user_id
        or edge.provenance_event_id != record.evidence_id
    ):
        raise ValueError("Supersedence journal identity mismatch")
    changed = {"lifecycle_state", "superseded_by", "updated_at"}
    if _snapshot_json.dumps(
        before.model_dump(exclude=changed), sort_keys=True
    ) != _snapshot_json.dumps(after.model_dump(exclude=changed), sort_keys=True):
        raise ValueError("Supersedence journal changes unrelated node fields")
    if not validate_transition(before.lifecycle_state, after.lifecycle_state):
        raise ValueError("Supersedence journal contains an invalid transition")
    return record


def _prepare(before, replacement, evidence, actor, operation_id, at):
    if before is None or replacement is None:
        raise ValueError("Both replacement nodes must exist")
    if (before.user_id, before.scope) != (replacement.user_id, replacement.scope):
        raise ValueError("Replacement nodes must have the same user and scope")
    if replacement.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
        raise ValueError("Replacement node must be active")
    if not validate_transition(before.lifecycle_state, LifecycleState.SUPERSEDED):
        raise ValueError(
            f"Cannot supersede: node is {before.lifecycle_state.value}, "
            "only Tentative or Stable nodes can be superseded"
        )
    after = before.model_copy(deep=True, update={
        "lifecycle_state": LifecycleState.SUPERSEDED,
        "superseded_by": replacement.id,
        "updated_at": at,
    })
    edge = MemoryEdge(
        id=uuid5(operation_id, "edge"),
        source_id=replacement.id,
        target_id=before.id,
        edge_type=EdgeType.SUPERSEDES,
        user_id=before.user_id,
        confidence=1.0,
        provenance_event_id=evidence,
        valid_from=at,
        created_at=at,
    )
    return SupersedenceRecord(
        operation_id=operation_id,
        actor_id=actor,
        evidence_id=evidence,
        before=before,
        replacement=replacement,
        after=after,
        edge=edge,
    )


def _validate_replay(record, *, old_id, new_id, evidence, actor, current, edge):
    if (
        str(record.before.id) != old_id
        or str(record.replacement.id) != new_id
        or record.evidence_id != evidence
        or record.actor_id != actor
    ):
        raise ValueError("Supersedence already exists with different inputs")
    if (
        current is None
        or current.lifecycle_state != LifecycleState.SUPERSEDED
        or current.superseded_by != record.replacement.id
        or edge != record.edge
    ):
        raise ValueError("Supersedence journal does not match current graph state")


def _checkpoint(stage):
    """Native transaction fault-injection boundary; no external work."""


def _supersede_many_duckdb(store, requests):
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        for old_id, new_id, evidence, actor, operation_id in requests:
            saved = conn.execute(
                "SELECT payload FROM operations WHERE id=? AND op_type='SUPERSEDENCE_APPLIED'",
                [str(operation_id)],
            ).fetchone()
            if saved is not None:
                record = read_record(saved[0])
                edge_row = conn.execute("SELECT * FROM edges WHERE id=?", [str(record.edge.id)]).fetchone()
                edge = store._row_to_edge(edge_row) if edge_row is not None else None
                _validate_replay(
                    record, old_id=old_id, new_id=new_id, evidence=evidence,
                    actor=actor, current=store._get_node_sync(old_id, True), edge=edge,
                )
                continue
            before = store._get_node_sync(old_id, True)
            replacement = store._get_node_sync(new_id, True)
            if before is None or replacement is None:
                raise ValueError("Both replacement nodes must exist")
            from prme.storage.transition_evidence import validate_duckdb

            validated = validate_duckdb(conn, str(evidence) if evidence else None, before.user_id, before.scope.value)
            record = _prepare(
                before, replacement, validated, actor, operation_id,
                datetime.now(timezone.utc),
            )
            _checkpoint("validated")
            conn.execute(
                "UPDATE nodes SET lifecycle_state=?,superseded_by=?,updated_at=? WHERE id=?",
                [LifecycleState.SUPERSEDED.value, new_id, record.after.updated_at, old_id],
            )
            store._create_edge_sync(record.edge)
            _checkpoint("mutated")
            conn.execute(
                """INSERT INTO operations
                (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
                VALUES (?,'SUPERSEDENCE_APPLIED',?,?,?,?,?)""",
                [str(operation_id), old_id, _payload(record), actor,
                 before.scope.value, record.after.updated_at],
            )
            _checkpoint("journaled")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def supersede_many_postgres(store, replacements, *, actor_id="system"):
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    requests = [_request(old, new, evidence, actor_id) for old, new, evidence in replacements]
    ids = sorted({node_id for request in requests for node_id in request[:2]})
    async with store._pool.acquire() as conn, conn.transaction():
        await conn.fetch(
            "SELECT id FROM nodes WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", ids,
        )
        for old_id, new_id, evidence, actor, operation_id in requests:
            saved = await conn.fetchval(
                "SELECT payload FROM operations WHERE id=$1 AND op_type='SUPERSEDENCE_APPLIED'",
                str(operation_id),
            )
            if saved is not None:
                record = read_record(saved)
                current_row = await conn.fetchrow(
                    f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=$1", old_id,
                )
                edge_row = await conn.fetchrow("SELECT * FROM edges WHERE id=$1", str(record.edge.id))
                _validate_replay(
                    record, old_id=old_id, new_id=new_id, evidence=evidence,
                    actor=actor,
                    current=store._record_to_node(current_row) if current_row else None,
                    edge=store._record_to_edge(edge_row) if edge_row else None,
                )
                continue
            rows = await conn.fetch(
                f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=ANY($1::uuid[])",
                [old_id, new_id],
            )
            nodes = {str(row["id"]): store._record_to_node(row) for row in rows}
            before, replacement = nodes.get(old_id), nodes.get(new_id)
            if before is None or replacement is None:
                raise ValueError("Both replacement nodes must exist")
            from prme.storage.transition_evidence import validate_postgres

            validated = await validate_postgres(
                conn, str(evidence) if evidence else None, before.user_id, before.scope.value,
            )
            record = _prepare(
                before, replacement, validated, actor, operation_id,
                datetime.now(timezone.utc),
            )
            _checkpoint("validated")
            await conn.execute(
                "UPDATE nodes SET lifecycle_state=$1,superseded_by=$2::uuid,updated_at=$3 WHERE id=$4",
                LifecycleState.SUPERSEDED.value, new_id, record.after.updated_at, old_id,
            )
            await store._create_edge_on_connection(conn, record.edge)
            _checkpoint("mutated")
            await conn.execute(
                """INSERT INTO operations
                (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
                VALUES ($1,'SUPERSEDENCE_APPLIED',$2,$3::jsonb,$4,$5,$6)""",
                str(operation_id), old_id, _payload(record), actor,
                before.scope.value, record.after.updated_at,
            )
            _checkpoint("journaled")
