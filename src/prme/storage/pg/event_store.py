"""PostgreSQL-backed async EventStore.

Implements the same interface as ``EventStore`` (DuckDB) using asyncpg
for natively async PostgreSQL access. No conn_lock or to_thread needed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from prme.models import Event
from prme.types import Scope

logger = logging.getLogger(__name__)

_EVENT_COLUMNS = (
    "id, timestamp, role, content, content_hash, "
    "user_id, session_id, scope, metadata, created_at"
)


class PgEventStore:
    """Async event store backed by PostgreSQL via asyncpg.

    All queries enforce user_id scoping to prevent cross-user data leakage.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(self, event: Event) -> str:
        """Append an event to the immutable event log."""
        metadata_json = (
            json.dumps(event.metadata) if event.metadata is not None else None
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO events (
                    id, timestamp, role, content, content_hash,
                    user_id, session_id, scope, metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
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
            )
        return str(event.id)

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
            f"ORDER BY timestamp DESC "
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
            f"ORDER BY timestamp DESC"
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
        })
