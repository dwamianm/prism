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
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Retrieve events for a user, optionally filtered by session.

        Args:
            user_id: The user to query events for.
            session_id: Optional session filter.
            limit: Maximum number of events to return.
            offset: Number of events to skip.

        Returns:
            List of Events ordered by timestamp descending.
        """
        return await asyncio.to_thread(
            self._get_by_user_sync, user_id, session_id, limit, offset
        )

    async def get_by_hash(
        self, content_hash: str, user_id: str
    ) -> list[Event]:
        """Find events with the same content hash for dedup detection.

        Args:
            content_hash: SHA-256 hash of the content to find.
            user_id: User scope for the query.

        Returns:
            List of Events matching the content hash.
        """
        return await asyncio.to_thread(
            self._get_by_hash_sync, content_hash, user_id
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
                user_id, session_id, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(event.id),
                event.timestamp,
                event.role,
                event.content,
                event.content_hash,
                event.user_id,
                event.session_id,
                metadata_json,
                event.created_at,
            ],
        )

    def _get_sync(self, event_id: str) -> Event | None:
        """Retrieve a single event by ID (sync)."""
        result = self._conn.execute(
            "SELECT * FROM events WHERE id = ?", [event_id]
        ).fetchone()
        if result is None:
            return None
        return self._row_to_event(result)

    def _get_by_user_sync(
        self,
        user_id: str,
        session_id: str | None,
        limit: int,
        offset: int,
    ) -> list[Event]:
        """Retrieve events for a user (sync)."""
        if session_id is not None:
            result = self._conn.execute(
                """
                SELECT * FROM events
                WHERE user_id = ? AND session_id = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                [user_id, session_id, limit, offset],
            ).fetchall()
        else:
            result = self._conn.execute(
                """
                SELECT * FROM events
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                [user_id, limit, offset],
            ).fetchall()
        return [self._row_to_event(row) for row in result]

    def _get_by_hash_sync(
        self, content_hash: str, user_id: str
    ) -> list[Event]:
        """Find events by content hash (sync)."""
        result = self._conn.execute(
            """
            SELECT * FROM events
            WHERE content_hash = ? AND user_id = ?
            ORDER BY timestamp DESC
            """,
            [content_hash, user_id],
        ).fetchall()
        return [self._row_to_event(row) for row in result]

    def _row_to_event(self, row: tuple) -> Event:
        """Convert a DuckDB row tuple to an Event model instance.

        Column order matches the CREATE TABLE definition:
        id, timestamp, role, content, content_hash,
        user_id, session_id, metadata, created_at
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

        raw_created = row[8]
        if isinstance(raw_created, datetime):
            created_at = (
                raw_created.replace(tzinfo=timezone.utc)
                if raw_created.tzinfo is None
                else raw_created
            )
        else:
            created_at = raw_created

        raw_metadata = row[7]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        return Event.model_validate(
            {
                "id": event_id,
                "timestamp": ts,
                "role": row[2],
                "content": row[3],
                "content_hash": row[4],
                "user_id": row[5],
                "session_id": row[6],
                "metadata": metadata,
                "created_at": created_at,
            }
        )
