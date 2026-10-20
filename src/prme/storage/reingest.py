"""Durable state of ``MemoryEngine.reingest()``: which sources are current, the model output it saved, and its commit.

A source is an event that has a memory. The engine derives entities, claims
and links from a source once, and the derivation journal (RFC-0016) keys that
one extraction, one plan and one receipt to the event, so a source that was
derived cannot be derived again through it. A re-derivation therefore keeps
its own records beside the journal, in the same ``operations`` table and with
IDs fixed by the source and the settings it ran under:

- ``REINGEST_EXTRACTED``: the checked model output for a source, saved before
  anything is planned, so a run that stops after the model call never asks it
  again.
- ``REINGEST_COMMITTED``: written in the transaction that publishes the new
  nodes and edges and retires the old claims. It is the marker: a source that
  has one under the present settings is current, and a second commit of it finds
  the first and changes nothing.

Neither touches the source event, its raw note, its extraction record or its
derivation receipt.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

from prme.models.derivation import DerivationPlan, DerivationReceipt
from prme.models.extraction import ExtractionBatchRecord, ExtractionRecord
from prme.models.reingest import ReingestRecord, reingest_extraction_id, reingest_marker_id
from prme.storage._threading import run_to_completion
from prme.storage.derivation import apply_duckdb, apply_postgres
from prme.storage.privacy import require_legacy_duck, require_legacy_pg, require_legacy_session
from prme.types import ACTIVE_LIFECYCLE_STATES, Scope

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.extraction_work import ExtractionWorkRepository
    from prme.storage.pg.graph_store import PgGraphStore

MARKER_TYPE = "REINGEST_COMMITTED"
EXTRACTION_TYPE = "REINGEST_EXTRACTED"
# The key a node's metadata carries once a re-derivation made or retired it.
REINGEST_KEY = "reingest"


class SourceRef(NamedTuple):
    """A source in event order: its effective time, its admission time and its ID."""

    effective: datetime
    stamp: datetime
    event_id: str
    # The source's own extraction job: None when it has none, else its status.
    work_status: str | None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _slots(start: int, count: int) -> str:
    return ", ".join(f"${start + index}" for index in range(count))


class ReingestRepository:
    """Reads and writes the re-derivation records on either backend, through the work repository's sessions."""

    def __init__(self, work: ExtractionWorkRepository) -> None:
        self._work = work

    async def scan_sources(self, *, user_id: str, scope: str | None = None, after: SourceRef | None = None,
                           limit: int = 1000) -> list[SourceRef]:
        """The owner's sources in event order after ``after``: effective time, then admission time, then ID.

        A source is an event with a raw note to derive from (it was saved through
        ``store()``, ``ingest()`` or the like) and some text; adapter control events
        have neither.
        """
        if not user_id or limit < 1:
            raise ValueError("Scanning sources requires user_id and a positive limit")
        args: list = [user_id]
        where = "e.user_id = $1 AND trim(e.content) <> ''"
        if scope is not None:
            args.append(scope)
            where += f" AND e.scope = ${len(args)}"
        effective = "COALESCE(e.event_time, e.timestamp)"
        order = f"{effective}, e.timestamp, CAST(e.id AS VARCHAR)"
        if after is not None:
            where += f" AND ({order}) > (${len(args) + 1}, ${len(args) + 2}, ${len(args) + 3})"
            args += [after.effective, after.stamp, after.event_id]
        async with self._work.session() as session:
            await require_legacy_session(session, user_id, scope)
            rows = await session.fetch(
                f"SELECT CAST(e.id AS VARCHAR) AS id, {effective} AS effective, e.timestamp AS stamp, "
                "w.status AS work_status FROM events e "
                "JOIN event_materializations m ON m.event_id = e.id "
                "LEFT JOIN event_extractions w ON w.event_id = e.id "
                f"WHERE {where} ORDER BY {order} LIMIT {int(limit)}", *args,
            )
        return [SourceRef(row["effective"], row["stamp"], row["id"], row["work_status"]) for row in rows]

    async def current(self, event_ids: list[str], fingerprint: str) -> set[str]:
        """The sources among ``event_ids`` that were re-derived under ``fingerprint``."""
        if not event_ids:
            return set()
        markers = {reingest_marker_id(event_id, fingerprint): event_id for event_id in event_ids}
        found: set[str] = set()
        ids = list(markers)
        async with self._work.session() as session:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                rows = await session.fetch(
                    f"SELECT id FROM operations WHERE op_type = '{MARKER_TYPE}' AND id IN ({_slots(1, len(chunk))})",
                    *chunk,
                )
                found.update(markers[row["id"]] for row in rows)
        return found

    async def saved_extractions(self, event_ids: list[str], *, user_id: str, extraction_key: str,
                                ) -> dict[str, ExtractionRecord]:
        """The model output saved for each source among ``event_ids`` under ``extraction_key``."""
        if not event_ids:
            return {}
        by_operation = {reingest_extraction_id(event_id, extraction_key): event_id for event_id in event_ids}
        saved: dict[str, ExtractionRecord] = {}
        ids = list(by_operation)
        async with self._work.session() as session:
            for event_id in event_ids:
                rows = await session.fetch("SELECT scope FROM events WHERE id=$1 AND user_id=$2", event_id, user_id)
                for row in rows:
                    await require_legacy_session(session, user_id, row["scope"])
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                rows = await session.fetch(
                    "SELECT o.id, o.payload, e.scope, e.content_hash FROM operations o JOIN events e "
                    "ON o.target_id = CAST(e.id AS VARCHAR) "
                    f"WHERE o.op_type = '{EXTRACTION_TYPE}' AND e.user_id = $1 AND o.id IN ({_slots(2, len(chunk))})",
                    user_id, *chunk,
                )
                for row in rows:
                    record = ExtractionRecord.model_validate(_json(row["payload"]))
                    record.verify_source(user_id, row["scope"], row["content_hash"])
                    saved[by_operation[row["id"]]] = record
        return saved

    async def save_extractions(self, batch: ExtractionBatchRecord | None, records: list[ExtractionRecord], *,
                               extraction_key: str) -> None:
        """Save a batched request, its response and each turn's checked output, without touching the sources' own.

        The batch record keeps the first response to the same request, as the
        extraction journal does. Each turn's output is saved once per
        ``extraction_key``; a second save of it keeps the first. Without a
        batch (a turn asked on its own) only the turn's output is saved.
        """
        async with self._work.session(transaction=True) as session:
            if batch is not None:
                await require_legacy_session(session, batch.user_id, batch.scope.value)
                # The saved batch includes omitted/retried turns and prior context,
                # not only the records this particular response happened to yield.
                for turn in batch.turns:
                    rows = await session.fetch(
                        "SELECT user_id,scope,content_hash FROM events WHERE id=$1", str(turn.event_id))
                    if not rows:
                        raise ValueError("Extraction requires a matching persisted source event")
                    row = rows[0]
                    batch.verify_turn(turn.event_id, row["user_id"], row["scope"], row["content_hash"])
                    await require_legacy_session(session, row["user_id"], row["scope"])
                for memory in batch.memories:
                    rows = await session.fetch("SELECT user_id,scope FROM nodes WHERE id=$1", str(memory.node_id))
                    if not rows or (rows[0]["user_id"], rows[0]["scope"]) != (batch.user_id, batch.scope.value):
                        from prme.privacy.policy import PrivacyDenied
                        raise PrivacyDenied("privacy_provider_unavailable")
            for record in records:
                rows = await session.fetch(
                    "SELECT user_id, scope, content_hash FROM events WHERE id = $1", str(record.event_id),
                )
                if not rows:
                    raise ValueError("Extraction requires a matching persisted source event")
                row = rows[0]
                record.verify_source(row["user_id"], row["scope"], row["content_hash"])
                if batch is None:
                    await require_legacy_session(session, row["user_id"], row["scope"])
                else:
                    batch.verify_turn(record.event_id, row["user_id"], row["scope"], row["content_hash"])
            if batch is not None:
                await session.execute(
                    "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                    "VALUES ($1, 'EXTRACTION_BATCH_RECORDED', $2, $3, 'extraction', $4, $5) ON CONFLICT (id) DO NOTHING",
                    str(batch.id), str(batch.head_event_id), json.dumps({"batch": batch.model_dump_json()}),
                    batch.scope.value, batch.created_at,
                )
            for record in records:
                await session.execute(
                    "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                    f"VALUES ($1, '{EXTRACTION_TYPE}', $2, $3, 'reingest', $4, $5) ON CONFLICT (id) DO NOTHING",
                    reingest_extraction_id(record.event_id, extraction_key), str(record.event_id),
                    record.model_dump_json(), record.scope.value, record.created_at,
                )

    async def derived_node_ids(self, event_id: str) -> set[str]:
        """The nodes the source's first derivation created, from its journaled receipt."""
        async with self._work.session() as session:
            rows = await session.fetch(
                "SELECT payload FROM operations WHERE id = $1 AND op_type = 'DERIVATION_COMMITTED'",
                _receipt_operation_id(event_id),
            )
        if not rows:
            return set()
        try:
            return {str(node_id) for node_id in DerivationReceipt.model_validate(_json(rows[0]["payload"])).node_ids}
        except (ValueError, TypeError):
            # A receipt from before the journal's present shape: the claims that cite the source alone still count.
            return set()


def _receipt_operation_id(event_id: str) -> str:
    from prme.models.derivation import derivation_operation_id

    return derivation_operation_id(event_id, committed=True)


def _archived_metadata(node, record: ReingestRecord, replaced_by: tuple[UUID, ...]) -> dict:
    """The node's metadata with the pointer to what replaced it, for the claim a re-derivation archives."""
    return {**(node.metadata or {}), REINGEST_KEY: {
        "operation_id": record.operation_id, "source_event_id": str(record.event_id),
        "fingerprint": record.fingerprint, "replaced_by": [str(node_id) for node_id in replaced_by],
    }}


def _check(plan: DerivationPlan, record: ReingestRecord) -> None:
    if (plan.event_id, plan.user_id, plan.scope, plan.id, plan.checksum) != (
        record.event_id, record.user_id, record.scope, record.plan_id, record.plan_checksum,
    ) or record.node_ids != tuple(node.id for node in plan.nodes) or record.edge_ids != tuple(
        edge.id for edge in plan.edges + plan.replacements
    ) or record.superseded != tuple(edge.target_id for edge in plan.replacements):
        raise ValueError("Re-derivation record does not describe its plan")


async def commit_duckdb(store: DuckPGQGraphStore, plan: DerivationPlan, record: ReingestRecord) -> ReingestRecord:
    snapshot = DerivationPlan.model_validate_json(plan.model_dump_json())
    async with store._conn_lock:
        return await run_to_completion(_commit_duckdb, store, snapshot, record)


def _commit_duckdb(store: DuckPGQGraphStore, plan: DerivationPlan, record: ReingestRecord) -> ReingestRecord:
    _check(plan, record)
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        require_legacy_duck(conn, plan.user_id, plan.scope.value)
        row = conn.execute(
            "SELECT payload FROM operations WHERE id = ? AND op_type = ?", [record.operation_id, MARKER_TYPE],
        ).fetchone()
        if row is not None:
            conn.execute("COMMIT")
            return ReingestRecord.model_validate(_json(row[0]))
        source = conn.execute(
            "SELECT user_id, scope, content_hash FROM events WHERE id = ?", [str(plan.event_id)],
        ).fetchone()
        if source is None:
            raise ValueError("A re-derivation requires a persisted source event")
        plan.verify_source(*source)
        apply_duckdb(store, plan)
        for entry in record.archived:
            node = store._get_node_sync(str(entry.node_id), True)
            if node is None or node.user_id != record.user_id or node.lifecycle_state not in ACTIVE_LIFECYCLE_STATES:
                continue
            conn.execute(
                "UPDATE nodes SET lifecycle_state = 'archived', metadata = ?, updated_at = ? WHERE id = ?",
                [json.dumps(_archived_metadata(node, record, entry.replaced_by)), record.created_at,
                 str(entry.node_id)],
            )
        conn.execute(
            "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
            "VALUES (?, ?, ?, ?, 'reingest', ?, ?)",
            [record.operation_id, MARKER_TYPE, str(record.event_id), record.model_dump_json(),
             record.scope.value, record.created_at],
        )
        conn.execute("COMMIT")
        return record
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def commit_postgres(store: PgGraphStore, plan: DerivationPlan, record: ReingestRecord) -> ReingestRecord:
    from prme.storage.pg.graph_store import _NODE_COLUMNS
    from prme.storage.pg.vector_sql import resolve_vector_sql

    plan = DerivationPlan.model_validate_json(plan.model_dump_json())
    _check(plan, record)
    async with store._pool.acquire() as conn, conn.transaction():
        await require_legacy_pg(conn, plan.user_id, plan.scope.value)
        vector_sql = await resolve_vector_sql(conn)
        # Serialize attempts for this immutable source, as a derivation commit does.
        source = await conn.fetchrow(
            "SELECT user_id, scope, content_hash FROM events WHERE id = $1 FOR UPDATE", str(plan.event_id),
        )
        if source is None:
            raise ValueError("A re-derivation requires a persisted source event")
        await require_legacy_pg(conn, plan.user_id, plan.scope.value)
        row = await conn.fetchrow(
            "SELECT payload FROM operations WHERE id = $1 AND op_type = $2", record.operation_id, MARKER_TYPE,
        )
        if row is not None:
            return ReingestRecord.model_validate(_json(row["payload"]))
        plan.verify_source(source["user_id"], source["scope"], source["content_hash"])
        await apply_postgres(store, conn, plan, vector_sql)
        for entry in record.archived:
            found = await conn.fetchrow(
                f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = $1 FOR UPDATE", str(entry.node_id),
            )
            node = store._record_to_node(found) if found is not None else None
            if node is None or node.user_id != record.user_id or node.lifecycle_state not in ACTIVE_LIFECYCLE_STATES:
                continue
            await conn.execute(
                "UPDATE nodes SET lifecycle_state = 'archived', metadata = $1::jsonb, updated_at = $2 WHERE id = $3",
                json.dumps(_archived_metadata(node, record, entry.replaced_by)), record.created_at,
                str(entry.node_id),
            )
        await conn.execute(
            "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
            "VALUES ($1, $2, $3, $4::jsonb, 'reingest', $5, $6)",
            record.operation_id, MARKER_TYPE, str(record.event_id), record.model_dump_json(),
            record.scope.value, record.created_at,
        )
        return record


def scope_value(scope: Scope | str | None) -> str | None:
    """The scope's value for a query, or None for every scope of the owner."""
    return None if scope is None else Scope(scope).value
