"""Backend-independent durable erasure fences and resumable logical cleanup.

The external ledger is acquired before policy approval and local transactions.
Only opaque identities are kept in jobs/targets; SQL removal is not media wiping.
"""
from __future__ import annotations

import asyncio

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
    "CREATE TABLE IF NOT EXISTS privacy_erasure_progress(request_id UUID NOT NULL,stage VARCHAR NOT NULL,body TEXT NOT NULL,PRIMARY KEY(request_id,stage))",
    "CREATE TABLE IF NOT EXISTS privacy_erasure_artifacts(request_id UUID PRIMARY KEY,artifact_id UUID NOT NULL,state VARCHAR NOT NULL)",
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

    async def existing_request(self, request_id, *, user_id):
        async with self.session() as session:
            rows = await session.fetch("SELECT intent,status FROM privacy_erasure_jobs WHERE request_id=$1 AND owner_id=$2",
                                       str(request_id), str(user_id))
        if not rows:
            return None
        return (ErasureIntent.model_validate_json(rows[0]["intent"]).request,
                ErasureStatus.model_validate_json(rows[0]["status"]))

    async def save_status(self, status):
        async with self.session(transaction=True) as session:
            await session.execute("UPDATE privacy_erasure_jobs SET status=$1 WHERE request_id=$2",
                                  status.model_dump_json(), str(status.request_id))


def affected(intent, record, purpose="memory"):
    request, policy = intent.request, record.snapshot.policy
    if (intent.project_id, intent.owner_id, request.scope) != (policy.project_id, policy.owner_id, policy.scope):
        return False
    if request.mode != "memory" and request.mode != purpose and not (request.mode == "evidence" and purpose == "processing"):
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
        async with repository.session(transaction=True) as session:
            for statement in DDL:
                await session.execute(statement)
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
        for intent in await repository.intents():
            await _discover(repository, intent)
            await _purge_sql(repository, intent)
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
        if _HELD.get() is not None and _HELD.get()[0] is self and _HELD.get()[1] and _HELD.get()[2] is asyncio.current_task():
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
            lease = [self, True, asyncio.current_task()]
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
        blocked = {node for intent in intents if intent.request.mode == "memory" for node in intent.request.node_ids}
        async with self.repository.session() as session:
            blocked.update(UUID(str(row["object_id"])) for row in await session.fetch(
                "SELECT DISTINCT object_id FROM privacy_erasure_targets WHERE kind='node'"))
            visited = set()
            while selected_nodes:
                if selected_nodes.intersection(blocked):
                    raise PrivacyDenied("privacy_erased")
                identifier = selected_nodes.pop()
                if identifier in visited:
                    continue
                visited.add(identifier)
                rows = await session.fetch("SELECT metadata FROM nodes WHERE id=$1", str(identifier))
                if rows:
                    import json
                    metadata = json.loads(rows[0]["metadata"]) if isinstance(rows[0]["metadata"], str) else rows[0]["metadata"]
                    reference = (metadata or {}).get("_prme_privacy_journal_v1", {})
                    selected_nodes.update(UUID(value) for value in reference.get("source_node_ids", ()))
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
            # A completed node selection has no live row left to resolve. The
            # current ledger has already been applied; retry its exact owned
            # intent before resolving targets for a new request.
            existing = await self.repository.existing_request(request.request_id, user_id=user_id)
            if existing is not None:
                if existing[0] != request:
                    raise PrivacyDenied("privacy_erasure_conflict")
                return existing[1]
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


# Operation payloads are typed closed records or JSON strings containing those
# records. Inspect structural UUID fields only, never substring-match content.
_ID_FIELDS = frozenset({"id", "event_id", "source_id", "representation_id", "node_id", "target_id",
    "source_node_id", "target_node_id", "old_node_id", "new_node_id", "request_id", "plan_id",
    "evidence_refs", "event_ids", "node_ids", "admitted_node_ids", "source_node_ids", "selected_source_node_ids",
    "provenance_event_id", "winner_id", "loser_id", "candidate_id", "operation_id", "chain_node_ids"})


def _identities(value, key=""):
    import json
    found = set()
    if isinstance(value, str):
        if key in _ID_FIELDS:
            try:
                found.add(UUID(value))
            except ValueError:
                pass
        if value[:1] in {"{", "["} and key not in {"content", "text", "query", "description"}:
            try:
                found.update(_identities(json.loads(value)))
            except (ValueError, TypeError):
                pass
    elif isinstance(value, dict):
        for name, item in value.items():
            found.update(_identities(item, name))
    elif isinstance(value, list):
        for item in value:
            found.update(_identities(item, key))
    return found


def _node_identities(value):
    """Find allocated nodes inside validated admitted journal snapshots."""
    import json
    found = set()
    if isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            return _node_identities(json.loads(value))
        except ValueError:
            return found
    if isinstance(value, dict):
        if {"id", "node_type", "user_id", "content", "metadata"}.issubset(value):
            try:
                found.add(UUID(value["id"]))
            except (ValueError, TypeError):
                pass
        for item in value.values():
            found.update(_node_identities(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_node_identities(item))
    return found


def _allocated_nodes(kind, value):
    """Exact outputs of existing typed codecs, including unpublished plans."""
    import json
    body = json.loads(value) if isinstance(value, str) else value
    if kind == "DIRECT_STORE_REQUESTED":
        from prme.models.direct_store import DirectStoreRecord
        return {DirectStoreRecord.from_operation_payload(body).node.id}
    if kind == "DERIVATION_PREPARED":
        from prme.storage.derivation_registry import decode_plan
        return {node.id for node in decode_plan(body).nodes}
    if kind in {"PROFILE_PREPARED", "PROFILE_PUBLISHED", "CONSOLIDATION_PREPARED", "CONSOLIDATION_PUBLISHED"}:
        from prme.models.profile import ProfilePublication
        from prme.models.consolidation import ConsolidationPublication
        codec = ProfilePublication if kind.startswith("PROFILE_") else ConsolidationPublication
        plan = codec.model_validate_json(body["publication"])
        if body["checksum"] != plan.checksum:
            raise PrivacyDenied("privacy_invalid_result")
        return {plan.node.id}
    return set()


async def _targets(session, request_id, kind=None):
    args = [str(request_id)]
    sql = "SELECT kind,object_id FROM privacy_erasure_targets WHERE request_id=$1"
    if kind:
        sql += " AND kind=$2"
        args.append(kind)
    return {(row["kind"], UUID(str(row["object_id"]))) for row in await session.fetch(sql, *args)}


async def _target(session, request_id, kind, identifier):
    return bool(await session.fetch("INSERT INTO privacy_erasure_targets VALUES($1,$2,$3) ON CONFLICT DO NOTHING RETURNING object_id",
                          str(request_id), kind, str(identifier)))


async def _independent_direct_support(session, identifier, metadata, event_roots, intent):
    """Retain only a full unchanged claim explicitly admitted by a live source.

    An evidence_refs membership is never itself support. This intentionally
    refuses uncertain extracted/mixed summaries and performs no text rewriting.
    """
    import json
    from prme.models.direct_store import DirectStoreRecord
    try:
        origin = UUID(metadata["_prme_privacy_v1"]["event_id"])
    except (KeyError, ValueError, TypeError):
        return False
    if origin in event_roots or UUID(str(identifier)) in intent.request.node_ids:
        return False
    rows = await session.fetch("SELECT body FROM privacy_admissions WHERE event_id=$1", str(origin))
    if not rows:
        return False
    record = PrivacyRecord.model_validate_json(rows[0]["body"])
    if record.kind != "direct" or (record.snapshot.memory_deadline is not None and record.snapshot.memory_deadline <= intent.created_at):
        # A finite original policy cannot be implicitly extended by a surviving
        # source. Its independently computed expiry remains a separate root.
        return False
    originals = await session.fetch("SELECT payload FROM operations WHERE target_id=$1 AND op_type='DIRECT_STORE_REQUESTED'", str(origin))
    if len(originals) != 1:
        return False
    original = DirectStoreRecord.from_operation_payload(originals[0]["payload"]).node
    columns = ("content", "node_type", "epistemic_type", "source_type", "valid_from", "valid_to", "event_time")
    actual = await session.fetch("SELECT " + ",".join(columns) + " FROM nodes WHERE id=$1 AND user_id=$2", str(identifier), str(intent.owner_id))
    if not actual or original.id != UUID(str(identifier)) or original.metadata != metadata:
        return False
    for name in columns:
        expected = getattr(original, name)
        if getattr(expected, "value", expected) != actual[0][name]:
            return False
    # Remove only the erased evidence edge; content, qualifiers and original
    # immutable admission remain unchanged and independently justified.
    refs = await session.fetch("SELECT evidence_refs FROM nodes WHERE id=$1", str(identifier))
    values = json.loads(refs[0]["evidence_refs"]) if isinstance(refs[0]["evidence_refs"], str) else refs[0]["evidence_refs"]
    remaining = [str(value) for value in values if UUID(str(value)) not in event_roots]
    await session.execute("UPDATE nodes SET evidence_refs=$1 WHERE id=$2", json.dumps(remaining), str(identifier))
    return True


async def _progress(session, request_id, stage, default=None):
    import json
    rows = await session.fetch("SELECT body FROM privacy_erasure_progress WHERE request_id=$1 AND stage=$2", str(request_id), stage)
    return json.loads(rows[0]["body"]) if rows else default


async def _save_progress(session, request_id, stage, body):
    import json
    await session.execute("INSERT INTO privacy_erasure_progress VALUES($1,$2,$3) ON CONFLICT(request_id,stage) DO UPDATE SET body=excluded.body",
                          str(request_id), stage, json.dumps(body, sort_keys=True))


async def _discover_step(repository, intent):
    """One durable keyset page; interrupted discovery resumes its exact cursor."""
    import json
    request = intent.request
    async with repository.session(transaction=True) as session:
        progress = await _progress(session, request.request_id, "discover", {"phase": "events", "after": "", "changed": False})
        if progress["phase"] == "done":
            return True
        for event in request.event_ids:
            await _target(session, request.request_id, "event", event)
        for node in request.node_ids:
            await _target(session, request.request_id, "node", node)
        phase, cursor = progress["phase"], progress["after"]
        if phase == "events":
            rows = await session.fetch("SELECT event_id,body FROM privacy_admissions WHERE user_id=$1 AND scope=$2 AND event_id>$3 ORDER BY event_id LIMIT 128",
                str(intent.owner_id), request.scope.value, cursor or str(UUID(int=0)))
            for row in rows:
                progress["after"] = str(row["event_id"])
                record = PrivacyRecord.model_validate_json(row["body"])
                if request.owner_all and record.snapshot.admitted_at <= intent.created_at:
                    await _target(session, request.request_id, "event", record.event_id)
            if len(rows) < 128:
                progress.update(phase="nodes" if request.mode == "memory" else "done", after="")
        else:
            targets = await _targets(session, request.request_id)
            roots = {identifier for _, identifier in targets}
            event_roots = {identifier for kind, identifier in targets if kind == "event"}
            if phase == "nodes":
                rows = await session.fetch("SELECT id,metadata,evidence_refs FROM nodes WHERE user_id=$1 AND scope=$2 AND id>$3 ORDER BY id LIMIT 128",
                    str(intent.owner_id), request.scope.value, cursor or str(UUID(int=0)))
                for row in rows:
                    progress["after"] = str(row["id"])
                    metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                    references = json.loads(row["evidence_refs"]) if isinstance(row["evidence_refs"], str) else row["evidence_refs"]
                    if _identities(metadata).intersection(roots) or {UUID(str(v)) for v in references or ()}.intersection(event_roots):
                        if await _independent_direct_support(session, row["id"], metadata, event_roots, intent):
                            continue
                        if await _target(session, request.request_id, "node", row["id"]):
                            progress["changed"] = True
                if len(rows) < 128:
                    progress.update(phase="operations", after="")
            else:
                rows = await session.fetch("SELECT id,op_type,target_id,payload FROM operations WHERE id>$1 AND op_type<>'PRIVACY_ERASED' ORDER BY id LIMIT 128", cursor)
                for row in rows:
                    progress["after"] = row["id"]
                    try:
                        output = UUID(str(row["target_id"]))
                    except ValueError:
                        output = None
                    if output in roots or _identities(row["payload"]).intersection(roots):
                        if await _target(session, request.request_id, "operation", UUID(row["id"])):
                            progress["changed"] = True
                        # A receipt's public request ID differs from its journal
                        # row ID. Feedback/citations depend on that exact logical
                        # copy identity, so include it in the next closure pass.
                        if row["op_type"] == "RETRIEVAL_REQUEST" and output is not None and await _target(session, request.request_id, "copy", output):
                            progress["changed"] = True
                        allocated = _allocated_nodes(row["op_type"], row["payload"])
                        for identifier in allocated:
                            if await _target(session, request.request_id, "node", identifier):
                                progress["changed"] = True
                if len(rows) < 128:
                    progress.update(phase="nodes" if progress["changed"] else "done", after="", changed=False)
        await _save_progress(session, request.request_id, "discover", progress)
        return progress["phase"] == "done"


async def _discover(repository, intent):
    while not await _discover_step(repository, intent):
        pass


async def _purge_sql_step(repository, intent):
    import json
    from prme.privacy.erasure import ErasedOperation
    from prme.storage.privacy_journals import finish_processing
    request = intent.request
    async with repository.session(transaction=True) as session:
        tables = await repository.tables(session)
        progress = await _progress(session, request.request_id, "sql", {"kind": "", "after": str(UUID(int=0)), "done": False})
        if progress["done"]:
            return True
        rows = await session.fetch("SELECT kind,object_id FROM privacy_erasure_targets WHERE request_id=$1 AND (kind>$2 OR (kind=$2 AND object_id>$3)) ORDER BY kind,object_id LIMIT 128",
                                   str(request.request_id), progress["kind"], progress["after"])
        targets = {(row["kind"], UUID(str(row["object_id"]))) for row in rows}
        if rows:
            progress.update(kind=rows[-1]["kind"], after=str(rows[-1]["object_id"]))
        progress["done"] = len(rows) < 128
        await _save_progress(session, request.request_id, "sql", progress)
        event_ids = sorted((identity for kind, identity in targets if kind == "event"), key=str)
        for event_id in event_ids:
            rows = await session.fetch("SELECT body FROM privacy_admissions WHERE event_id=$1 AND user_id=$2", str(event_id), str(intent.owner_id))
            if not rows:
                continue
            record = PrivacyRecord.model_validate_json(rows[0]["body"])
            if request.mode == "memory" and record.pseudonym_epoch is not None:
                await _target(session, request.request_id, "epoch", record.pseudonym_epoch)
            if (request.mode == "memory" and record.pseudonym_epoch is None and record.sanitization is not None
                    and any(name.endswith(":pseudonymize") and count for name, count in record.sanitization.action_counts)):
                raise PrivacyDenied("privacy_migration_required")
            for payload in (record.evidence if request.mode != "processing" else None, record.processing):
                if payload is not None:
                    await _target(session, request.request_id, "key", payload.key_version)
            if record.retry is not None:
                await _target(session, request.request_id, "key", record.retry.key_version)
            if record.processing_state == "pending":
                await finish_processing(session, record, "expired" if intent.reason == "expired" else "cancelled", intent.created_at)
            after = record.model_copy(update={"evidence": None if request.mode != "processing" else record.evidence,
                "processing": None, "processing_state": "expired" if intent.reason == "expired" else "cancelled",
                "processing_attempt": 0, "retry": None,
                "evidence_disposition": ("expired" if intent.reason == "expired" else "erased") if request.mode != "processing" else record.evidence_disposition,
                "pseudonym_epoch": None if request.mode == "memory" else record.pseudonym_epoch})
            await session.execute("UPDATE privacy_admissions SET body=$1 WHERE event_id=$2", after.model_dump_json(), str(event_id))
            if request.mode == "memory":
                # Keep an opaque enrollment reservation. A predictable digest of
                # erased content is not a valid integrity/replay record.
                await session.execute("UPDATE events SET content='',content_hash=$1,metadata=$2 WHERE id=$3 AND user_id=$4",
                    "", json.dumps({"_prme_privacy_v1": record.reference(), "erasure": "memory"}), str(event_id), str(intent.owner_id))
                await session.execute("UPDATE event_materializations SET status='failed',last_error='privacy_erased' WHERE event_id=$1", str(event_id))
        if request.mode != "memory":
            return progress["done"]
        node_ids = sorted((identity for kind, identity in targets if kind == "node"), key=str)
        for identifier in node_ids:
            node = str(identifier)
            await session.execute("DELETE FROM edges WHERE user_id=$1 AND (source_id=$2 OR target_id=$2)", str(intent.owner_id), node)
            if "lexical_documents" in tables:
                await session.execute("DELETE FROM lexical_documents WHERE node_id=$1", node)
            if "vector_metadata" in tables:
                await session.execute("DELETE FROM vector_payloads WHERE vector_key IN (SELECT vector_key FROM vector_metadata WHERE node_id=$1)", node)
                await session.execute("DELETE FROM vector_staging WHERE node_id=$1", node)
                await session.execute("DELETE FROM vector_metadata WHERE node_id=$1", node)
            await session.execute("DELETE FROM nodes WHERE id=$1 AND user_id=$2", node, str(intent.owner_id))
            if "profile_work" in tables:
                # request_hash is a copy commitment; remove the work row rather
                # than retain it under a fictitious successful terminal state.
                await session.execute("DELETE FROM profile_work WHERE plan_id=$1 AND user_id=$2", node, str(intent.owner_id))
            for table in ("derivation_artifact_owners", "profile_registered_plans", "consolidation_registered_plans"):
                if table in tables:
                    await session.execute(f"DELETE FROM {table} WHERE node_id=$1", node)
            if "consolidation_stage_fences" in tables:
                await session.execute("UPDATE consolidation_stage_fences SET epoch=epoch+1 WHERE plan_id=$1", node)
        for event_id in event_ids:
            await session.execute("DELETE FROM edges WHERE provenance_event_id=$1 AND user_id=$2", str(event_id), str(intent.owner_id))
        for kind, identifier in targets:
            if kind != "operation":
                continue
            rows = await session.fetch("SELECT op_type FROM operations WHERE id=$1", str(identifier))
            if not rows or rows[0]["op_type"] == "PRIVACY_ERASED":
                continue
            tombstone = ErasedOperation(operation_id=identifier, request_id=request.request_id,
                original_type=rows[0]["op_type"], owner_id=intent.owner_id, scope=request.scope)
            await session.execute("UPDATE operations SET op_type='PRIVACY_ERASED',payload=$1 WHERE id=$2", tombstone.model_dump_json(), str(identifier))
        await session.execute("UPDATE privacy_erasure_epoch SET epoch=epoch+1 WHERE singleton=1")
        return progress["done"]


async def _purge_sql(repository, intent):
    while not await _purge_sql_step(repository, intent):
        pass


async def _retire_keys(coordinator, intent):
    runtime = coordinator.admission.require_runtime()
    repository = coordinator.repository
    async with repository.session() as session:
        selected = {identity for _, identity in await _targets(session, intent.request.request_id, "key")}
        epochs = {identity for _, identity in await _targets(session, intent.request.request_id, "epoch")}
        live = set()
        live_epochs = set()
        rows = await session.fetch("SELECT body FROM privacy_admissions")
        for row in rows:
            record = PrivacyRecord.model_validate_json(row["body"])
            live.update(payload.key_version for payload in (record.evidence, record.processing) if payload is not None)
            if record.retry:
                live.add(record.retry.key_version)
            if record.pseudonym_epoch is not None:
                live_epochs.add((record.snapshot.policy.project_id, record.snapshot.policy.owner_id,
                                 record.snapshot.policy.scope.value, record.pseudonym_epoch))
    retired = selected - live
    boundary = (intent.project_id, intent.owner_id, intent.request.scope.value)
    epochs = {epoch for epoch in epochs if (*boundary, epoch) not in live_epochs}
    if not retired and not epochs:
        return
    custody = runtime.key_custody
    if custody is None:
        raise PrivacyDenied("privacy_provider_unavailable")
    inventory = {item.version: item for item in custody.inventory()}
    for epoch in epochs:
        matches = [item.version for item in inventory.values() if item.auxiliary is not None
                   and item.auxiliary.purpose == "pseudonym" and item.auxiliary.epoch == epoch
                   and (item.auxiliary.boundary.project_id, item.auxiliary.boundary.owner_id, item.auxiliary.boundary.scope) == boundary]
        if not matches:
            raise PrivacyDenied("privacy_provider_unavailable")
        retired.update(matches)
    for version in sorted(retired, key=str):
        entry = inventory.get(version)
        if entry is None:
            raise PrivacyDenied("privacy_provider_unavailable")
        # Custody records denial before removing native private parts. A prior
        # failed removal is still pending even when its public state is retired.
        # The existing exact-version operation retries removal idempotently.
        await custody.retire(version)


async def process_erasures(coordinator, *, user_id, limit=16, budget_ms=1000):
    """Explicit resumable local maintenance; no offline scheduler is promised."""
    import time
    if type(limit) is not int or not 1 <= limit <= 128 or type(budget_ms) is not int or not 1 <= budget_ms <= 60000:
        raise PrivacyDenied("privacy_configuration_invalid")
    expires = time.monotonic() + budget_ms / 1000
    results = []
    async with coordinator.guard():
        runtime = coordinator.admission.require_runtime()
        async with coordinator.repository.session() as session:
            # Completed requests must not consume the bounded pending-work
            # window forever. Keep the JSON status portable across backends.
            rows = await session.fetch("SELECT intent,status FROM privacy_erasure_jobs WHERE owner_id=$1 ORDER BY CASE WHEN status LIKE '%\"logical_live_copies\":\"purged\"%' THEN 1 ELSE 0 END,request_id LIMIT $2", user_id, limit)
        for row in rows:
            intent = ErasureIntent.model_validate_json(row["intent"])
            status = ErasureStatus.model_validate_json(row["status"])
            saved = coordinator.admission.effective(None, user_id=user_id, scope=intent.request.scope)
            async with runtime.authority.guard(saved, "publication"):
                while status.logical_live_copies != "purged" and time.monotonic() < expires:
                    try:
                        if status.stage == "discover":
                            if not await _discover_step(coordinator.repository, intent):
                                continue
                            next_stage = "sql"
                        elif status.stage == "sql":
                            if not await _purge_sql_step(coordinator.repository, intent):
                                continue
                            next_stage = "indexes"
                        elif status.stage == "indexes":
                            from prme.storage.erasure_indexes import purge_indexes
                            await purge_indexes(coordinator.admission.engine, intent)
                            next_stage = "keys"
                        elif status.stage == "keys":
                            await _retire_keys(coordinator, intent)
                            next_stage = "verify"
                        else:
                            await _verify_purge(coordinator, intent)
                            completed = status.model_copy(update={"state": "live_purged", "logical_live_copies": "purged", "failure": None})
                            await coordinator.repository.save_status(completed)
                            status = completed
                            break
                        advanced = status.model_copy(update={"stage": next_stage, "failure": None})
                        await coordinator.repository.save_status(advanced)
                        status = advanced
                    except Exception:
                        failure = {"indexes": "index_unavailable", "keys": "provider_unavailable", "verify": "coverage_unavailable"}.get(status.stage, "storage_unavailable")
                        status = status.model_copy(update={"failure": failure, "attempts": status.attempts + 1})
                        await coordinator.repository.save_status(status)
                        break
                results.append(status)
    return tuple(results)


async def _verify_purge(coordinator, intent):
    """Verify every saved target against live stores, never an empty root scan."""
    repository = coordinator.repository
    async with repository.session() as session:
        targets = await _targets(session, intent.request.request_id)
        if not targets:
            # An owner-wide request for an empty protected store still has an
            # access cutoff; it provides no positive purge evidence.
            if not intent.request.owner_all:
                raise PrivacyDenied("privacy_invalid_result")
            return
        tables = await repository.tables(session)
        for kind, identifier in targets:
            if kind == "event":
                rows = await session.fetch("SELECT body FROM privacy_admissions WHERE event_id=$1", str(identifier))
                if not rows:
                    raise PrivacyDenied("privacy_invalid_result")
                record = PrivacyRecord.model_validate_json(rows[0]["body"])
                if record.processing is not None or (intent.request.mode != "processing" and record.evidence is not None):
                    raise PrivacyDenied("privacy_invalid_result")
            if intent.request.mode != "memory":
                continue
            if kind == "node":
                for table, column in (("nodes", "id"), ("vector_metadata", "node_id"), ("vector_staging", "node_id"), ("lexical_documents", "node_id")):
                    if table in tables and await session.fetch(f"SELECT 1 FROM {table} WHERE {column}=$1 LIMIT 1", str(identifier)):
                        raise PrivacyDenied("privacy_invalid_result")
                if await session.fetch("SELECT 1 FROM edges WHERE source_id=$1 OR target_id=$1 LIMIT 1", str(identifier)):
                    raise PrivacyDenied("privacy_invalid_result")
            if kind == "operation":
                rows = await session.fetch("SELECT op_type,payload FROM operations WHERE id=$1", str(identifier))
                if rows and rows[0]["op_type"] != "PRIVACY_ERASED":
                    raise PrivacyDenied("privacy_invalid_result")
        if "vector_metadata" in tables:
            valid_keys = {row["vector_key"] for row in await session.fetch("SELECT vector_key FROM vector_metadata")}
        else:
            valid_keys = None
    if valid_keys is not None:
        import numpy as np
        from prme.storage.erasure_indexes import _ENGINES, _documents
        selected = {str(identifier) for kind, identifier in targets if kind == "node"}
        for handle in _ENGINES.get(coordinator.admission.engine._erasure_store_id, ()):
            if handle._closed:
                continue
            async with handle._vector_index._write_lock:
                actual = {int(key) for key in np.asarray(handle._vector_index._index.keys)}
                if actual != valid_keys:
                    raise PrivacyDenied("privacy_invalid_result")
            async with handle._lexical_index._write_lock:
                from prme.storage._threading import run_to_completion
                documents = await run_to_completion(_documents, handle._lexical_index, set())
                if selected.intersection(documents):
                    raise PrivacyDenied("privacy_invalid_result")
