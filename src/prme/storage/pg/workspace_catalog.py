"""Transactional names and schema ownership for PostgreSQL workspaces."""

from uuid import UUID, uuid4

import asyncpg

from prme.storage.namespace_identity import NamespaceIdentityError
from prme.storage.pg.namespace import NamespacePool, _ConnectionPool, prepare_namespace


class WorkspaceCatalog:
    def __init__(self, pool, name: str):
        self.pool = pool
        self.name = name

    async def initialize(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DISCARD TEMP")
            await conn.execute("SELECT pg_catalog.set_config('search_path', 'pg_catalog', false)")
            async with conn.transaction():
                await conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('prme_workspace_catalog_v1', 0))")
                exists = await conn.fetchval("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname='prme_workspace'")
                if not exists:
                    if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace "
                                           "WHERE nspname ~ '^prme_ns_[0-9a-f]{32}$')"):
                        raise NamespaceIdentityError("Workspace registry is missing while namespace schemas exist; restore before opening")
                    await conn.execute("CREATE SCHEMA prme_workspace")
                    await conn.execute("CREATE TABLE prme_workspace.metadata (singleton BOOLEAN PRIMARY KEY CHECK(singleton), "
                                       "version INTEGER NOT NULL)")
                    await conn.execute("INSERT INTO prme_workspace.metadata VALUES (true, 1)")
                    await conn.execute("CREATE TABLE prme_workspace.namespaces (workspace TEXT COLLATE \"C\" NOT NULL, "
                                       "name TEXT COLLATE \"C\" NOT NULL, id UUID UNIQUE NOT NULL, "
                                       "initialized BOOLEAN NOT NULL DEFAULT false, PRIMARY KEY (workspace, name))")
                try:
                    rows = await conn.fetch("SELECT version FROM prme_workspace.metadata")
                    if [row["version"] for row in rows] != [1]:
                        raise NamespaceIdentityError("Unsupported PostgreSQL workspace registry version")
                    await conn.fetch("SELECT workspace, name, id, initialized FROM prme_workspace.namespaces LIMIT 1")
                except asyncpg.UndefinedTableError as exc:
                    raise NamespaceIdentityError("PostgreSQL workspace registry is incomplete; restore before opening") from exc
                # Existing extensions retain their installed schema. New shared
                # extensions are never installed inside a project's namespace.
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
                await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")

    async def resolve(self, name: str, *, create: bool) -> UUID:
        async with self.pool.acquire() as conn, conn.transaction():
            if create:
                await conn.execute("INSERT INTO prme_workspace.namespaces (workspace, name, id) VALUES ($1, $2, $3) "
                                   "ON CONFLICT (workspace, name) DO NOTHING", self.name, name, uuid4())
            identifier = await conn.fetchval("SELECT id FROM prme_workspace.namespaces WHERE workspace=$1 AND name=$2",
                                             self.name, name)
            if identifier is None:
                raise KeyError(name)
            return identifier

    async def list_namespaces(self) -> list[tuple[str, UUID]]:
        async with self.pool.acquire() as conn:
            return [(r['name'], r['id']) for r in await conn.fetch(
                "SELECT name, id FROM prme_workspace.namespaces WHERE workspace=$1 ORDER BY name", self.name)]

    async def prepare(self, name: str, identifier: UUID, *, embedding_dim: int) -> NamespacePool:
        async with self.pool.acquire() as conn:
            await conn.execute("DISCARD TEMP")
            async with conn.transaction():
                row = await conn.fetchrow("SELECT id, initialized FROM prme_workspace.namespaces "
                                          "WHERE workspace=$1 AND name=$2 FOR UPDATE", self.name, name)
                if row is None or row['id'] != identifier:
                    raise NamespaceIdentityError("PostgreSQL workspace registry identity changed")
                # Registry publication and initial schema/identity creation have
                # one commit, including on cancellation or process failure.
                prepared = await prepare_namespace(_ConnectionPool(conn), identifier, embedding_dim=embedding_dim,
                                                    create=not row['initialized'])
                await conn.execute("UPDATE prme_workspace.namespaces SET initialized=true WHERE id=$1", identifier)
        return NamespacePool(self.pool, identifier, prepared.vector_sql)
