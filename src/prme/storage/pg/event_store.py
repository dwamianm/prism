"""PostgreSQL-backed async EventStore.

Implements the same interface as ``EventStore`` (DuckDB) using asyncpg
for natively async PostgreSQL access. No conn_lock or to_thread needed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from prme.models import Event, MemoryNode, ProcessingStatus
from prme.models.direct_store import DirectStoreRecord, direct_store_operation_id
from prme.models.extraction import ExtractionRecord, extraction_operation_id
from prme.models.derivation import DerivationPlan, DerivationReceipt, derivation_operation_id
from prme.types import Scope
from prme.storage.extraction_work import ExtractionWorkRepository, validate_pg_claim
from prme.storage.fast_ingest import (
    FastIngestAdmission,
    FastIngestBatchRecord,
    fast_ingest_payload,
    replay_fast_ingest,
    validate_fast_ingest_record,
)
from prme.models.extraction_work import ExtractionClaim

logger = logging.getLogger(__name__)

_EVENT_COLUMNS = (
    "id, timestamp, role, content, content_hash, "
    "user_id, session_id, scope, metadata, created_at, event_time"
)


class PgEventStore:
    """Async event store backed by PostgreSQL via asyncpg.

    All queries enforce user_id scoping to prevent cross-user data leakage.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.extraction_work = ExtractionWorkRepository(pool=pool)

    async def get_derivation_receipt(self, event_id: str, *, user_id: str) -> DerivationReceipt | None:
        """Read verified completion through the immutable source owner's scope."""
        from prme.storage.derivation import _receipt

        event_id = str(UUID(event_id))
        async with self._pool.acquire() as conn:
            plan = await self._get_derivation_plan(conn, event_id, user_id)
            if plan is None:
                return None
            row = await conn.fetchrow(
                "SELECT payload FROM operations WHERE id = $1 AND target_id = $2 "
                "AND op_type = 'DERIVATION_COMMITTED'", plan.receipt_operation_id, event_id,
            )
            return _receipt(plan, row["payload"] if row else None)

    async def get_derivation_plan(self, event_id: str, *, user_id: str, revision: int | None = None) -> DerivationPlan | None:
        async with self._pool.acquire() as conn:
            return await self._get_derivation_plan(conn, event_id, user_id, revision)

    async def _get_derivation_plan(self, conn, event_id: str, user_id: str, revision: int | None = None) -> DerivationPlan | None:
        if revision is None:
            work = await conn.fetchrow("SELECT plan_revision FROM event_extractions WHERE event_id = $1", event_id)
            revision = work["plan_revision"] if work else 1
        row = await conn.fetchrow(
            "SELECT o.payload, e.scope, e.content_hash FROM operations o JOIN events e "
            "ON o.target_id = e.id::text WHERE o.id = $1 AND e.id = $2 "
            "AND e.user_id = $3 AND o.op_type = 'DERIVATION_PREPARED'",
            derivation_operation_id(event_id, revision=revision), event_id, user_id,
        )
        if row is None:
            return None
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        plan = (DerivationPlan.model_validate_json(payload["plan"]) if isinstance(payload["plan"], str)
                else DerivationPlan.model_validate(payload["plan"]))
        plan.verify_source(user_id, row["scope"], row["content_hash"])
        if plan.event_id != UUID(event_id) or plan.revision != revision or plan.checksum != payload["checksum"]:
            raise ValueError("Prepared derivation identity or checksum does not match")
        return plan

    async def record_derivation_plan(self, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> DerivationPlan:
        """Concurrent planners converge on the first immutable prepared plan."""
        plan = DerivationPlan.model_validate_json(plan.model_dump_json())
        async with self._pool.acquire() as conn, conn.transaction():
            source = await conn.fetchrow(
                "SELECT user_id, scope, content_hash FROM events WHERE id = $1 FOR UPDATE", str(plan.event_id),
            )
            if source is None:
                raise ValueError("A derivation requires a persisted source event")
            plan.verify_source(source["user_id"], source["scope"], source["content_hash"])
            managed = await validate_pg_claim(conn, str(plan.event_id), plan.user_id, claim)
            if plan.revision != (managed["plan_revision"] if managed else 1):
                raise ValueError("Prepared plan does not match the current work revision")
            await conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                "VALUES ($1, 'DERIVATION_PREPARED', $2, $3::jsonb, 'derivation', $4, $5) "
                "ON CONFLICT (id) DO NOTHING",
                plan.prepared_operation_id, str(plan.event_id),
                # Preserve numerical serialization (including signed zero):
                # JSONB may normalize numbers inside a structured plan object.
                json.dumps({"plan": plan.model_dump_json(), "checksum": plan.checksum}),
                plan.scope.value, plan.created_at,
            )
            saved = await self._get_derivation_plan(conn, str(plan.event_id), plan.user_id)
            if saved is None or (saved.id == plan.id and saved.checksum != plan.checksum):
                raise ValueError("Prepared derivation ID conflicts with a different payload")
            from prme.storage.derivation_registry import register_pg
            await register_pg(conn, saved)
            if managed is not None:
                await conn.execute("UPDATE event_extractions SET plan_id = $1 WHERE event_id = $2",
                                   str(saved.id), str(plan.event_id))
            await validate_pg_claim(conn, str(plan.event_id), plan.user_id, claim)
            return saved

    async def get_extraction(self, event_id: str, *, user_id: str) -> ExtractionRecord | None:
        """Read a saved extraction through its immutable source owner's scope."""
        async with self._pool.acquire() as conn:
            return await self._get_extraction(conn, event_id, user_id)

    async def _get_extraction(self, conn, event_id: str, user_id: str) -> ExtractionRecord | None:
        row = await conn.fetchrow(
            "SELECT o.payload, e.scope, e.content_hash FROM operations o JOIN events e "
            "ON o.target_id = e.id::text WHERE o.id = $1 AND e.id = $2 "
            "AND e.user_id = $3 AND o.op_type = 'EXTRACTION_VALIDATED'",
            extraction_operation_id(event_id), event_id, user_id,
        )
        if row is None:
            return None
        payload = row["payload"]
        record = (ExtractionRecord.model_validate_json(payload) if isinstance(payload, str)
                  else ExtractionRecord.model_validate(payload))
        record.verify_source(user_id, row["scope"], row["content_hash"])
        if str(record.event_id) != str(UUID(event_id)):
            raise ValueError("Extraction record does not match its source event")
        return record

    async def record_extraction(self, record: ExtractionRecord, *, claim: ExtractionClaim | None = None) -> ExtractionRecord:
        """Save once in the operation log; first committed output wins."""
        record = ExtractionRecord.model_validate_json(record.model_dump_json())
        async with self._pool.acquire() as conn, conn.transaction():
            source = await conn.fetchrow(
                "SELECT user_id, scope, content_hash FROM events WHERE id = $1 FOR UPDATE", str(record.event_id),
            )
            if source is None:
                raise ValueError("Extraction requires a matching persisted source event")
            record.verify_source(source["user_id"], source["scope"], source["content_hash"])
            await validate_pg_claim(conn, str(record.event_id), record.user_id, claim)
            await conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, "
                "namespace_id, created_at) VALUES ($1, 'EXTRACTION_VALIDATED', $2, $3::jsonb, $4, $5, $6) "
                "ON CONFLICT (id) DO NOTHING",
                record.operation_id, str(record.event_id), record.model_dump_json(),
                "extraction", record.scope.value, record.created_at,
            )
            saved = await self._get_extraction(conn, str(record.event_id), record.user_id)
            if saved is None:
                raise ValueError("Extraction operation ID conflicts with another operation")
            await validate_pg_claim(conn, str(record.event_id), record.user_id, claim)
            return saved

    async def append(self, event: Event, *, defer_materialization: bool = False,
                     defer_extraction: bool = False, store_node: MemoryNode | None = None) -> str:
        """Append an event and optional typed store intent/work atomically."""
        from prme.storage.metadata import snapshot_metadata

        event = event.model_copy(update={"metadata": snapshot_metadata(event.metadata)})
        if store_node is not None:
            store_node = store_node.model_copy(update={"metadata": snapshot_metadata(store_node.metadata)})
        record = None
        if store_node is not None:
            if defer_extraction:
                raise ValueError("Direct store work cannot also request LLM extraction")
            record = DirectStoreRecord.model_validate_json(DirectStoreRecord(
                schema_version=1 if store_node.content == event.content else 2,
                event_id=event.id, content_hash=event.content_hash, node=store_node,
            ).model_dump_json())
            record.verify_source(event)
            defer_materialization = True
        metadata_json = (
            json.dumps(event.metadata) if event.metadata is not None else None
        )
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO events (
                        id, timestamp, role, content, content_hash,
                        user_id, session_id, scope, metadata, created_at, event_time
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11)
                    """,
                    str(event.id),
                    event.timestamp,
                    event.role,
                    event.content,
                    event.content_hash,
                    event.user_id,
                    event.session_id,
                    event.scope.value,
                    metadata_json,
                    event.created_at,
                    event.event_time,
                )
                if defer_materialization:
                    await conn.execute(
                        "INSERT INTO event_materializations (event_id) VALUES ($1)",
                        str(event.id),
                    )
                if defer_extraction:
                    await conn.execute("INSERT INTO event_extractions (event_id) VALUES ($1)", str(event.id))
                if record is not None:
                    await conn.execute(
                        "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                        "VALUES ($1, 'DIRECT_STORE_REQUESTED', $2, $3::jsonb, 'store', $4, $5)",
                        record.operation_id, str(event.id), record.operation_payload(), event.scope.value, event.created_at,
                    )
        return str(event.id)

    async def append_many(
        self,
        events: Sequence[Event],
        *,
        defer_materialization: bool = False,
        record: FastIngestBatchRecord | None = None,
    ) -> FastIngestAdmission:
        """Atomically append an ordered raw-event batch and optional repair work."""
        from prme.storage.metadata import snapshot_metadata

        snapshots = tuple(
            event.model_copy(update={"metadata": snapshot_metadata(event.metadata)})
            for event in events
        )
        event_ids = [str(event.id) for event in snapshots]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("A raw event batch cannot contain duplicate IDs")
        if record is not None:
            validate_fast_ingest_record(record, snapshots)
        if record is not None and not defer_materialization:
            raise ValueError("Fast-ingest retry identity requires durable materialization")
        if not snapshots:
            return FastIngestAdmission(event_ids=())
        rows = [self._event_values(event) for event in snapshots]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if record is not None:
                    existing = await conn.fetchrow(
                        "SELECT op_type,payload FROM operations WHERE id=$1",
                        str(record.operation_id),
                    )
                    replayed = replay_fast_ingest(
                        tuple(existing) if existing is not None else None,
                        record,
                    )
                    if replayed is not None:
                        await self._validate_fast_ingest_replay(
                            conn, replayed, record.user_id
                        )
                        return replayed
                    inserted = await conn.fetchval(
                        """INSERT INTO operations
                        (id, op_type, target_id, payload, actor_id, created_at)
                        VALUES ($1, 'FAST_INGEST_BATCH', $2, $3::jsonb, $4, $5)
                        ON CONFLICT (id) DO NOTHING RETURNING id""",
                        str(record.operation_id),
                        str(record.request_id),
                        fast_ingest_payload(record),
                        record.user_id,
                        record.admitted_at,
                    )
                    if inserted is None:
                        existing = await conn.fetchrow(
                            "SELECT op_type,payload FROM operations WHERE id=$1",
                            str(record.operation_id),
                        )
                        replayed = replay_fast_ingest(
                            tuple(existing) if existing is not None else None,
                            record,
                        )
                        if replayed is None:
                            raise ValueError(
                                "Fast-ingest request conflict has no durable journal"
                            )
                        await self._validate_fast_ingest_replay(
                            conn, replayed, record.user_id
                        )
                        return replayed
                await conn.executemany(
                    """
                    INSERT INTO events (
                        id, timestamp, role, content, content_hash,
                        user_id, session_id, scope, metadata, created_at, event_time
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11)
                    """,
                    rows,
                )
                if defer_materialization:
                    await conn.executemany(
                        "INSERT INTO event_materializations (event_id) VALUES ($1)",
                        [(event_id,) for event_id in event_ids],
                    )
        return FastIngestAdmission(event_ids=tuple(event.id for event in snapshots))

    @staticmethod
    async def _validate_fast_ingest_replay(
        conn,
        admission: FastIngestAdmission,
        user_id: str,
    ) -> None:
        for event_id in admission.event_ids:
            row = await conn.fetchrow(
                """SELECT e.id FROM events e
                JOIN event_materializations m ON m.event_id=e.id
                WHERE e.id=$1 AND e.user_id=$2""",
                str(event_id),
                user_id,
            )
            if row is None:
                raise ValueError("Fast-ingest journal references unavailable work")

    @staticmethod
    def _event_values(event: Event) -> tuple[object, ...]:
        metadata_json = (
            json.dumps(event.metadata) if event.metadata is not None else None
        )
        return (
            str(event.id),
            event.timestamp,
            event.role,
            event.content,
            event.content_hash,
            event.user_id,
            event.session_id,
            event.scope.value,
            metadata_json,
            event.created_at,
            event.event_time,
        )

    async def get_direct_store(self, event_id: str, *, user_id: str) -> DirectStoreRecord | None:
        """Read initial typed values only through their source owner's scope."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT o.payload FROM operations o JOIN events e ON o.target_id = e.id::text "
                "WHERE o.id = $1 AND o.op_type = 'DIRECT_STORE_REQUESTED' AND e.id = $2 AND e.user_id = $3",
                direct_store_operation_id(event_id), event_id, user_id,
            )
        if row is None:
            return None
        payload = row["payload"]
        record = DirectStoreRecord.from_operation_payload(payload)
        event = await self.get(event_id)
        if event is None:
            raise ValueError("Direct store source is missing")
        record.verify_source(event)
        return record

    async def pending_materializations(
        self, *, user_id: str | None = None, limit: int = 500,
    ) -> list[Event]:
        columns = ", ".join(f"e.{c.strip()}" for c in _EVENT_COLUMNS.split(","))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {columns} FROM events e JOIN event_materializations m "
                "ON e.id = m.event_id WHERE m.status = 'pending' "
                "AND ($1::varchar IS NULL OR e.user_id = $1) "
                "ORDER BY m.attempts, e.timestamp, e.id LIMIT $2", user_id, limit,
            )
        return [self._record_to_event(row) for row in rows]

    async def materialization_count(self) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM event_materializations WHERE status = 'pending'"
            )

    async def finish_materialization(self, event_id: str, *, error: str | None = None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE event_materializations SET status = $1, attempts = attempts + 1, "
                "last_error = $2, updated_at = now() WHERE event_id = $3 "
                "AND status = 'pending'",
                "complete" if error is None else "pending", error, event_id,
            )

    async def processing_status(self, event_id: str, *, user_id: str) -> ProcessingStatus | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT m.event_id, m.status, m.attempts, m.last_error, m.updated_at "
                "FROM event_materializations m JOIN events e ON e.id = m.event_id "
                "WHERE m.event_id = $1 AND e.user_id = $2", event_id, user_id,
            )
        return ProcessingStatus(**dict(row)) if row is not None else None

    async def processing_counts(self, *, user_id: str) -> tuple[int, int]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT count(*), count(m.last_error) FROM event_materializations m "
                "JOIN events e ON e.id = m.event_id WHERE m.status = 'pending' AND e.user_id = $1",
                user_id,
            )
        return row[0], row[1]

    async def get(self, event_id: str) -> Event | None:
        """Retrieve an event by its ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = $1",
                event_id,
            )
        if row is None:
            return None
        return self._record_to_event(row)

    async def get_by_user(
        self,
        user_id: str,
        *,
        session_id: str | None = None,
        scopes: list[Scope] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Retrieve events for a user, optionally filtered by session and scope."""
        conditions: list[str] = ["user_id = $1"]
        params: list = [user_id]
        idx = 2

        if session_id is not None:
            conditions.append(f"session_id = ${idx}")
            params.append(session_id)
            idx += 1

        if scopes is not None:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(scopes)))
            conditions.append(f"scope IN ({placeholders})")
            params.extend(s.value for s in scopes)
            idx += len(scopes)

        where = " AND ".join(conditions)
        query = (
            f"SELECT {_EVENT_COLUMNS} FROM events "
            f"WHERE {where} "
            f"ORDER BY timestamp DESC, id DESC "
            f"LIMIT ${idx} OFFSET ${idx + 1}"
        )
        params.extend([limit, offset])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._record_to_event(row) for row in rows]

    async def get_by_hash(
        self,
        content_hash: str,
        user_id: str,
        *,
        scopes: list[Scope] | None = None,
    ) -> list[Event]:
        """Find events with the same content hash for dedup detection."""
        conditions: list[str] = ["content_hash = $1", "user_id = $2"]
        params: list = [content_hash, user_id]
        idx = 3

        if scopes is not None:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(scopes)))
            conditions.append(f"scope IN ({placeholders})")
            params.extend(s.value for s in scopes)

        where = " AND ".join(conditions)
        query = (
            f"SELECT {_EVENT_COLUMNS} FROM events "
            f"WHERE {where} "
            f"ORDER BY timestamp DESC, id DESC"
        )

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._record_to_event(row) for row in rows]

    # --- Row conversion ---

    @staticmethod
    def _record_to_event(row: asyncpg.Record) -> Event:
        """Convert an asyncpg Record to an Event model instance."""
        raw_id = row["id"]
        event_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))

        raw_ts = row["timestamp"]
        ts = (
            raw_ts.replace(tzinfo=timezone.utc)
            if isinstance(raw_ts, datetime) and raw_ts.tzinfo is None
            else raw_ts
        )

        raw_created = row["created_at"]
        created_at = (
            raw_created.replace(tzinfo=timezone.utc)
            if isinstance(raw_created, datetime) and raw_created.tzinfo is None
            else raw_created
        )

        raw_metadata = row["metadata"]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        scope_value = row["scope"]
        scope = Scope(scope_value) if scope_value is not None else Scope.PERSONAL

        return Event.model_validate({
            "id": event_id,
            "timestamp": ts,
            "role": row["role"],
            "content": row["content"],
            "content_hash": row["content_hash"],
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "scope": scope,
            "metadata": metadata,
            "created_at": created_at,
            "event_time": row.get("event_time"),
        })
