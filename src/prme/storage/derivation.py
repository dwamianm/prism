"""Atomic graph publication of an already journaled derivation plan.

Inference and external index staging belong before this boundary. This module
does no provider I/O and never compensates by deleting a committed derivation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from prme.models.derivation import DerivationPlan, DerivationReceipt, StaleDerivationPlanError, node_checksum
from prme.models.nodes import MemoryNode
from prme.models.extraction_work import ExtractionClaim
from prme.storage._threading import run_to_completion
from prme.storage.extraction_work import validate_duck_claim, validate_pg_claim
from prme.types import EpistemicType, LifecycleState

if TYPE_CHECKING:
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.pg.graph_store import PgGraphStore


def _payload(value):
    return json.loads(value) if isinstance(value, str) else value


def _receipt(plan: DerivationPlan, payload) -> DerivationReceipt | None:
    if payload is None:
        return None
    receipt = DerivationReceipt.model_validate(_payload(payload))
    if (receipt.event_id, receipt.plan_id, receipt.user_id, receipt.plan_checksum) != (
        plan.event_id, plan.id, plan.user_id, plan.checksum,
    ):
        raise ValueError("Derivation receipt conflicts with the requested plan")
    if receipt.node_ids != tuple(node.id for node in plan.nodes) or receipt.edge_ids != tuple(
        edge.id for edge in plan.edges + plan.replacements
    ):
        raise ValueError("Derivation receipt has different artifact identities")
    return receipt


def _validate_dependencies(plan: DerivationPlan, current: dict[str, MemoryNode]) -> None:
    for expected in plan.references:
        actual = current.get(str(expected.id))
        if actual is None or node_checksum(actual) != node_checksum(expected):
            raise StaleDerivationPlanError("Derivation dependency changed; an explicit replan is required")
    nodes = {node.id: node for node in plan.nodes + plan.references}
    states = {key: node.lifecycle_state for key, node in nodes.items()}
    for edge in plan.replacements:
        old, new = nodes[edge.target_id], nodes[edge.source_id]
        active = (LifecycleState.TENTATIVE, LifecycleState.STABLE)
        if states[old.id] not in active or states[new.id] not in active:
            raise ValueError("Only tentative or stable assertions can participate in replacement")
        if new.epistemic_type not in (EpistemicType.OBSERVED, EpistemicType.ASSERTED):
            raise ValueError("A hypothetical or unverified derivation cannot retire prior knowledge")
        if plan.materialization_policy in {"temporal_validity_v7", "speech_act_v8", "speech_act_v9", "speech_act_v10", "speech_act_v11"}:
            old_effective = old.event_time or old.valid_from
            new_effective = new.event_time or new.valid_from
            if new_effective < old_effective:
                raise ValueError("An older effective assertion cannot retire a later assertion")
        elif old.event_time and new.event_time and new.event_time < old.event_time:
            raise ValueError("An older effective assertion cannot retire a later assertion")
        if edge.created_at < old.updated_at:
            raise ValueError("A replacement cannot precede its dependency's last update")
        states[old.id] = LifecycleState.SUPERSEDED


def _new_receipt(plan: DerivationPlan, claim: ExtractionClaim | None = None) -> DerivationReceipt:
    return DerivationReceipt(
        event_id=plan.event_id, plan_id=plan.id, user_id=plan.user_id,
        generation=claim.generation if claim else None,
        plan_checksum=plan.checksum, node_ids=tuple(node.id for node in plan.nodes),
        edge_ids=tuple(edge.id for edge in plan.edges + plan.replacements),
    )


async def commit_duckdb(store: DuckPGQGraphStore, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> DerivationReceipt:
    snapshot = DerivationPlan.model_validate_json(plan.model_dump_json())
    async with store._conn_lock:
        return await run_to_completion(_commit_duckdb, store, snapshot, claim)


def _commit_duckdb(store: DuckPGQGraphStore, plan: DerivationPlan, claim: ExtractionClaim | None = None) -> DerivationReceipt:
    import numpy as np
    from prme.storage.event_store import EventStore

    conn = store._conn
    conn.execute("BEGIN TRANSACTION")
    try:
        saved = EventStore(conn)._get_derivation_plan_sync(str(plan.event_id), plan.user_id)
        if saved is None or saved.checksum != plan.checksum:
            raise ValueError("Commit requires the exact journaled derivation plan")
        row = conn.execute(
            "SELECT payload FROM operations WHERE id = ? AND op_type = 'DERIVATION_COMMITTED'",
            [plan.receipt_operation_id],
        ).fetchone()
        receipt = _receipt(plan, row[0] if row else None)
        if receipt is None:
            managed = validate_duck_claim(conn, str(plan.event_id), plan.user_id, claim, plan_id=str(plan.id))
            current = {}
            for reference in plan.references:
                node = store._get_node_sync(str(reference.id), True)
                if node is not None:
                    current[str(node.id)] = node
            _validate_dependencies(plan, current)
            # Numerical staging must already be durable. A saved plan alone
            # is not evidence that the external vector index was prepared.
            for embedding in plan.embeddings:
                rows = conn.execute(
                    "SELECT vm.embedding_model, vm.embedding_version, vm.embedding_dim, vp.vector_data "
                    "FROM vector_metadata vm JOIN vector_payloads vp USING (vector_key) "
                    "WHERE vm.node_id = ? AND vm.user_id = ?",
                    [str(embedding.node_id), plan.user_id],
                ).fetchall()
                expected = (embedding.model, embedding.version, embedding.dimension,
                            np.asarray(embedding.values, dtype="<f4").tobytes())
                if not rows or any(tuple(row) != expected for row in rows):
                    raise ValueError("Prepared vectors must be durably staged before graph publication")
            for node in plan.nodes:
                store._create_node_sync(node)
            for edge in plan.edges:
                store._create_edge_sync(edge)
            published_nodes = {node.id: node for node in plan.nodes}
            for edge in plan.replacements:
                if plan.materialization_policy in {"temporal_validity_v7", "speech_act_v8", "speech_act_v9", "speech_act_v10", "speech_act_v11"}:
                    replacement = published_nodes[edge.source_id]
                    conn.execute(
                        "UPDATE nodes SET lifecycle_state = 'superseded', superseded_by = ?, "
                        "valid_to = CASE WHEN ? >= valid_from AND "
                        "(valid_to IS NULL OR valid_to > ?) THEN ? ELSE valid_to END, "
                        "updated_at = ? WHERE id = ?",
                        [str(edge.source_id), replacement.valid_from, replacement.valid_from,
                         replacement.valid_from, edge.created_at, str(edge.target_id)],
                    )
                else:
                    conn.execute(
                        "UPDATE nodes SET lifecycle_state = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                        [str(edge.source_id), edge.created_at, str(edge.target_id)],
                    )
                store._create_edge_sync(edge)
            validate_duck_claim(conn, str(plan.event_id), plan.user_id, claim, plan_id=str(plan.id))
            receipt = _new_receipt(plan, claim)
            conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                "VALUES (?, 'DERIVATION_COMMITTED', ?, ?, 'derivation', ?, ?)",
                [plan.receipt_operation_id, str(plan.event_id), receipt.model_dump_json(), plan.scope.value, receipt.created_at],
            )
            if managed is not None:
                conn.execute(
                    "UPDATE event_extractions SET status = 'complete', lease_expires_at = NULL, "
                    "last_error = NULL, updated_at = ? WHERE event_id = ?",
                    [receipt.created_at, str(plan.event_id)],
                )
        conn.execute("COMMIT")
        return receipt
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def commit_postgres(store: PgGraphStore, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> DerivationReceipt:
    from prme.storage.pg.event_store import PgEventStore
    from prme.storage.pg.graph_store import _NODE_COLUMNS

    plan = DerivationPlan.model_validate_json(plan.model_dump_json())
    async with store._pool.acquire() as conn, conn.transaction():
        from prme.storage.pg.vector_sql import resolve_vector_sql
        vector_sql = await resolve_vector_sql(conn)
        # Serialize attempts for this immutable source before checking the
        # prepared plan and receipt. Future replanning must use this same fence.
        await conn.fetchrow("SELECT id FROM events WHERE id = $1 FOR UPDATE", str(plan.event_id))
        saved = await PgEventStore(store._pool)._get_derivation_plan(conn, str(plan.event_id), plan.user_id)
        if saved is None or saved.checksum != plan.checksum:
            raise ValueError("Commit requires the exact journaled derivation plan")
        row = await conn.fetchrow(
            "SELECT payload FROM operations WHERE id = $1 AND op_type = 'DERIVATION_COMMITTED'",
            plan.receipt_operation_id,
        )
        receipt = _receipt(plan, row["payload"] if row else None)
        if receipt is not None:
            return receipt
        managed = await validate_pg_claim(conn, str(plan.event_id), plan.user_id, claim, plan_id=str(plan.id))
        ids = sorted(str(node.id) for node in plan.references)
        rows = await conn.fetch(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = ANY($1::uuid[]) ORDER BY id FOR UPDATE", ids,
        )
        current = {str(row["id"]): store._record_to_node(row) for row in rows}
        _validate_dependencies(plan, current)
        for node in plan.nodes:
            await store._create_node_on_connection(conn, node)
        # pgvector and generated text columns become visible in this same
        # transaction, using already-computed numerical values.
        for embedding in plan.embeddings:
            await conn.execute(
                f"UPDATE nodes SET embedding = $1::{vector_sql.type}, embedding_model = $2, embedding_version = $3 WHERE id = $4",
                "[" + ",".join(str(value) for value in embedding.values) + "]",
                embedding.model, embedding.version, str(embedding.node_id),
            )
        for edge in plan.edges:
            await store._create_edge_on_connection(conn, edge)
        published_nodes = {node.id: node for node in plan.nodes}
        for edge in plan.replacements:
            if plan.materialization_policy in {"temporal_validity_v7", "speech_act_v8", "speech_act_v9", "speech_act_v10", "speech_act_v11"}:
                replacement = published_nodes[edge.source_id]
                await conn.execute(
                    "UPDATE nodes SET lifecycle_state = 'superseded', superseded_by = $1, "
                    "valid_to = CASE WHEN $2 >= valid_from AND "
                    "(valid_to IS NULL OR valid_to > $2) THEN $2 ELSE valid_to END, "
                    "updated_at = $3 WHERE id = $4",
                    str(edge.source_id), replacement.valid_from, edge.created_at,
                    str(edge.target_id),
                )
            else:
                await conn.execute(
                    "UPDATE nodes SET lifecycle_state = 'superseded', superseded_by = $1, updated_at = $2 WHERE id = $3",
                    str(edge.source_id), edge.created_at, str(edge.target_id),
                )
            await store._create_edge_on_connection(conn, edge)
        await validate_pg_claim(conn, str(plan.event_id), plan.user_id, claim, plan_id=str(plan.id))
        receipt = _new_receipt(plan, claim)
        await conn.execute(
            "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
            "VALUES ($1, 'DERIVATION_COMMITTED', $2, $3::jsonb, 'derivation', $4, $5)",
            plan.receipt_operation_id, str(plan.event_id), receipt.model_dump_json(), plan.scope.value, receipt.created_at,
        )
        if managed is not None:
            await conn.execute(
                "UPDATE event_extractions SET status = 'complete', lease_expires_at = NULL, "
                "last_error = NULL, updated_at = clock_timestamp() WHERE event_id = $1", str(plan.event_id),
            )
        return receipt
