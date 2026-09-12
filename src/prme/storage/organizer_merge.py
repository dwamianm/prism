"""Atomic, journaled organizer merges; no embedding or provider calls in a transaction."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.storage._threading import run_to_completion
from prme.types import EdgeType, LifecycleState

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore


class MergeRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    operation_id: str
    kind: Literal["duplicate", "alias"]
    user_id: str
    score: float
    canonical_before: MemoryNode
    retired_before: MemoryNode
    canonical_after: MemoryNode
    retired_after: MemoryNode
    original_edges: tuple[MemoryEdge, ...]
    published_edges: tuple[MemoryEdge, ...]


@dataclass(frozen=True)
class MergeResult:
    operation_id: str
    canonical_id: str
    retired_id: str
    applied: bool


def _request(a, b, user_id, kind, score):
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Organizer merge requires an owner")
    if kind not in {"duplicate", "alias"} or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Organizer merge requires a supported kind and finite score in [0, 1]")
    ids = sorted((str(UUID(a)), str(UUID(b))))
    operation = str(uuid5(UUID(ids[0]), f"prme:organizer-merge:v1:{ids[1]}:{kind}"))
    return ids, operation


def _result(record, applied):
    return MergeResult(record.operation_id, str(record.canonical_before.id), str(record.retired_before.id), applied)


def _payload(record):
    text = record.model_dump_json()
    return json.dumps({"record": text, "sha256": hashlib.sha256(text.encode()).hexdigest()})


def _replayed(value, operation, ids, user_id, kind):
    if value is None:
        return None
    payload = json.loads(value) if isinstance(value, str) else value
    raw = payload["record"]
    if hashlib.sha256(raw.encode()).hexdigest() != payload["sha256"]:
        raise ValueError("Organizer merge journal checksum mismatch")
    record = MergeRecord.model_validate_json(raw)
    if (record.operation_id, record.user_id, record.kind) != (operation, user_id, kind) or sorted(
        [str(record.canonical_before.id), str(record.retired_before.id)]
    ) != ids:
        raise ValueError("Organizer merge journal identity mismatch")
    return _result(record, False)


def _prepare(nodes, edges, ids, operation, user_id, kind, score):
    from prme.organizer.alias_resolution import _MERGE_CONFIDENCE_THRESHOLD, _is_abbreviation_match, _is_case_variation, _pick_canonical_entity
    from prme.organizer.deduplication import _pick_canonical
    from prme.organizer.merge_policy import alias_pair_allowed, duplicate_merge_allowed

    if ids[0] == ids[1] or any(nid not in nodes for nid in ids):
        return None
    a, b = (nodes[nid] for nid in ids)
    if any(n.user_id != user_id or n.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE) for n in (a, b)):
        return None
    if kind == "duplicate":
        if not duplicate_merge_allowed(a, b):
            return None
        keep, retired = _pick_canonical(a, b)
    else:
        if score < _MERGE_CONFIDENCE_THRESHOLD or not alias_pair_allowed(a, b) or not (
            _is_abbreviation_match(a.content, b.content) or _is_case_variation(a.content, b.content)
            or a.content.strip().casefold() == b.content.strip().casefold()
        ):
            return None
        keep, retired = _pick_canonical_entity(a, b)
    now = datetime.now(timezone.utc)
    evidence = list(keep.evidence_refs)
    for ref in retired.evidence_refs:
        if ref not in evidence:
            evidence.append(ref)
    canonical_after = keep.model_copy(deep=True, update={"evidence_refs": evidence, "updated_at": now})
    retired_after = retired.model_copy(deep=True, update={"lifecycle_state": LifecycleState.SUPERSEDED,
                                                        "superseded_by": keep.id, "updated_at": now})
    originals, copies = [], []
    for edge in sorted(edges, key=lambda edge: str(edge.id)):
        if retired.id not in (edge.source_id, edge.target_id) or edge.edge_type == EdgeType.SUPERSEDES:
            continue
        if edge.user_id != user_id:
            raise ValueError("Organizer merge cannot transfer another owner's relationship")
        originals.append(edge)
        source = keep.id if edge.source_id == retired.id else edge.source_id
        target = keep.id if edge.target_id == retired.id else edge.target_id
        if source == target and edge.source_id != edge.target_id:
            continue
        copies.append(edge.model_copy(deep=True, update={
            "id": uuid5(edge.id, f"prme:edge-transfer:v1:{retired.id}:{keep.id}"),
            "source_id": source, "target_id": target,
        }))
    copies.append(MemoryEdge(id=uuid5(UUID(operation), "supersedes"), source_id=keep.id, target_id=retired.id,
        edge_type=EdgeType.SUPERSEDES, user_id=user_id, confidence=1., valid_from=now, created_at=now,
        metadata={"reason": "deduplication" if kind == "duplicate" else "alias_resolution", "score": score,
                  "merge_operation_id": operation}))
    return MergeRecord(operation_id=operation, kind=kind, user_id=user_id, score=score,
        canonical_before=keep, retired_before=retired, canonical_after=canonical_after, retired_after=retired_after,
        original_edges=tuple(originals), published_edges=tuple(copies))


def _checkpoint(stage):
    """Transaction fault-injection boundary; never performs external work."""


async def merge_duckdb(store: "DuckPGQGraphStore", a: str, b: str, *, user_id: str, kind: str, score: float) -> MergeResult | None:
    ids, operation = _request(a, b, user_id, kind, score)
    async with store._conn_lock:
        return await run_to_completion(_merge_duckdb, store, ids, operation, user_id, kind, score)


def _merge_duckdb(store, ids, operation, user_id, kind, score):
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        row = conn.execute("SELECT payload FROM operations WHERE id=? AND op_type='ORGANIZER_MERGED' AND actor_id=?", [operation, user_id]).fetchone()
        replayed = _replayed(row[0] if row else None, operation, ids, user_id, kind)
        if replayed:
            conn.execute("COMMIT")
            return replayed
        nodes = {nid: store._get_node_sync(nid, True) for nid in ids}
        nodes = {nid: node for nid, node in nodes.items() if node is not None}
        edges = store._get_edges_sync(None, None, ids, None, None, None)
        record = _prepare(nodes, edges, ids, operation, user_id, kind, score)
        if record is None:
            conn.execute("COMMIT")
            return None
        _checkpoint("validated")
        conn.execute("UPDATE nodes SET evidence_refs=?, updated_at=? WHERE id=?",
            [json.dumps([str(ref) for ref in record.canonical_after.evidence_refs]), record.canonical_after.updated_at, str(record.canonical_after.id)])
        _checkpoint("evidence")
        existing = {edge.id: edge for edge in edges}
        for edge in record.published_edges:
            if edge.id in existing:
                if existing[edge.id] != edge:
                    raise ValueError("Transferred edge identity conflicts with stored content")
            else:
                store._create_edge_sync(edge)
            _checkpoint("edge")
        conn.execute("UPDATE nodes SET lifecycle_state='superseded', superseded_by=?, updated_at=? WHERE id=?",
            [str(record.canonical_after.id), record.retired_after.updated_at, str(record.retired_after.id)])
        _checkpoint("retirement")
        conn.execute("INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) VALUES (?,'ORGANIZER_MERGED',?,?,?,?,?)",
            [operation, str(record.retired_after.id), _payload(record), user_id, record.canonical_after.scope.value, record.canonical_after.updated_at])
        _checkpoint("journal")
        conn.execute("COMMIT")
        return _result(record, True)
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def merge_postgres(store: "PgGraphStore", a: str, b: str, *, user_id: str, kind: str, score: float) -> MergeResult | None:
    from prme.storage.pg.graph_store import _NODE_COLUMNS
    ids, operation = _request(a, b, user_id, kind, score)
    async with store._pool.acquire() as conn, conn.transaction():
        # Lock shared canonical nodes before reading evidence or committed work.
        rows = await conn.fetch(f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=ANY($1::uuid[]) AND user_id=$2 ORDER BY id FOR UPDATE", ids, user_id)
        value = await conn.fetchval("SELECT payload FROM operations WHERE id=$1 AND op_type='ORGANIZER_MERGED' AND actor_id=$2", operation, user_id)
        replayed = _replayed(value, operation, ids, user_id, kind)
        if replayed:
            return replayed
        nodes = {str(row["id"]): store._record_to_node(row) for row in rows}
        rows = await conn.fetch("SELECT * FROM edges WHERE source_id=ANY($1::uuid[]) OR target_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", ids)
        edges = [store._record_to_edge(row) for row in rows]
        record = _prepare(nodes, edges, ids, operation, user_id, kind, score)
        if record is None:
            return None
        _checkpoint("validated")
        await conn.execute("UPDATE nodes SET evidence_refs=$1::jsonb, updated_at=$2 WHERE id=$3", json.dumps([str(ref) for ref in record.canonical_after.evidence_refs]), record.canonical_after.updated_at, str(record.canonical_after.id))
        _checkpoint("evidence")
        existing = {edge.id: edge for edge in edges}
        for edge in record.published_edges:
            if edge.id in existing:
                if existing[edge.id] != edge:
                    raise ValueError("Transferred edge identity conflicts with stored content")
            else:
                await store._create_edge_on_connection(conn, edge)
            _checkpoint("edge")
        await conn.execute("UPDATE nodes SET lifecycle_state='superseded', superseded_by=$1::uuid, updated_at=$2 WHERE id=$3", str(record.canonical_after.id), record.retired_after.updated_at, str(record.retired_after.id))
        _checkpoint("retirement")
        await conn.execute("INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) VALUES ($1,'ORGANIZER_MERGED',$2,$3::jsonb,$4,$5,$6)", operation, str(record.retired_after.id), _payload(record), user_id, record.canonical_after.scope.value, record.canonical_after.updated_at)
        _checkpoint("journal")
        return _result(record, True)
