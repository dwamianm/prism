"""Atomic, evidence-checked retirement of a source represented by a summary."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.storage import _snapshot_json
from prme.storage._threading import run_to_completion
from prme.types import EdgeType, LifecycleState, NodeType


class RetirementRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    version: Literal[1] = 1
    at: AwareDatetime
    preserve_recent_days: int = Field(ge=0)
    min_confidence_preserve: float = Field(ge=0, le=1)
    before: MemoryNode
    after: MemoryNode
    summary: MemoryNode
    edge: MemoryEdge

    @property
    def operation_id(self) -> str:
        return str(uuid5(self.summary.id, 'prme:consolidation-retired:v1:' + str(self.before.id)))


def eligible(source, summary, *, user_id, at, preserve_recent_days, min_confidence_preserve):
    from prme.organizer.consolidation import _render_source, _source_fingerprint
    active = (LifecycleState.TENTATIVE, LifecycleState.STABLE)
    if source is None or summary is None:
        return False
    meta = summary.metadata or {}
    return (
        source.id != summary.id
        and summary.node_type == NodeType.SUMMARY
        and source.lifecycle_state in active and summary.lifecycle_state in active
        and (source.user_id, source.scope) == (summary.user_id, summary.scope)
        and (user_id is None or source.user_id == user_id)
        and not source.pinned
        and source.confidence < min_confidence_preserve
        and source.created_at <= at - timedelta(days=preserve_recent_days)
        and meta.get('consolidation_content_sha256') == hashlib.sha256(summary.content.encode()).hexdigest()
        and isinstance(meta.get('consolidation_coverage'), dict)
        and meta['consolidation_coverage'].get(str(source.id)) == _source_fingerprint(source)
        and _render_source(source) in summary.content
        and set(source.evidence_refs) <= set(summary.evidence_refs)
    )


def payload(record):
    raw = _snapshot_json.dumps(record.model_dump(mode='python'))
    return json.dumps({'record': raw, 'sha256': hashlib.sha256(raw.encode()).hexdigest()})


def read_record(value):
    value = json.loads(value) if isinstance(value, str) else value
    if hashlib.sha256(value['record'].encode()).hexdigest() != value['sha256']:
        raise ValueError('Consolidation retirement checksum mismatch')
    record = RetirementRecord.model_validate(_snapshot_json.loads(value['record']))
    if not eligible(record.before, record.summary, user_id=record.before.user_id, at=record.at,
                    preserve_recent_days=record.preserve_recent_days,
                    min_confidence_preserve=record.min_confidence_preserve):
        raise ValueError('Consolidation retirement lacked eligible source coverage')
    if (record.after.lifecycle_state != LifecycleState.SUPERSEDED
            or record.after.superseded_by != record.summary.id or record.after.updated_at != record.at
            or record.edge != _edge(record.before, record.summary, record.at)):
        raise ValueError('Consolidation retirement output identity mismatch')
    changed = {'lifecycle_state', 'superseded_by', 'updated_at'}
    if _snapshot_json.dumps(record.before.model_dump(exclude=changed), sort_keys=True) != _snapshot_json.dumps(
            record.after.model_dump(exclude=changed), sort_keys=True):
        raise ValueError('Consolidation retirement changed unrelated source fields')
    return record


def _checkpoint(stage):
    """Native transaction fault-injection boundary; no external work."""


def _edge(source, summary, at):
    return MemoryEdge(id=uuid5(summary.id, 'prme:consolidation-supersedes:v1:' + str(source.id)),
                      source_id=summary.id, target_id=source.id, edge_type=EdgeType.SUPERSEDES,
                      user_id=source.user_id, confidence=1.0, created_at=at, valid_from=at)


async def retire_duckdb(store, source_id, summary_id, **policy):
    source_id, summary_id = str(UUID(source_id)), str(UUID(summary_id))
    async with store._conn_lock:
        return await run_to_completion(_retire_duckdb, store, source_id, summary_id, policy)


def _retire_duckdb(store, source_id, summary_id, policy):
    conn = store._conn
    conn.execute('BEGIN TRANSACTION')
    try:
        # Use a real updated_at write as the DuckDB conflict claim. A no-op
        # assignment can be elided for freshly published generated nodes,
        # allowing a concurrent writer to win and making this transaction fail
        # only after it has already authorized retirement. Ineligible attempts
        # roll back below, so they do not leave a maintenance-only timestamp.
        for identity in sorted({source_id, summary_id}):
            conn.execute('UPDATE nodes SET updated_at=current_timestamp WHERE id=?', [identity])
        source = store._get_node_sync(source_id, True)
        summary = store._get_node_sync(summary_id, True)
        if not eligible(source, summary, **policy):
            conn.execute('ROLLBACK')
            return False
        _checkpoint('validated')
        at = policy['at']
        edge = _edge(source, summary, at)
        conn.execute("UPDATE nodes SET lifecycle_state='superseded',superseded_by=?,updated_at=? WHERE id=?",
                     [summary_id, at, source_id])
        store._create_edge_sync(edge)
        _checkpoint('mutated')
        record = RetirementRecord(before=source, after=store._get_node_sync(source_id, True), summary=summary,
                                  edge=edge, **{k:v for k,v in policy.items() if k != 'user_id'})
        conn.execute("INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
                     "VALUES (?,'CONSOLIDATION_RETIRED',?,?,?,?,?)",
                     [record.operation_id, source_id, payload(record), source.user_id, source.scope.value, at])
        _checkpoint('journaled')
        conn.execute('COMMIT')
        return True
    except BaseException:
        import duckdb
        try:
            conn.execute('ROLLBACK')
        except duckdb.TransactionException:
            pass
        raise


async def retire_postgres(store, source_id, summary_id, **policy):
    from prme.storage.pg.graph_store import _NODE_COLUMNS
    source_id, summary_id = str(UUID(source_id)), str(UUID(summary_id))
    async with store._pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(f'SELECT {_NODE_COLUMNS} FROM nodes WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE',
                                sorted({source_id, summary_id}))
        nodes = {str(row['id']):store._record_to_node(row) for row in rows}
        source, summary = nodes.get(source_id), nodes.get(summary_id)
        if not eligible(source, summary, **policy):
            return False
        _checkpoint('validated')
        at = policy['at']
        edge = _edge(source, summary, at)
        await conn.execute("UPDATE nodes SET lifecycle_state='superseded',superseded_by=$1,updated_at=$2 WHERE id=$3",
                           summary_id, at, source_id)
        await store._create_edge_on_connection(conn, edge)
        _checkpoint('mutated')
        row = await conn.fetchrow(f'SELECT {_NODE_COLUMNS} FROM nodes WHERE id=$1', source_id)
        record = RetirementRecord(before=source, after=store._record_to_node(row), summary=summary,
                                  edge=edge, **{k:v for k,v in policy.items() if k != 'user_id'})
        await conn.execute("INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
                           "VALUES ($1,'CONSOLIDATION_RETIRED',$2,$3::jsonb,$4,$5,$6)",
                           record.operation_id, source_id, payload(record), source.user_id, source.scope.value, at)
        _checkpoint('journaled')
        return True
