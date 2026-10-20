"""Protected processing lifecycle on the existing SQL work/slot transactions.

This is local durable accounting, not the PV-06 current-policy/erasure authority.
It never opens evidence or reconstructs processing input from an operation log.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid5

from prme.privacy.policy import PrivacyDenied
from prme.privacy.records import PrivacyRecord, PRIVACY_KEY


async def guarded_query(repository, sql, args, guard):
    """Hold the existing policy authority through the actual SQL transaction.

    Acquire the independent ledger/policy lease before the connection lock.
    The yielded lifetime check runs again inside the real transaction, after
    any connection wait and before commit; expiry rolls the effect back.
    """
    async with guard() as check:
        if not callable(check):
            raise PrivacyDenied("privacy_capability_unavailable")
        if repository.pool is not None:
            async with repository.pool.acquire() as conn, conn.transaction():
                check()
                result = [dict(row) for row in await conn.fetch(sql, *args)]
                check()
                return result
        from prme.storage._threading import run_to_completion
        async with repository.lock:
            def execute():
                repository.conn.execute("BEGIN TRANSACTION")
                try:
                    check()
                    cursor = repository.conn.execute(sql, list(args))
                    names = [item[0] for item in cursor.description]
                    result = [dict(zip(names, row)) for row in cursor.fetchall()]
                    check()
                    repository.conn.execute("COMMIT")
                    return result
                except BaseException:
                    repository.conn.execute("ROLLBACK")
                    raise
            return await run_to_completion(execute)


def processing_deadline(record):
    return min(
        value
        for value in (
            record.snapshot.processing_deadline,
            record.snapshot.memory_deadline,
        )
        if value is not None
    )


def validate_processing(record, row, claim, now):
    if (
        record is None
        or record.kind != "extraction"
        or record.processing_state == "not_required"
    ):
        return
    if (
        record.processing_state != "pending"
        or processing_deadline(record) <= now
        or row is None
        or claim is None
        or row["status"] != "running"
        or row["generation"] != claim.generation
        or row["lease_expires_at"] is None
        or row["lease_expires_at"] <= now
        or claim.event_id != record.event_id
        or claim.user_id != str(record.snapshot.policy.owner_id)
    ):
        raise PrivacyDenied("privacy_processing_unavailable")


def validate_duck_processing(conn, event_id, row, claim, now):
    found = conn.execute(
        "SELECT body FROM privacy_admissions WHERE event_id=?", [event_id]
    ).fetchone()
    if found:
        validate_processing(
            PrivacyRecord.model_validate_json(found[0]), row, claim, now
        )
    else:
        source = conn.execute(
            "SELECT metadata FROM events WHERE id=?", [event_id]
        ).fetchone()
        if source and PRIVACY_KEY in json.loads(source[0] or "{}"):
            raise PrivacyDenied("privacy_processing_unavailable")


async def validate_pg_processing(conn, event_id, row, claim, now):
    found = await conn.fetchrow(
        "SELECT body FROM privacy_admissions WHERE event_id=$1 FOR UPDATE", event_id
    )
    if found:
        validate_processing(
            PrivacyRecord.model_validate_json(found["body"]), row, claim, now
        )
    else:
        source = await conn.fetchrow(
            "SELECT metadata FROM events WHERE id=$1", event_id
        )
        if source:
            metadata = source["metadata"]
            if PRIVACY_KEY in (
                json.loads(metadata or "{}")
                if isinstance(metadata, str)
                else metadata or {}
            ):
                raise PrivacyDenied("privacy_processing_unavailable")


async def processing_record(session, event_id, user_id):
    lock = " FOR UPDATE" if session.postgres else ""
    rows = await session.fetch(
        "SELECT body FROM privacy_admissions WHERE event_id=$1 AND user_id=$2" + lock,
        event_id,
        user_id,
    )
    return PrivacyRecord.model_validate_json(rows[0]["body"]) if rows else None


async def finish_processing(session, record, state, now):
    """Terminal generation, slot removal and content-free journal commit together."""
    if record.processing_state != "pending":
        return False
    if state not in {"cancelled", "expired"}:
        raise PrivacyDenied("privacy_invalid_result")
    event_id = str(record.event_id)
    after = record.model_copy(
        update={"processing": None, "processing_state": state, "processing_attempt": 0}
    )
    await session.execute(
        "UPDATE privacy_admissions SET body=$1 WHERE event_id=$2",
        after.model_dump_json(),
        event_id,
    )
    await session.execute(
        "UPDATE event_extractions SET status='failed',generation=generation+1,"
        "lease_expires_at=NULL,last_error=$2,updated_at=$3 WHERE event_id=$1",
        event_id,
        "privacy_" + state,
        now,
    )
    payload = json.dumps(
        {
            "schema_version": 1,
            "kind": "processing_terminal",
            "event_id": event_id,
            "source_id": str(record.source_id),
            "representation_id": str(record.representation_id),
            "policy_id": str(record.snapshot.policy.policy_id),
            "policy_revision": record.snapshot.policy.revision,
            "state": state,
            "replay": "unavailable",
            "created_at": now.isoformat(),
        },
        sort_keys=True,
    )
    await session.execute(
        "INSERT INTO operations(id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
        "VALUES ($1,'PRIVACY_PROCESSING_TERMINAL',$2,$3,'privacy',$4,$5) ON CONFLICT DO NOTHING",
        str(uuid5(record.event_id, "prme:processing-terminal:v1:" + state)),
        event_id,
        payload,
        record.snapshot.policy.scope.value,
        now,
    )
    return True


class ProcessingJournal:
    def __init__(self, work, *, clock=None):
        self.work = work
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def _current(self, session, claim):
        event_id = str(claim.event_id)
        await session.lock_work(event_id)
        record = await processing_record(session, event_id, claim.user_id)
        rows = await session.fetch(
            "SELECT status,generation,lease_expires_at FROM event_extractions WHERE event_id=$1",
            event_id,
        )
        if record is None:
            raise PrivacyDenied("privacy_processing_unavailable")
        validate_processing(record, rows[0] if rows else None, claim, self.clock())
        return record

    async def begin(self, claim):
        async with self.work.session(transaction=True) as session:
            record = await self._current(session, claim)
            if record.processing_attempt:
                # An interrupted allowance is spent, never silently replenished.
                raise PrivacyDenied("privacy_processing_unavailable")
            after = record.model_copy(update={"processing_attempt": claim.generation})
            await session.execute(
                "UPDATE privacy_admissions SET body=$1 WHERE event_id=$2",
                after.model_dump_json(),
                str(record.event_id),
            )
            return after

    async def checkpoint(self, claim, before, payload, *, release=False):
        async with self.work.session(transaction=True) as session:
            current = await self._current(session, claim)
            if (
                current != before
                or current.processing_attempt != claim.generation
                or current.processing is None
                or payload.context
                != current.processing.context.model_copy(
                    update={
                        "object_generation": current.processing.context.object_generation
                        + 1
                    }
                )
            ):
                raise PrivacyDenied("privacy_processing_unavailable")
            after = current.model_copy(
                update={
                    "processing": payload,
                    "processing_attempt": 0 if release else claim.generation,
                }
            )
            await session.execute(
                "UPDATE privacy_admissions SET body=$1 WHERE event_id=$2",
                after.model_dump_json(),
                str(current.event_id),
            )
            return after

    async def terminate(self, claim, *, state="cancelled"):
        async with self.work.session(transaction=True) as session:
            await session.lock_work(str(claim.event_id))
            record = await processing_record(
                session, str(claim.event_id), claim.user_id
            )
            rows = await session.fetch(
                "SELECT generation,status FROM event_extractions WHERE event_id=$1",
                str(claim.event_id),
            )
            if (
                record is None
                or not rows
                or rows[0]["generation"] != claim.generation
                or rows[0]["status"] == "complete"
            ):
                return False
            return await finish_processing(session, record, state, self.clock())

    async def expire(self, *, user_id=None):
        # Metadata/opaque ciphertext only; no source or diagnostic key is read.
        async with self.work.session(transaction=True) as session:
            where = " WHERE user_id=$1" if user_id else ""
            rows = await session.fetch(
                "SELECT event_id,user_id FROM privacy_admissions" + where,
                *((user_id,) if user_id else ()),
            )
            count = 0
            for row in rows:
                event_id = str(row["event_id"])
                await session.lock_work(event_id)
                record = await processing_record(session, event_id, row["user_id"])
                if (
                    record.processing_state == "pending"
                    and processing_deadline(record) <= self.clock()
                ):
                    count += await finish_processing(
                        session, record, "expired", self.clock()
                    )
            return count
