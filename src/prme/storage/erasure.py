"""Backend-independent durable erasure fences and resumable logical cleanup.

The external ledger is acquired before policy approval and local transactions.
Only opaque identities are kept in jobs/targets; SQL removal is not media wiping.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import UUID, uuid4, uuid5

from prme.privacy.erasure import ErasureIntent, ErasureRequest, ErasureStatus, LedgerHead
from prme.privacy.policy import PrivacyDenied
from prme.privacy.records import PrivacyRecord
from prme.storage.extraction_work import ExtractionWorkRepository
from prme.storage._threading import run_async_to_completion

DDL = (
    "CREATE TABLE IF NOT EXISTS privacy_store_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),project_id UUID NOT NULL,body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS privacy_erasure_jobs(request_id UUID PRIMARY KEY,owner_id UUID NOT NULL,scope VARCHAR NOT NULL,intent TEXT NOT NULL,status TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS privacy_erasure_targets(request_id UUID NOT NULL,kind VARCHAR NOT NULL,object_id UUID NOT NULL,PRIMARY KEY(request_id,kind,object_id))",
    "CREATE TABLE IF NOT EXISTS privacy_erasure_epoch(singleton INTEGER PRIMARY KEY CHECK(singleton=1),epoch BIGINT NOT NULL)",
)
_HELD: ContextVar[object | None] = ContextVar("prme_erasure_guard", default=None)


class ErasureRepository(ExtractionWorkRepository):
    async def tables(self, session):
        rows = await session.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")
        return {row["table_name"] for row in rows}

    async def state(self, session=None):
        if session is None:
            async with self.session() as current:
                return await self.state(current)
        rows = await session.fetch("SELECT project_id,body FROM privacy_store_state WHERE singleton=1")
        if len(rows) != 1:
            raise PrivacyDenied("privacy_ledger_unavailable")
        return UUID(str(rows[0]["project_id"])), LedgerHead.model_validate_json(rows[0]["body"])

    async def intents(self, session=None):
        if session is None:
            async with self.session() as current:
                return await self.intents(current)
        return tuple(ErasureIntent.model_validate_json(row["intent"]) for row in
                     await session.fetch("SELECT intent FROM privacy_erasure_jobs ORDER BY request_id"))

    async def apply(self, entry):
        async with self.session(transaction=True) as session:
            project, before = await self.state(session)
            if entry.head.sequence <= before.sequence:
                return
            if (entry.head.sequence != before.sequence + 1 or entry.previous_token != before.token
                    or entry.head.ledger_id != before.ledger_id or entry.head.store_id != before.store_id
                    or entry.intent.project_id != project):
                raise PrivacyDenied("privacy_ledger_stale")
            intent = entry.intent
            status = ErasureStatus(request_id=intent.request.request_id, mode=intent.request.mode,
                                   state="blocked_from_access", facts_may_remain=intent.request.mode != "memory",
                                   stage="discover")
            await session.execute("INSERT INTO privacy_erasure_jobs VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                str(intent.request.request_id), str(intent.owner_id), intent.request.scope.value,
                intent.model_dump_json(), status.model_dump_json())
            await session.execute("UPDATE privacy_store_state SET body=$1 WHERE singleton=1", entry.head.model_dump_json())
            # A real changing value participates in every native transaction;
            # unlike a no-op DuckDB update it creates an MVCC write conflict.
            await session.execute("UPDATE privacy_erasure_epoch SET epoch=epoch+1 WHERE singleton=1")

    async def status(self, request_id, *, user_id):
        async with self.session() as session:
            rows = await session.fetch("SELECT status FROM privacy_erasure_jobs WHERE request_id=$1 AND owner_id=$2",
                                       str(request_id), str(user_id))
        return ErasureStatus.model_validate_json(rows[0]["status"]) if rows else None

    async def save_status(self, status):
        async with self.session(transaction=True) as session:
            await session.execute("UPDATE privacy_erasure_jobs SET status=$1 WHERE request_id=$2",
                                  status.model_dump_json(), str(status.request_id))


def affected(intent, record, purpose="memory"):
    request, policy = intent.request, record.snapshot.policy
    if (intent.project_id, intent.owner_id, request.scope) != (policy.project_id, policy.owner_id, policy.scope):
        return False
    if request.mode != "memory" and request.mode != purpose:
        return False
    return (record.event_id in request.event_ids
            or (request.owner_all and record.snapshot.admitted_at <= intent.created_at))


async def prepare_store(*, conn=None, pool=None, runtime=None, namespace_id=None, conn_lock=None):
    """No-content lineage/current-ledger check BEFORE registry/native recovery.

    Existing protected admissions without a binding require an explicit versioned
    migration; missing identity can never silently become a fresh store.
    """
    repository = ErasureRepository(conn=conn, pool=pool, conn_lock=conn_lock)
    async with repository.session() as session:
        tables = await repository.tables(session)
        bound = "privacy_store_state" in tables
        protected = False
        if "privacy_admissions" in tables:
            protected = bool(await session.fetch("SELECT event_id FROM privacy_admissions LIMIT 1"))
        if runtime is None:
            if bound or protected:
                raise PrivacyDenied("privacy_ledger_unavailable")
            return None
        ledger = runtime.erasure_ledger
        if ledger is None:
            raise PrivacyDenied("privacy_ledger_unavailable")
        if not bound and protected:
            raise PrivacyDenied("privacy_migration_required")
    async with ledger.guard():
        if not bound:
            head = await ledger.register_store(namespace_id or uuid4(), runtime.project_id)
            async with repository.session(transaction=True) as session:
                for statement in DDL:
                    await session.execute(statement)
                await session.execute("INSERT INTO privacy_store_state VALUES(1,$1,$2)", str(runtime.project_id), head.model_dump_json())
                await session.execute("INSERT INTO privacy_erasure_epoch VALUES(1,0)")
        project, checkpoint = await repository.state()
        if project != runtime.project_id or (namespace_id is not None and checkpoint.store_id != namespace_id):
            raise PrivacyDenied("privacy_boundary_mismatch")
        entries = await ledger.entries_since(checkpoint)
        for entry in entries:
            await repository.apply(entry)
        # Exact read deadlines already deny without maintenance. Persist due
        # roots now so recovered publications cannot outlive offline expiry.
        async with repository.session() as session:
            tables = await repository.tables(session)
        if "privacy_admissions" in tables:
            after = UUID(int=0)
            while True:
                async with repository.session() as session:
                    rows = await session.fetch("SELECT event_id,body FROM privacy_admissions WHERE event_id>$1 ORDER BY event_id LIMIT 128", str(after))
                if not rows:
                    break
                for row in rows:
                    after = UUID(str(row["event_id"]))
                    record = PrivacyRecord.model_validate_json(row["body"])
                    for mode, deadline, present in (
                        ("memory", record.snapshot.memory_deadline, True),
                        ("evidence", record.snapshot.evidence_deadline, record.evidence is not None),
                        ("processing", record.snapshot.processing_deadline, record.processing_state == "pending"),
                    ):
                        if present and deadline is not None and runtime.clock() >= deadline:
                            request = ErasureRequest(request_id=uuid5(record.event_id, "prme:expiry:v1:" + mode),
                                scope=record.snapshot.policy.scope, mode=mode, event_ids=(record.event_id,))
                            intent = ErasureIntent(request=request, store_id=checkpoint.store_id, project_id=project,
                                owner_id=record.snapshot.policy.owner_id, created_at=deadline, reason="expired")
                            await repository.apply(await ledger.append_intent(intent))
    return repository


async def apply_current_ledger(repository, runtime):
    """Apply independent intents for an already bound restored store.

    Recovery callers run this before reconstructing registries/native indexes.
    Export and publication must additionally hold ErasureCoordinator.guard through
    their actual effects; this checkpoint by itself is not a disclosure permit.
    """
    if runtime is None or runtime.erasure_ledger is None:
        raise PrivacyDenied("privacy_ledger_unavailable")
    async with runtime.erasure_ledger.guard():
        project, checkpoint = await repository.state()
        if project != runtime.project_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        for entry in await runtime.erasure_ledger.entries_since(checkpoint):
            await repository.apply(entry)
        return (await repository.state())[1]


class ErasureCoordinator:
    def __init__(self, admission):
        self.admission = admission
        source = admission.repository
        self.repository = ErasureRepository(conn=source.conn, pool=source.pool, conn_lock=source.lock)

    @asynccontextmanager
    async def guard(self, records=(), *, purpose="memory", snapshots=(), node_ids=()):
        runtime = self.admission.require_runtime()
        ledger = runtime.erasure_ledger
        if ledger is None:
            raise PrivacyDenied("privacy_ledger_unavailable")
        if _HELD.get() is not None and _HELD.get()[0] is self and _HELD.get()[1]:
            # Nested helpers share the outer current-ledger lease, never create
            # a second authorization decision. Policy lease remains external.
            await self.assert_records_current(records, purpose=purpose, snapshots=snapshots, node_ids=node_ids)
            yield
            await self.assert_records_current(records, purpose=purpose, snapshots=snapshots, node_ids=node_ids)
            return
        async with ledger.guard():
            project, checkpoint = await self.repository.state()
            if project != runtime.project_id:
                raise PrivacyDenied("privacy_boundary_mismatch")
            for entry in await ledger.entries_since(checkpoint):
                await self.repository.apply(entry)
            lease = [self, True]
            token = _HELD.set(lease)
            try:
                await self.assert_records_current(records, purpose=purpose, snapshots=snapshots, node_ids=node_ids)
                yield
                await self.assert_records_current(records, purpose=purpose, snapshots=snapshots, node_ids=node_ids)
            finally:
                lease[1] = False
                _HELD.reset(token)

    async def assert_records_current(self, records, *, purpose="memory", snapshots=(), node_ids=()):
        intents = await self.repository.intents()
        selected_nodes = set(map(UUID, map(str, node_ids)))
        if any(intent.request.mode == "memory" and selected_nodes.intersection(intent.request.node_ids) for intent in intents):
            raise PrivacyDenied("privacy_erased")
        for record in records:
            if any(affected(intent, record, purpose) for intent in intents):
                raise PrivacyDenied("privacy_erased")
        # Before an admission gets an identity, an owner-wide cutoff also fences
        # the work admitted before acknowledgement; new admissions remain valid.
        for saved in snapshots:
            for intent in intents:
                if (intent.request.owner_all and intent.request.mode == "memory"
                        and (saved.policy.project_id, saved.policy.owner_id, saved.policy.scope) ==
                        (intent.project_id, intent.owner_id, intent.request.scope)
                        and saved.admitted_at <= intent.created_at):
                    raise PrivacyDenied("privacy_erased")

    async def current_records(self, records):
        records = tuple(records)
        async with self.guard():
            intents = await self.repository.intents()
            return tuple(row for row in records if not any(affected(intent, row) for intent in intents)
                         and (row.snapshot.memory_deadline is None or self.admission.require_runtime().clock() < row.snapshot.memory_deadline))

    async def request(self, request: ErasureRequest, *, user_id: str):
        request = ErasureRequest.model_validate(request)
        runtime = self.admission.require_runtime()
        if str(UUID(user_id)) != user_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        saved = self.admission.effective(None, user_id=user_id, scope=request.scope)
        async with self.guard(), runtime.authority.guard(saved, "publication"):
            # Resolve selected identities in the actual store and owner boundary.
            for event_id in request.event_ids:
                record = await self.admission.repository.get(str(event_id), user_id=user_id)
                if record is None or record.snapshot.policy.scope != request.scope:
                    raise PrivacyDenied("privacy_boundary_mismatch")
            for node_id in request.node_ids:
                row = await self.admission.repository._object_identity("nodes", node_id, owner=user_id, scope=request.scope.value)
                if row is None or not row[2]:
                    raise PrivacyDenied("privacy_boundary_mismatch")
            _, head = await self.repository.state()
            intent = ErasureIntent(request=request, store_id=head.store_id, project_id=runtime.project_id,
                                   owner_id=UUID(user_id), created_at=runtime.clock())
            async def commit():
                # Independent fsync commit precedes the local transactional
                # fence. An interrupted apply is replayed before the next effect.
                await self.repository.apply(await runtime.erasure_ledger.append_intent(intent))
            await run_async_to_completion(commit())
            return await self.repository.status(request.request_id, user_id=user_id)
