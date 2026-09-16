"""Reconstruct profile ownership and missing work rows from immutable operations.

Only validated plans and transitions authorize derived state. Corrupt records
remain unregistered; diagnostics contain operation identities, never payloads.
"""

from __future__ import annotations

import json
import logging

from prme.models.profile import ProfilePublication
from prme.storage.profile_retirement import discarded_state, discard_operation_id
from prme.storage.profile_work import (
    pg_sql,
    replacement_operation,
    reserve_duck,
    reserve_pg,
    unpack,
)

logger = logging.getLogger(__name__)
DDL = (
    "CREATE TABLE IF NOT EXISTS profile_registered_plans "
    "(operation_id VARCHAR PRIMARY KEY, node_id VARCHAR NOT NULL)"
)
PENDING = (
    "SELECT o.id,o.op_type,o.target_id,o.payload,o.actor_id,o.namespace_id FROM operations o "
    "WHERE o.op_type IN ('PROFILE_PREPARED','PROFILE_PUBLISHED') AND ("
    "NOT EXISTS (SELECT 1 FROM profile_registered_plans r WHERE r.operation_id=o.id) "
    "OR NOT EXISTS (SELECT 1 FROM derivation_artifact_owners a WHERE a.node_id=o.target_id) "
    "OR (o.op_type='PROFILE_PREPARED' AND NOT EXISTS (SELECT 1 FROM profile_work w WHERE w.plan_id=o.target_id)))"
)


def decode(row):
    operation_id, kind, target, value, owner, scope = row
    if kind == "PROFILE_PUBLISHED":
        if owner != "profile":
            raise ValueError("Invalid profile publication actor")
        body = json.loads(value) if isinstance(value, str) else value
        owner = ProfilePublication.model_validate_json(body["publication"]).node.user_id
    elif kind != "PROFILE_PREPARED":
        raise ValueError("Invalid profile operation type")
    plan = unpack(value, user_id=owner, plan_id=target)
    expected = (
        plan.prepared_operation_id if kind == "PROFILE_PREPARED" else plan.operation_id
    )
    if expected != operation_id or scope != plan.node.scope.value:
        raise ValueError("Profile operation identity mismatch")
    return plan


def work_state(plan, published, transitions):
    """Validate publication/replacement receipts before restoring a lost row."""
    if published is not None:
        if decode(published).checksum != plan.checksum:
            raise ValueError("Published profile differs from its prepared plan")
        if transitions:
            raise ValueError("Published profile also has a replacement transition")
        return "complete", None
    for operation_id, value, owner, scope, replacement in transitions:
        value = json.loads(value) if isinstance(value, str) else value
        if not isinstance(value, dict) or not isinstance(value.get("replaced_by"), str):
            raise ValueError("Invalid profile replacement receipt")
        if replacement is None:
            raise ValueError("Profile replacement has no prepared successor")
        successor = decode(replacement)
        if (
            successor.key != plan.key
            or value["replaced_by"] != str(successor.node.id)
            or owner != plan.node.user_id
            or scope != plan.node.scope.value
            or operation_id != replacement_operation(str(plan.node.id), successor)
        ):
            raise ValueError("Profile replacement identity mismatch")
    if len(transitions) > 1:
        raise ValueError("Profile preparation has conflicting replacements")
    return ("abandoned", "ReplacedPreparation") if transitions else ("pending", None)


OPERATION = "SELECT id,op_type,target_id,payload,actor_id,namespace_id FROM operations WHERE id=?"
TRANSITIONS = (
    "SELECT id,payload,actor_id,namespace_id FROM operations "
    "WHERE op_type='PROFILE_PREPARATION_REPLACED' AND target_id=?"
)
WORK = (
    "INSERT INTO profile_work (plan_id,user_id,scope,profile_key,request_hash,prepared_operation_id,status,last_error,collection_operation_id) "
    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING"
)
HEAD = (
    "INSERT INTO profile_publication_heads VALUES (?,?) ON CONFLICT (profile_key) DO UPDATE "
    "SET generation=GREATEST(profile_publication_heads.generation,excluded.generation)"
)


def work_values(plan, status, error):
    return [
        str(plan.node.id),
        plan.node.user_id,
        plan.node.scope.value,
        plan.key,
        plan.request_hash,
        plan.prepared_operation_id,
        status,
        error,
        plan.collection_operation_id,
    ]


def successor_id(value):
    from uuid import UUID, uuid5

    body = json.loads(value) if isinstance(value, str) else value
    return str(uuid5(UUID(body["replaced_by"]), "prme:profile-prepared:v1"))


def collection_id(identity):
    from uuid import UUID, uuid5

    return str(uuid5(UUID(identity), "prme:profile-stage-collected:v1"))


def initialize_duck(conn):
    conn.execute(DDL)
    conn.execute(
        "ALTER TABLE profile_work ADD COLUMN IF NOT EXISTS fence_epoch BIGINT DEFAULT 0"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profile_preparation_heads (profile_key VARCHAR PRIMARY KEY, epoch BIGINT NOT NULL)"
    )
    conn.execute(
        "ALTER TABLE profile_work ADD COLUMN IF NOT EXISTS collection_operation_id VARCHAR"
    )
    conn.execute("BEGIN TRANSACTION")
    try:
        migration_cursor = ""
        while True:
            batch = conn.execute(
                "SELECT plan_id FROM profile_work WHERE collection_operation_id IS NULL AND plan_id>? ORDER BY plan_id LIMIT 128",
                [migration_cursor],
            ).fetchall()
            if not batch:
                break
            for (identity,) in batch:
                migration_cursor = identity
                try:
                    operation_id = collection_id(identity)
                except ValueError:
                    logger.warning(
                        "Invalid profile collection identity for %s", identity
                    )
                    continue
                conn.execute(
                    "UPDATE profile_work SET collection_operation_id=? WHERE plan_id=?",
                    [operation_id, identity],
                )
        cursor = ""
        while True:
            rows = conn.execute(
                PENDING + " AND o.id>? ORDER BY o.id LIMIT 128", [cursor]
            ).fetchall()
            if not rows:
                break
            for row in rows:
                cursor = row[0]
                try:
                    plan = decode(row)
                    state = None
                    if row[1] == "PROFILE_PREPARED":
                        published = conn.execute(
                            OPERATION, [plan.operation_id]
                        ).fetchone()
                        transitions = []
                        for transition in conn.execute(
                            TRANSITIONS, [str(plan.node.id)]
                        ).fetchall():
                            replacement = conn.execute(
                                OPERATION, [successor_id(transition[1])]
                            ).fetchone()
                            transitions.append((*transition, replacement))
                        state = work_state(plan, published, transitions)
                        discarded = conn.execute(
                            OPERATION, [discard_operation_id(plan)]
                        ).fetchone()
                        if discarded is not None:
                            if state != ("pending", None):
                                raise ValueError(
                                    "Conflicting profile retirement receipts"
                                )
                            state = discarded_state(plan, discarded)
                except (ValueError, KeyError, TypeError):
                    logger.warning(
                        "Profile ownership registration incomplete for %s", row[0]
                    )
                    continue
                reserve_duck(conn, plan, legacy=True)
                if state is not None:
                    conn.execute(WORK, work_values(plan, *state))
                if row[1] == "PROFILE_PUBLISHED":
                    conn.execute(HEAD, [plan.key, plan.generation + 1])
                conn.execute(
                    "INSERT INTO profile_registered_plans VALUES (?,?) ON CONFLICT DO NOTHING",
                    [row[0], str(plan.node.id)],
                )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


async def initialize_pg(conn):
    async with conn.transaction():
        await conn.execute(DDL)
        await conn.execute(
            "ALTER TABLE profile_work ADD COLUMN IF NOT EXISTS fence_epoch BIGINT DEFAULT 0"
        )
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS profile_preparation_heads (profile_key VARCHAR PRIMARY KEY, epoch BIGINT NOT NULL)"
        )
        await conn.execute(
            "ALTER TABLE profile_work ADD COLUMN IF NOT EXISTS collection_operation_id VARCHAR"
        )
        migration_cursor = ""
        while True:
            batch = await conn.fetch(
                "SELECT plan_id FROM profile_work WHERE collection_operation_id IS NULL AND plan_id>$1 ORDER BY plan_id LIMIT 128",
                migration_cursor,
            )
            if not batch:
                break
            for row in batch:
                migration_cursor = row[0]
                try:
                    operation_id = collection_id(row[0])
                except ValueError:
                    logger.warning("Profile collection identity migration incomplete")
                    continue
                await conn.execute(
                    "UPDATE profile_work SET collection_operation_id=$1 WHERE plan_id=$2",
                    operation_id,
                    row[0],
                )
        cursor = ""
        while True:
            rows = await conn.fetch(
                PENDING + " AND o.id>$1 ORDER BY o.id LIMIT 128", cursor
            )
            if not rows:
                break
            for row in rows:
                cursor = row[0]
                try:
                    plan = decode(row)
                    state = None
                    if row[1] == "PROFILE_PREPARED":
                        published = await conn.fetchrow(
                            pg_sql(OPERATION), plan.operation_id
                        )
                        transitions = []
                        for transition in await conn.fetch(
                            pg_sql(TRANSITIONS), str(plan.node.id)
                        ):
                            replacement = await conn.fetchrow(
                                pg_sql(OPERATION), successor_id(transition[1])
                            )
                            transitions.append((*transition, replacement))
                        state = work_state(plan, published, transitions)
                        discarded = await conn.fetchrow(
                            pg_sql(OPERATION), discard_operation_id(plan)
                        )
                        if discarded is not None:
                            if state != ("pending", None):
                                raise ValueError(
                                    "Conflicting profile retirement receipts"
                                )
                            state = discarded_state(plan, discarded)
                except (ValueError, KeyError, TypeError):
                    logger.warning(
                        "Profile ownership registration incomplete for %s", row[0]
                    )
                    continue
                await reserve_pg(conn, plan, legacy=True)
                if state is not None:
                    await conn.execute(pg_sql(WORK), *work_values(plan, *state))
                if row[1] == "PROFILE_PUBLISHED":
                    await conn.execute(pg_sql(HEAD), plan.key, plan.generation + 1)
                await conn.execute(
                    "INSERT INTO profile_registered_plans VALUES ($1,$2) ON CONFLICT DO NOTHING",
                    row[0],
                    str(plan.node.id),
                )
