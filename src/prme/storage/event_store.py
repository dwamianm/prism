"""Async EventStore wrapping DuckDB append-only event log.

The EventStore provides async methods for appending and retrieving
immutable events. All DuckDB operations are run via asyncio.to_thread
to avoid blocking the event loop.
"""

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

import duckdb

from prme.models import Event
from prme.types import Scope

# Explicit column list used in all SELECT queries to avoid positional
# dependency issues when the schema evolves (e.g., ALTER TABLE adds
# columns at the end rather than at the CREATE TABLE position).
_EVENT_COLUMNS = (
    "id, timestamp, role, content, content_hash, "
    "user_id, session_id, scope, metadata, created_at"
)


class EventStore:
    """Async event store backed by DuckDB.

    Provides append-only event storage with retrieval by ID,
    user_id, session_id, and content_hash. All queries enforce
    user_id scoping to prevent cross-user data leakage.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self._write_lock = asyncio.Lock()

    # --- Public async API ---

    async def append(self, event: Event) -> str:
        """Append an event to the immutable event log.

        Args:
            event: The Event to store.

        Returns:
            The string representation of the event's UUID.
        """
        async with self._write_lock:
            await asyncio.to_thread(self._append_sync, event)
        return str(event.id)

    async def get(self, event_id: str) -> Event | None:
        """Retrieve an event by its ID.

        Args:
            event_id: String UUID of the event.

        Returns:
            The Event if found, None otherwise.
        """
        return await asyncio.to_thread(self._get_sync, event_id)

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
        return await asyncio.to_thread(
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
        return await asyncio.to_thread(
            self._get_by_hash_sync, content_hash, user_id, scopes
        )

    # --- Internal sync methods ---

    def _append_sync(self, event: Event) -> None:
        """Insert an event into the events table (sync)."""
        metadata_json = (
            json.dumps(event.metadata) if event.metadata is not None else None
        )
        self._conn.execute(
            """
            INSERT INTO events (
                id, timestamp, role, content, content_hash,
                user_id, session_id, scope, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
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
            ],
        )

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
            f"ORDER BY timestamp DESC "
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
            f"ORDER BY timestamp DESC"
        )

        result = self._conn.execute(query, params).fetchall()
        return [self._row_to_event(row) for row in result]

    def _row_to_event(self, row: tuple) -> Event:
        """Convert a DuckDB row tuple to an Event model instance.

        Column order matches the explicit SELECT column list:
        id(0), timestamp(1), role(2), content(3), content_hash(4),
        user_id(5), session_id(6), scope(7), metadata(8), created_at(9)
        """
        raw_id = row[0]
        event_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))

        raw_ts = row[1]
        if isinstance(raw_ts, datetime):
            ts = (
                raw_ts.replace(tzinfo=timezone.utc)
                if raw_ts.tzinfo is None
                else raw_ts
            )
        else:
            ts = raw_ts

        raw_created = row[9]
        if isinstance(raw_created, datetime):
            created_at = (
                raw_created.replace(tzinfo=timezone.utc)
                if raw_created.tzinfo is None
                else raw_created
            )
        else:
            created_at = raw_created

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
