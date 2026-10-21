"""Closed logical exports, with existing admission/journal and erasure guards.

This is not a SQL dump. Only typed admitted memory, opaque source identities,
validated executable journals and encrypted evidence can enter this format.
Recovery secrets and the independent erasure authority are never serialized.
"""
from __future__ import annotations

from datetime import datetime
import importlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from prme.models.derivation import PreparedEmbedding, node_checksum
from prme.privacy.crypto import decode_envelope
from prme.privacy.erasure import ErasedOperation
from prme.privacy.journals import JournalDependency, JournalManifest, project_manifest_node
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import (
    ExportEvent, ExportOperation, ExportWork, ProtectedExport,
    ProtectedExportManifest, ProtectedStoreFormat, PrivacyPortabilityError,
)
from prme.privacy.records import PrivacyRecord
from prme.storage.privacy_format import preflight_format

MAX_EXPORT_BYTES = 64 * 1024 * 1024
MAX_EXPORT_ROWS = 10000
_OMITTED = ("private_keys", "independent_erasure_authority", "external_backups",
            "operator_rotation_jobs", "native_index_snapshots")


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _manifest(records, now):
    return JournalManifest(journal_id=UUID(int=0), created_at=now,
        dependencies=tuple(JournalDependency.from_record(record) for record in records))


def _same_manifest(manifest, records):
    for dependency in manifest.dependencies:
        original = records.get(dependency.event_id)
        if original is None or dependency != JournalDependency.from_record(original):
            raise PrivacyPortabilityError("privacy_export_invalid")


def _node(node, manifest):
    if node_checksum(project_manifest_node(node, manifest)) != node_checksum(node):
        raise PrivacyPortabilityError("privacy_legacy_unclassified")


def _original_model(raw, model):
    # Some historical nested node models ignore unknown extension members.
    # A validated projection must not leave ignored bytes in an exported string.
    if _json(raw) != model.model_dump(mode="json"):
        raise PrivacyPortabilityError("privacy_legacy_unclassified")


def _alias_record_bytes(raw, record):
    """Check ignored/duplicate bytes without rewriting the existing snapshot."""
    from prme.privacy.crypto import _pairs
    from prme.storage import _snapshot_json
    json.loads(raw, object_pairs_hook=_pairs)
    if _snapshot_json.loads(raw) != _snapshot_json.loads(_snapshot_json.dumps(record.model_dump(mode="python"))):
        raise PrivacyPortabilityError("privacy_legacy_unclassified")


def _operation(operation, records, manifest, plans, aliases, alias_edges):
    """Consume existing codecs; keep their original serialized record bytes."""
    from prme.models.direct_store import DirectStoreRecord
    from prme.models.derivation import DerivationReceipt
    from prme.storage.derivation_registry import decode_plan
    from prme.privacy.journals import SnapshotEnvelope, validate_generated_publication
    body = _json(operation.payload)
    if body.get("kind") == "erased_operation":
        marker = ErasedOperation.model_validate(body)
        if marker.operation_id != operation.id or operation.op_type != "PRIVACY_ERASED":
            raise ValueError()
        return
    kind = operation.op_type
    if kind == "DIRECT_STORE_REQUESTED":
        if set(body) != {"record", "sha256"}:
            raise ValueError()
        record = DirectStoreRecord.from_operation_payload(body)
        _original_model(body["record"], record)
        if record.schema_version != 2 or record.operation_id != str(operation.id) or record.event_id not in records:
            raise ValueError()
        _node(record.node, manifest)
    elif kind == "DERIVATION_PREPARED":
        if set(body) != {"plan", "checksum"}:
            raise ValueError()
        plan = decode_plan(body)
        _original_model(body["plan"], plan)
        if plan.event_id not in records or plan.prepared_operation_id != str(operation.id):
            raise ValueError()
        for node in (*plan.nodes, *plan.references):
            _node(node, manifest)
        from prme.ingestion.admitted_statements import project_admitted_edge
        for edge in (*plan.edges, *plan.replacements):
            if edge != project_admitted_edge(edge):
                raise ValueError()
        # The model validates embedding/lexical identities and exact content.
        plans[plan.id] = plan
    elif kind == "DERIVATION_COMMITTED":
        receipt = DerivationReceipt.model_validate(body)
        plan = plans.get(receipt.plan_id)
        if plan is None or receipt.plan_checksum != plan.checksum or receipt.event_id != plan.event_id:
            raise ValueError()
    elif kind == "RETRIEVAL_REQUEST":
        from prme.models.privacy_receipts import ProtectedRetrievalReceipt
        import hashlib
        if (set(body) != {"kind", "schema_version", "request_id", "receipt", "receipt_checksum"}
                or body["kind"] != "protected_retrieval_operation" or body["schema_version"] != 1):
            raise ValueError()
        receipt = ProtectedRetrievalReceipt.model_validate_json(body["receipt"])
        _original_model(body["receipt"], receipt)
        if (body["receipt_checksum"] != hashlib.sha256(body["receipt"].encode()).hexdigest()
                or str(receipt.request_id) != body["request_id"] or operation.target_id != body["request_id"]
                or str(receipt.user_id) != operation.actor_id):
            raise ValueError()
        _same_manifest(receipt.manifest, records)
    elif kind == "ANSWER_CITATIONS_RECORDED":
        from prme.models.privacy_receipts import ProtectedCitationRecord
        from prme.storage.citations import CitationRepository, citation_operation_id
        if set(body) != {"record", "checksum"}:
            raise ValueError()
        receipt = CitationRepository._read_citation(body, user_id=operation.actor_id, citation_id=UUID(operation.target_id))
        if not isinstance(receipt, ProtectedCitationRecord) or citation_operation_id(operation.actor_id, receipt.citation_id) != str(operation.id):
            raise ValueError()
        _original_model(body["record"], receipt)
    elif kind in {"PROFILE_PREPARED", "PROFILE_PUBLISHED", "CONSOLIDATION_PREPARED", "CONSOLIDATION_PUBLISHED"}:
        if set(body) != {"publication", "checksum"}:
            raise ValueError()
        if kind.startswith("PROFILE_"):
            from prme.storage.profile_registry import decode
            plan = decode((str(operation.id), kind, operation.target_id, body, operation.actor_id, operation.namespace_id))
            family = "profile"
        else:
            from prme.storage.consolidation_publication import _unpack, _validate_operation
            plan = _unpack(body)
            _validate_operation(plan, (kind, operation.target_id, body, operation.actor_id, operation.namespace_id), kind)
            family = "summary"
        if plan.privacy_manifest is None:
            raise ValueError()
        _original_model(body["publication"], plan)
        _same_manifest(plan.privacy_manifest, records)
        validate_generated_publication(plan, kind=family)
    elif kind == "PRIVACY_PROCESSING_TERMINAL":
        from prme.privacy.crypto import _pairs
        body = json.loads(operation.payload, object_pairs_hook=_pairs)
        record = records.get(UUID(body["event_id"]))
        state = body["state"]
        created = datetime.fromisoformat(body["created_at"])
        if (record is None or record.kind != "extraction" or state not in {"cancelled", "expired"}
                or record.processing_state != state or record.processing is not None
                or type(body["schema_version"]) is not int or type(body["policy_revision"]) is not int
                or operation.id != uuid5(record.event_id, "prme:processing-terminal:v1:" + state)
                or operation.target_id != str(record.event_id) or operation.actor_id != "privacy"
                or operation.namespace_id != record.snapshot.policy.scope.value or operation.created_at != created
                or body != {"schema_version": 1, "kind": "processing_terminal", "event_id": str(record.event_id),
                    "source_id": str(record.source_id), "representation_id": str(record.representation_id),
                    "policy_id": str(record.snapshot.policy.policy_id), "policy_revision": record.snapshot.policy.revision,
                    "state": state, "replay": "unavailable", "created_at": body["created_at"]}):
            raise ValueError()
    elif kind == "ALIAS_PROPOSED":
        from prme.privacy.crypto import _pairs
        from prme.storage.alias_proposal import ProtectedAliasProposalRecord, read_record
        body = json.loads(operation.payload, object_pairs_hook=_pairs)
        if set(body) != {"record", "sha256"}:
            raise ValueError()
        record = read_record(body)
        if not isinstance(record, ProtectedAliasProposalRecord):
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        _alias_record_bytes(body["record"], record)
        _same_manifest(record.manifest, records)
        if (record.operation_id != operation.id or operation.target_id != str(record.right_before.id)
                or operation.actor_id != record.left_before.user_id
                or operation.namespace_id != record.left_before.scope.value
                or operation.created_at != record.edge.created_at):
            raise ValueError()
        aliases[record.operation_id] = record
        alias_edges[record.edge.id] = record.edge
    elif kind in {"ALIAS_PROPOSAL_ACCEPTED", "ALIAS_PROPOSAL_REJECTED"}:
        from prme.privacy.crypto import _pairs
        from prme.storage.alias_review import ProtectedAliasReviewRecord, read_review_record
        body = json.loads(operation.payload, object_pairs_hook=_pairs)
        if set(body) != {"record", "sha256"}:
            raise ValueError()
        record = read_review_record(body)
        if not isinstance(record, ProtectedAliasReviewRecord):
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        _alias_record_bytes(body["record"], record)
        _same_manifest(record.manifest, records)
        proposal = aliases.get(record.proposal_operation_id)
        if (proposal is None or record.operation_id != operation.id
                or operation.target_id != str(record.proposal_operation_id) or operation.actor_id != record.user_id
                or operation.namespace_id != record.scope.value or operation.created_at != record.decided_at
                or kind != ("ALIAS_PROPOSAL_ACCEPTED" if record.decision == "accepted" else "ALIAS_PROPOSAL_REJECTED")
                or record.manifest != proposal.manifest.model_copy(update={"journal_id": record.operation_id})
                or (record.source_id, record.target_id, record.score, record.user_id, record.scope) != (
                    proposal.edge.source_id, proposal.edge.target_id, proposal.score,
                    proposal.left_before.user_id, proposal.left_before.scope)):
            raise ValueError()
        if record.verified_edge is not None:
            alias_edges[record.verified_edge.id] = record.verified_edge
    elif body.get("kind") == "protected_snapshot":
        envelope = SnapshotEnvelope.model_validate(body)
        _same_manifest(envelope.manifest, records)
        # Existing family readers verify original bytes/checksums/projections.
        if envelope.family == "organizer_merge":
            from prme.storage.organizer_merge import MergeRecord, _replayed
            from prme.storage import _snapshot_json
            record = MergeRecord.model_validate(_snapshot_json.loads(envelope.record))
            _replayed(body, record.operation_id,
                sorted((str(record.canonical_before.id), str(record.retired_before.id))), record.user_id, record.kind)
        else:
            module = importlib.import_module("prme.storage." + envelope.family)
            record = module.read_record(body)
        from prme.storage import _snapshot_json
        if _snapshot_json.loads(envelope.record) != _snapshot_json.loads(_snapshot_json.dumps(record.model_dump(mode="python"))):
            raise ValueError()
        if str(record.operation_id) != str(operation.id):
            raise ValueError()
    else:
        # There is no arbitrary operation-payload passthrough or source fallback.
        raise PrivacyPortabilityError("privacy_legacy_unclassified")


def validate_export(bundle: ProtectedExport) -> ProtectedExport:
    """Validate complete dependencies without private lookup or decrypt."""
    try:
        bundle = ProtectedExport.model_validate_json(bundle.model_dump_json())
        info = bundle.manifest
        if info.format.state != "complete" or info.ledger_checkpoint.store_id != info.format.store_id:
            raise ValueError()
        records = {record.event_id: record for record in bundle.admissions}
        if len(records) != len(bundle.admissions) or len({n.id for n in bundle.nodes}) != len(bundle.nodes):
            raise ValueError()
        if len({row.id for row in bundle.unavailable_history}) != len(bundle.unavailable_history):
            raise ValueError()
        manifest = _manifest(bundle.admissions, info.created_at)
        for record in bundle.admissions:
            if record.snapshot.policy.project_id != info.format.project_id:
                raise ValueError()
            for purpose in ("evidence", "processing"):
                payload = getattr(record, purpose)
                if payload is not None:
                    context = payload.context
                    if (context.event_id, context.source_id, context.representation_id, context.purpose) != (
                            record.event_id, record.source_id, record.representation_id, purpose):
                        raise ValueError()
                    decode_envelope(payload, record.snapshot)
        from prme.privacy.journals import JOURNAL_KEY, GeneratedNodeReference
        by_node = {node.id: node for node in bundle.nodes}
        dependencies = {}
        for node in bundle.nodes:
            _node(node, manifest)
            reference = (node.metadata or {}).get(JOURNAL_KEY)
            sources = set(GeneratedNodeReference.model_validate(reference).source_node_ids) if reference else set()
            if not sources <= by_node.keys() or node.id in sources:
                raise ValueError()
            if not set(node.evidence_refs) <= records.keys() | sources:
                raise ValueError()
            for identifier in sources | set(node.evidence_refs):
                boundary = ((str(records[identifier].snapshot.policy.owner_id), records[identifier].snapshot.policy.scope)
                    if identifier in records else (by_node[identifier].user_id, by_node[identifier].scope))
                if boundary != (node.user_id, node.scope):
                    raise ValueError()
            dependencies[node.id] = sources
        # A complete generated-memory graph must terminate in admitted sources,
        # including previous publications, without a circular identity claim.
        pending = dict(dependencies)
        while pending:
            ready = {identifier for identifier, sources in pending.items() if not sources.intersection(pending)}
            if not ready:
                raise ValueError()
            for identifier in ready:
                del pending[identifier]
        alias_edges = {}
        if info.mode == "protected":
            if len({op.id for op in bundle.operations}) != len(bundle.operations):
                raise ValueError()
            plans, aliases = {}, {}
            ordered = sorted(bundle.operations, key=lambda op: op.op_type not in {"DERIVATION_PREPARED", "ALIAS_PROPOSED"})
            for operation in ordered:
                _operation(operation, records, manifest, plans, aliases, alias_edges)
        node_ids = {node.id for node in bundle.nodes}
        from prme.ingestion.admitted_statements import project_admitted_edge
        for edge in bundle.edges:
            if edge.source_id not in node_ids or edge.target_id not in node_ids:
                raise ValueError()
            alias = alias_edges.get(edge.id)
            proven_alias = alias is not None and edge.model_dump(mode="json") == alias.model_dump(mode="json")
            if edge.provenance_event_id not in records and not (proven_alias or (
                    edge.provenance_event_id is None and edge.target_id in dependencies[edge.source_id]
                    and edge.edge_type.value == "derived_from")):
                raise ValueError()
            if edge.model_dump(mode="json") != project_admitted_edge(edge).model_dump(mode="json"):
                raise ValueError()
        if info.mode == "active_memory_only":
            if bundle.events or bundle.operations or bundle.work or bundle.embeddings:
                raise ValueError()
            if any(r.evidence is not None or r.processing is not None or r.retry is not None for r in records.values()):
                raise ValueError()
        else:
            if {event.id for event in bundle.events} != records.keys() or len(bundle.events) != len(records):
                raise ValueError()
            for event in bundle.events:
                record = records[event.id]
                if (event.user_id, event.scope) != (record.snapshot.policy.owner_id, record.snapshot.policy.scope):
                    raise ValueError()
            if len({e.node_id for e in bundle.embeddings}) != len(bundle.embeddings):
                raise ValueError()
            by_node = {node.id: node for node in bundle.nodes}
            for embedding in bundle.embeddings:
                if embedding.node_id not in by_node or embedding.content != by_node[embedding.node_id].content:
                    raise ValueError()
            for work in bundle.work:
                if work.event_id not in records or (work.kind == "extraction" and work.work_order is None):
                    raise ValueError()
        return bundle
    except (PrivacyDenied, PrivacyPortabilityError):
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PrivacyPortabilityError("privacy_export_invalid") from None


async def _capture(engine, mode, *, _migration_id=None):
    from prme.storage.pg.graph_store import PgGraphStore, _NODE_COLUMNS, _EDGE_COLUMNS
    from prme.storage.extraction_work import ExtractionWorkRepository
    source = engine._privacy.repository
    repository = ExtractionWorkRepository(conn=source.conn, pool=source.pool, conn_lock=source.lock)
    async with repository.session(transaction=True) as session:
        async def rows(sql):
            result = await session.fetch(sql + f" LIMIT {MAX_EXPORT_ROWS + 1}")
            if len(result) > MAX_EXPORT_ROWS:
                raise PrivacyPortabilityError("privacy_source_capacity")
            return result
        descriptor = ProtectedStoreFormat.model_validate_json((await rows("SELECT body FROM privacy_format"))[0]["body"])
        if _migration_id is not None:
            if descriptor.migration_id != _migration_id or descriptor.state != "staging":
                raise PrivacyPortabilityError("privacy_migration_incomplete")
            descriptor = descriptor.model_copy(update={"state": "complete"})
        checkpoint = (await engine._privacy.erasure.repository.state(session))[1]
        records = tuple(PrivacyRecord.model_validate_json(row["body"]) for row in await rows("SELECT body FROM privacy_admissions ORDER BY event_id"))
        nodes = tuple(PgGraphStore._record_to_node(row) for row in await rows(f"SELECT {_NODE_COLUMNS} FROM nodes ORDER BY id"))
        edges = tuple(PgGraphStore._record_to_edge(row) for row in await rows(f"SELECT {_EDGE_COLUMNS} FROM edges ORDER BY id"))
        events, operations, work, embeddings = (), (), [], []
        alias_relationships_omitted = False
        if mode == "protected":
            events = tuple(ExportEvent.model_validate(row) for row in await rows(
                "SELECT id,timestamp,role,user_id,session_id,scope,created_at,event_time FROM events ORDER BY id"))
            operations = tuple(ExportOperation.model_validate({**row, "payload": row["payload"] if isinstance(row["payload"], str) else json.dumps(row["payload"])})
                for row in await rows("SELECT id,op_type,target_id,payload,actor_id,namespace_id,created_at FROM operations ORDER BY id"))
            for kind, table in (("materialization", "event_materializations"), ("extraction", "event_extractions")):
                for row in await rows(f"SELECT * FROM {table} ORDER BY event_id"):
                    row["kind"] = kind
                    if row.get("last_error"):
                        row["last_error"] = "privacy_processing_unavailable"
                    work.append(ExportWork.model_validate(row))
            if session.postgres:
                vector_rows = await rows("SELECT id AS node_id,embedding::text AS vector_data,embedding_model,embedding_version FROM nodes WHERE embedding IS NOT NULL ORDER BY id")
            else:
                vector_rows = await rows("SELECT vm.node_id,vp.vector_data,vm.embedding_model,vm.embedding_version FROM vector_metadata vm JOIN vector_payloads vp USING(vector_key) JOIN nodes n ON CAST(n.id AS VARCHAR)=vm.node_id ORDER BY vm.node_id")
            contents = {str(node.id): node.content for node in nodes}
            for row in vector_rows:
                if session.postgres:
                    values = tuple(json.loads(row["vector_data"]))
                else:
                    import numpy as np
                    values = tuple(map(float, np.frombuffer(row["vector_data"], dtype="<f4")))
                embeddings.append(PreparedEmbedding(node_id=row["node_id"], content=contents[str(row["node_id"])],
                    values=values, model=row["embedding_model"], version=row["embedding_version"], dimension=len(values)))
        else:
            active = {"tentative", "stable", "contested"}
            nodes = tuple(node for node in nodes if node.lifecycle_state.value in active)
            ids = {node.id for node in nodes}
            edges = tuple(edge for edge in edges if edge.source_id in ids and edge.target_id in ids)
            aliases = {edge.id for edge in edges if edge.provenance_event_id is None and (edge.metadata or {}).get("relation") == "alias"}
            alias_relationships_omitted = bool(aliases)
            edges = tuple(edge for edge in edges if edge.id not in aliases)
            records = tuple(record.model_copy(update={"evidence": None, "processing": None, "retry": None}) for record in records)
        unavailable_history = ()
        names = await rows("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='privacy_migration_history'")
        if names:
            from prme.models.privacy_reads import ProtectedOperationView
            unavailable_history = tuple(ProtectedOperationView.model_validate_json(row["body"]) for row in await rows("SELECT body FROM privacy_migration_history ORDER BY id"))
        omitted = _OMITTED + (() if mode == "protected" else ("evidence", "processing_input", "executable_history", "vectors", "restoration"))
        if alias_relationships_omitted:
            omitted += ("alias_relationships",)
        if unavailable_history:
            omitted += ("legacy_originals_retained_separately", "legacy_history_checksum_and_replay",)
        return validate_export(ProtectedExport(manifest=ProtectedExportManifest(mode=mode, format=descriptor,
            created_at=engine._privacy.require_runtime().clock(), ledger_checkpoint=checkpoint, omitted=omitted),
            admissions=records, events=events, nodes=tuple(node.model_dump() for node in nodes),
            edges=tuple(edge.model_dump() for edge in edges), embeddings=tuple(embeddings),
            operations=operations, work=tuple(work), unavailable_history=unavailable_history))


def _publish_bytes(path: Path, data: bytes):
    """Exclusive, same-directory publication. Never overwrite or rename-fallback."""
    if len(data) > MAX_EXPORT_BYTES:
        raise PrivacyPortabilityError("privacy_source_capacity")
    if path.is_symlink() or path.exists():
        raise PrivacyPortabilityError("privacy_destination_occupied")
    temporary = path.with_name("." + uuid4().hex + ".export")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise PrivacyPortabilityError("privacy_destination_occupied") from None
    finally:
        temporary.unlink(missing_ok=True)


async def export_protected_pack(engine, destination, *, mode="protected") -> ProtectedExportManifest:
    """Export all admitted boundaries under their current actual-effect leases.

    The runtime supplies the independent ledger, policy authority and clock.
    Native key lookup/unwrapping is unnecessary; recovery keys stay separate.
    """
    if mode not in {"protected", "active_memory_only"}:
        raise PrivacyPortabilityError("privacy_export_invalid")
    coordinator = engine._privacy
    runtime = coordinator.require_runtime()
    source = coordinator.repository
    await preflight_format(conn=source.conn, pool=source.pool, runtime=runtime,
                           namespace_id=engine._config.namespace_id, conn_lock=source.lock)
    async with coordinator.erasure.guard():
        bundle = await _capture(engine, mode)
        records = tuple(await coordinator.repository._read("1=1", ()))
        live = await coordinator.erasure.current_records(records)
        live_ids = {record.event_id for record in live}
        if any(record.event_id not in live_ids and (record.evidence or record.processing or record.retry)
               for record in records):
            raise PrivacyDenied("privacy_erased")
        await coordinator.check_disclosure(live)
        async with coordinator.publication_guards(live, node_ids=tuple(node.id for node in bundle.nodes)):
            # This is the actual external effect, not an earlier checkpoint.
            _publish_bytes(Path(destination), bundle.model_dump_json().encode())
    return bundle.manifest


def _read_export(raw) -> ProtectedExport:
    try:
        # Duplicate object members are ambiguous and never accepted.
        from prme.privacy.crypto import _pairs
        value = json.loads(raw, object_pairs_hook=_pairs)
        return validate_export(ProtectedExport.model_validate(value))
    except (PrivacyDenied, PrivacyPortabilityError):
        raise
    except (ValueError, TypeError):
        raise PrivacyPortabilityError("privacy_export_invalid") from None


def read_protected_export(path) -> ProtectedExport:
    from prme.storage.privacy_source import _read_bounded
    return _read_export(_read_bounded(Path(path), MAX_EXPORT_BYTES))


async def _load_export(repository, bundle, *, _cursor=None):
    """Insert only the closed snapshot columns, before any recovery consumer."""
    from enum import Enum
    from prme.storage.pg.graph_store import _NODE_COLUMNS, _EDGE_COLUMNS
    from prme.storage.privacy import _values
    records = {record.event_id: record for record in bundle.admissions}
    async with repository.session(transaction=True) as session:
        async def insert(table, columns, values):
            values = [value.value if isinstance(value, Enum) else str(value) if isinstance(value, UUID) else value for value in values]
            placeholders = ",".join(f"${i+1}" for i in range(len(values)))
            await session.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", *values)
        for event in bundle.events:
            record = records[event.id]
            data = event.model_dump()
            data.update(content="", metadata=json.dumps({"_prme_privacy_v1": record.reference()}))
            await insert("events", tuple(data), tuple(data.values()))
        for record in bundle.admissions:
            await insert("privacy_admissions", ("event_id", "user_id", "scope", "request_id", "request_index", "body"), _values(record))
        for table, columns, objects in (("nodes", _NODE_COLUMNS, bundle.nodes), ("edges", _EDGE_COLUMNS, bundle.edges)):
            names = tuple(name.strip() for name in columns.split(","))
            for obj in objects:
                data = obj.model_dump(mode="python")
                for field in ("metadata", "evidence_refs"):
                    if field in data:
                        data[field] = json.dumps(data[field], default=str) if data[field] is not None else None
                await insert(table, names, tuple(data[name] for name in names))
        for operation in bundle.operations:
            data = operation.model_dump()
            await insert("operations", tuple(data), tuple(data.values()))
        for work in bundle.work:
            data = work.model_dump()
            kind = data.pop("kind")
            if kind == "materialization":
                data = {key: data[key] for key in ("event_id", "status", "attempts", "last_error", "updated_at")}
                table = "event_materializations"
            else:
                table = "event_extractions"
                # An imported lease can never be a currently owned work claim.
                data["generation"] += 1
                data["lease_expires_at"] = None
                if data["status"] == "running":
                    data["status"] = "pending"
            await insert(table, tuple(data), tuple(data.values()))
        if not session.postgres:
            maximum = max((work.work_order or 0 for work in bundle.work), default=0)
            await session.execute("UPDATE extraction_work_sequence SET next_ordinal=$1", maximum + 1)
        else:
            await session.execute("SELECT pg_catalog.setval(pg_catalog.pg_get_serial_sequence('event_extractions','work_order'),"
                                  "GREATEST(COALESCE((SELECT MAX(work_order) FROM event_extractions),0)+1,1),false)")
        nodes = {node.id: node for node in bundle.nodes}
        first = 1
        if not session.postgres:
            first += (await session.fetch("SELECT COALESCE(MAX(vector_key),0) AS n FROM vector_metadata"))[0]["n"]
        for number, embedding in enumerate(bundle.embeddings, first):
            if session.postgres:
                from prme.storage.pg.vector_sql import resolve_vector_sql
                vector_sql = await resolve_vector_sql(session.conn)
                await session.execute(f"UPDATE nodes SET embedding=$1::{vector_sql.type},embedding_model=$2,embedding_version=$3 WHERE id=$4",
                    json.dumps(embedding.values), embedding.model, embedding.version, str(embedding.node_id))
            else:
                import numpy as np
                await insert("vector_metadata", ("vector_key", "node_id", "user_id", "embedding_model", "embedding_version", "embedding_dim"),
                    (number, str(embedding.node_id), nodes[embedding.node_id].user_id, embedding.model, embedding.version, embedding.dimension))
                await insert("vector_payloads", ("vector_key", "vector_data"),
                    (number, np.asarray(embedding.values, dtype="<f4").tobytes()))
        if bundle.unavailable_history:
            await session.execute("CREATE TABLE IF NOT EXISTS privacy_migration_history(id VARCHAR PRIMARY KEY,body VARCHAR NOT NULL)")
            for row in bundle.unavailable_history:
                await insert("privacy_migration_history", ("id", "body"), (row.id, row.model_dump_json()))
        if _cursor is not None:
            await session.execute("UPDATE privacy_migration_job SET completed=$1 WHERE singleton=1", _cursor)


async def _restore_database(conn, pool, config, runtime, bundle):
    """One quarantined empty destination, with no serving or native consumers."""
    from prme.storage.erasure import DDL, ErasureRepository, prepare_store
    from prme.storage.privacy_format import install_duck_format, install_pg_format
    repository = ErasureRepository(conn=conn, pool=pool)
    async with repository.session(transaction=True) as session:
        # Never merge a snapshot into an occupied database/schema.
        tables = await session.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'")
        if tables:
            raise PrivacyPortabilityError("privacy_destination_occupied")
        for sql in DDL:
            await session.execute(sql)
        await session.execute("INSERT INTO privacy_store_state VALUES(1,$1,$2)", str(runtime.project_id), bundle.manifest.ledger_checkpoint.model_dump_json())
        await session.execute("INSERT INTO privacy_erasure_epoch VALUES(1,0)")
    # These initializers see zero content. Their reconstructed registries remain
    # empty until the independent ledger has purged the loaded snapshot below.
    if pool is None:
        from prme.storage.schema import initialize_database
        from prme.storage.vector_index import VectorIndex
        from types import SimpleNamespace
        initialize_database(conn, protected=True)
        VectorIndex._init_metadata_table(SimpleNamespace(_conn=conn, _index_path=None))
        install_duck_format(conn)
    else:
        from prme.storage.pg.schema import initialize_pg_database
        await initialize_pg_database(pool, embedding_dim=config.embedding.dimension, protected=True)
        async with pool.acquire() as current:
            await install_pg_format(current)
    async with repository.session(transaction=True) as session:
        staged = bundle.manifest.format.model_copy(update={"state": "staging"})
        await session.execute("UPDATE privacy_format SET body=$1", staged.model_dump_json())
    await _load_export(repository, bundle)
    await preflight_format(conn=conn, pool=pool, runtime=runtime, namespace_id=config.namespace_id, allow_staging=True)
    # Mandatory current independent authority, expiry, closure and purge before
    # loading native indexes or reconstructing any imported executable journal.
    await prepare_store(conn=conn, pool=pool, runtime=runtime, namespace_id=config.namespace_id)
    async with repository.session(transaction=True) as session:
        await session.execute("UPDATE privacy_format SET body=$1", bundle.manifest.format.model_dump_json())
    return (await repository.state())[1]


async def restore_protected_export(source, config, *, privacy_runtime, embedding_provider):
    """Restore this closed format into an empty, explicitly selected destination.

    Local publication is exclusive and retains the source artifact. PostgreSQL
    accepts an explicitly selected empty schema/database only; it does not
    import SQL, replace an occupied namespace, or restore authentication data.
    """
    import asyncio
    import shutil
    from types import SimpleNamespace
    from prme import MemoryEngine
    from prme.privacy.admission import AdmissionCoordinator, preflight_runtime
    from prme.storage.event_store import EventStore
    from prme.storage.privacy_source import _read_bounded
    from prme.storage.pack_tools import _attach, _duckdb_key, _secret
    from prme.privacy.keys import managed_key_usage
    await preflight_runtime(config, privacy_runtime)
    raw = _read_bounded(Path(source), MAX_EXPORT_BYTES)
    bundle = _read_export(raw)
    if bundle.manifest.mode != "protected":
        raise PrivacyPortabilityError("privacy_export_invalid")
    runtime = privacy_runtime
    if runtime is None or runtime.erasure_ledger is None:
        raise PrivacyDenied("privacy_ledger_unavailable")
    if (runtime.project_id != bundle.manifest.format.project_id or
            config.namespace_id is not None and config.namespace_id != bundle.manifest.format.store_id):
        raise PrivacyDenied("privacy_boundary_mismatch")
    for embedding in bundle.embeddings:
        if (embedding.model, embedding.version, embedding.dimension) != (
                embedding_provider.model_name, embedding_provider.model_version, embedding_provider.dimension):
            raise PrivacyPortabilityError("privacy_export_invalid")
    async with runtime.erasure_ledger.guard():
        await runtime.erasure_ledger.entries_since(bundle.manifest.ledger_checkpoint)
    # Prove the artifact remains exactly the input throughout the operation.
    import hashlib
    original = hashlib.sha256(raw).digest()
    del raw
    if config.backend == "postgres":
        return await _restore_postgres(source, config, runtime, bundle, original)
    if config.encryption_enabled and config.pack_path is None:
        raise PrivacyPortabilityError("privacy_format_unsupported")
    pool, conn, stage = None, None, None
    target = None
    try:
        import duckdb
        single = config.pack_path is not None
        target = Path(config.pack_path if single else config.db_path).absolute()
        if not single:
            target = target.parent
            if Path(config.vector_path).parent.absolute() != target or Path(config.lexical_path).parent.absolute() != target:
                raise PrivacyPortabilityError("privacy_format_unsupported")
        if target.exists() or target.is_symlink():
            raise PrivacyPortabilityError("privacy_destination_occupied")
        if not target.parent.is_dir():
            raise PrivacyPortabilityError("privacy_source_unavailable")
        stage = target.with_name("." + uuid4().hex + ".restore")
        stage.mkdir(mode=0o700)
        database = stage / ("memory.prme" if single else "memory.duckdb")
        working = config.model_copy(update={"pack_path": str(database) if single else None,
            "db_path": str(database), "vector_path": str(stage / "vectors.usearch"), "lexical_path": str(stage / "lexical_index")})
        conn = duckdb.connect(":memory:", config={"temp_directory": "", "threads": 1})
        key = _duckdb_key(_secret(config.encryption_key)) if single and config.encryption_enabled else None
        _attach(conn, str(database), "restored", key=key, read_only=False)
        conn.execute("USE restored")
        conn.execute("SET TimeZone='UTC'")
        await _restore_database(conn, pool, working, runtime, bundle)
        if conn is not None and config.namespace_id is not None:
            from prme.storage.namespace_identity import bind_namespace
            bind_namespace(conn, config.namespace_id, fresh=True)
        if conn is not None:
            conn.close()
            conn = None
        if pool is not None:
            await pool.close()
            pool = None
        engine = await MemoryEngine.create(working, privacy_runtime=runtime, embedding_provider=embedding_provider)
        try:
            if config.backend != "postgres" and config.pack_path is None:
                from prme.storage.engine import _load_lexical_from_graph
                _load_lexical_from_graph(engine._conn, engine._lexical_index)
            verified_head = (await engine._privacy.erasure.repository.state())[1]
        finally:
            await engine.close()
        # Reopen SQL only for the final effect guard; no native handles survive
        # publication and no recovered work can run in this adapter.
        import duckdb
        conn = duckdb.connect(":memory:", config={"temp_directory": "", "threads": 1})
        _attach(conn, str(database), "restored", key=key, read_only=False)
        conn.execute("USE restored")
        events = EventStore(conn, asyncio.Lock(), protected=True)
        coordinator = AdmissionCoordinator(SimpleNamespace(_config=working, _event_store=events), runtime)
        async with coordinator.erasure.guard():
            if (await coordinator.erasure.repository.state())[1] != verified_head:
                raise PrivacyDenied("privacy_ledger_stale")
            records = await coordinator.erasure.current_records(await coordinator.repository._read("1=1", ()))
            nodes = await coordinator.repository._query("SELECT id FROM nodes", ())
            async with coordinator.publication_guards(records, node_ids=tuple(row[0] for row in nodes)):
                with managed_key_usage(verified_head.store_id):
                    async with coordinator.writer_guard(records):
                        if hashlib.sha256(_read_bounded(Path(source), MAX_EXPORT_BYTES)).digest() != original:
                            raise PrivacyPortabilityError("privacy_source_changed")
                        if target is not None:
                            if target.exists() or target.is_symlink():
                                raise PrivacyPortabilityError("privacy_destination_occupied")
                            if config.pack_path is not None:
                                os.link(database, target)
                            else:
                                # Reserve the pathname exclusively before the
                                # atomic rename; never replace another store.
                                target.mkdir(mode=0o700)
                                os.replace(stage, target)
                                stage = None
        return {"schema_version": 1, "state": "complete", "source_retained": True,
                "store_id": str(verified_head.store_id), "ledger_sequence": verified_head.sequence,
                "physical_erasure": False}
    finally:
        if conn is not None:
            conn.close()
        if pool is not None:
            await pool.close()
        if stage is not None:
            shutil.rmtree(stage)


async def _restore_postgres(source, config, runtime, bundle, original):
    """Empty explicit PG target; the final transaction commit publishes it."""
    import hashlib
    import asyncpg
    from types import SimpleNamespace
    from prme.privacy.admission import AdmissionCoordinator
    from prme.privacy.keys import managed_key_usage
    from prme.storage.pg.namespace import _ConnectionPool
    from prme.storage.pg.event_store import PgEventStore
    from prme.storage.pg.schema import initialize_pg_database
    from prme.storage.privacy_source import _read_bounded
    if config.namespace_id is not None:
        raise PrivacyPortabilityError("privacy_format_unsupported")
    conn = await asyncpg.connect(config.database_url.get_secret_value())
    transaction = conn.transaction()
    await transaction.start()
    committed = False
    try:
        pool = _ConnectionPool(conn)
        await _restore_database(None, pool, config, runtime, bundle)
        # All imported recovery runs only after current ledger and expiry.
        await initialize_pg_database(pool, embedding_dim=config.embedding.dimension, protected=True)
        coordinator = AdmissionCoordinator(SimpleNamespace(_config=config,
            _event_store=PgEventStore(pool, protected=True)), runtime)
        async with coordinator.erasure.guard():
            head = (await coordinator.erasure.repository.state())[1]
            records = await coordinator.erasure.current_records(await coordinator.repository._read("1=1", ()))
            nodes = await conn.fetch("SELECT id FROM nodes")
            async with coordinator.publication_guards(records, node_ids=tuple(row[0] for row in nodes)):
                with managed_key_usage(head.store_id):
                    async with coordinator.writer_guard(records):
                        if hashlib.sha256(_read_bounded(Path(source), MAX_EXPORT_BYTES)).digest() != original:
                            raise PrivacyPortabilityError("privacy_source_changed")
                        await transaction.commit()
                        committed = True
        return {"schema_version": 1, "state": "complete", "source_retained": True,
                "store_id": str(head.store_id), "ledger_sequence": head.sequence, "physical_erasure": False}
    finally:
        if not committed:
            await transaction.rollback()
        await conn.close()
