"""PostgreSQL vectors kept by node, model name and model version (``enable_multi_model_embeddings``, dwamianm/prism#368).

``nodes.embedding`` is one fixed ``vector(N)`` column, so a store is tied to the model whose dimension N it was
created with. With the option on, vectors live in ``node_embeddings`` instead: an untyped ``vector`` column, one row
per node and model, keyed by ``(node_id, model_name, model_version)``. Each model gets its own partial HNSW index on
a dimension cast expression, so models of different dimensions, and different models of the same dimension, never
share an index:

    CREATE INDEX ... ON node_embeddings USING hnsw ((embedding::vector(768)) vector_cosine_ops)
    WHERE model_name = 'BAAI/bge-base-en-v1.5' AND model_version = 'fastembed-0.7.4'

pgvector indexes a ``vector`` column without dimensions only through such a cast, and a partial index only serves a
query that repeats its predicate, so the approximate search spells the model out as literals. (One table per
dimension would put every model of one dimension into a single index, and a search for one model would then filter
the other's rows after the index scan.) Exact search, the default, never uses these indexes.

``PgModelVectorIndex`` is one model's view of the table, with the interface of ``PgVectorIndex``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

import asyncpg

from prme.storage.embedding import EmbeddingProvider, encode_texts
from prme.storage.multi_model import ACTIVE_STATES, ModelIdentity, StoredModel
from prme.storage.pg.vector_index import PgVectorIndex
from prme.storage.pg.vector_sql import VectorSQL, resolve_vector_sql

logger = logging.getLogger(__name__)

# pgvector indexes vectors of up to 2,000 dimensions. A larger model still stores and searches exactly.
MAX_INDEXED_DIMENSIONS = 2000
_ADVISORY_LOCK = "SELECT pg_try_advisory_lock(hashtext('prme.multi_model_embeddings:' || coalesce(current_schema(), '')))"
_ADVISORY_UNLOCK = "SELECT pg_advisory_unlock(hashtext('prme.multi_model_embeddings:' || coalesce(current_schema(), '')))"
_ADOPTION_BATCH = 1000
_STATE_PLACEHOLDERS = ", ".join(f"'{state}'" for state in ACTIVE_STATES)


async def acquire_setup_lock(conn: asyncpg.Connection, *, timeout: float = 900.0) -> None:
    """Take the store's setup lock, waiting without holding a statement open.

    A blocking ``pg_advisory_lock`` is a statement in a transaction, and a ``CREATE INDEX CONCURRENTLY`` in the
    process that holds the lock waits for every such transaction to end, so the two deadlock. Polling the
    try-lock leaves nothing open between tries.
    """
    deadline = time.monotonic() + timeout
    while not await conn.fetchval(_ADVISORY_LOCK):
        if time.monotonic() > deadline:
            raise TimeoutError("Another process has held this store's embedding setup lock for too long")
        await asyncio.sleep(0.05)


def sql_literal(value: str) -> str:
    """Quote a string as a SQL literal, for the places PostgreSQL takes no parameter (partial index predicates)."""
    if "\x00" in value:
        raise ValueError("A model name or version cannot contain NUL")
    if "\\" in value:
        return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    return "'" + value.replace("'", "''") + "'"


def index_name(identity: ModelIdentity) -> str:
    digest = hashlib.sha256(f"{identity.name}\x00{identity.version}".encode()).hexdigest()[:20]
    return f"idx_node_embeddings_hnsw_{digest}"


def vector_text(values) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


async def ensure_model_tables(conn: asyncpg.Connection, vector_sql: VectorSQL) -> None:
    """Create the vector table, its model index and the small state table, when they are missing."""
    await conn.execute(f"""
        CREATE TABLE IF NOT EXISTS node_embeddings (
            node_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            model_name VARCHAR NOT NULL,
            model_version VARCHAR NOT NULL,
            dimension INTEGER NOT NULL,
            embedding {vector_sql.type} NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (node_id, model_name, model_version)
        )""")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_node_embeddings_model ON node_embeddings (model_name, model_version)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS embedding_store_state (
            key VARCHAR PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")


async def adopt_legacy_embeddings(conn: asyncpg.Connection, vector_sql: VectorSQL) -> dict[str, int]:
    """Copy the vectors of ``nodes.embedding`` into ``node_embeddings`` as their model's rows, once.

    No model runs: each vector keeps the model name and version its row recorded, and its dimension is read from
    the vector. A row without a recorded model has unknown provenance, which the engine never guesses from today's
    configuration, so it is left for ``reembed()``. The column itself is not changed, so an engine that opens the
    store with the option off keeps reading it. The copy goes in batches that each commit, skips what a batch
    already copied, and a marker row says when it finished, so an interrupted run resumes and a finished one costs
    one query.
    """
    marker = await conn.fetchval("SELECT value FROM embedding_store_state WHERE key = 'legacy_adoption'")
    if marker is not None:
        return json.loads(marker) if isinstance(marker, str) else dict(marker)
    has_column = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'nodes'::regclass "
        "AND attname = 'embedding' AND NOT attisdropped)"
    )
    adopted = unknown = 0
    if has_column:
        last: str | None = None
        while True:
            row = await conn.fetchrow(
                f"""
                WITH batch AS (
                    SELECT n.id, n.embedding_model, n.embedding_version, n.embedding FROM nodes n
                    WHERE n.embedding IS NOT NULL AND n.embedding_model IS NOT NULL
                      AND n.embedding_version IS NOT NULL AND ($1::uuid IS NULL OR n.id > $1::uuid)
                    ORDER BY n.id LIMIT $2
                ), copied AS (
                    INSERT INTO node_embeddings (node_id, model_name, model_version, dimension, embedding)
                    SELECT id, embedding_model, embedding_version, {vector_sql.dimensions}(embedding), embedding
                    FROM batch ON CONFLICT (node_id, model_name, model_version) DO NOTHING RETURNING 1
                )
                SELECT (SELECT count(*) FROM copied) AS copied, (SELECT id::text FROM batch ORDER BY id DESC LIMIT 1) AS last,
                       (SELECT count(*) FROM batch) AS seen
                """,
                last, _ADOPTION_BATCH,
            )
            adopted += row["copied"]
            if row["seen"] < _ADOPTION_BATCH:
                break
            last = row["last"]
        unknown = await conn.fetchval(
            "SELECT count(*) FROM nodes WHERE embedding IS NOT NULL "
            "AND (embedding_model IS NULL OR embedding_version IS NULL)"
        )
    summary = {"adopted": int(adopted), "unknown_provenance": int(unknown)}
    await conn.execute(
        "INSERT INTO embedding_store_state (key, value) VALUES ('legacy_adoption', $1::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        json.dumps({**summary, "completed_at": datetime.now(timezone.utc).isoformat()}),
    )
    logger.info("multi_model.legacy_vectors_adopted", extra=summary)
    if unknown:
        logger.warning(
            "multi_model.legacy_vectors_unknown_provenance",
            extra={"vectors": int(unknown)},
        )
    return summary


async def ensure_model_index(pool: asyncpg.Pool, vector_sql: VectorSQL, identity: ModelIdentity) -> None:
    """Create the partial HNSW index of one model when it is missing or was left invalid by a failed build."""
    if identity.dimension > MAX_INDEXED_DIMENSIONS:
        logger.warning(
            "multi_model.no_hnsw_index",
            extra={"model": identity.name, "dimension": identity.dimension, "limit": MAX_INDEXED_DIMENSIONS},
        )
        return
    name = index_name(identity)
    async with pool.acquire() as conn:
        # Two engines that open one store together must not create indexes on the table at once: a plain
        # CREATE INDEX and a CONCURRENTLY one deadlock. The adoption's lock serializes them.
        await acquire_setup_lock(conn)
        try:
            await _create_model_index(conn, vector_sql, identity, name)
        finally:
            await conn.execute(_ADVISORY_UNLOCK)


async def _create_model_index(conn: asyncpg.Connection, vector_sql: VectorSQL, identity: ModelIdentity, name: str) -> None:
    state = await conn.fetchrow(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN pg_namespace s ON s.oid = c.relnamespace WHERE c.relname = $1 AND s.nspname = current_schema()",
        name,
    )
    if state is not None and state["indisvalid"]:
        return
    if state is not None:
        await conn.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
    populated = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM node_embeddings WHERE model_name = $1 AND model_version = $2)",
        identity.name, identity.version,
    )
    # An index over rows already stored is built without blocking writers. An empty one is instant.
    concurrently = "CONCURRENTLY " if populated else ""
    await conn.execute(
        f'CREATE INDEX {concurrently}IF NOT EXISTS "{name}" ON node_embeddings USING hnsw '
        f"((embedding::{vector_sql.type}({identity.dimension})) {vector_sql.cosine_ops}) "
        f"WITH (m = 16, ef_construction = 64) "
        f"WHERE model_name = {sql_literal(identity.name)} AND model_version = {sql_literal(identity.version)}"
    )


async def drop_model_index(pool: asyncpg.Pool, identity: ModelIdentity) -> None:
    async with pool.acquire() as conn:
        await conn.execute(f'DROP INDEX IF EXISTS "{index_name(identity)}"')


async def initialize_multi_model_storage(pool: asyncpg.Pool, vector_sql: VectorSQL) -> dict[str, int]:
    """Create the model tables and adopt the legacy column's vectors, serialized across processes.

    Another process that opens the same store waits on the advisory lock until this one has finished, then
    finds the marker and goes on. Readers that run with the option off are not affected: they read the column,
    which is left as it was.
    """
    async with pool.acquire() as conn:
        await acquire_setup_lock(conn)
        try:
            await ensure_model_tables(conn, vector_sql)
            return await adopt_legacy_embeddings(conn, vector_sql)
        finally:
            await conn.execute(_ADVISORY_UNLOCK)


async def write_node_embedding(
    conn: asyncpg.Connection, vector_sql: VectorSQL, *, node_id: str, model: str, version: str, values,
) -> None:
    """Store one prepared vector inside a publication's transaction (multi-model storage)."""
    await conn.execute(
        "INSERT INTO node_embeddings (node_id, model_name, model_version, dimension, embedding) "
        f"VALUES ($1::uuid, $2, $3, $4, $5::{vector_sql.type}) "
        "ON CONFLICT (node_id, model_name, model_version) DO UPDATE SET "
        "embedding = EXCLUDED.embedding, dimension = EXCLUDED.dimension, created_at = now()",
        str(node_id), model, version, len(values), vector_text(values),
    )


class PgModelVectorIndex(PgVectorIndex):
    """One model's vectors in ``node_embeddings``, searched and written like ``PgVectorIndex``'s column."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_provider: EmbeddingProvider,
        *,
        exact_search: bool = True,
        vector_sql: VectorSQL | None = None,
    ) -> None:
        super().__init__(pool, embedding_provider, exact_search=exact_search, vector_sql=vector_sql)

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity.of(self._provider)

    def _model_clause(self, alias: str, identity: ModelIdentity | None = None) -> str:
        identity = identity or self.identity
        return (f"{alias}.model_name = {sql_literal(identity.name)} "
                f"AND {alias}.model_version = {sql_literal(identity.version)}")

    # --- writes ---

    async def index(self, node_id: str, content: str, user_id: str, *, replace: bool = False) -> int:
        """Embed ``content`` and store the vector as this model's row for the node, replacing an earlier one."""
        sql = await self._sql()
        embedding = await encode_texts(self._provider, [content])
        identity = self.identity
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "INSERT INTO node_embeddings (node_id, model_name, model_version, dimension, embedding) "
                f"SELECT n.id, $2, $3, $4, $5::{sql.type} FROM nodes n WHERE n.id = $1::uuid AND n.user_id = $6 "
                "ON CONFLICT (node_id, model_name, model_version) DO UPDATE SET "
                "embedding = EXCLUDED.embedding, dimension = EXCLUDED.dimension, created_at = now()",
                node_id, identity.name, identity.version, identity.dimension, vector_text(embedding[0]), user_id,
            )
        if result == "INSERT 0 0":
            logger.debug("Node %s not found for embedding; content may be non-node", node_id)
        return 0

    async def put_vectors(self, rows: list[tuple[str, str, str, list[float]]]) -> int:
        """Store vectors made elsewhere, as ``(node_id, user_id, content, vector)``; returns how many were new.

        A node that already has a vector under this model, is no longer active, or belongs to another owner is
        skipped, so a re-embed that raced a write or a retirement leaves the newer state alone.
        """
        sql = await self._sql()
        identity = self.identity
        written = 0
        async with self._pool.acquire() as conn:
            for node_id, user_id, _content, values in rows:
                if len(values) != identity.dimension:
                    raise ValueError("Embedding provider returned an unexpected vector dimension")
                result = await conn.execute(
                    "INSERT INTO node_embeddings (node_id, model_name, model_version, dimension, embedding) "
                    f"SELECT n.id, $2, $3, $4, $5::{sql.type} FROM nodes n WHERE n.id = $1::uuid AND n.user_id = $6 "
                    f"AND n.lifecycle_state IN ({_STATE_PLACEHOLDERS}) "
                    "ON CONFLICT (node_id, model_name, model_version) DO NOTHING",
                    node_id, identity.name, identity.version, identity.dimension, vector_text(values), user_id,
                )
                written += int(result.split()[-1])
        return written

    async def delete_by_node_id(self, node_id: str) -> int:
        """Remove this model's vector for a retired node; returns 1 when one was removed."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM node_embeddings ne WHERE ne.node_id = $1::uuid AND {self._model_clause('ne')}",
                node_id,
            )
        return int(result.split()[-1])

    async def delete_other_models(self, node_id: str, keep: list[ModelIdentity]) -> int:
        """Remove a node's vectors of every model that is not in ``keep``, including models not open here."""
        clause = "".join(f" AND NOT ({self._model_clause('ne', item)})" for item in keep)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM node_embeddings ne WHERE ne.node_id = $1::uuid{clause}", node_id,
            )
        return int(result.split()[-1])

    async def clear(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(f"DELETE FROM node_embeddings ne WHERE {self._model_clause('ne')}")

    async def drop_rows(self) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(f"DELETE FROM node_embeddings ne WHERE {self._model_clause('ne')}")
        return int(result.split()[-1])

    async def drop_rows_of(self, identity: ModelIdentity) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(f"DELETE FROM node_embeddings ne WHERE {self._model_clause('ne', identity)}")
        return int(result.split()[-1])

    # --- reads ---

    async def search_by_vector(
        self,
        vector: list[float],
        user_id: str,
        *,
        k: int = 10,
        scope: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[dict]:
        """Nearest neighbors among this model's vectors, with the filters and ordering of ``PgVectorIndex``."""
        if k <= 0:
            return []
        sql = await self._sql()
        identity = self.identity
        if len(vector) != identity.dimension:
            raise ValueError("Query embedding has invalid dimensions")
        conditions = [
            "n.user_id = $1",
            self._model_clause("ne"),
            f"n.lifecycle_state IN ({_STATE_PLACEHOLDERS})",
        ]
        params: list = [user_id]
        idx = 2
        if scope:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(scope)))
            conditions.append(f"n.scope IN ({placeholders})")
            params.extend(scope)
            idx += len(scope)
        if time_from is not None:
            conditions.append(
                f"(n.valid_to IS NULL OR n.valid_to > ${idx} OR n.node_type IN ('entity', 'preference'))"
            )
            params.append(time_from)
            idx += 1
        if time_to is not None:
            conditions.append(f"(n.valid_from <= ${idx} OR n.node_type IN ('entity', 'preference'))")
            params.append(time_to)
            idx += 1
        where = " AND ".join(conditions)
        vector_str = vector_text(vector)
        if self._exact_search:
            # The materialization boundary keeps the limit from becoming an index scan before eligibility is
            # known, as in PgVectorIndex. Undefined distances (zero-norm vectors) are not results.
            query = (
                "WITH eligible AS MATERIALIZED ("
                f"SELECT n.id::text AS node_id, (ne.embedding {sql.cosine} ${idx}::{sql.type}) AS distance "
                f"FROM node_embeddings ne JOIN nodes n ON n.id = ne.node_id WHERE {where}) "
                "SELECT * FROM eligible WHERE distance < 'Infinity'::float8 "
                f"ORDER BY distance, node_id::uuid LIMIT ${idx + 1}"
            )
        else:
            cast = f"{sql.type}({identity.dimension})"
            query = (
                f"SELECT n.id::text AS node_id, (ne.embedding::{cast} {sql.cosine} ${idx}::{cast}) AS distance "
                f"FROM node_embeddings ne JOIN nodes n ON n.id = ne.node_id WHERE {where} "
                f"ORDER BY ne.embedding::{cast} {sql.cosine} ${idx}::{cast} LIMIT ${idx + 1}"
            )
        params.extend([vector_str, k])
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        results = []
        for row in rows:
            distance = float(row["distance"])
            results.append({"node_id": row["node_id"], "score": 1.0 - distance, "distance": distance})
        return results

    # --- what the store holds ---

    async def has_vectors(self) -> bool:
        async with self._pool.acquire() as conn:
            return bool(await conn.fetchval(
                f"SELECT EXISTS (SELECT 1 FROM node_embeddings ne WHERE {self._model_clause('ne')})"
            ))

    async def stored_models(self) -> list[StoredModel]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model_name, model_version, max(dimension) AS dimension, count(*) AS vectors "
                "FROM node_embeddings GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2"
            )
        return [StoredModel(ModelIdentity(r["model_name"], r["model_version"], int(r["dimension"])), int(r["vectors"]))
                for r in rows]

    def _expected(self, user_id: str | None, first: int) -> tuple[str, list]:
        sql = (f"FROM nodes n WHERE n.lifecycle_state IN ({_STATE_PLACEHOLDERS}) AND n.content ~ '\\S'")
        params: list = []
        if user_id is not None:
            sql += f" AND n.user_id = ${first}"
            params.append(user_id)
        return sql, params

    async def coverage_counts(
        self, user_id: str | None = None, identity: ModelIdentity | None = None,
    ) -> tuple[int, int]:
        """``(memories that should have a vector, memories that have none under a model)``; the model is this one's."""
        base, params = self._expected(user_id, 1)
        lacks = (" AND NOT EXISTS (SELECT 1 FROM node_embeddings ne WHERE ne.node_id = n.id AND "
                 + self._model_clause("ne", identity) + ")")
        async with self._pool.acquire() as conn:
            expected = await conn.fetchval("SELECT count(*) " + base, *params)
            missing = await conn.fetchval("SELECT count(*) " + base + lacks, *params)
        return int(expected), int(missing)

    async def missing_nodes(
        self, *, after: str | None = None, upper: str | None = None, limit: int = 64, user_id: str | None = None,
        identity: ModelIdentity | None = None,
    ) -> list[tuple[str, str, str]]:
        """Up to ``limit`` memories without a vector under a model (this one's by default), by id after ``after``."""
        base, params = self._expected(user_id, 1)
        sql = "SELECT n.id::text, n.content, n.user_id " + base
        sql += (" AND NOT EXISTS (SELECT 1 FROM node_embeddings ne WHERE ne.node_id = n.id AND "
                + self._model_clause("ne", identity) + ")")
        if after is not None:
            params.append(after)
            sql += f" AND n.id > ${len(params)}::uuid"
        if upper is not None:
            params.append(upper)
            sql += f" AND n.id <= ${len(params)}::uuid"
        params.append(limit)
        sql += f" ORDER BY n.id LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [(row[0], row[1], row[2]) for row in rows]


async def open_multi_model_index(
    pool: asyncpg.Pool,
    config,
    vector_sql: VectorSQL,
    embedding_provider: EmbeddingProvider,
    additional_providers: list[EmbeddingProvider],
):
    """Prepare the model tables, adopt the legacy column once, and open the active and additional models' indexes."""
    from prme.storage.multi_model import MultiModelVectorIndex

    await initialize_multi_model_storage(pool, vector_sql)

    async def open_one(provider: EmbeddingProvider) -> PgModelVectorIndex:
        await ensure_model_index(pool, vector_sql, ModelIdentity.of(provider))
        return PgModelVectorIndex(
            pool, provider, exact_search=config.vector_exact_search, vector_sql=vector_sql,
        )

    async def drop_files(identity: ModelIdentity) -> None:
        await drop_model_index(pool, identity)

    active = await open_one(embedding_provider)
    extras = [await open_one(provider) for provider in additional_providers]
    # A model the store already holds keeps its index even when it is no longer configured.
    async with pool.acquire() as conn:
        for row in await conn.fetch("SELECT model_name, model_version, max(dimension) AS d FROM node_embeddings GROUP BY 1, 2"):
            await ensure_model_index(pool, vector_sql, ModelIdentity(row["model_name"], row["model_version"], int(row["d"])))
    return MultiModelVectorIndex(active, extras, make_index=open_one, drop_model_files=drop_files)
