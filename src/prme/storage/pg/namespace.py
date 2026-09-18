"""Identity-checked schema leases over one application-owned PostgreSQL pool.

These are internal storage capabilities, not database authorization grants.
Application code must not expose their raw connections to untrusted callers.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg

from prme.storage.namespace_identity import NamespaceIdentityError
from prme.storage.pg.schema import initialize_pg_database
from prme.storage.pg.vector_sql import VectorSQL, quote_identifier, resolve_vector_sql


def namespace_schema(identifier: UUID) -> str:
    return "prme_ns_" + identifier.hex


async def _identity(conn, schema: str, expected: UUID):
    if await conn.fetchval("SELECT pg_catalog.current_schema()") != schema:
        raise NamespaceIdentityError("Namespace schema is missing or inaccessible")
    try:
        rows = await conn.fetch(
            f"SELECT version, namespace_id, relations FROM {quote_identifier(schema)}.prme_namespace_identity"
        )
    except asyncpg.UndefinedTableError as exc:
        raise NamespaceIdentityError("Existing schema has no namespace identity; explicit import is required") from exc
    if len(rows) != 1 or rows[0]["version"] != 1 or rows[0]["namespace_id"] != expected:
        raise NamespaceIdentityError("PostgreSQL namespace identity does not match")
    return rows[0]


class NamespacePool:
    """An engine owns this facade; the workspace owns its underlying pool.

    Every checkout clears temporary relations, selects only its private schema,
    and verifies durable identity. Closing drains queued and active checkouts,
    without closing another namespace's connections.
    """

    def __init__(self, pool: asyncpg.Pool, identifier: UUID, vector_sql: VectorSQL):
        self._pool = pool
        self.identifier = identifier
        self.schema = namespace_schema(identifier)
        self.vector_sql = vector_sql
        self._closing = False
        self._active = 0
        self._drained = asyncio.Event()
        self._drained.set()

    @asynccontextmanager
    async def acquire(self):
        if self._closing:
            raise RuntimeError("PostgreSQL namespace pool is closed")
        self._active += 1
        self._drained.clear()
        try:
            async with self._pool.acquire() as conn:
                # asyncpg's normal reset does not remove temporary relations.
                # pg_temp otherwise precedes even a private-only search path.
                await conn.execute("DISCARD TEMP")
                await conn.execute("SELECT pg_catalog.set_config('search_path', $1, false)", quote_identifier(self.schema))
                await _identity(conn, self.schema, self.identifier)
                yield conn
        finally:
            self._active -= 1
            if not self._active:
                self._drained.set()

    async def close(self):
        self._closing = True
        await self._drained.wait()


class _ConnectionPool:
    """Run the ordinary initializer on the already fenced transaction."""

    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


async def prepare_namespace(pool: asyncpg.Pool, identifier: UUID, *, embedding_dim: int,
                            create: bool = False) -> NamespacePool:
    """Atomically create or reopen a bound namespace, refusing silent data loss.

    The caller first installs extensions outside private schemas. Serialized
    initialization can add tables during an upgrade, but cannot recreate a
    previously initialized table that has disappeared.
    """
    schema = namespace_schema(identifier)
    quoted = quote_identifier(schema)
    async with pool.acquire() as conn:
        await conn.execute("DISCARD TEMP")
        await resolve_vector_sql(conn)
        async with conn.transaction():
            await conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))", schema)
            exists = await conn.fetchval("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = $1", schema)
            if not exists:
                if not create:
                    raise NamespaceIdentityError("Initialized namespace schema is missing; restore before opening")
                await conn.execute(f"CREATE SCHEMA {quoted}")
                await conn.execute(
                    f"CREATE TABLE {quoted}.prme_namespace_identity ("
                    "singleton BOOLEAN PRIMARY KEY CHECK(singleton), version INTEGER NOT NULL CHECK(version = 1), "
                    "namespace_id UUID NOT NULL, relations TEXT[] NOT NULL)"
                )
                await conn.execute(f"INSERT INTO {quoted}.prme_namespace_identity VALUES (true, 1, $1, '{{}}')", identifier)
            await conn.execute("SELECT pg_catalog.set_config('search_path', $1, true)", quoted)
            identity = await _identity(conn, schema, identifier)
            present = await _relations(conn, schema)
            if not set(identity["relations"]).issubset(present):
                raise NamespaceIdentityError("Initialized namespace relations are missing; restore before opening")
            vector_sql = await initialize_pg_database(_ConnectionPool(conn), embedding_dim=embedding_dim)
            await conn.execute(f"UPDATE {quoted}.prme_namespace_identity SET relations = $1", await _relations(conn, schema))
    return NamespacePool(pool, identifier, vector_sql)


async def _relations(conn, schema: str) -> list[str]:
    return [row["relname"] for row in await conn.fetch(
        "SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid "
        "WHERE n.nspname = $1 AND c.relkind IN ('r', 'p', 'S') ORDER BY c.relname", schema
    )]
