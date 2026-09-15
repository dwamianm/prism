"""Async EventStore wrapping DuckDB append-only event log.

The EventStore provides async methods for appending and retrieving
immutable events. All DuckDB operations are run via asyncio.to_thread
to avoid blocking the event loop.
"""

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

import duckdb

from prme.models import Event, MemoryNode, ProcessingStatus
from prme.models.direct_store import DirectStoreRecord, direct_store_operation_id
from prme.models.extraction import ExtractionRecord, extraction_operation_id
from prme.models.derivation import DerivationPlan, DerivationReceipt, derivation_operation_id
from prme.storage._threading import run_to_completion
from prme.storage.extraction_work import ExtractionWorkRepository, insert_duck_work, validate_duck_claim
from prme.models.extraction_work import ExtractionClaim
from prme.types import Scope

# Explicit column list used in all SELECT queries to avoid positional
# dependency issues when the schema evolves (e.g., ALTER TABLE adds
# columns at the end rather than at the CREATE TABLE position).
_EVENT_COLUMNS = (
    "id, timestamp, role, content, content_hash, "
    "user_id, session_id, scope, metadata, created_at, event_time"
)


class EventStore:
    """Async event store backed by DuckDB.

    Provides append-only event storage with retrieval by ID,
    user_id, session_id, and content_hash. All queries enforce
    user_id scoping to prevent cross-user data leakage.
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        conn_lock: asyncio.Lock | None = None,
    ) -> None:
        self._conn = conn
        self._conn_lock = conn_lock if conn_lock is not None else asyncio.Lock()
        self.extraction_work = ExtractionWorkRepository(conn=conn, conn_lock=self._conn_lock)

    # --- Public async API ---

    async def get_derivation_receipt(self, event_id: str, *, user_id: str) -> DerivationReceipt | None:
        """Read verified completion through the immutable source owner's scope."""
        async with self._conn_lock:
            return await run_to_completion(self._get_derivation_receipt_sync, event_id, user_id)

    def _get_derivation_receipt_sync(self, event_id: str, user_id: str) -> DerivationReceipt | None:
        from prme.storage.derivation import _receipt

        event_id = str(UUID(event_id))
        plan = self._get_derivation_plan_sync(event_id, user_id)
        if plan is None:
            return None
        row = self._conn.execute(
            "SELECT payload FROM operations WHERE id = ? AND target_id = ? "
            "AND op_type = 'DERIVATION_COMMITTED'", [plan.receipt_operation_id, event_id],
        ).fetchone()
        return _receipt(plan, row[0] if row else None)

    async def get_derivation_plan(self, event_id: str, *, user_id: str, revision: int | None = None) -> DerivationPlan | None:
        """Read the immutable prepared derivation through the source owner."""
        async with self._conn_lock:
            return await run_to_completion(self._get_derivation_plan_sync, event_id, user_id, revision)

    def _get_derivation_plan_sync(self, event_id: str, user_id: str, revision: int | None = None) -> DerivationPlan | None:
        if revision is None:
            work = self._conn.execute("SELECT plan_revision FROM event_extractions WHERE event_id = ?", [event_id]).fetchone()
            revision = work[0] if work else 1
        row = self._conn.execute(
            "SELECT o.payload, e.scope, e.content_hash FROM operations o JOIN events e "
            "ON o.target_id = e.id::VARCHAR WHERE o.id = ? AND e.id = ? "
            "AND e.user_id = ? AND o.op_type = 'DERIVATION_PREPARED'",
            [derivation_operation_id(event_id, revision=revision), event_id, user_id],
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        plan = (DerivationPlan.model_validate_json(payload["plan"]) if isinstance(payload["plan"], str)
                else DerivationPlan.model_validate(payload["plan"]))
        plan.verify_source(user_id, row[1], row[2])
        if plan.event_id != UUID(event_id) or plan.revision != revision or plan.checksum != payload["checksum"]:
            raise ValueError("Prepared derivation identity or checksum does not match")
        return plan

    async def record_derivation_plan(self, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> DerivationPlan:
        """Save the first prepared plan; same-ID payload changes are rejected."""
        snapshot = DerivationPlan.model_validate_json(plan.model_dump_json())
        async with self._conn_lock:
            return await run_to_completion(self._record_derivation_plan_sync, snapshot, claim)

    def _record_derivation_plan_sync(self, plan: DerivationPlan, claim: ExtractionClaim | None = None) -> DerivationPlan:
        self._conn.execute("BEGIN TRANSACTION")
        try:
            managed = validate_duck_claim(self._conn, str(plan.event_id), plan.user_id, claim)
            source = self._conn.execute(
                "SELECT user_id, scope, content_hash FROM events WHERE id = ?", [str(plan.event_id)],
            ).fetchone()
            if source is None:
                raise ValueError("A derivation requires a persisted source event")
            plan.verify_source(*source)
            if plan.revision != (managed["plan_revision"] if managed else 1):
                raise ValueError("Prepared plan does not match the current work revision")
            self._conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                "VALUES (?, 'DERIVATION_PREPARED', ?, ?, 'derivation', ?, ?) ON CONFLICT (id) DO NOTHING",
                [plan.prepared_operation_id, str(plan.event_id),
                 json.dumps({"plan": plan.model_dump_json(), "checksum": plan.checksum}),
                 plan.scope.value, plan.created_at],
            )
            saved = self._get_derivation_plan_sync(str(plan.event_id), plan.user_id)
            if saved is None or (saved.id == plan.id and saved.checksum != plan.checksum):
                raise ValueError("Prepared derivation ID conflicts with a different payload")
            from prme.storage.derivation_registry import register_duck
            register_duck(self._conn, saved)
            if managed is not None:
                self._conn.execute("UPDATE event_extractions SET plan_id = ? WHERE event_id = ?",
                                   [str(saved.id), str(plan.event_id)])
            validate_duck_claim(self._conn, str(plan.event_id), plan.user_id, claim)
            self._conn.execute("COMMIT")
            return saved
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    async def get_extraction(self, event_id: str, *, user_id: str) -> ExtractionRecord | None:
        """Read the saved extraction only through its source owner's boundary."""
        async with self._conn_lock:
            return await run_to_completion(self._get_extraction_sync, event_id, user_id)

    def _get_extraction_sync(self, event_id: str, user_id: str) -> ExtractionRecord | None:
        row = self._conn.execute(
            "SELECT o.payload, e.scope, e.content_hash FROM operations o JOIN events e "
            "ON o.target_id = e.id::VARCHAR WHERE o.id = ? AND e.id = ? "
            "AND e.user_id = ? AND o.op_type = 'EXTRACTION_VALIDATED'",
            [extraction_operation_id(event_id), event_id, user_id],
        ).fetchone()
        if row is None:
            return None
        record = ExtractionRecord.model_validate_json(row[0])
        record.verify_source(user_id, row[1], row[2])
        if str(record.event_id) != str(UUID(event_id)):
            raise ValueError("Extraction record does not match its source event")
        return record

    async def record_extraction(self, record: ExtractionRecord, *, claim: ExtractionClaim | None = None) -> ExtractionRecord:
        """Append once; concurrent attempts reuse the first durable extraction."""
        # Serialize before yielding so mutation of a caller's nested dict cannot
        # change the durable payload while a worker thread is waiting to run.
        snapshot = ExtractionRecord.model_validate_json(record.model_dump_json())
        async with self._conn_lock:
            return await run_to_completion(self._record_extraction_sync, snapshot, claim)

    def _record_extraction_sync(self, record: ExtractionRecord, claim: ExtractionClaim | None = None) -> ExtractionRecord:
        self._conn.execute("BEGIN TRANSACTION")
        try:
            validate_duck_claim(self._conn, str(record.event_id), record.user_id, claim)
            source = self._conn.execute(
                "SELECT user_id, scope, content_hash FROM events WHERE id = ?", [str(record.event_id)],
            ).fetchone()
            if source is None:
                raise ValueError("Extraction requires a matching persisted source event")
            record.verify_source(*source)
            self._conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, "
                "namespace_id, created_at) VALUES (?, 'EXTRACTION_VALIDATED', ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO NOTHING",
                [record.operation_id, str(record.event_id), record.model_dump_json(),
                 "extraction", record.scope.value, record.created_at],
            )
            saved = self._get_extraction_sync(str(record.event_id), record.user_id)
            if saved is None:
                raise ValueError("Extraction operation ID conflicts with another operation")
            validate_duck_claim(self._conn, str(record.event_id), record.user_id, claim)
            self._conn.execute("COMMIT")
            return saved
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    async def append(self, event: Event, *, defer_materialization: bool = False,
                     defer_extraction: bool = False, store_node: MemoryNode | None = None) -> str:
        """Append an event to the immutable event log.

        Args:
            event: The Event to store.

        Returns:
            The string representation of the event's UUID.
        """
        from prme.storage.metadata import snapshot_metadata
        event = event.model_copy(update={"metadata": snapshot_metadata(event.metadata)})
        if store_node is not None:
            store_node = store_node.model_copy(update={"metadata": snapshot_metadata(store_node.metadata)})
        record = None
        if store_node is not None:
            if defer_extraction:
                raise ValueError("Direct store work cannot also request LLM extraction")
            record = DirectStoreRecord.model_validate_json(DirectStoreRecord(
                event_id=event.id, content_hash=event.content_hash, node=store_node,
            ).model_dump_json())
            record.verify_source(event)
            defer_materialization = True
        async with self._conn_lock:
            await run_to_completion(self._append_with_work_sync, event, defer_materialization, defer_extraction, record)
        return str(event.id)

    async def append_many(
        self,
        events: Sequence[Event],
        *,
        defer_materialization: bool = False,
    ) -> list[str]:
        """Atomically append an ordered raw-event batch and optional repair work.

        This admission path intentionally excludes direct-store snapshots and
        extraction work. It backs ``ingest_fast_many()``, whose raw NOTE
        materialization is deterministic from each immutable event.
        """
        from prme.storage.metadata import snapshot_metadata

        snapshots = tuple(
            event.model_copy(update={"metadata": snapshot_metadata(event.metadata)})
            for event in events
        )
        event_ids = [str(event.id) for event in snapshots]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("A raw event batch cannot contain duplicate IDs")
        if not snapshots:
            return []
        async with self._conn_lock:
            await run_to_completion(
                self._append_many_sync,
                snapshots,
                defer_materialization,
            )
        return event_ids

    def _append_many_sync(
        self,
        events: tuple[Event, ...],
        defer_materialization: bool,
    ) -> None:
        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._conn.executemany(
                """
                INSERT INTO events (
                    id, timestamp, role, content, content_hash,
                    user_id, session_id, scope, metadata, created_at,
                    event_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._event_values(event) for event in events],
            )
            if defer_materialization:
                self._conn.executemany(
                    "INSERT INTO event_materializations (event_id) VALUES (?)",
                    [(str(event.id),) for event in events],
                )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def _append_with_work_sync(self, event: Event, deferred: bool, extraction: bool = False, record: DirectStoreRecord | None = None) -> None:
        if not deferred and not extraction:
            self._append_sync(event)
            return
        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._append_sync(event)
            if deferred:
                self._conn.execute(
                    "INSERT INTO event_materializations (event_id) VALUES (?)", [str(event.id)],
                )
            if extraction:
                insert_duck_work(self._conn, str(event.id))
            if record is not None:
                self._conn.execute(
                    "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                    "VALUES (?, 'DIRECT_STORE_REQUESTED', ?, ?, 'store', ?, ?)",
                    [record.operation_id, str(event.id), record.operation_payload(), event.scope.value, event.created_at],
                )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    async def get_direct_store(self, event_id: str, *, user_id: str) -> DirectStoreRecord | None:
        """Read initial typed values only through their source owner's scope."""
        async with self._conn_lock:
            row = await run_to_completion(lambda: self._conn.execute(
                "SELECT o.payload FROM operations o JOIN events e ON o.target_id = e.id::VARCHAR "
                "WHERE o.id = ? AND o.op_type = 'DIRECT_STORE_REQUESTED' AND e.id = ? AND e.user_id = ?",
                [direct_store_operation_id(event_id), event_id, user_id],
            ).fetchone())
        if row is None:
            return None
        record = DirectStoreRecord.from_operation_payload(row[0])
        event = await self.get(event_id)
        if event is None:
            raise ValueError("Direct store source is missing")
        record.verify_source(event)
        return record

    async def pending_materializations(
        self, *, user_id: str | None = None, limit: int = 500,
    ) -> list[Event]:
        """Read a bounded batch of durable work, oldest/retried-last first."""
        def read() -> list[Event]:
            columns = ", ".join(f"e.{c.strip()}" for c in _EVENT_COLUMNS.split(","))
            scoped = " AND e.user_id = ?" if user_id is not None else ""
            params = [user_id, limit] if user_id is not None else [limit]
            rows = self._conn.execute(
                f"SELECT {columns} FROM events e JOIN event_materializations m "
                "ON e.id = m.event_id WHERE m.status = 'pending'" + scoped +
                " ORDER BY m.attempts, e.timestamp, e.id LIMIT ?", params,
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

        async with self._conn_lock:
            return await run_to_completion(read)

    async def materialization_count(self) -> int:
        async with self._conn_lock:
            return await run_to_completion(lambda: self._conn.execute(
                "SELECT count(*) FROM event_materializations WHERE status = 'pending'"
            ).fetchone()[0])

    async def finish_materialization(self, event_id: str, *, error: str | None = None) -> None:
        """Acknowledge durable completion or retain a failed item for retry."""
        async with self._conn_lock:
            await run_to_completion(
                self._conn.execute,
                "UPDATE event_materializations SET status = ?, attempts = attempts + 1, "
                "last_error = ?, updated_at = current_timestamp WHERE event_id = ? "
                "AND status = 'pending'",
                ["complete" if error is None else "pending", error, event_id],
            )

    async def processing_status(self, event_id: str, *, user_id: str) -> ProcessingStatus | None:
        async with self._conn_lock:
            row = await run_to_completion(lambda: self._conn.execute(
                "SELECT m.event_id, m.status, m.attempts, m.last_error, m.updated_at "
                "FROM event_materializations m JOIN events e ON e.id = m.event_id "
                "WHERE m.event_id = ? AND e.user_id = ?", [event_id, user_id],
            ).fetchone())
        if row is None:
            return None
        return ProcessingStatus(**dict(zip(
            ("event_id", "status", "attempts", "last_error", "updated_at"), row,
        )))

    async def processing_counts(self, *, user_id: str) -> tuple[int, int]:
        async with self._conn_lock:
            row = await run_to_completion(lambda: self._conn.execute(
                "SELECT count(*), count(m.last_error) FROM event_materializations m "
                "JOIN events e ON e.id = m.event_id WHERE m.status = 'pending' AND e.user_id = ?",
                [user_id],
            ).fetchone())
        return row[0], row[1]

    async def get(self, event_id: str) -> Event | None:
        """Retrieve an event by its ID.

        Args:
            event_id: String UUID of the event.

        Returns:
            The Event if found, None otherwise.
        """
        async with self._conn_lock:
            return await run_to_completion(self._get_sync, event_id)

    async def get_by_user(
        self,
        user_id: str,
        *,
        session_id: str | None = None,
        scopes: list[Scope] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Retrieve events for a user, optionally filtered by session and scope.

        Args:
            user_id: The user to query events for.
            session_id: Optional session filter.
            scopes: Optional list of scopes to filter by. When None,
                returns events from all scopes (backward compatible).
            limit: Maximum number of events to return.
            offset: Number of events to skip.

        Returns:
            List of Events ordered by timestamp descending.
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._get_by_user_sync, user_id, session_id, scopes, limit, offset
            )

    async def get_by_hash(
        self,
        content_hash: str,
        user_id: str,
        *,
        scopes: list[Scope] | None = None,
    ) -> list[Event]:
        """Find events with the same content hash for dedup detection.

        Args:
            content_hash: SHA-256 hash of the content to find.
            user_id: User scope for the query.
            scopes: Optional list of scopes to filter by. When None,
                returns events from all scopes (backward compatible).

        Returns:
            List of Events matching the content hash.
        """
        async with self._conn_lock:
            return await run_to_completion(
                self._get_by_hash_sync, content_hash, user_id, scopes
            )

    # --- Internal sync methods ---

    def _append_sync(self, event: Event) -> None:
        """Insert an event into the events table (sync)."""
        self._conn.execute(
            """
            INSERT INTO events (
                id, timestamp, role, content, content_hash,
                user_id, session_id, scope, metadata, created_at,
                event_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._event_values(event),
        )

    @staticmethod
    def _event_values(event: Event) -> list[object]:
        metadata_json = (
            json.dumps(event.metadata) if event.metadata is not None else None
        )
        return [
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
        ]

    def _get_sync(self, event_id: str) -> Event | None:
        """Retrieve a single event by ID (sync)."""
        result = self._conn.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = ?",
            [event_id],
        ).fetchone()
        if result is None:
            return None
        return self._row_to_event(result)

    def _get_by_user_sync(
        self,
        user_id: str,
        session_id: str | None,
        scopes: list[Scope] | None,
        limit: int,
        offset: int,
    ) -> list[Event]:
        """Retrieve events for a user (sync).

        Builds a dynamic WHERE clause from the provided filters.
        """
        conditions: list[str] = ["user_id = ?"]
        params: list = [user_id]

        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)

        if scopes is not None:
            placeholders = ", ".join(["?" for _ in scopes])
            conditions.append(f"scope IN ({placeholders})")
            params.extend([s.value for s in scopes])

        where_clause = " AND ".join(conditions)
        query = (
            f"SELECT {_EVENT_COLUMNS} FROM events "
            f"WHERE {where_clause} "
            f"ORDER BY timestamp DESC, id DESC "
            f"LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        result = self._conn.execute(query, params).fetchall()
        return [self._row_to_event(row) for row in result]

    def _get_by_hash_sync(
        self,
        content_hash: str,
        user_id: str,
        scopes: list[Scope] | None,
    ) -> list[Event]:
        """Find events by content hash (sync)."""
        conditions: list[str] = ["content_hash = ?", "user_id = ?"]
        params: list = [content_hash, user_id]

        if scopes is not None:
            placeholders = ", ".join(["?" for _ in scopes])
            conditions.append(f"scope IN ({placeholders})")
            params.extend([s.value for s in scopes])

        where_clause = " AND ".join(conditions)
        query = (
            f"SELECT {_EVENT_COLUMNS} FROM events "
            f"WHERE {where_clause} "
            f"ORDER BY timestamp DESC, id DESC"
        )

        result = self._conn.execute(query, params).fetchall()
        return [self._row_to_event(row) for row in result]

    def _row_to_event(self, row: tuple) -> Event:
        """Convert a DuckDB row tuple to an Event model instance.

        Column order matches the explicit SELECT column list:
        id(0), timestamp(1), role(2), content(3), content_hash(4),
        user_id(5), session_id(6), scope(7), metadata(8), created_at(9),
        event_time(10)
        """
        raw_id = row[0]
        event_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))

        def _ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        ts = _ensure_tz(row[1]) if isinstance(row[1], datetime) else row[1]
        created_at = _ensure_tz(row[9]) if isinstance(row[9], datetime) else row[9]

        # Bi-temporal: event_time at column 10 (issue #21)
        raw_event_time = row[10] if len(row) > 10 else None
        event_time = _ensure_tz(raw_event_time)

        raw_metadata = row[8]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        # Map scope VARCHAR back to Scope enum. Works because Scope
        # uses the (str, Enum) pattern for DuckDB VARCHAR compatibility.
        scope_value = row[7]
        scope = Scope(scope_value) if scope_value is not None else Scope.PERSONAL

        return Event.model_validate(
            {
                "id": event_id,
                "timestamp": ts,
                "event_time": event_time,
                "role": row[2],
                "content": row[3],
                "content_hash": row[4],
                "user_id": row[5],
                "session_id": row[6],
                "scope": scope,
                "metadata": metadata,
                "created_at": created_at,
            }
        )
