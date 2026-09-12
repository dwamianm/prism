"""Durable prepared profile inputs, scoped discovery and idempotent admission.

The immutable operation stores the complete plan. Mutable work rows track
processing; a publication and its completed work state commit together.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid5

from prme.models.profile import ProfileJobStatus, ProfilePublication, StaleProfileError
from prme.storage._threading import run_to_completion

PROFILE_WORK_DDL = """CREATE TABLE IF NOT EXISTS profile_work (
    plan_id VARCHAR PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    profile_key VARCHAR NOT NULL,
    request_hash VARCHAR NOT NULL,
    prepared_operation_id VARCHAR NOT NULL UNIQUE,
    collection_operation_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',
    attempts BIGINT NOT NULL DEFAULT 0,
    fence_epoch BIGINT NOT NULL DEFAULT 0,
    last_error VARCHAR,
    last_attempt_at TIMESTAMPTZ,
    admitted_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
)"""


def payload(plan):
    return json.dumps(
        {"checksum": plan.checksum, "publication": plan.model_dump_json()}
    )


def unpack(value, *, user_id, plan_id=None):
    value = json.loads(value) if isinstance(value, str) else value
    plan = ProfilePublication.model_validate_json(value["publication"])
    if plan.checksum != value["checksum"] or plan.node.user_id != user_id:
        raise ValueError("Prepared profile checksum or owner mismatch")
    if plan_id is not None and str(plan.node.id) != str(plan_id):
        raise ValueError("Prepared profile identity mismatch")
    return plan


def replacement_operation(old_id, new):
    return str(uuid5(new.node.id, "prme:profile-preparation-replaced:" + old_id))


class ProfileWorkStore:
    def __init__(self, *, conn=None, conn_lock=None, pool=None):
        self.conn, self.conn_lock, self.pool = conn, conn_lock, pool

    async def get(self, plan_id: str, *, user_id: str) -> ProfilePublication | None:
        plan_id = str(UUID(str(plan_id)))
        if not user_id:
            raise ValueError("user_id must be nonempty")
        sql = (
            "SELECT o.payload,w.user_id,w.scope,w.profile_key,w.request_hash,w.prepared_operation_id,"
            "o.op_type,o.actor_id,o.namespace_id,o.target_id,w.collection_operation_id FROM profile_work w "
            "LEFT JOIN operations o ON o.id=w.prepared_operation_id WHERE w.plan_id=? AND w.user_id=?"
        )
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(pg_sql(sql), plan_id, user_id)
        else:
            async with self.conn_lock:
                row = await run_to_completion(
                    lambda: self.conn.execute(sql, [plan_id, user_id]).fetchone()
                )
        if row is None:
            return None
        if row[0] is None:
            raise ValueError("Prepared profile work lost its immutable plan")
        plan = unpack(row[0], user_id=user_id, plan_id=plan_id)
        if tuple(row[1:]) != (
            user_id,
            plan.node.scope.value,
            plan.key,
            plan.request_hash,
            plan.prepared_operation_id,
            "PROFILE_PREPARED",
            user_id,
            plan.node.scope.value,
            plan_id,
            plan.collection_operation_id,
        ):
            raise ValueError("Prepared profile work or operation identity mismatch")
        return plan

    async def reusable(
        self, key: str, request_hash: str, *, user_id: str
    ) -> ProfilePublication | None:
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT plan_id FROM profile_work WHERE profile_key=$1 AND request_hash=$2 "
                    "AND user_id=$3 AND status='pending'",
                    key,
                    request_hash,
                    user_id,
                )
        else:
            async with self.conn_lock:
                row = await run_to_completion(
                    lambda: self.conn.execute(
                        "SELECT plan_id FROM profile_work WHERE profile_key=? AND request_hash=? AND user_id=? AND status='pending'",
                        [key, request_hash, user_id],
                    ).fetchone()
                )
        return await self.get(row[0], user_id=user_id) if row else None

    async def prepare(self, plan: ProfilePublication) -> ProfilePublication:
        plan = ProfilePublication.model_validate_json(plan.model_dump_json())
        if self.pool is not None:
            return await self._prepare_pg(plan)
        async with self.conn_lock:
            import duckdb

            try:
                return await run_to_completion(self._prepare_duck, plan)
            except duckdb.TransactionException as exc:
                raise StaleProfileError(
                    "Concurrent profile preparation changed; retry explicitly"
                ) from exc

    def _prepare_duck(self, plan):
        conn = self.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            prior = conn.execute(
                "SELECT payload FROM operations WHERE id=? AND op_type='PROFILE_PREPARED'",
                [plan.prepared_operation_id],
            ).fetchone()
            if prior is not None:
                saved = unpack(
                    prior[0], user_id=plan.node.user_id, plan_id=plan.node.id
                )
                if saved.checksum != plan.checksum:
                    raise ValueError("Prepared profile identity cannot be overwritten")
                conn.execute("COMMIT")
                return saved
            conn.execute(
                "INSERT INTO profile_publication_heads VALUES (?, 0) ON CONFLICT DO NOTHING",
                [plan.key],
            )
            conn.execute(
                "INSERT INTO profile_preparation_heads VALUES (?,0) ON CONFLICT DO NOTHING",
                [plan.key],
            )
            conn.execute(
                "UPDATE profile_preparation_heads SET epoch=epoch+1 WHERE profile_key=?",
                [plan.key],
            )
            generation = conn.execute(
                "SELECT generation FROM profile_publication_heads WHERE profile_key=?",
                [plan.key],
            ).fetchone()[0]
            if generation != plan.generation:
                raise StaleProfileError("Profile generation changed before preparation")
            rows = conn.execute(
                "SELECT plan_id, request_hash, prepared_operation_id FROM profile_work WHERE profile_key=? AND status='pending'",
                [plan.key],
            ).fetchall()
            for old_id, request_hash, operation_id in rows:
                if request_hash == plan.request_hash:
                    value = conn.execute(
                        "SELECT payload FROM operations WHERE id=? AND op_type='PROFILE_PREPARED' AND actor_id=?",
                        [operation_id, plan.node.user_id],
                    ).fetchone()
                    if value is None:
                        raise ValueError(
                            "Prepared profile work lost its immutable plan"
                        )
                    saved = unpack(value[0], user_id=plan.node.user_id, plan_id=old_id)
                    conn.execute("COMMIT")
                    return saved
                conn.execute(
                    "UPDATE profile_work SET status='abandoned', fence_epoch=fence_epoch+1, last_error='ReplacedPreparation' WHERE plan_id=?",
                    [old_id],
                )
                conn.execute(
                    "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id) "
                    "VALUES (?, 'PROFILE_PREPARATION_REPLACED', ?, ?, ?, ?)",
                    [
                        replacement_operation(old_id, plan),
                        old_id,
                        json.dumps({"replaced_by": str(plan.node.id)}),
                        plan.node.user_id,
                        plan.node.scope.value,
                    ],
                )
            reserve_duck(conn, plan)
            conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id) VALUES (?, 'PROFILE_PREPARED', ?, ?, ?, ?)",
                [
                    plan.prepared_operation_id,
                    str(plan.node.id),
                    payload(plan),
                    plan.node.user_id,
                    plan.node.scope.value,
                ],
            )
            conn.execute(
                "INSERT INTO profile_work (plan_id,user_id,scope,profile_key,request_hash,prepared_operation_id,collection_operation_id) VALUES (?,?,?,?,?,?,?)",
                [
                    str(plan.node.id),
                    plan.node.user_id,
                    plan.node.scope.value,
                    plan.key,
                    plan.request_hash,
                    plan.prepared_operation_id,
                    plan.collection_operation_id,
                ],
            )
            conn.execute(
                "INSERT INTO profile_registered_plans VALUES (?,?)",
                [plan.prepared_operation_id, str(plan.node.id)],
            )
            conn.execute("COMMIT")
            return plan
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    async def _prepare_pg(self, plan):
        async with self.pool.acquire() as conn, conn.transaction():
            prior = await conn.fetchval(
                "SELECT payload FROM operations WHERE id=$1 AND op_type='PROFILE_PREPARED'",
                plan.prepared_operation_id,
            )
            if prior is not None:
                saved = unpack(prior, user_id=plan.node.user_id, plan_id=plan.node.id)
                if saved.checksum != plan.checksum:
                    raise ValueError("Prepared profile identity cannot be overwritten")
                return saved
            await conn.execute(
                "INSERT INTO profile_publication_heads VALUES ($1, 0) ON CONFLICT DO NOTHING",
                plan.key,
            )
            generation = await conn.fetchval(
                "SELECT generation FROM profile_publication_heads WHERE profile_key=$1 FOR UPDATE",
                plan.key,
            )
            if generation != plan.generation:
                raise StaleProfileError("Profile generation changed before preparation")
            rows = await conn.fetch(
                "SELECT plan_id, request_hash, prepared_operation_id FROM profile_work WHERE profile_key=$1 AND status='pending' FOR UPDATE",
                plan.key,
            )
            for old_id, request_hash, operation_id in rows:
                if request_hash == plan.request_hash:
                    value = await conn.fetchval(
                        "SELECT payload FROM operations WHERE id=$1 AND op_type='PROFILE_PREPARED' AND actor_id=$2",
                        operation_id,
                        plan.node.user_id,
                    )
                    if value is None:
                        raise ValueError(
                            "Prepared profile work lost its immutable plan"
                        )
                    return unpack(value, user_id=plan.node.user_id, plan_id=old_id)
                await conn.execute(
                    "UPDATE profile_work SET status='abandoned', fence_epoch=fence_epoch+1, last_error='ReplacedPreparation' WHERE plan_id=$1",
                    old_id,
                )
                await conn.execute(
                    "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id) VALUES ($1,'PROFILE_PREPARATION_REPLACED',$2,$3::jsonb,$4,$5)",
                    replacement_operation(old_id, plan),
                    old_id,
                    json.dumps({"replaced_by": str(plan.node.id)}),
                    plan.node.user_id,
                    plan.node.scope.value,
                )
            await reserve_pg(conn, plan)
            await conn.execute(
                "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id) VALUES ($1,'PROFILE_PREPARED',$2,$3::jsonb,$4,$5)",
                plan.prepared_operation_id,
                str(plan.node.id),
                payload(plan),
                plan.node.user_id,
                plan.node.scope.value,
            )
            await conn.execute(
                "INSERT INTO profile_work (plan_id,user_id,scope,profile_key,request_hash,prepared_operation_id,collection_operation_id) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                str(plan.node.id),
                plan.node.user_id,
                plan.node.scope.value,
                plan.key,
                plan.request_hash,
                plan.prepared_operation_id,
                plan.collection_operation_id,
            )
            await conn.execute(
                "INSERT INTO profile_registered_plans VALUES ($1,$2)",
                plan.prepared_operation_id,
                str(plan.node.id),
            )
            return plan

    async def list(
        self, *, user_id: str, scope=None, limit: int = 100, status: str = "pending"
    ) -> list[ProfileJobStatus]:
        if not user_id or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Nonempty user_id and limit between 1 and 1000 required")
        fields = "plan_id,scope,status,attempts,last_error"
        conditions = ["user_id=?", "status=?"]
        parameters: list[str | int] = [user_id, status]
        if scope is not None:
            conditions.append("scope=?")
            parameters.append(scope.value)
        parameters.append(limit)
        sql = f"SELECT {fields} FROM profile_work WHERE {' AND '.join(conditions)} ORDER BY last_attempt_at NULLS FIRST, admitted_at, plan_id LIMIT ?"
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(pg_sql(sql), *parameters)
        else:
            async with self.conn_lock:
                rows = await run_to_completion(
                    lambda: self.conn.execute(sql, parameters).fetchall()
                )
        return [
            ProfileJobStatus(
                plan_id=row[0],
                scope=row[1],
                status=row[2],
                attempts=row[3],
                last_error=row[4],
            )
            for row in rows
        ]

    async def status(self, plan_id: str, *, user_id: str) -> dict | None:
        if not user_id:
            raise ValueError("user_id must be nonempty")
        plan_id = str(UUID(str(plan_id)))
        fields = "plan_id,scope,status,attempts,last_error"
        sql = f"SELECT {fields} FROM profile_work WHERE plan_id=? AND user_id=?"
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(pg_sql(sql), plan_id, user_id)
        else:
            async with self.conn_lock:
                row = await run_to_completion(
                    lambda: self.conn.execute(sql, [plan_id, user_id]).fetchone()
                )
        return dict(zip(fields.split(","), row)) if row else None

    async def count(self, *, user_id: str, scope=None) -> int:
        sql = "SELECT count(*) FROM profile_work WHERE user_id=? AND status='pending'"
        params = [user_id]
        if scope is not None:
            sql += " AND scope=?"
            params.append(scope.value)
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                return await conn.fetchval(pg_sql(sql), *params)
        async with self.conn_lock:
            return await run_to_completion(
                lambda: self.conn.execute(sql, params).fetchone()[0]
            )

    async def failed(self, plan_id: str, *, user_id: str, reason: str) -> None:
        sql = "UPDATE profile_work SET attempts=attempts+1,last_error=?,last_attempt_at=current_timestamp WHERE plan_id=? AND user_id=? AND status='pending'"
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                await conn.execute(pg_sql(sql), reason, plan_id, user_id)
        else:
            async with self.conn_lock:
                await run_to_completion(
                    lambda: self.conn.execute(sql, [reason, plan_id, user_id])
                )


def pg_sql(sql):
    parts = sql.split("?")
    return "".join(
        part + (f"${i + 1}" if i < len(parts) - 1 else "")
        for i, part in enumerate(parts)
    )


def validate_work_row(plan, row, saved, *, expected_status="pending"):
    if row is None:
        if saved is not None:
            raise ValueError("Journaled profile is missing its work record")
        return False
    if tuple(row[:5]) != (
        plan.node.user_id,
        plan.node.scope.value,
        plan.key,
        plan.request_hash,
        plan.prepared_operation_id,
    ):
        raise ValueError("Profile work identity differs from its prepared inputs")
    if row[5] != expected_status:
        raise StaleProfileError("Profile preparation is no longer pending")
    if (
        saved is None
        or unpack(saved, user_id=plan.node.user_id, plan_id=plan.node.id).checksum
        != plan.checksum
    ):
        raise ValueError(
            "Profile publication requires its exact immutable prepared inputs"
        )
    return True


def validate_duck_work(conn, plan, *, required=False, expected_status="pending"):
    row = conn.execute(
        "SELECT user_id,scope,profile_key,request_hash,prepared_operation_id,status FROM profile_work WHERE plan_id=?",
        [str(plan.node.id)],
    ).fetchone()
    saved = conn.execute(
        "SELECT payload FROM operations WHERE id=? AND op_type='PROFILE_PREPARED' AND actor_id=?",
        [plan.prepared_operation_id, plan.node.user_id],
    ).fetchone()
    managed = validate_work_row(
        plan, row, saved[0] if saved else None, expected_status=expected_status
    )
    if required and not managed:
        raise ValueError("Managed profile staging requires a prepared journal record")
    if managed:
        owner = conn.execute(
            "SELECT operation_id FROM derivation_artifact_owners WHERE node_id=?",
            [str(plan.node.id)],
        ).fetchone()
        if owner is None or owner[0] != plan.prepared_operation_id:
            raise ValueError("Prepared profile does not own its staged identity")
        # DuckDB elides no-op updates and permits writes to different columns.
        # A changing, unindexed epoch shared with every transition provides the
        # conflict, without exposing a transient status to callers.
        conn.execute(
            "UPDATE profile_work SET fence_epoch=fence_epoch+1 WHERE plan_id=?",
            [str(plan.node.id)],
        )
    return managed


async def validate_pg_work(conn, plan, *, expected_status="pending"):
    row = await conn.fetchrow(
        "SELECT user_id,scope,profile_key,request_hash,prepared_operation_id,status FROM profile_work WHERE plan_id=$1 FOR UPDATE",
        str(plan.node.id),
    )
    saved = await conn.fetchval(
        "SELECT payload FROM operations WHERE id=$1 AND op_type='PROFILE_PREPARED' AND actor_id=$2",
        plan.prepared_operation_id,
        plan.node.user_id,
    )
    managed = validate_work_row(plan, row, saved, expected_status=expected_status)
    if managed:
        owner = await conn.fetchval(
            "SELECT operation_id FROM derivation_artifact_owners WHERE node_id=$1",
            str(plan.node.id),
        )
        if owner != plan.prepared_operation_id:
            raise ValueError("Prepared profile does not own its staged identity")
    return managed


class ProfileStageFence:
    """Keep a managed preparation pending throughout each external native write."""

    def __init__(self, conn, conn_lock, plan):
        self.conn, self.conn_lock = conn, conn_lock
        self.plan = ProfilePublication.model_validate_json(plan.model_dump_json())

    def verify_embedding(self, embedding, user_id):
        if user_id != self.plan.node.user_id or embedding != self.plan.embedding:
            raise ValueError("Embedding differs from the fenced profile")

    def verify_plan(self, plan):
        if plan.checksum != self.plan.checksum:
            raise ValueError("Documents differ from the fenced profile")

    def hold(self):
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            conn = self.conn.cursor()
            try:
                conn.execute("BEGIN TRANSACTION")
                validate_duck_work(conn, self.plan, required=True)
                yield
                validate_duck_work(conn, self.plan, required=True)
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


def reserve_duck(conn, plan, *, legacy=False):
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
            raise ValueError("Profile identity belongs to another prepared operation")
        conn.execute(
            "UPDATE derivation_artifact_owners SET operation_id=NULL WHERE node_id=?",
            [str(plan.node.id)],
        )


async def reserve_pg(conn, plan, *, legacy=False):
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
            raise ValueError("Profile identity belongs to another prepared operation")
        await conn.execute(
            "UPDATE derivation_artifact_owners SET operation_id=NULL WHERE node_id=$1",
            str(plan.node.id),
        )
