"""Atomic profile replacement, separate from assertion supersedence.

External indexes are staged before entering this boundary. Failed staging never
retires a usable graph view. A committed operation retains the complete input;
a repeated commit returns its identity without reactivating an archived view.
Uncommitted local staging is conservatively retained, not automatically retried.
"""

from __future__ import annotations

import json

import duckdb
from typing import TYPE_CHECKING

from prme.models.derivation import node_checksum
from prme.models.profile import ProfilePublication, StaleProfileError
from prme.storage._threading import run_to_completion

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore

HEADS_DDL = """CREATE TABLE IF NOT EXISTS profile_publication_heads (
    profile_key VARCHAR PRIMARY KEY, generation BIGINT NOT NULL
)"""


def _payload(plan: ProfilePublication) -> str:
    # Keep numerical JSON unchanged by PostgreSQL JSONB normalization.
    return json.dumps(
        {"checksum": plan.checksum, "publication": plan.model_dump_json()}
    )


def _replayed(plan: ProfilePublication, value) -> bool:
    if value is None:
        return False
    payload = json.loads(value) if isinstance(value, str) else value
    saved = ProfilePublication.model_validate_json(payload["publication"])
    if saved.checksum != payload["checksum"] or saved.checksum != plan.checksum:
        raise ValueError(
            "Profile publication identity conflicts with its committed operation"
        )
    return True


def _validate(plan: ProfilePublication, current, prior_ids) -> None:
    if set(prior_ids) != {str(n.id) for n in plan.previous}:
        raise StaleProfileError("Current profiles changed; rebuild explicitly")
    for expected in plan.sources + plan.previous:
        actual = current.get(str(expected.id))
        if actual is None or node_checksum(actual) != node_checksum(expected):
            raise StaleProfileError("Profile dependency changed; rebuild explicitly")


async def generation_duckdb(store: DuckPGQGraphStore, key: str) -> int:
    async with store._conn_lock:

        def read():
            row = store._conn.execute(
                "SELECT generation FROM profile_publication_heads WHERE profile_key = ?",
                [key],
            ).fetchone()
            return row[0] if row else 0

        return await run_to_completion(read)


async def generation_postgres(store: PgGraphStore, key: str) -> int:
    async with store._pool.acquire() as conn:
        return (
            await conn.fetchval(
                "SELECT generation FROM profile_publication_heads WHERE profile_key = $1",
                key,
            )
            or 0
        )


async def commit_duckdb(store: DuckPGQGraphStore, plan: ProfilePublication) -> str:
    snapshot = ProfilePublication.model_validate_json(plan.model_dump_json())
    async with store._conn_lock:
        try:
            return await run_to_completion(_commit_duckdb, store, snapshot)
        except duckdb.TransactionException as exc:
            raise StaleProfileError(
                "Concurrent profile storage change; rebuild explicitly"
            ) from exc


def _commit_duckdb(store: DuckPGQGraphStore, plan: ProfilePublication) -> str:
    import numpy as np

    conn, node, embedding = store._conn, plan.node, plan.embedding
    conn.execute("BEGIN TRANSACTION")
    try:
        row = conn.execute(
            "SELECT payload FROM operations WHERE id = ? AND op_type = 'PROFILE_PUBLISHED'",
            [plan.operation_id],
        ).fetchone()
        if not _replayed(plan, row[0] if row else None):
            conn.execute(
                "INSERT INTO profile_publication_heads VALUES (?, 0) ON CONFLICT DO NOTHING",
                [plan.key],
            )
            row = conn.execute(
                "SELECT generation FROM profile_publication_heads WHERE profile_key = ?",
                [plan.key],
            ).fetchone()
            assert row is not None
            generation = row[0]
            if generation != plan.generation:
                raise StaleProfileError(
                    "Profile generation changed; rebuild explicitly"
                )
            # This write also conflicts with another connection publishing the
            # same initially-empty profile key under DuckDB snapshot isolation.
            conn.execute(
                "UPDATE profile_publication_heads SET generation = generation + 1 WHERE profile_key = ?",
                [plan.key],
            )
            current = {}
            for dep in sorted(plan.sources + plan.previous, key=lambda n: str(n.id)):
                conn.execute(
                    "UPDATE nodes SET updated_at = updated_at WHERE id = ?",
                    [str(dep.id)],
                )
                actual = store._get_node_sync(str(dep.id), True)
                if actual is not None:
                    current[str(dep.id)] = actual
            rows = conn.execute(
                "SELECT id FROM nodes WHERE user_id = ? AND scope = ? AND node_type = 'summary' "
                "AND lifecycle_state IN ('tentative', 'stable') "
                "AND json_extract_string(metadata, '$.entity_profile') = 'true' "
                "AND json_extract_string(metadata, '$.entity_name') = ?",
                [node.user_id, node.scope.value, plan.entity_name],
            ).fetchall()
            _validate(plan, current, [str(row[0]) for row in rows])
            rows = conn.execute(
                "SELECT vm.user_id, vm.embedding_model, vm.embedding_version, vm.embedding_dim, vp.vector_data, vs.content "
                "FROM vector_metadata vm JOIN vector_payloads vp USING (vector_key) "
                "JOIN vector_staging vs ON vm.vector_key = vs.vector_key AND vm.node_id = vs.node_id WHERE vm.node_id = ?",
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
            if len(rows) != 1 or tuple(rows[0]) != expected:
                raise ValueError(
                    "Exact profile vectors must be durably staged before publication"
                )
            store._create_node_sync(node)
            for old in plan.previous:
                conn.execute(
                    "UPDATE nodes SET lifecycle_state = 'archived', updated_at = ? WHERE id = ?",
                    [node.created_at, str(old.id)],
                )
            conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                "VALUES (?, 'PROFILE_PUBLISHED', ?, ?, 'profile', ?, ?)",
                [
                    plan.operation_id,
                    str(node.id),
                    _payload(plan),
                    node.scope.value,
                    node.created_at,
                ],
            )
        conn.execute("COMMIT")
        return str(node.id)
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except duckdb.TransactionException:
            # A failed COMMIT can already have ended the transaction. Preserve
            # that original failure rather than masking it with rollback noise.
            pass
        raise


async def commit_postgres(store: PgGraphStore, plan: ProfilePublication) -> str:
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    plan = ProfilePublication.model_validate_json(plan.model_dump_json())
    node, embedding = plan.node, plan.embedding
    async with store._pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO profile_publication_heads VALUES ($1, 0) ON CONFLICT DO NOTHING",
            plan.key,
        )
        generation = await conn.fetchval(
            "SELECT generation FROM profile_publication_heads WHERE profile_key = $1 FOR UPDATE",
            plan.key,
        )
        value = await conn.fetchval(
            "SELECT payload FROM operations WHERE id = $1 AND op_type = 'PROFILE_PUBLISHED'",
            plan.operation_id,
        )
        if _replayed(plan, value):
            return str(node.id)
        if generation != plan.generation:
            raise StaleProfileError("Profile generation changed; rebuild explicitly")
        ids = sorted(str(n.id) for n in plan.sources + plan.previous)
        rows = await conn.fetch(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = ANY($1::uuid[]) ORDER BY id FOR UPDATE",
            ids,
        )
        current = {str(row["id"]): store._record_to_node(row) for row in rows}
        rows = await conn.fetch(
            "SELECT id FROM nodes WHERE user_id = $1 AND scope = $2 AND node_type = 'summary' "
            "AND lifecycle_state IN ('tentative', 'stable') "
            "AND metadata ->> 'entity_profile' = 'true' AND metadata ->> 'entity_name' = $3",
            node.user_id,
            node.scope.value,
            plan.entity_name,
        )
        _validate(plan, current, [str(row["id"]) for row in rows])
        await store._create_node_on_connection(conn, node)
        await conn.execute(
            "UPDATE nodes SET embedding = $1::vector, embedding_model = $2, embedding_version = $3 WHERE id = $4",
            "[" + ",".join(str(v) for v in embedding.values) + "]",
            embedding.model,
            embedding.version,
            str(node.id),
        )
        for old in plan.previous:
            await conn.execute(
                "UPDATE nodes SET lifecycle_state = 'archived', updated_at = $1 WHERE id = $2",
                node.created_at,
                str(old.id),
            )
        await conn.execute(
            "UPDATE profile_publication_heads SET generation = generation + 1 WHERE profile_key = $1",
            plan.key,
        )
        await conn.execute(
            "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
            "VALUES ($1, 'PROFILE_PUBLISHED', $2, $3::jsonb, 'profile', $4, $5)",
            plan.operation_id,
            str(node.id),
            _payload(plan),
            node.scope.value,
            node.created_at,
        )
        return str(node.id)
