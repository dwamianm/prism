"""Reclaim uniquely owned abandoned profile staging under a native-write fence."""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
import time

from prme.ingestion.errors import extraction_failure_code
from prme.models.profile import ProfilePublication, ProfileCollectionResult
from prme.types import Scope
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine
from prme.storage._threading import run_to_completion
from prme.storage.profile_work import pg_sql, validate_duck_work, validate_pg_work


def collection_operation_id(plan):
    return plan.collection_operation_id


def registry_queries():
    from prme.storage.derivation_registry import _PENDING
    from prme.storage.profile_registry import PENDING

    return _PENDING + " LIMIT 1", PENDING + " LIMIT 1"


def validate_collect_duck(conn, plan):
    for query in registry_queries():
        if conn.execute(query).fetchone():
            raise ValueError("Incomplete artifact ownership registry")
    validate_duck_work(conn, plan, required=True, expected_status="abandoned")
    if conn.execute("SELECT 1 FROM nodes WHERE id=?", [str(plan.node.id)]).fetchone():
        raise ValueError("Graph-visible profile cannot be collected")
    if conn.execute(
        "SELECT 1 FROM operations WHERE id=?", [plan.operation_id]
    ).fetchone():
        raise ValueError("Published profile cannot be collected")


async def validate_collect_pg(conn, plan):
    for query in registry_queries():
        if await conn.fetchrow(query):
            raise ValueError("Incomplete artifact ownership registry")
    if not await validate_pg_work(conn, plan, expected_status="abandoned"):
        raise ValueError("Collection requires durable profile work")
    if await conn.fetchval("SELECT 1 FROM nodes WHERE id=$1", str(plan.node.id)):
        raise ValueError("Graph-visible profile cannot be collected")
    if await conn.fetchval("SELECT 1 FROM operations WHERE id=$1", plan.operation_id):
        raise ValueError("Published profile cannot be collected")


class ProfileCollectionFence:
    def __init__(self, conn, conn_lock, plan):
        self.conn, self.conn_lock = conn, conn_lock
        self.plan = ProfilePublication.model_validate_json(plan.model_dump_json())

    def verify_collection_plan(self, plan):
        if self.plan.checksum != plan.checksum:
            raise ValueError("Collection requires the exact prepared profile")

    @contextmanager
    def hold(self):
        conn = self.conn.cursor()
        try:
            conn.execute("BEGIN TRANSACTION")
            validate_collect_duck(conn, self.plan)
            yield
            validate_collect_duck(conn, self.plan)
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def validate_collection_receipt(plan, row):
    if row is None:
        return False
    kind, target, value, owner, scope = row
    value = json.loads(value) if isinstance(value, str) else value
    if (
        kind != "PROFILE_STAGE_COLLECTED"
        or target != str(plan.node.id)
        or not isinstance(value, dict)
        or value.get("checksum") != plan.checksum
        or owner != plan.node.user_id
        or scope != plan.node.scope.value
    ):
        raise ValueError("Invalid profile collection receipt")
    return True


RECEIPT = (
    "SELECT op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?"
)
APPEND = (
    "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id) "
    "VALUES (?,'PROFILE_STAGE_COLLECTED',?,?,?,?)"
)


async def acknowledge(store, plan):
    args = [
        collection_operation_id(plan),
        str(plan.node.id),
        json.dumps({"checksum": plan.checksum}),
        plan.node.user_id,
        plan.node.scope.value,
    ]
    if store.pool is not None:
        async with store.pool.acquire() as conn, conn.transaction():
            await validate_collect_pg(conn, plan)
            row = await conn.fetchrow(pg_sql(RECEIPT), collection_operation_id(plan))
            if not validate_collection_receipt(plan, row):
                await conn.execute(pg_sql(APPEND), *args)
        return

    def commit():
        conn = store.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            validate_collect_duck(conn, plan)
            if conn.execute(
                "SELECT 1 FROM vector_metadata WHERE node_id=?", [str(plan.node.id)]
            ).fetchone():
                raise ValueError("Profile collection still has durable staged vectors")
            row = conn.execute(RECEIPT, [collection_operation_id(plan)]).fetchone()
            if not validate_collection_receipt(plan, row):
                conn.execute(APPEND, args)
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    async with store.conn_lock:
        await run_to_completion(commit)


async def collected(store, plan):
    if store.pool is not None:
        async with store.pool.acquire() as conn:
            row = await conn.fetchrow(pg_sql(RECEIPT), collection_operation_id(plan))
    else:
        async with store.conn_lock:
            row = await run_to_completion(
                lambda: store.conn.execute(
                    RECEIPT, [collection_operation_id(plan)]
                ).fetchone()
            )
    return validate_collection_receipt(plan, row)


async def execute(store, sql, args=()):
    if store.pool is not None:
        async with store.pool.acquire() as conn:
            return await conn.fetch(pg_sql(sql), *args)
    async with store.conn_lock:
        return await run_to_completion(lambda: store.conn.execute(sql, args).fetchall())


async def collect(
    engine: MemoryEngine,
    *,
    user_id: str,
    scope: Scope | None = None,
    limit: int = 100,
    budget_ms: float = 5000,
) -> ProfileCollectionResult:

    if not user_id or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Nonempty user_id and limit between 1 and 1000 required")
    if not math.isfinite(budget_ms) or budget_ms < 0:
        raise ValueError("budget_ms must be finite and nonnegative")
    scope = Scope(scope) if scope is not None else None
    store = engine._profile_work
    checksum = (
        "o.payload->>'checksum'=p.payload->>'checksum'"
        if store.pool is not None
        else "json_extract_string(o.payload,'$.checksum')=json_extract_string(p.payload,'$.checksum')"
    )
    where = (
        " FROM profile_work w WHERE w.status='abandoned' AND w.user_id=? AND NOT EXISTS "
        "(SELECT 1 FROM operations o JOIN operations p ON p.id=w.prepared_operation_id "
        "WHERE o.id=w.collection_operation_id AND o.op_type='PROFILE_STAGE_COLLECTED' "
        "AND o.target_id=w.plan_id AND o.actor_id=w.user_id AND o.namespace_id=w.scope AND "
        + checksum
        + ")"
    )
    args = [user_id]
    if scope is not None:
        where += " AND w.scope=?"
        args.append(scope.value)
    result: ProfileCollectionResult = {
        "collected": 0,
        "failed": 0,
        "remaining": 0,
        "errors": {},
        "blocked_reason": None,
    }
    for query in registry_queries():
        if await execute(store, query):
            result["blocked_reason"] = "IncompleteOwnershipRegistry"
            result["remaining"] = (
                await execute(store, "SELECT count(*)" + where, args)
            )[0][0]
            return result
    jobs = await execute(
        store,
        "SELECT w.plan_id"
        + where
        + " ORDER BY w.last_attempt_at NULLS FIRST,w.admitted_at,w.plan_id LIMIT ?",
        [*args, limit],
    )
    start = time.monotonic()
    for row in jobs:
        if (time.monotonic() - start) * 1000 >= budget_ms:
            break
        profile_id, plan = row[0], None
        try:
            plan = await store.get(profile_id, user_id=user_id)
            if plan is None:
                raise ValueError("Abandoned profile lost its immutable preparation")
            if await collected(store, plan):
                result["collected"] += 1
                continue
            if engine._pool is None:
                fence = ProfileCollectionFence(
                    engine._conn, engine._event_store._conn_lock, plan
                )
                # The durable vector payload remains a retry anchor until the
                # lexical document has been removed. Work remains discoverable
                # even if both deletes succeed before acknowledgement is lost.
                await engine._lexical_index.delete_profile_stage(plan, fence=fence)
                await engine._vector_index.delete_profile_stage(plan, fence=fence)
            await acknowledge(store, plan)
            result["collected"] += 1
        except Exception as exc:
            try:
                confirmed = plan is not None and await collected(store, plan)
            except Exception:
                confirmed = False
            if confirmed:
                result["collected"] += 1
                continue
            reason = extraction_failure_code(exc)
            await execute(
                store,
                "UPDATE profile_work SET last_attempt_at=current_timestamp,attempts=attempts+1,last_error=? "
                "WHERE plan_id=? AND user_id=? AND status='abandoned'",
                [reason, profile_id, user_id],
            )
            result["errors"][profile_id] = reason
            result["failed"] += 1
    result["remaining"] = (await execute(store, "SELECT count(*)" + where, args))[0][0]
    return result
