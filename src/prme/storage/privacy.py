"""Atomic storage of opt-in admission envelopes and their separate payload slots."""

from __future__ import annotations

from collections.abc import Sequence
import json
from uuid import UUID

from prme.models import Event, MemoryEdge, MemoryNode
from prme.models.direct_store import DirectStoreRecord
from prme.privacy.policy import PrivacyDenied
from prme.privacy.records import PRIVACY_KEY, PrivacyRecord
from prme.storage._threading import run_to_completion
from prme.types import EdgeType, Scope

DDL = """CREATE TABLE IF NOT EXISTS privacy_admissions (
    event_id UUID PRIMARY KEY, user_id VARCHAR NOT NULL, scope VARCHAR NOT NULL,
    request_id UUID, request_index INTEGER NOT NULL, body TEXT NOT NULL,
    UNIQUE(user_id, request_id, request_index)
)"""
ROTATION_DDL = """CREATE TABLE IF NOT EXISTS privacy_rotation_jobs (
    job_id UUID PRIMARY KEY, body TEXT NOT NULL
)"""


def initialize_duck(conn) -> None:
    conn.execute(DDL)
    conn.execute(ROTATION_DDL)


async def initialize_pg(conn) -> None:
    await conn.execute(DDL)
    await conn.execute(ROTATION_DDL)


def _protected_range_sql(postgres: bool, scope: str | None, *, unscoped: bool = False) -> str:
    # Opaque enrollment metadata only, including orphaned or malformed markers.
    # An empty event body or missing work row cannot establish legacy status.
    where = "1=1" if unscoped else "user_id=$1"
    if scope is not None:
        where += " AND scope=$1" if unscoped else " AND scope=$2"
    marker = (f"metadata ? '{PRIVACY_KEY}'" if postgres else
              f"json_extract(metadata,'$.{PRIVACY_KEY}') IS NOT NULL")
    return (f"SELECT 1 FROM privacy_admissions WHERE {where} UNION ALL "
            f"SELECT 1 FROM events WHERE {where} AND {marker} UNION ALL "
            f"SELECT 1 FROM nodes WHERE {where} AND {marker} LIMIT 1")


def require_legacy_duck(conn, user_id: str | None, scope: str | None) -> None:
    args = ([] if user_id is None else [user_id]) + ([scope] if scope is not None else [])
    if conn.execute(_protected_range_sql(False, scope, unscoped=user_id is None), args).fetchone() is not None:
        raise PrivacyDenied("privacy_provider_unavailable")


async def require_legacy_pg(conn, user_id: str | None, scope: str | None) -> None:
    args = ([] if user_id is None else [user_id]) + ([scope] if scope is not None else [])
    if await conn.fetchrow(_protected_range_sql(True, scope, unscoped=user_id is None), *args) is not None:
        raise PrivacyDenied("privacy_provider_unavailable")


async def require_legacy_session(session, user_id: str, scope: str | None) -> None:
    if session.postgres:
        await require_legacy_pg(session.conn, user_id, scope)
    else:
        await run_to_completion(require_legacy_duck, session.conn, user_id, scope)


def _values(record: PrivacyRecord) -> tuple:
    return (str(record.event_id), str(record.snapshot.policy.owner_id), record.snapshot.policy.scope.value,
            str(record.retry.context.request_id) if record.retry else None,
            record.request_index, record.model_dump_json())


def _validate(event: Event, record: PrivacyRecord, node: MemoryNode | None) -> DirectStoreRecord | None:
    if (event.id, event.user_id, event.scope) != (record.event_id, str(record.snapshot.policy.owner_id), record.snapshot.policy.scope):
        raise PrivacyDenied("privacy_boundary_mismatch")
    if event.content or event.metadata != {PRIVACY_KEY: record.reference()}:
        raise PrivacyDenied("privacy_invalid_result")
    if node is None:
        return None
    direct = DirectStoreRecord(schema_version=2, event_id=event.id, content_hash=event.content_hash, node=node)
    direct.verify_source(event)
    return direct


class PrivacyRepository:
    def __init__(self, event_store):
        self.events = event_store
        self.conn = getattr(event_store, "_conn", None)
        self.pool = getattr(event_store, "_pool", None)
        self.lock = getattr(event_store, "_conn_lock", None)

    async def page(self, *, user_id: str, scope: str, after: UUID | None,
                   limit: int = 128) -> tuple[PrivacyRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= 128:
            raise PrivacyDenied("privacy_configuration_invalid")
        args = (user_id, scope, str(after or UUID(int=0)), limit)
        operator = ">=" if after is None else ">"
        sql = f"SELECT body FROM privacy_admissions WHERE user_id=? AND scope=? AND event_id{operator}? ORDER BY event_id LIMIT ?"
        rows = await self._query(sql, args)
        return tuple(PrivacyRecord.model_validate_json(row[0]) for row in rows)

    async def _query(self, sql: str, args: tuple):
        if self.pool is not None:
            for index in range(len(args)):
                sql = sql.replace("?", f"${index + 1}", 1)
            async with self.pool.acquire() as conn:
                return await conn.fetch(sql, *args)
        async with self.lock:
            return await run_to_completion(lambda: self.conn.execute(sql, args).fetchall())

    async def rotation_job(self, job_id: UUID):
        from prme.privacy.rotation import RotationJob
        rows = await self._query("SELECT body FROM privacy_rotation_jobs WHERE job_id=?", (str(job_id),))
        return RotationJob.model_validate_json(rows[0][0]) if rows else None

    async def save_rotation(self, job, *, before=None) -> bool:
        if before is None:
            rows = await self._query(
                "INSERT INTO privacy_rotation_jobs VALUES (?,?) ON CONFLICT DO NOTHING RETURNING job_id",
                (str(job.job_id), job.model_dump_json()))
        else:
            if job.job_id != before.job_id or job.revision != before.revision + 1:
                raise PrivacyDenied("privacy_invalid_result")
            rows = await self._query(
                "UPDATE privacy_rotation_jobs SET body=? WHERE job_id=? AND body=? RETURNING job_id",
                (job.model_dump_json(), str(job.job_id), before.model_dump_json()))
        return bool(rows)

    async def get(self, event_id: str, *, user_id: str) -> PrivacyRecord | None:
        rows = await self._read("event_id=? AND user_id=?", (event_id, user_id))
        return rows[0] if rows else None

    async def for_owner(self, user_id: str) -> tuple[PrivacyRecord, ...]:
        return await self._read("user_id=?", (user_id,))

    async def require_legacy_range(self, user_id: str | None, scope: str | None) -> None:
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                await require_legacy_pg(conn, user_id, scope)
        else:
            async with self.lock:
                await run_to_completion(require_legacy_duck, self.conn, user_id, scope)

    async def create_legacy_group_edge(self, graph, edge: MemoryEdge, *, user_id: str, scope: Scope) -> str:
        """Check the actual group participants on the connection that writes."""
        edge = MemoryEdge.model_validate_json(edge.model_dump_json())
        scope = Scope(scope).value
        if (edge.user_id != user_id or edge.edge_type != EdgeType.MENTIONS
                or (edge.metadata or {}).get("basis") != "group_member" or edge.provenance_event_id is None):
            raise PrivacyDenied("privacy_boundary_mismatch")
        identities = [("nodes", edge.source_id), ("nodes", edge.target_id),
                      ("events", edge.provenance_event_id)]
        if "named_in" in (edge.metadata or {}):
            try:
                identities.append(("events", UUID(edge.metadata["named_in"])))
            except (ValueError, TypeError, AttributeError):
                raise PrivacyDenied("privacy_boundary_mismatch") from None
        if self.pool is not None:
            if graph._pool is not self.pool:
                raise PrivacyDenied("privacy_boundary_mismatch")
            async with self.pool.acquire() as conn, conn.transaction():
                await require_legacy_pg(conn, user_id, scope)
                for table, identifier in identities:
                    row = await conn.fetchrow(f"SELECT user_id,scope FROM {table} WHERE id=$1 FOR SHARE",
                                              str(identifier))
                    if row is None or tuple(row) != (user_id, scope):
                        raise PrivacyDenied("privacy_boundary_mismatch")
                await graph._create_edge_on_connection(conn, edge)
                await require_legacy_pg(conn, user_id, scope)
        else:
            if graph._conn is not self.conn:
                raise PrivacyDenied("privacy_boundary_mismatch")

            def write():
                self.conn.execute("BEGIN TRANSACTION")
                try:
                    require_legacy_duck(self.conn, user_id, scope)
                    for table, identifier in identities:
                        row = self.conn.execute(f"SELECT user_id,scope FROM {table} WHERE id=?",
                                                [str(identifier)]).fetchone()
                        if row != (user_id, scope):
                            raise PrivacyDenied("privacy_boundary_mismatch")
                    graph._create_edge_sync(edge)
                    self.conn.execute("COMMIT")
                except BaseException:
                    self.conn.execute("ROLLBACK")
                    raise

            async with self.lock:
                await run_to_completion(write)
        return str(edge.id)

    async def request(self, request_id: UUID, *, user_id: str) -> tuple[PrivacyRecord, ...]:
        return await self._read("user_id=? AND request_id=?", (user_id, str(request_id)))

    async def _object_identity(self, table, identifier, *, owner=None, scope=None):
        if table not in {"events", "nodes"}:
            raise PrivacyDenied("privacy_invalid_result")
        try:
            identifier = str(UUID(str(identifier)))
        except (TypeError, ValueError):
            return None
        evidence = "evidence_refs" if table == "nodes" else "NULL"
        where, values = "id=?", [identifier]
        for column, value in (("user_id", owner), ("scope", scope)):
            if value is not None:
                where += f" AND {column}=?"
                values.append(value)
        if self.pool is not None:
            for index in range(len(values)):
                where = where.replace("?", f"${index + 1}", 1)
            async with self.pool.acquire() as conn:
                return await conn.fetchrow(
                    f"SELECT user_id,scope,metadata ? '{PRIVACY_KEY}',metadata->'{PRIVACY_KEY}'->>'event_id',"
                    f"{evidence} FROM {table} WHERE {where}", *values)
        async with self.lock:
            return await run_to_completion(lambda: self.conn.execute(
                f"SELECT user_id,scope,json_extract(metadata,'$.{PRIVACY_KEY}') IS NOT NULL,"
                f"json_extract_string(metadata,'$.{PRIVACY_KEY}.event_id'),{evidence} "
                f"FROM {table} WHERE {where}", values).fetchone())

    async def records_for_objects(self, identifiers, *, owner=None, supplemental_evidence=None) -> tuple[PrivacyRecord, ...] | None:
        """Resolve enrollment/dependencies using only opaque identity columns.

        A protected neighbor, even for the same owner, does not enroll a legacy
        object. None means an unscoped target could not establish its boundary.
        This lookup does not grant authorization or open a source payload.
        """
        rows, boundaries = [], set()
        for table, identifier in identifiers:
            row = await self._object_identity(table, identifier, owner=owner)
            if row is None:
                if owner is None:
                    return None
                continue
            rows.append((table, str(UUID(str(identifier))), row))
            boundaries.add((row[0], row[1]))
        if supplemental_evidence is not None and len(boundaries) == 1:
            # Evidence is usable only in the selected primary nodes' boundary.
            # Missing/foreign evidence is left to the existing transactional
            # validator. Discovered ownership never authorizes a protected node
            # or protected evidence: both remain in the returned fence set.
            actual_owner, actual_scope = next(iter(boundaries))
            row = await self._object_identity("events", supplemental_evidence, owner=actual_owner, scope=actual_scope)
            if row is not None:
                rows.append(("events", str(UUID(str(supplemental_evidence))), row))
        records = {}
        for table, identifier, row in rows:
            object_owner, scope, marked, origin, evidence_refs = row
            try:
                refs = [identifier] if table == "events" else json.loads(evidence_refs or "[]")
                if marked:
                    origin = str(UUID(origin))
                    if table == "events" and origin != identifier:
                        raise ValueError()
                    refs.append(origin)
                for reference in refs:
                    saved = await self.get(str(UUID(reference)), user_id=object_owner)
                    if saved is not None:
                        if saved.snapshot.policy.scope.value != scope:
                            raise PrivacyDenied("privacy_boundary_mismatch")
                        records[saved.event_id] = saved
                if marked and UUID(origin) not in records:
                    raise PrivacyDenied("privacy_processing_unavailable")
            except (ValueError, TypeError):
                raise PrivacyDenied("privacy_processing_unavailable") from None
        return tuple(records.values())

    async def has_legacy_request(self, request_id: UUID, *, user_id: str) -> bool:
        from prme.storage.fast_ingest import fast_ingest_operation_id
        identifier = str(fast_ingest_operation_id(user_id, request_id))
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT id FROM operations WHERE id=$1", identifier)
        else:
            async with self.lock:
                row = await run_to_completion(lambda: self.conn.execute("SELECT id FROM operations WHERE id=?", [identifier]).fetchone())
        return row is not None

    async def _read(self, where: str, args: tuple) -> tuple[PrivacyRecord, ...]:
        sql = "SELECT body FROM privacy_admissions WHERE " + where + " ORDER BY request_index,event_id"
        if self.pool is not None:
            for index in range(len(args)):
                sql = sql.replace("?", f"${index + 1}", 1)
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *args)
        else:
            async with self.lock:
                rows = await run_to_completion(lambda: self.conn.execute(sql, args).fetchall())
        return tuple(PrivacyRecord.model_validate_json(row[0]) for row in rows)

    async def accept(self, events: Sequence[Event], records: Sequence[PrivacyRecord],
                     nodes: Sequence[MemoryNode | None], *, extraction: bool = False) -> None:
        """Commit the entire preflighted batch or none of it, including jobs."""
        events = tuple(Event.model_validate_json(e.model_dump_json()) for e in events)
        records = tuple(PrivacyRecord.model_validate_json(r.model_dump_json()) for r in records)
        nodes = tuple(MemoryNode.model_validate_json(n.model_dump_json()) if n else None for n in nodes)
        direct = tuple(_validate(e, r, n) for e, r, n in zip(events, records, nodes, strict=True))
        if self.pool is not None:
            async with self.pool.acquire() as conn, conn.transaction():
                for event, record, intent in zip(events, records, direct, strict=True):
                    await conn.execute(
                        "INSERT INTO events (id,timestamp,role,content,content_hash,user_id,session_id,scope,metadata,created_at,event_time) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11)", *self.events._event_values(event))
                    await conn.execute("INSERT INTO privacy_admissions VALUES ($1,$2,$3,$4,$5,$6)", *_values(record))
                    if intent is not None:
                        await conn.execute("INSERT INTO event_materializations(event_id) VALUES ($1)", str(event.id))
                        await conn.execute(
                            "INSERT INTO operations(id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
                            "VALUES ($1,'DIRECT_STORE_REQUESTED',$2,$3::jsonb,'store',$4,$5)",
                            intent.operation_id, str(event.id), intent.operation_payload(), event.scope.value, event.created_at)
                    if extraction:
                        await conn.execute("INSERT INTO event_extractions(event_id) VALUES ($1)", str(event.id))
        else:
            async with self.lock:
                await run_to_completion(self._accept_duck, events, records, direct, extraction)

    def _accept_duck(self, events, records, direct, extraction):
        from prme.storage.extraction_work import insert_duck_work
        self.conn.execute("BEGIN TRANSACTION")
        try:
            for event, record, intent in zip(events, records, direct, strict=True):
                self.events._append_sync(event)
                self.conn.execute("INSERT INTO privacy_admissions VALUES (?,?,?,?,?,?)", _values(record))
                if intent is not None:
                    self.conn.execute("INSERT INTO event_materializations(event_id) VALUES (?)", [str(event.id)])
                    self.conn.execute(
                        "INSERT INTO operations(id,op_type,target_id,payload,actor_id,namespace_id,created_at) "
                        "VALUES (?,'DIRECT_STORE_REQUESTED',?,?,'store',?,?)",
                        [intent.operation_id, str(event.id), intent.operation_payload(), event.scope.value, event.created_at])
                if extraction:
                    insert_duck_work(self.conn, str(event.id))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    async def replace_envelope(self, before: PrivacyRecord, after: PrivacyRecord) -> bool:
        """CAS seam for PV-03 rewrap; immutable admission identity cannot change."""
        for name in PrivacyRecord.model_fields:
            if name not in {"evidence", "processing"} and getattr(before, name) != getattr(after, name):
                raise PrivacyDenied("privacy_invalid_result")
        for name in ("evidence", "processing"):
            old, new = getattr(before, name), getattr(after, name)
            if old == new:
                continue
            if old is None or new is None or old.context != new.context or new.envelope_revision != old.envelope_revision + 1:
                raise PrivacyDenied("privacy_invalid_result")
        args = (after.model_dump_json(), str(before.event_id), before.model_dump_json())
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                result = await conn.fetchrow("UPDATE privacy_admissions SET body=$1 WHERE event_id=$2 AND body=$3 RETURNING event_id", *args)
        else:
            async with self.lock:
                result = await run_to_completion(lambda: self.conn.execute(
                    "UPDATE privacy_admissions SET body=? WHERE event_id=? AND body=? RETURNING event_id", args).fetchone())
        return result is not None


def consume_duck(conn, event_id: str) -> None:
    """Called inside the actual derivation publication transaction."""
    row = conn.execute("SELECT body FROM privacy_admissions WHERE event_id=?", [event_id]).fetchone()
    if row is not None:
        record = PrivacyRecord.model_validate_json(row[0])
        if record.processing is not None:
            record = record.model_copy(update={"processing": None, "processing_state": "complete", "processing_attempt": 0})
            conn.execute("UPDATE privacy_admissions SET body=? WHERE event_id=?", [record.model_dump_json(), event_id])


async def consume_pg(conn, event_id: str) -> None:
    row = await conn.fetchrow("SELECT body FROM privacy_admissions WHERE event_id=$1 FOR UPDATE", event_id)
    if row is not None:
        record = PrivacyRecord.model_validate_json(row[0])
        if record.processing is not None:
            record = record.model_copy(update={"processing": None, "processing_state": "complete", "processing_attempt": 0})
            await conn.execute("UPDATE privacy_admissions SET body=$1 WHERE event_id=$2", record.model_dump_json(), event_id)
