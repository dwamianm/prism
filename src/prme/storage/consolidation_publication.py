"""Durable preparation and atomic publication of extractive summaries."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import duckdb

from prme.models.consolidation import (
    ConsolidationPublication,
    StaleConsolidationError,
)
from prme.models.derivation import node_checksum
from prme.storage._threading import run_to_completion

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore

logger = logging.getLogger(__name__)


HEADS_DDL = """CREATE TABLE IF NOT EXISTS consolidation_publication_heads (
    consolidation_key VARCHAR PRIMARY KEY, generation BIGINT NOT NULL
)"""

REGISTRY_DDL = """CREATE TABLE IF NOT EXISTS consolidation_registered_plans (
    operation_id VARCHAR PRIMARY KEY, node_id VARCHAR NOT NULL
)"""

STAGE_DDL = """CREATE TABLE IF NOT EXISTS consolidation_stage_fences (
    plan_id VARCHAR PRIMARY KEY, epoch BIGINT NOT NULL DEFAULT 0
)"""

PENDING = (
    "SELECT o.id,o.op_type,o.target_id,o.payload,o.actor_id,o.namespace_id FROM operations o "
    "WHERE o.op_type IN ('CONSOLIDATION_PREPARED','CONSOLIDATION_PUBLISHED') AND ("
    "NOT EXISTS (SELECT 1 FROM consolidation_registered_plans r WHERE r.operation_id=o.id) "
    "OR NOT EXISTS (SELECT 1 FROM derivation_artifact_owners a WHERE a.node_id=o.target_id))"
)


def _checkpoint(_stage: str) -> None:
    """Fault-injection hook used by transactional race tests."""


def _payload(plan: ConsolidationPublication) -> str:
    return json.dumps(
        {"checksum": plan.checksum, "publication": plan.model_dump_json()}
    )


def _unpack(value, *, owner: str | None = None) -> ConsolidationPublication:
    body = json.loads(value) if isinstance(value, str) else value
    plan = ConsolidationPublication.model_validate_json(body["publication"])
    if plan.checksum != body["checksum"]:
        raise ValueError("Consolidation publication checksum does not match")
    if owner is not None and plan.node.user_id != owner:
        raise ValueError("Consolidation publication owner does not match")
    return plan


def _validate_operation(plan, row, expected_type: str) -> bool:
    if row is None:
        return False
    op_type, target_id, payload, actor_id, namespace_id = row
    saved = _unpack(payload)
    expected_actor = plan.node.user_id if expected_type == "CONSOLIDATION_PREPARED" else "consolidation"
    if (
        op_type != expected_type
        or target_id != str(plan.node.id)
        or actor_id != expected_actor
        or namespace_id != plan.node.scope.value
        or saved.checksum != plan.checksum
    ):
        raise ValueError("Consolidation operation conflicts with its prepared inputs")
    return True


def _reserve_duck(conn, plan: ConsolidationPublication, *, legacy: bool = False) -> None:
    conn.execute(
        "INSERT INTO derivation_artifact_owners VALUES (?,?) ON CONFLICT DO NOTHING",
        [str(plan.node.id), plan.prepared_operation_id],
    )
    owner = conn.execute(
        "SELECT operation_id FROM derivation_artifact_owners WHERE node_id=?",
        [str(plan.node.id)],
    ).fetchone()[0]
    if owner != plan.prepared_operation_id:
        if not legacy:
            raise ValueError("Consolidation identity belongs to another prepared operation")
        conn.execute(
            "UPDATE derivation_artifact_owners SET operation_id=NULL WHERE node_id=?",
            [str(plan.node.id)],
        )


async def _reserve_pg(conn, plan: ConsolidationPublication, *, legacy: bool = False) -> None:
    await conn.execute(
        "INSERT INTO derivation_artifact_owners VALUES ($1,$2) ON CONFLICT DO NOTHING",
        str(plan.node.id),
        plan.prepared_operation_id,
    )
    owner = await conn.fetchval(
        "SELECT operation_id FROM derivation_artifact_owners WHERE node_id=$1",
        str(plan.node.id),
    )
    if owner != plan.prepared_operation_id:
        if not legacy:
            raise ValueError("Consolidation identity belongs to another prepared operation")
        await conn.execute(
            "UPDATE derivation_artifact_owners SET operation_id=NULL WHERE node_id=$1",
            str(plan.node.id),
        )


def initialize_duck(conn) -> None:
    conn.execute(HEADS_DDL)
    conn.execute(REGISTRY_DDL)
    conn.execute(STAGE_DDL)
    conn.execute(
        "INSERT INTO consolidation_stage_fences "
        "SELECT target_id,0 FROM operations WHERE op_type='CONSOLIDATION_PREPARED' "
        "ON CONFLICT DO NOTHING"
    )
    conn.execute("BEGIN TRANSACTION")
    try:
        for row in conn.execute(PENDING).fetchall():
            try:
                operation_id, op_type, target_id, payload, actor_id, scope = row
                plan = _unpack(payload)
                expected = plan.prepared_operation_id if op_type == "CONSOLIDATION_PREPARED" else plan.operation_id
                expected_actor = plan.node.user_id if op_type == "CONSOLIDATION_PREPARED" else "consolidation"
                if (
                    operation_id != expected
                    or target_id != str(plan.node.id)
                    or actor_id != expected_actor
                    or scope != plan.node.scope.value
                ):
                    raise ValueError("Invalid consolidation journal identity")
                _reserve_duck(conn, plan, legacy=True)
                conn.execute(
                    "INSERT INTO consolidation_registered_plans VALUES (?,?) ON CONFLICT DO NOTHING",
                    [operation_id, str(plan.node.id)],
                )
            except (ValueError, KeyError, TypeError):
                logger.warning(
                    "Consolidation ownership registration incomplete for %s", row[0]
                )
        for operation_id, payload in conn.execute(
            "SELECT id,payload FROM operations WHERE op_type='CONSOLIDATION_PUBLISHED'"
        ).fetchall():
            try:
                plan = _unpack(payload)
                if operation_id != plan.operation_id:
                    raise ValueError("Invalid consolidation publication identity")
                conn.execute(
                    "INSERT INTO consolidation_publication_heads VALUES (?,?) "
                    "ON CONFLICT (consolidation_key) DO UPDATE SET generation="
                    "GREATEST(consolidation_publication_heads.generation,excluded.generation)",
                    [plan.key, plan.generation + 1],
                )
            except (ValueError, KeyError, TypeError):
                logger.warning(
                    "Consolidation head reconstruction incomplete for %s", operation_id
                )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def initialize_pg(conn) -> None:
    await conn.execute(HEADS_DDL)
    await conn.execute(REGISTRY_DDL)
    await conn.execute(STAGE_DDL)
    await conn.execute(
        "INSERT INTO consolidation_stage_fences "
        "SELECT target_id,0 FROM operations WHERE op_type='CONSOLIDATION_PREPARED' "
        "ON CONFLICT DO NOTHING"
    )
    async with conn.transaction():
        for row in await conn.fetch(PENDING):
            try:
                operation_id, op_type, target_id, payload, actor_id, scope = row
                plan = _unpack(payload)
                expected = plan.prepared_operation_id if op_type == "CONSOLIDATION_PREPARED" else plan.operation_id
                expected_actor = plan.node.user_id if op_type == "CONSOLIDATION_PREPARED" else "consolidation"
                if (
                    operation_id != expected
                    or target_id != str(plan.node.id)
                    or actor_id != expected_actor
                    or scope != plan.node.scope.value
                ):
                    raise ValueError("Invalid consolidation journal identity")
                await _reserve_pg(conn, plan, legacy=True)
                await conn.execute(
                    "INSERT INTO consolidation_registered_plans VALUES ($1,$2) ON CONFLICT DO NOTHING",
                    operation_id,
                    str(plan.node.id),
                )
            except (ValueError, KeyError, TypeError):
                logger.warning(
                    "Consolidation ownership registration incomplete for %s", row[0]
                )
        for row in await conn.fetch(
            "SELECT id,payload FROM operations WHERE op_type='CONSOLIDATION_PUBLISHED'"
        ):
            try:
                plan = _unpack(row[1])
                if row[0] != plan.operation_id:
                    raise ValueError("Invalid consolidation publication identity")
                await conn.execute(
                    "INSERT INTO consolidation_publication_heads VALUES ($1,$2) "
                    "ON CONFLICT (consolidation_key) DO UPDATE SET generation="
                    "GREATEST(consolidation_publication_heads.generation,excluded.generation)",
                    plan.key,
                    plan.generation + 1,
                )
            except (ValueError, KeyError, TypeError):
                logger.warning(
                    "Consolidation head reconstruction incomplete for %s", row[0]
                )


async def generation_duckdb(store: DuckPGQGraphStore, key: str) -> int:
    async with store._conn_lock:
        return await run_to_completion(
            lambda: (
                store._conn.execute(
                    "SELECT generation FROM consolidation_publication_heads WHERE consolidation_key=?",
                    [key],
                ).fetchone()
                or (0,)
            )[0]
        )


async def generation_postgres(store: PgGraphStore, key: str) -> int:
    async with store._pool.acquire() as conn:
        return (
            await conn.fetchval(
                "SELECT generation FROM consolidation_publication_heads WHERE consolidation_key=$1",
                key,
            )
            or 0
        )


async def get_prepared_duckdb(
    store: DuckPGQGraphStore, node_id: str, *, user_id: str
) -> ConsolidationPublication | None:
    from uuid import UUID, uuid5

    node_id = str(UUID(node_id))
    operation_id = str(uuid5(UUID(node_id), "prme:consolidation-prepared:v1"))
    async with store._conn_lock:
        row = await run_to_completion(
            lambda: store._conn.execute(
                "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations "
                "WHERE id=? AND actor_id=?",
                [operation_id, user_id],
            ).fetchone()
        )
    if row is None:
        return None
    plan = _unpack(row[2], owner=user_id)
    if (
        row[0] != "CONSOLIDATION_PREPARED"
        or row[1] != node_id
        or row[3] != user_id
        or row[4] != plan.node.scope.value
        or str(plan.node.id) != node_id
    ):
        raise ValueError("Prepared consolidation lookup found an invalid journal record")
    return plan


async def get_prepared_postgres(
    store: PgGraphStore, node_id: str, *, user_id: str
) -> ConsolidationPublication | None:
    from uuid import UUID, uuid5

    node_id = str(UUID(node_id))
    operation_id = str(uuid5(UUID(node_id), "prme:consolidation-prepared:v1"))
    async with store._pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations "
            "WHERE id=$1 AND actor_id=$2",
            operation_id,
            user_id,
        )
    if row is None:
        return None
    plan = _unpack(row[2], owner=user_id)
    if (
        row[0] != "CONSOLIDATION_PREPARED"
        or str(row[1]) != node_id
        or row[3] != user_id
        or row[4] != plan.node.scope.value
        or str(plan.node.id) != node_id
    ):
        raise ValueError("Prepared consolidation lookup found an invalid journal record")
    return plan


async def prepare_duckdb(
    store: DuckPGQGraphStore, plan: ConsolidationPublication
) -> ConsolidationPublication:
    import asyncio

    plan = ConsolidationPublication.model_validate_json(plan.model_dump_json())
    for attempt in range(5):
        try:
            async with store._conn_lock:
                return await run_to_completion(_prepare_duckdb, store, plan)
        except (duckdb.ConstraintException, duckdb.TransactionException) as exc:
            await asyncio.sleep(0.01 * (attempt + 1))
            saved = await get_prepared_duckdb(
                store, str(plan.node.id), user_id=plan.node.user_id
            )
            if saved is not None:
                if saved.key != plan.key or saved.request_hash != plan.request_hash:
                    raise ValueError("Prepared consolidation identity conflicts") from exc
                return saved
            if attempt == 4:
                raise StaleConsolidationError(
                    "Concurrent consolidation preparation changed; retry"
                ) from exc
    raise AssertionError("unreachable")


def _prepare_duckdb(store, plan):
    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        row = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?",
            [plan.prepared_operation_id],
        ).fetchone()
        if row is not None:
            saved = _unpack(row[2], owner=plan.node.user_id)
            if (
                row[0] != "CONSOLIDATION_PREPARED"
                or row[1] != str(saved.node.id)
                or row[3] != saved.node.user_id
                or row[4] != saved.node.scope.value
                or saved.key != plan.key
                or saved.request_hash != plan.request_hash
            ):
                raise ValueError("Prepared consolidation identity conflicts")
            conn.execute("COMMIT")
            return saved
        _reserve_duck(conn, plan)
        conn.execute(
            "INSERT INTO consolidation_stage_fences VALUES (?,0) ON CONFLICT DO NOTHING",
            [str(plan.node.id)],
        )
        conn.execute(
            "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
            "VALUES (?,'CONSOLIDATION_PREPARED',?,?,?,?,?) ON CONFLICT DO NOTHING",
            [
                plan.prepared_operation_id,
                str(plan.node.id),
                _payload(plan),
                plan.node.user_id,
                plan.node.scope.value,
                plan.node.created_at,
            ],
        )
        saved_row = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?",
            [plan.prepared_operation_id],
        ).fetchone()
        if saved_row is None:
            raise ValueError("Prepared consolidation journal write was lost")
        saved = _unpack(saved_row[2], owner=plan.node.user_id)
        if (
            saved_row[0] != "CONSOLIDATION_PREPARED"
            or saved_row[1] != str(saved.node.id)
            or saved_row[3] != saved.node.user_id
            or saved_row[4] != saved.node.scope.value
            or saved.key != plan.key
            or saved.request_hash != plan.request_hash
        ):
            raise ValueError("Prepared consolidation identity conflicts")
        conn.execute(
            "INSERT INTO consolidation_registered_plans VALUES (?,?) ON CONFLICT DO NOTHING",
            [saved.prepared_operation_id, str(saved.node.id)],
        )
        conn.execute("COMMIT")
        return saved
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


async def prepare_postgres(
    store: PgGraphStore, plan: ConsolidationPublication
) -> ConsolidationPublication:
    plan = ConsolidationPublication.model_validate_json(plan.model_dump_json())
    async with store._pool.acquire() as conn, conn.transaction():
        await _reserve_pg(conn, plan)
        await conn.execute(
            "INSERT INTO consolidation_stage_fences VALUES ($1,0) ON CONFLICT DO NOTHING",
            str(plan.node.id),
        )
        await conn.execute(
            "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
            "VALUES ($1,'CONSOLIDATION_PREPARED',$2,$3::jsonb,$4,$5,$6) ON CONFLICT DO NOTHING",
            plan.prepared_operation_id,
            str(plan.node.id),
            _payload(plan),
            plan.node.user_id,
            plan.node.scope.value,
            plan.node.created_at,
        )
        row = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=$1",
            plan.prepared_operation_id,
        )
        if row is None:
            raise ValueError("Prepared consolidation journal write was lost")
        saved = _unpack(row[2], owner=plan.node.user_id)
        if (
            row[0] != "CONSOLIDATION_PREPARED"
            or str(row[1]) != str(saved.node.id)
            or row[3] != saved.node.user_id
            or row[4] != saved.node.scope.value
            or saved.key != plan.key
            or saved.request_hash != plan.request_hash
        ):
            raise ValueError("Prepared consolidation identity conflicts")
        await conn.execute(
            "INSERT INTO consolidation_registered_plans VALUES ($1,$2) ON CONFLICT DO NOTHING",
            saved.prepared_operation_id,
            str(saved.node.id),
        )
        return saved


def _validate_dependencies(plan, current, prior_ids) -> None:
    if set(prior_ids) != {str(node.id) for node in plan.previous}:
        raise StaleConsolidationError("Current consolidation summary changed; rebuild")
    for expected in plan.sources + plan.previous:
        actual = current.get(str(expected.id))
        if actual is None or node_checksum(actual) != node_checksum(expected):
            raise StaleConsolidationError("Consolidation dependency changed; rebuild")


class ConsolidationStageFence:
    """Serialize external index staging for one immutable DuckDB plan."""

    def __init__(self, conn, conn_lock, plan: ConsolidationPublication):
        self.conn = conn
        self.conn_lock = conn_lock
        self.plan = ConsolidationPublication.model_validate_json(plan.model_dump_json())

    def verify_embedding(self, embedding, user_id):
        if embedding != self.plan.embedding or user_id != self.plan.node.user_id:
            raise ValueError("Embedding differs from the fenced consolidation")

    def verify_plan(self, plan):
        if plan.checksum != self.plan.checksum:
            raise ValueError("Document differs from the fenced consolidation")

    def hold(self):
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            conn = self.conn.cursor()
            try:
                conn.execute("BEGIN TRANSACTION")
                row = conn.execute(
                    "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?",
                    [self.plan.prepared_operation_id],
                ).fetchone()
                if not _validate_operation(
                    self.plan, row, "CONSOLIDATION_PREPARED"
                ):
                    raise ValueError("Staging requires durable consolidation preparation")
                conn.execute(
                    "UPDATE consolidation_stage_fences SET epoch=epoch+1 WHERE plan_id=?",
                    [str(self.plan.node.id)],
                )
                yield
                row = conn.execute(
                    "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?",
                    [self.plan.prepared_operation_id],
                ).fetchone()
                if not _validate_operation(
                    self.plan, row, "CONSOLIDATION_PREPARED"
                ):
                    raise ValueError("Consolidation preparation changed during staging")
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

        return transaction()


async def commit_duckdb(store: DuckPGQGraphStore, plan: ConsolidationPublication) -> str:
    import asyncio

    plan = ConsolidationPublication.model_validate_json(plan.model_dump_json())
    for attempt in range(5):
        try:
            async with store._conn_lock:
                return await run_to_completion(_commit_duckdb, store, plan)
        except (duckdb.ConstraintException, duckdb.TransactionException) as exc:
            if attempt == 4:
                raise StaleConsolidationError(
                    "Concurrent consolidation publication changed; retry"
                ) from exc
            await asyncio.sleep(0.01 * (attempt + 1))
    raise AssertionError("unreachable")


def _commit_duckdb(store, plan):
    import numpy as np

    conn, node, embedding = store._conn, plan.node, plan.embedding
    conn.execute("BEGIN TRANSACTION")
    try:
        row = conn.execute(
            "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?",
            [plan.operation_id],
        ).fetchone()
        if not _validate_operation(plan, row, "CONSOLIDATION_PUBLISHED"):
            prepared = conn.execute(
                "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?",
                [plan.prepared_operation_id],
            ).fetchone()
            if not _validate_operation(plan, prepared, "CONSOLIDATION_PREPARED"):
                raise ValueError("Consolidation publication requires durable preparation")
            _reserve_duck(conn, plan)
            conn.execute(
                "INSERT INTO consolidation_publication_heads VALUES (?,0) ON CONFLICT DO NOTHING",
                [plan.key],
            )
            generation = conn.execute(
                "SELECT generation FROM consolidation_publication_heads WHERE consolidation_key=?",
                [plan.key],
            ).fetchone()[0]
            if generation != plan.generation:
                raise StaleConsolidationError("Consolidation generation changed; rebuild")
            conn.execute(
                "UPDATE consolidation_publication_heads SET generation=generation+1 WHERE consolidation_key=?",
                [plan.key],
            )
            current = {}
            for dependency in sorted(plan.sources + plan.previous, key=lambda item: str(item.id)):
                actual = store._get_node_sync(str(dependency.id), True)
                if actual is not None:
                    current[str(actual.id)] = actual
            metadata = node.metadata or {}
            if metadata.get("summary_publication_kind") == "hierarchical_source_excerpts_v2":
                prior_rows = conn.execute(
                    "SELECT id FROM nodes WHERE user_id=? AND scope=? AND node_type='summary' "
                    "AND lifecycle_state IN ('tentative','stable') AND ("
                    "(json_extract_string(metadata,'$.consolidation_summary')='true' "
                    "AND json_extract_string(metadata,'$.consolidation_key')=?) OR ("
                    "json_extract_string(metadata,'$.consolidation_key') IS NULL "
                    "AND json_extract_string(metadata,'$.summarization_level')=? "
                    "AND json_extract_string(metadata,'$.period_key')=? "
                    "AND json_extract_string(metadata,'$.summary_format')='source-excerpts-v1'))",
                    [
                        node.user_id,
                        node.scope.value,
                        plan.key,
                        metadata["summarization_level"],
                        metadata["period_key"],
                    ],
                ).fetchall()
            else:
                prior_rows = conn.execute(
                    "SELECT id FROM nodes WHERE user_id=? AND scope=? AND node_type='summary' "
                    "AND lifecycle_state IN ('tentative','stable') "
                    "AND json_extract_string(metadata,'$.consolidation_summary')='true' "
                    "AND json_extract_string(metadata,'$.consolidation_key')=?",
                    [node.user_id, node.scope.value, plan.key],
                ).fetchall()
            _validate_dependencies(plan, current, [str(value[0]) for value in prior_rows])
            # Validate the original snapshots first, then make a real write to
            # every dependency. A writer that committed after this transaction's
            # snapshot conflicts here; one that starts later conflicts with this
            # claim. Source timestamps are restored before commit so maintenance
            # does not manufacture a new consolidation request on the next pass.
            for dependency in sorted(plan.sources + plan.previous, key=lambda item: str(item.id)):
                conn.execute(
                    "UPDATE nodes SET updated_at=CASE WHEN updated_at=current_timestamp "
                    "THEN updated_at+INTERVAL '1 microsecond' ELSE current_timestamp END WHERE id=?",
                    [str(dependency.id)],
                )
            _checkpoint("validated")
            vectors = conn.execute(
                "SELECT vm.user_id,vm.embedding_model,vm.embedding_version,vm.embedding_dim,"
                "vp.vector_data,vs.content FROM vector_metadata vm JOIN vector_payloads vp USING (vector_key) "
                "JOIN vector_staging vs ON vm.vector_key=vs.vector_key AND vm.node_id=vs.node_id "
                "WHERE vm.node_id=?",
                [str(node.id)],
            ).fetchall()
            expected = (
                node.user_id,
                embedding.model,
                embedding.version,
                embedding.dimension,
                np.asarray(embedding.values, dtype="<f4").tobytes(),
                node.content,
            )
            if len(vectors) != 1 or tuple(vectors[0]) != expected:
                raise ValueError("Exact consolidation vector must be staged before publication")
            store._create_node_sync(node)
            for edge in plan.edges:
                store._create_edge_sync(edge)
            for source in plan.sources:
                conn.execute(
                    "UPDATE nodes SET updated_at=? WHERE id=?",
                    [source.updated_at, str(source.id)],
                )
            for old in plan.previous:
                conn.execute(
                    "UPDATE nodes SET lifecycle_state='archived',updated_at=? WHERE id=?",
                    [node.created_at, str(old.id)],
                )
            conn.execute(
                "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
                "VALUES (?,'CONSOLIDATION_PUBLISHED',?,?,'consolidation',?,?)",
                [
                    plan.operation_id,
                    str(node.id),
                    _payload(plan),
                    node.scope.value,
                    node.created_at,
                ],
            )
            conn.execute(
                "INSERT INTO consolidation_registered_plans VALUES (?,?) ON CONFLICT DO NOTHING",
                [plan.operation_id, str(node.id)],
            )
        conn.execute("COMMIT")
        return str(node.id)
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise


async def commit_postgres(store: PgGraphStore, plan: ConsolidationPublication) -> str:
    from prme.storage.pg.graph_store import _NODE_COLUMNS
    from prme.storage.pg.vector_sql import resolve_vector_sql

    plan = ConsolidationPublication.model_validate_json(plan.model_dump_json())
    node, embedding = plan.node, plan.embedding
    async with store._pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO consolidation_publication_heads VALUES ($1,0) ON CONFLICT DO NOTHING",
            plan.key,
        )
        generation = await conn.fetchval(
            "SELECT generation FROM consolidation_publication_heads WHERE consolidation_key=$1 FOR UPDATE",
            plan.key,
        )
        row = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=$1",
            plan.operation_id,
        )
        if _validate_operation(plan, row, "CONSOLIDATION_PUBLISHED"):
            return str(node.id)
        prepared = await conn.fetchrow(
            "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=$1",
            plan.prepared_operation_id,
        )
        if not _validate_operation(plan, prepared, "CONSOLIDATION_PREPARED"):
            raise ValueError("Consolidation publication requires durable preparation")
        await _reserve_pg(conn, plan)
        if generation != plan.generation:
            raise StaleConsolidationError("Consolidation generation changed; rebuild")
        ids = sorted(str(node.id) for node in plan.sources + plan.previous)
        rows = await conn.fetch(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
            ids,
        )
        current = {str(row["id"]): store._record_to_node(row) for row in rows}
        metadata = node.metadata or {}
        if metadata.get("summary_publication_kind") == "hierarchical_source_excerpts_v2":
            prior_rows = await conn.fetch(
                "SELECT id FROM nodes WHERE user_id=$1 AND scope=$2 AND node_type='summary' "
                "AND lifecycle_state IN ('tentative','stable') AND ("
                "(metadata->>'consolidation_summary'='true' "
                "AND metadata->>'consolidation_key'=$3) OR ("
                "metadata->>'consolidation_key' IS NULL "
                "AND metadata->>'summarization_level'=$4 "
                "AND metadata->>'period_key'=$5 "
                "AND metadata->>'summary_format'='source-excerpts-v1')) FOR UPDATE",
                node.user_id,
                node.scope.value,
                plan.key,
                metadata["summarization_level"],
                metadata["period_key"],
            )
        else:
            prior_rows = await conn.fetch(
                "SELECT id FROM nodes WHERE user_id=$1 AND scope=$2 AND node_type='summary' "
                "AND lifecycle_state IN ('tentative','stable') "
                "AND metadata->>'consolidation_summary'='true' "
                "AND metadata->>'consolidation_key'=$3 FOR UPDATE",
                node.user_id,
                node.scope.value,
                plan.key,
            )
        _validate_dependencies(plan, current, [str(row["id"]) for row in prior_rows])
        await store._create_node_on_connection(conn, node)
        vector_sql = await resolve_vector_sql(conn)
        await conn.execute(
            f"UPDATE nodes SET embedding=$1::{vector_sql.type},embedding_model=$2,embedding_version=$3 WHERE id=$4",
            "[" + ",".join(str(value) for value in embedding.values) + "]",
            embedding.model,
            embedding.version,
            str(node.id),
        )
        for edge in plan.edges:
            await store._create_edge_on_connection(conn, edge)
        for old in plan.previous:
            await conn.execute(
                "UPDATE nodes SET lifecycle_state='archived',updated_at=$1 WHERE id=$2",
                node.created_at,
                str(old.id),
            )
        await conn.execute(
            "UPDATE consolidation_publication_heads SET generation=generation+1 WHERE consolidation_key=$1",
            plan.key,
        )
        await conn.execute(
            "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
            "VALUES ($1,'CONSOLIDATION_PUBLISHED',$2,$3::jsonb,'consolidation',$4,$5)",
            plan.operation_id,
            str(node.id),
            _payload(plan),
            node.scope.value,
            node.created_at,
        )
        await conn.execute(
            "INSERT INTO consolidation_registered_plans VALUES ($1,$2) ON CONFLICT DO NOTHING",
            plan.operation_id,
            str(node.id),
        )
        return str(node.id)
