"""Explicit, owner-scoped retirement of an unpublished profile preparation."""

from __future__ import annotations

import json
from uuid import uuid5

from prme.models.profile import StaleProfileError
from prme.storage._threading import run_to_completion
from prme.storage.profile_work import validate_duck_work, validate_pg_work


def discard_operation_id(plan):
    return str(uuid5(plan.node.id, "prme:profile-preparation-discarded:v1"))


def discarded_state(plan, row):
    if row is None:
        return None
    operation, kind, target, value, owner, scope = row
    value = json.loads(value) if isinstance(value, str) else value
    if (
        operation != discard_operation_id(plan)
        or kind != "PROFILE_PREPARATION_DISCARDED"
        or target != str(plan.node.id)
        or owner != plan.node.user_id
        or scope != plan.node.scope.value
        or value != {"checksum": plan.checksum}
    ):
        raise ValueError("Invalid profile discard receipt")
    return "abandoned", "DiscardedPreparation"


async def discard(store, profile_id, *, user_id):
    plan = await store.get(profile_id, user_id=user_id)
    if plan is None:
        return False
    if store.pool is not None:
        return await _discard_pg(store, plan)
    import duckdb

    async with store.conn_lock:
        try:
            return await run_to_completion(_discard_duck, store.conn, plan)
        except duckdb.TransactionException as exc:
            raise StaleProfileError(
                "Concurrent profile operation changed; retry explicitly"
            ) from exc


def _discard_duck(conn, plan):
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "INSERT INTO profile_preparation_heads VALUES (?,0) ON CONFLICT DO NOTHING",
            [plan.key],
        )
        conn.execute(
            "UPDATE profile_preparation_heads SET epoch=epoch+1 WHERE profile_key=?",
            [plan.key],
        )
        state = conn.execute(
            "SELECT status FROM profile_work WHERE plan_id=? AND user_id=?",
            [str(plan.node.id), plan.node.user_id],
        ).fetchone()
        if state is None or state[0] == "complete":
            conn.execute("COMMIT")
            return False
        if state[0] == "abandoned":
            conn.execute("COMMIT")
            return True
        validate_duck_work(conn, plan, required=True)
        conn.execute(
            "UPDATE profile_work SET status='abandoned',fence_epoch=fence_epoch+1,last_error='DiscardedPreparation' WHERE plan_id=?",
            [str(plan.node.id)],
        )
        conn.execute(
            "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id) VALUES (?,'PROFILE_PREPARATION_DISCARDED',?,?,?,?)",
            [
                discard_operation_id(plan),
                str(plan.node.id),
                json.dumps({"checksum": plan.checksum}),
                plan.node.user_id,
                plan.node.scope.value,
            ],
        )
        conn.execute("COMMIT")
        return True
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


async def _discard_pg(store, plan):
    async with store.pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT generation FROM profile_publication_heads WHERE profile_key=$1 FOR UPDATE",
            plan.key,
        )
        state = await conn.fetchval(
            "SELECT status FROM profile_work WHERE plan_id=$1 AND user_id=$2 FOR UPDATE",
            str(plan.node.id),
            plan.node.user_id,
        )
        if state is None or state == "complete":
            return False
        if state == "abandoned":
            return True
        if not await validate_pg_work(conn, plan):
            raise ValueError("Profile discard requires durable preparation")
        await conn.execute(
            "UPDATE profile_work SET status='abandoned',fence_epoch=fence_epoch+1,last_error='DiscardedPreparation' WHERE plan_id=$1",
            str(plan.node.id),
        )
        await conn.execute(
            "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id) VALUES ($1,'PROFILE_PREPARATION_DISCARDED',$2,$3::jsonb,$4,$5)",
            discard_operation_id(plan),
            str(plan.node.id),
            json.dumps({"checksum": plan.checksum}),
            plan.node.user_id,
            plan.node.scope.value,
        )
        return True
