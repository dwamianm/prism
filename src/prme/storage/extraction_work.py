"""Shared durable work semantics for DuckDB and PostgreSQL (RFC-0016)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import math
import json
from uuid import UUID, uuid5

import duckdb
from prme.models.extraction import extraction_operation_id
from prme.models.extraction_work import ExtractionClaim, ExtractionStatus, StaleExtractionClaimError
from prme.storage._threading import run_to_completion


def insert_duck_work(conn, event_id: str) -> None:
    ordinal = conn.execute(
        "UPDATE extraction_work_sequence SET next_ordinal = next_ordinal + 1 RETURNING next_ordinal - 1"
    ).fetchone()[0]
    conn.execute("INSERT INTO event_extractions (event_id, work_order) VALUES (?, ?)", [event_id, ordinal])


def validate_claim(row, event_id: str, user_id: str, claim: ExtractionClaim | None, now: datetime,
                   *, plan_id: str | None = None) -> None:
    if row is None:
        if claim is not None:
            raise StaleExtractionClaimError("Extraction work no longer exists")
        return  # Unmanaged component/legacy derivations retain their explicit commit API.
    if (claim is None or str(claim.event_id) != str(UUID(event_id)) or claim.user_id != user_id
            or row["status"] != "running" or row["generation"] != claim.generation
            or row["lease_expires_at"] is None or row["lease_expires_at"] <= now
            or (plan_id is not None and str(row["plan_id"]) != plan_id)):
        raise StaleExtractionClaimError("Extraction lease expired or was superseded")


def duck_claim_row(conn, event_id: str):
    row = conn.execute(
        "SELECT status, generation, lease_expires_at, plan_id, plan_revision FROM event_extractions WHERE event_id = ?",
        [event_id],
    ).fetchone()
    return dict(zip(("status", "generation", "lease_expires_at", "plan_id", "plan_revision"), row)) if row else None


def validate_duck_claim(conn, event_id, user_id, claim, *, plan_id=None):
    row = duck_claim_row(conn, event_id)
    now = datetime.now(timezone.utc)
    validate_claim(row, event_id, user_id, claim, now, plan_id=plan_id)
    if row is not None:
        # A read alone cannot fence another DuckDB connection's MVCC update.
        # Touch the owned work row in the same transaction as derived writes
        # so a concurrent generation change causes a write conflict/rollback.
        conn.execute("UPDATE event_extractions SET updated_at = ? WHERE event_id = ?", [now, event_id])
    return row


async def validate_pg_claim(conn, event_id, user_id, claim, *, plan_id=None):
    row = await conn.fetchrow(
        "SELECT status, generation, lease_expires_at, plan_id, plan_revision FROM event_extractions "
        "WHERE event_id = $1 FOR UPDATE", event_id,
    )
    now = await conn.fetchval("SELECT clock_timestamp()")
    validate_claim(row, event_id, user_id, claim, now, plan_id=plan_id)
    return row


class _Session:
    def __init__(self, conn, postgres):
        self.conn, self.postgres = conn, postgres

    async def fetch(self, sql, *args):
        if self.postgres:
            return [dict(row) for row in await self.conn.fetch(sql, *args)]
        def read():
            cursor = self.conn.execute(sql, list(args))
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        return await run_to_completion(read)

    async def execute(self, sql, *args):
        if self.postgres:
            await self.conn.execute(sql, *args)
        else:
            await run_to_completion(self.conn.execute, sql, list(args))

    async def now(self):
        if self.postgres:
            return await self.conn.fetchval("SELECT clock_timestamp()")
        return datetime.now(timezone.utc)

    async def lock_work(self, event_id):
        if self.postgres:
            # Sample lease time only after any competing publication releases
            # its row lock. A timestamp captured before waiting can revive an
            # expired lease once the UPDATE finally executes.
            await self.fetch("SELECT event_id FROM event_extractions WHERE event_id = $1 FOR UPDATE", event_id)


class ExtractionWorkRepository:
    def __init__(self, *, conn=None, conn_lock=None, pool=None):
        self.conn, self.lock, self.pool = conn, conn_lock or asyncio.Lock(), pool

    @asynccontextmanager
    async def session(self, *, transaction=False):
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                if transaction:
                    async with conn.transaction():
                        yield _Session(conn, True)
                else:
                    yield _Session(conn, True)
        else:
            async with self.lock:
                session = _Session(self.conn, False)
                try:
                    if transaction:
                        await session.execute("BEGIN TRANSACTION")
                    yield session
                    if transaction:
                        await session.execute("COMMIT")
                except BaseException:
                    if transaction:
                        # Commit may have completed before cancellation was
                        # delivered. DuckDB then has no transaction to roll back.
                        try:
                            await session.execute("ROLLBACK")
                        except Exception:
                            pass
                    raise

    async def status(self, event_id: str, *, user_id: str) -> ExtractionStatus | None:
        if not user_id:
            raise ValueError("Extraction status requires user_id")
        event_id = str(UUID(event_id))
        async with self.session() as session:
            rows = await session.fetch(
                "SELECT w.event_id, w.status, w.attempts, w.generation, w.plan_id, "
                "w.lease_expires_at, w.next_attempt_at, w.last_error, w.updated_at, w.plan_revision, "
                "EXISTS (SELECT 1 FROM operations o WHERE o.id = $3) AS extracted "
                "FROM event_extractions w JOIN events e ON e.id = w.event_id "
                "WHERE w.event_id = $1 AND e.user_id = $2",
                event_id, user_id, extraction_operation_id(event_id),
            )
        if not rows:
            return None
        row = rows[0]
        extracted = row.pop("extracted")
        row["phase"] = ("complete" if row["status"] == "complete" else "publication" if row["plan_id"]
                        else "preparation" if extracted else "extraction")
        return ExtractionStatus(**row)

    async def claim(self, *, user_id: str, event_id: str | None = None,
                    lease_seconds: float = 300, ignore_schedule: bool = False) -> ExtractionClaim | None:
        try:
            return await self._claim(user_id=user_id, event_id=event_id, lease_seconds=lease_seconds,
                                     ignore_schedule=ignore_schedule)
        except duckdb.TransactionException as exc:
            # Another connection may still be finishing an expired worker's
            # transaction. Treat its write conflict like PostgreSQL SKIP LOCKED.
            if "conflict" not in str(exc).lower():
                raise
            return None

    async def _claim(self, *, user_id: str, event_id: str | None,
                     lease_seconds: float, ignore_schedule: bool) -> ExtractionClaim | None:
        if not user_id or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Extraction claims require an owner and a positive finite lease")
        async with self.session(transaction=True) as session:
            now = await session.now()
            params = [user_id, now]
            specific = ""
            if event_id is not None:
                specific = " AND w.event_id = $3"
                params.append(str(UUID(event_id)))
            due = "" if ignore_schedule else " AND w.next_attempt_at <= $2"
            lock = " FOR UPDATE OF w SKIP LOCKED" if session.postgres else ""
            rows = await session.fetch(
                "SELECT w.event_id, w.generation, w.attempts, w.plan_revision FROM event_extractions w "
                "JOIN events e ON e.id = w.event_id WHERE e.user_id = $1 "
                "AND (w.status = 'pending' OR (w.status = 'running' AND w.lease_expires_at <= $2))" + due + specific +
                " AND NOT EXISTS (SELECT 1 FROM event_extractions prior JOIN events pe ON pe.id = prior.event_id "
                "WHERE pe.user_id = e.user_id AND pe.scope = e.scope AND prior.work_order < w.work_order "
                "AND prior.status IN ('pending', 'running')) ORDER BY w.work_order LIMIT 1" + lock,
                *params,
            )
            if not rows:
                return None
            row = rows[0]
            expires = now + timedelta(seconds=lease_seconds)
            await session.execute(
                "UPDATE event_extractions SET status = 'running', generation = generation + 1, "
                "attempts = attempts + 1, lease_expires_at = $2, updated_at = $3 WHERE event_id = $1",
                str(row["event_id"]), expires, now,
            )
            return ExtractionClaim(event_id=row["event_id"], user_id=user_id, generation=row["generation"] + 1,
                                   attempts=row["attempts"] + 1, lease_expires_at=expires, plan_revision=row["plan_revision"])

    async def renew(self, claim: ExtractionClaim, *, lease_seconds: float = 300) -> bool:
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Lease duration must be positive and finite")
        async with self.session(transaction=True) as session:
            await session.lock_work(str(claim.event_id))
            now = await session.now()
            rows = await session.fetch(
                "UPDATE event_extractions SET lease_expires_at = $4, updated_at = $3 WHERE event_id = $1 "
                "AND generation = $2 AND status = 'running' AND lease_expires_at > $3 "
                "AND EXISTS (SELECT 1 FROM events e WHERE e.id = event_id AND e.user_id = $5) RETURNING event_id",
                str(claim.event_id), claim.generation, now, now + timedelta(seconds=lease_seconds), claim.user_id,
            )
            return bool(rows)

    async def fail(self, claim: ExtractionClaim, *, error: str, retry_after: float | None = None) -> bool:
        # Persist reason codes, never provider exception messages or credentials.
        if not error or len(error) > 100 or any(not (c.isalnum() or c == "_") for c in error):
            raise ValueError("Extraction failures require a bounded reason code")
        if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
            raise ValueError("Retry delay must be finite and nonnegative")
        async with self.session(transaction=True) as session:
            await session.lock_work(str(claim.event_id))
            now = await session.now()
            rows = await session.fetch(
                "UPDATE event_extractions SET status = $4, last_error = $5, next_attempt_at = $6, "
                "lease_expires_at = NULL, updated_at = $3 WHERE event_id = $1 AND generation = $2 "
                "AND status = 'running' AND lease_expires_at > $3 "
                "AND EXISTS (SELECT 1 FROM events e WHERE e.id = event_id AND e.user_id = $7) RETURNING event_id",
                str(claim.event_id), claim.generation, now, "failed" if retry_after is None else "pending",
                error, now + timedelta(seconds=retry_after or 0), claim.user_id,
            )
            return bool(rows)

    async def replan(self, event_id: str, *, user_id: str) -> bool:
        """Queue a new immutable plan revision; never interrupt a live worker."""
        try:
            return await self._replan(event_id, user_id=user_id)
        except duckdb.TransactionException as exc:
            if "conflict" not in str(exc).lower():
                raise
            return False

    async def _replan(self, event_id: str, *, user_id: str) -> bool:
        if not user_id:
            raise ValueError("Replanning requires user_id")
        event_id = str(UUID(event_id))
        async with self.session(transaction=True) as session:
            lock = " FOR UPDATE" if session.postgres else ""
            source = await session.fetch("SELECT scope FROM events WHERE id = $1 AND user_id = $2" + lock,
                                         event_id, user_id)
            if not source:
                return False
            rows = await session.fetch("SELECT status, generation, plan_id, plan_revision, lease_expires_at "
                                       "FROM event_extractions WHERE event_id = $1" + lock, event_id)
            now = await session.now()
            if not rows:
                return False
            row = rows[0]
            if (row["status"] == "complete" or row["plan_id"] is None
                    or (row["status"] == "running" and row["lease_expires_at"] > now)):
                return False
            revision, generation = row["plan_revision"] + 1, row["generation"] + 1
            await session.execute(
                "UPDATE event_extractions SET status = 'pending', plan_id = NULL, plan_revision = $2, "
                "generation = $3, lease_expires_at = NULL, next_attempt_at = $4, updated_at = $4, "
                "last_error = NULL WHERE event_id = $1", event_id, revision, generation, now,
            )
            payload = json.dumps({"event_id": event_id, "user_id": user_id,
                                  "previous_plan_id": str(row["plan_id"]), "revision": revision,
                                  "generation": generation})
            await session.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id, created_at) "
                "VALUES ($1, 'DERIVATION_REPLAN_REQUESTED', $2, $3, 'derivation', $4, $5)",
                str(uuid5(UUID(event_id), f"prme:derivation-replan:v1:{revision}")), event_id,
                payload, source[0]["scope"], now,
            )
            return True

    async def retry(self, event_id: str, *, user_id: str) -> bool:
        if not user_id:
            raise ValueError("Retry requires user_id")
        async with self.session(transaction=True) as session:
            now = await session.now()
            rows = await session.fetch(
                "UPDATE event_extractions SET status = 'pending', next_attempt_at = $2, updated_at = $2 "
                "WHERE event_id = $1 AND status IN ('pending', 'failed') "
                "AND EXISTS (SELECT 1 FROM events e WHERE e.id = event_id AND e.user_id = $3) RETURNING event_id",
                str(UUID(event_id)), now, user_id,
            )
            return bool(rows)

    async def counts(self, *, user_id: str) -> tuple[int, int]:
        if not user_id:
            raise ValueError("Extraction counts require user_id")
        async with self.session() as session:
            rows = await session.fetch(
                "SELECT count(*) FILTER (WHERE w.status IN ('pending', 'running')) AS pending, "
                "count(*) FILTER (WHERE w.status = 'failed') AS failed FROM event_extractions w "
                "JOIN events e ON e.id = w.event_id WHERE e.user_id = $1", user_id,
            )
            return rows[0]["pending"], rows[0]["failed"]
