"""Atomic, retry-safe condition evaluation for both graph backends."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict

from prme.models.nodes import MemoryNode
from prme.storage import _snapshot_json
from prme.storage._threading import run_to_completion
from prme.types import ConditionEvaluationMethod, ConditionState, EpistemicType


EVIDENCE_ERROR = "Evidence event not found in the node's owner and scope"
_METHODS = frozenset(ConditionEvaluationMethod)


class ConditionEvaluationConflict(ValueError):
    """A retry identity is already bound to another condition evaluation."""


class ConditionEvaluationRecord(BaseModel):
    """Complete immutable record of one explicit condition evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    policy: Literal["explicit_condition_evaluation_v1"] = (
        "explicit_condition_evaluation_v1"
    )
    operation_id: UUID
    request_id: UUID | None = None
    condition: str
    from_state: ConditionState
    to_state: ConditionState
    evaluated_at: AwareDatetime
    evaluation_method: ConditionEvaluationMethod
    reason: str | None = None
    evidence_id: UUID | None = None
    triggered_by: str
    before: MemoryNode
    after: MemoryNode


def _request_id(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, (str, UUID)):
        raise ValueError("request_id must be a UUID")
    try:
        return UUID(str(value))
    except ValueError:
        raise ValueError("request_id must be a UUID") from None


def _operation_id(owner: str, request_id: UUID | None) -> UUID:
    if request_id is None:
        return uuid4()
    return uuid5(
        NAMESPACE_URL,
        json.dumps(
            ["prme:condition-evaluation:v1", owner, str(request_id)],
            separators=(",", ":"),
        ),
    )


def _state(value: ConditionState | str) -> ConditionState:
    try:
        return ConditionState(value)
    except (ValueError, TypeError):
        raise ValueError(
            "state must be one of: unknown, true, false, expired"
        ) from None


def _method(value: ConditionEvaluationMethod | str) -> ConditionEvaluationMethod:
    try:
        method = ConditionEvaluationMethod(value)
    except (ValueError, TypeError):
        method = None
    if method not in _METHODS:
        raise ValueError("evaluation_method must be one of: user, tool, rule, llm")
    return method


def _timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    return value.astimezone(timezone.utc)


def _text(value: str | None, *, name: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    result = value.strip()
    if len(result) > 4000:
        raise ValueError(f"{name} must be at most 4000 characters")
    return result


def _evidence_id(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(EVIDENCE_ERROR) from None


def _validate_node(node: MemoryNode | None, node_id: str, user_id: str | None) -> MemoryNode:
    if node is None or (user_id is not None and node.user_id != user_id):
        raise ValueError(f"Node {node_id!r} not found")
    if node.epistemic_type != EpistemicType.CONDITIONAL:
        raise ValueError("Only conditional memory nodes can have a condition evaluated")
    condition = (node.metadata or {}).get("condition")
    if not isinstance(condition, str) or not condition.strip():
        raise ValueError("Conditional memory is missing its condition text")
    return node


def _current_state(node: MemoryNode) -> ConditionState:
    try:
        return ConditionState((node.metadata or {}).get("condition_state", "unknown"))
    except (ValueError, TypeError):
        return ConditionState.UNKNOWN


def _payload(record: ConditionEvaluationRecord) -> str:
    raw = _snapshot_json.dumps(record.model_dump(mode="python"))
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def read_record(payload) -> ConditionEvaluationRecord:
    """Validate and parse a retained condition-evaluation record."""
    value = json.loads(payload) if isinstance(payload, str) else payload
    raw = value["record"]
    if hashlib.sha256(raw.encode()).hexdigest() != value["sha256"]:
        raise ValueError("Condition evaluation journal checksum mismatch")
    record = ConditionEvaluationRecord.model_validate(_snapshot_json.loads(raw))
    if (
        record.before.id != record.after.id
        or record.before.user_id != record.after.user_id
        or record.before.scope != record.after.scope
        or record.before.epistemic_type != EpistemicType.CONDITIONAL
        or record.after.epistemic_type != EpistemicType.CONDITIONAL
    ):
        raise ValueError("Condition evaluation journal identity mismatch")
    if record.request_id is not None and record.operation_id != _operation_id(
        record.before.user_id, record.request_id
    ):
        raise ValueError("Condition evaluation journal request identity mismatch")
    return record


def _replayed(
    row,
    *,
    operation_id: UUID,
    request_id: UUID | None,
    node: MemoryNode,
    state: ConditionState,
    method: ConditionEvaluationMethod,
    reason: str | None,
    evidence: UUID | None,
    actor: str,
    evaluated_at: datetime | None,
) -> bool:
    if row is None:
        return False
    kind, payload = row
    if kind != "EPISTEMIC_TRANSITION":
        raise ConditionEvaluationConflict(
            "request_id is already bound to another operation"
        )
    record = read_record(payload)
    if (
        record.operation_id != operation_id
        or record.request_id != request_id
        or record.before.id != node.id
        or record.before.user_id != node.user_id
        or record.before.scope != node.scope
        or record.to_state != state
        or record.evaluation_method != method
        or record.reason != reason
        or record.evidence_id != evidence
        or record.triggered_by != actor
        or (evaluated_at is not None and record.evaluated_at != evaluated_at)
    ):
        raise ConditionEvaluationConflict(
            "request_id is already bound to a different condition evaluation"
        )
    return True


def _values(
    before: MemoryNode,
    *,
    state: ConditionState,
    method: ConditionEvaluationMethod,
    reason: str | None,
    evidence: UUID | None,
    actor: str,
    evaluated_at: datetime,
) -> tuple[str, str, datetime]:
    metadata = dict(before.metadata or {})
    metadata.update(
        condition_state=state.value,
        condition_evaluated_at=evaluated_at.isoformat(),
        condition_evaluation_method=method.value,
        condition_evaluated_by=actor,
    )
    for key, value in (
        ("condition_evaluation_reason", reason),
        ("condition_evaluation_evidence_id", str(evidence) if evidence else None),
    ):
        if value is None:
            metadata.pop(key, None)
        else:
            metadata[key] = value
    refs = list(before.evidence_refs)
    if evidence is not None and evidence not in refs:
        refs.append(evidence)
    return json.dumps(metadata), json.dumps([str(ref) for ref in refs]), datetime.now(timezone.utc)


def _record(
    *, operation_id, request_id, before, after, state, method, reason,
    evidence, actor, evaluated_at,
) -> ConditionEvaluationRecord:
    return ConditionEvaluationRecord(
        operation_id=operation_id,
        request_id=request_id,
        condition=before.metadata["condition"].strip(),
        from_state=_current_state(before),
        to_state=state,
        evaluated_at=evaluated_at,
        evaluation_method=method,
        reason=reason,
        evidence_id=evidence,
        triggered_by=actor,
        before=before,
        after=after,
    )


def _checkpoint(stage: str) -> None:
    """Transaction fault-injection point, with no external work."""


async def evaluate_condition_duckdb(
    store, node_id, state, *, user_id, evidence_id=None, request_id=None,
    evaluation_method=ConditionEvaluationMethod.USER, reason=None,
    actor_id=None, evaluated_at=None,
) -> MemoryNode:
    inputs = _inputs(
        state, user_id=user_id, evidence_id=evidence_id, request_id=request_id,
        evaluation_method=evaluation_method, reason=reason, actor_id=actor_id,
        evaluated_at=evaluated_at,
    )
    async with store._conn_lock:
        return await run_to_completion(_evaluate_duckdb, store, node_id, inputs)


def _inputs(
    state, *, user_id, evidence_id, request_id, evaluation_method, reason,
    actor_id, evaluated_at,
):
    return {
        "state": _state(state),
        "user_id": user_id,
        "evidence": _evidence_id(evidence_id),
        "request_id": _request_id(request_id),
        "method": _method(evaluation_method),
        "reason": _text(reason, name="reason"),
        "actor_id": _text(actor_id, name="actor_id"),
        "evaluated_at": _timestamp(evaluated_at),
    }


def _evaluate_duckdb(store, node_id: str, inputs: dict) -> MemoryNode:
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        before = _validate_node(
            store._get_node_sync(node_id, True), node_id, inputs["user_id"]
        )
        actor = inputs["actor_id"] or before.user_id
        operation_id = _operation_id(before.user_id, inputs["request_id"])
        row = conn.execute(
            "SELECT op_type,payload FROM operations WHERE id=?", [str(operation_id)]
        ).fetchone()
        if _replayed(
            row, operation_id=operation_id, request_id=inputs["request_id"],
            node=before, state=inputs["state"], method=inputs["method"],
            reason=inputs["reason"], evidence=inputs["evidence"], actor=actor,
            evaluated_at=inputs["evaluated_at"],
        ):
            conn.execute("COMMIT")
            return before
        if inputs["evidence"] is not None and conn.execute(
            "SELECT id FROM events WHERE id=? AND user_id=? AND scope=?",
            [str(inputs["evidence"]), before.user_id, before.scope.value],
        ).fetchone() is None:
            raise ValueError(EVIDENCE_ERROR)
        _checkpoint("validated")
        when = inputs["evaluated_at"] or datetime.now(timezone.utc)
        metadata, evidence_refs, updated_at = _values(
            before, state=inputs["state"], method=inputs["method"],
            reason=inputs["reason"], evidence=inputs["evidence"], actor=actor,
            evaluated_at=when,
        )
        conn.execute(
            "UPDATE nodes SET metadata=?, evidence_refs=?, updated_at=? WHERE id=?",
            [metadata, evidence_refs, updated_at, node_id],
        )
        _checkpoint("updated")
        after = store._get_node_sync(node_id, True)
        record = _record(
            operation_id=operation_id, request_id=inputs["request_id"],
            before=before, after=after, state=inputs["state"], method=inputs["method"],
            reason=inputs["reason"], evidence=inputs["evidence"], actor=actor,
            evaluated_at=when,
        )
        conn.execute(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES (?,'EPISTEMIC_TRANSITION',?,?,?,?,?)""",
            [str(operation_id), node_id, _payload(record), actor,
             before.scope.value, updated_at],
        )
        _checkpoint("journal")
        conn.execute("COMMIT")
        return after
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def evaluate_condition_postgres(
    store, node_id, state, *, user_id, evidence_id=None, request_id=None,
    evaluation_method=ConditionEvaluationMethod.USER, reason=None,
    actor_id=None, evaluated_at=None,
) -> MemoryNode:
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    inputs = _inputs(
        state, user_id=user_id, evidence_id=evidence_id, request_id=request_id,
        evaluation_method=evaluation_method, reason=reason, actor_id=actor_id,
        evaluated_at=evaluated_at,
    )
    async with store._pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=$1 "
            "AND ($2::text IS NULL OR user_id=$2) FOR UPDATE",
            node_id, inputs["user_id"],
        )
        before = _validate_node(
            store._record_to_node(row) if row is not None else None,
            node_id, inputs["user_id"],
        )
        actor = inputs["actor_id"] or before.user_id
        operation_id = _operation_id(before.user_id, inputs["request_id"])
        existing = await conn.fetchrow(
            "SELECT op_type,payload FROM operations WHERE id=$1", str(operation_id)
        )
        if _replayed(
            tuple(existing) if existing else None,
            operation_id=operation_id, request_id=inputs["request_id"],
            node=before, state=inputs["state"], method=inputs["method"],
            reason=inputs["reason"], evidence=inputs["evidence"], actor=actor,
            evaluated_at=inputs["evaluated_at"],
        ):
            return before
        if inputs["evidence"] is not None and await conn.fetchval(
            "SELECT id FROM events WHERE id=$1 AND user_id=$2 AND scope=$3",
            str(inputs["evidence"]), before.user_id, before.scope.value,
        ) is None:
            raise ValueError(EVIDENCE_ERROR)
        _checkpoint("validated")
        when = inputs["evaluated_at"] or datetime.now(timezone.utc)
        metadata, evidence_refs, updated_at = _values(
            before, state=inputs["state"], method=inputs["method"],
            reason=inputs["reason"], evidence=inputs["evidence"], actor=actor,
            evaluated_at=when,
        )
        row = await conn.fetchrow(
            f"""UPDATE nodes SET metadata=$1::jsonb,evidence_refs=$2::jsonb,
            updated_at=$3 WHERE id=$4 RETURNING {_NODE_COLUMNS}""",
            metadata, evidence_refs, updated_at, node_id,
        )
        _checkpoint("updated")
        after = store._record_to_node(row)
        record = _record(
            operation_id=operation_id, request_id=inputs["request_id"],
            before=before, after=after, state=inputs["state"], method=inputs["method"],
            reason=inputs["reason"], evidence=inputs["evidence"], actor=actor,
            evaluated_at=when,
        )
        inserted = await conn.fetchval(
            """INSERT INTO operations
            (id,op_type,target_id,payload,actor_id,namespace_id,created_at)
            VALUES ($1,'EPISTEMIC_TRANSITION',$2,$3::jsonb,$4,$5,$6)
            ON CONFLICT (id) DO NOTHING RETURNING id""",
            str(operation_id), node_id, _payload(record), actor,
            before.scope.value, updated_at,
        )
        if inserted is None:
            raise ConditionEvaluationConflict(
                "request_id is already bound to a different condition evaluation"
            )
        _checkpoint("journal")
        return after
