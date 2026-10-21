"""Explicit, bounded migration of retained originals into protected copies.

Inspection never opens an engine, initializes a schema, or acquires private
evidence keys. Original artifacts remain outside the managed destination.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from prme.privacy.capabilities import RetryCommitment, RetryContext
from prme.privacy.policy import FrozenModel, PolicySnapshot, PrivacyDenied
from prme.privacy.portability import MigrationStatus

from prme.privacy.portability import CopyDisposition, PrivacyMigrationPlan, PrivacyPortabilityError
from prme.storage.privacy_source import open_privacy_source

MAX_ROWS = 10000
MAX_BYTES = 64 * 1024 * 1024

# C01-C45, including the separately managed native custody/rotation/recovery
# additions. A missing local table in PostgreSQL is an explicit layout fact,
# never evidence that an external copy was purged.
_COPY_TABLES = {
    "C01": ("events",), "C02": ("events", "privacy_admissions"), "C03": ("events", "operations"),
    "C04": ("nodes",), "C05": ("nodes",), "C06": ("edges",),
    "C07": ("operations",), "C08": ("operations",), "C09": ("operations",),
    "C10": ("event_materializations", "event_extractions", "profile_work"),
    "C11": ("vector_metadata", "vector_payloads"),
    "C12": ("vector_staging", "derivation_artifact_owners", "derivation_registered_plans", "profile_registered_plans"),
    "C13": ("vector_snapshot",), "C14": ("nodes",), "C15": ("operations",),
    "C16": ("operations",), "C17": ("operations",), "C18": ("operations",),
    "C19": ("operations",), "C20": ("operations",), "C21": ("operations",),
    "C22": ("operations", "privacy_admissions"), "C23": ("operations",),
    "C28": ("events",), "C37": ("event_materializations", "event_extractions", "profile_work", "privacy_admissions"),
    "C38": ("blobs",), "C39": ("prme_namespace_identity", "prme_manifest"),
    "C41": ("privacy_admissions",), "C42": ("privacy_store_state", "privacy_erasure_jobs", "privacy_erasure_intents"),
    "C44": ("privacy_rotation_jobs",),
}
_EXTERNAL = {"C24", "C25", "C26", "C27", "C29", "C30", "C31", "C32", "C33", "C34", "C35", "C36", "C40", "C43", "C45"}
_REBUILD = {"C11", "C12", "C13", "C14"}
_HISTORY_MESSAGE = "_prme_chat_message"
_HISTORY_CONTROL = "_prme_chat_history_control"
_HISTORY_KEYS = frozenset({_HISTORY_MESSAGE, _HISTORY_CONTROL})


def _history_metadata(metadata):
    """Recognize closed text-history envelopes, never deserialize framework objects.

    Optional framework packages and their arbitrary model extras are not trusted
    migration codecs. Unknown shapes stay unsupported even with the disposition.
    The returned text/role must also match the original immutable source event.
    """
    if metadata is None:
        return frozenset(), None, None
    if not isinstance(metadata, dict):
        raise ValueError("Unclassified legacy history")
    keys = _HISTORY_KEYS.intersection(metadata)
    if not keys:
        return keys, None, None
    if len(keys) != 1:
        raise ValueError("Ambiguous legacy history")
    key = next(iter(keys))
    value = metadata[key]
    json.dumps(value, allow_nan=False)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unclassified legacy history version")
    if key == _HISTORY_CONTROL:
        operation = value.get("operation")
        expected = {"version", "operation"} | ({"target_event_id"} if operation == "delete" else set())
        if operation not in {"clear", "delete"} or set(value) != expected:
            raise ValueError("Unclassified legacy history control")
        if operation == "delete":
            target = value["target_event_id"]
            if not isinstance(target, str) or str(UUID(target)) != target:
                raise ValueError("Unclassified legacy history target")
        return keys, "", "system"
    if set(value) != {"version", "format", "message"} or not isinstance(value["message"], dict):
        raise ValueError("Unclassified legacy history envelope")
    message = value["message"]
    if value["format"] == "langchain-v1":
        if set(message) != {"type", "data"} or not isinstance(message["data"], dict):
            raise ValueError("Unclassified LangChain message")
        data, kind = message["data"], message["type"]
        roles = {"human": "user", "ai": "assistant", "system": "system"}
        common = {"content", "additional_kwargs", "response_metadata", "type", "name", "id"}
        extra = {"example"} | ({"tool_calls", "invalid_tool_calls", "usage_metadata"} if kind == "ai" else set())
        if (not isinstance(kind, str) or kind not in roles or not common <= data.keys()
                or not data.keys() <= common | extra or data["type"] != kind
                or not isinstance(data["content"], str)
                or not isinstance(data["additional_kwargs"], dict) or not isinstance(data["response_metadata"], dict)
                or any(data[name] is not None and not isinstance(data[name], str) for name in ("name", "id"))
                or ("example" in data and type(data["example"]) is not bool)
                or any(name in data and (not isinstance(data[name], list) or any(not isinstance(item, dict) for item in data[name]))
                       for name in ("tool_calls", "invalid_tool_calls"))
                or (data.get("usage_metadata") is not None and not isinstance(data["usage_metadata"], dict))):
            raise ValueError("Unclassified LangChain message data")
        return keys, data["content"], roles[kind]
    if value["format"] == "llamaindex-v1":
        roles = {"user": "user", "developer": "user", "system": "system", "assistant": "assistant",
                 "function": "tool", "tool": "tool", "chatbot": "assistant", "model": "assistant"}
        if (set(message) != {"role", "additional_kwargs", "blocks"}
                or not isinstance(message["role"], str) or message["role"] not in roles
                or not isinstance(message["additional_kwargs"], dict) or not isinstance(message["blocks"], list)
                or any(not isinstance(block, dict) or set(block) != {"block_type", "text"}
                       or block["block_type"] != "text" or not isinstance(block["text"], str) for block in message["blocks"])):
            raise ValueError("Unclassified LlamaIndex message data")
        return keys, "\n".join(block["text"] for block in message["blocks"]), roles[message["role"]]
    raise ValueError("Unclassified legacy history format")


def _project_legacy_history(events, by_event, nodes, kinds, *, unavailable_history):
    """Called only after original event/journal/node identity validation."""
    event_keys = {}
    for identifier, event in events.items():
        keys, text, role = _history_metadata(event.metadata)
        event_keys[identifier] = keys
        if not keys:
            continue
        if not unavailable_history or (event.content, event.role) != (text, role):
            raise ValueError("Legacy history requires an explicit matching disposition")
        if _HISTORY_CONTROL in keys:
            control = event.metadata[_HISTORY_CONTROL]
            if not event.session_id or by_event[identifier]:
                raise ValueError("Invalid legacy history control source")
            if control["operation"] == "delete":
                target = events.get(UUID(control["target_event_id"]))
                if (target is None or target.id == event.id or _HISTORY_CONTROL in (target.metadata or {})
                        or (target.user_id, target.scope, target.session_id) != (event.user_id, event.scope, event.session_id)
                        or target.timestamp > event.timestamp):
                    raise ValueError("Foreign legacy history control")
    primary = {node.id: identifier for identifier, values in by_event.items() for node in values}
    for node in nodes.values():
        keys, _, _ = _history_metadata(node.metadata)
        if not keys:
            continue
        identifier = primary.get(node.id)
        if (not unavailable_history or identifier is None or kinds[identifier] != "direct"
                or keys != event_keys[identifier] or _HISTORY_CONTROL in keys
                or any(node.metadata[key] != events[identifier].metadata[key] for key in keys)):
            raise ValueError("Unclassified legacy history node")
    def projected(value):
        metadata = value.metadata
        if not _HISTORY_KEYS.intersection(metadata or {}):
            return value
        return value.model_copy(update={"metadata": {key: item for key, item in metadata.items() if key not in _HISTORY_KEYS} or None})
    return ({identifier: projected(event) for identifier, event in events.items()},
            {identifier: [projected(node) for node in values] for identifier, values in by_event.items()})


def _password(config):
    value = config.encryption_key
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


@asynccontextmanager
async def _source(config):
    """A frozen SQL snapshot, never a serving or source-writing engine."""
    if config.backend == "postgres":
        import asyncpg
        conn = await asyncpg.connect(config.database_url.get_secret_value())
        try:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                async def query(sql):
                    return [dict(row) for row in await conn.fetch(sql)]
                yield query
        finally:
            await conn.close()
    else:
        path = config.pack_path or config.db_path
        if not Path(path).exists() and Path(str(path) + ".enc").exists():
            path = str(path) + ".enc"
        with open_privacy_source(path, encryption_key=_password(config)) as conn:
            async def query(sql):
                cursor = conn.execute(sql)
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            yield query


async def _inventory(query):
    names = await query("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY table_name")
    counts = {}
    for row in names:
        name = row["table_name"]
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise PrivacyPortabilityError("privacy_format_unsupported")
        counts[name] = int((await query(f'SELECT count(*) AS n FROM "{name}"'))[0]["n"])
    if not {"events", "nodes", "edges", "operations"} <= counts.keys():
        raise PrivacyPortabilityError("privacy_format_unsupported")
    return counts


async def inspect_privacy_migration(config, *, policy=None) -> PrivacyMigrationPlan:
    """Content-free dry-run; no writes, runtime capabilities or key creation."""
    layout = "postgres" if config.backend == "postgres" else "pack" if config.pack_path else "directory"
    async with _source(config) as query:
        counts = await _inventory(query)
        protected = "privacy_format" in counts
        if protected:
            from prme.privacy.portability import ProtectedStoreFormat
            try:
                ProtectedStoreFormat.model_validate_json((await query("SELECT body FROM privacy_format"))[0]["body"])
            except (ValueError, IndexError, KeyError):
                raise PrivacyPortabilityError("privacy_format_unsupported") from None
        copies = []
        for number in range(1, 46):
            identifier = f"C{number:02}"
            tables = _COPY_TABLES.get(identifier, ())
            if identifier in _EXTERNAL:
                copies.append(CopyDisposition(copy_id=identifier, disposition="external_unknown"))
                continue
            if identifier == "C11" and layout == "postgres":
                count = int((await query("SELECT count(*) AS n FROM nodes WHERE embedding IS NOT NULL"))[0]["n"])
            elif identifier == "C13" and layout == "directory":
                # File contents are retained; an index file's existence is not
                # a proof of its logical entries or recoverability.
                base = Path(config.vector_path)
                count = int(base.exists() or Path(str(base) + ".enc").exists())
            else:
                present = [table for table in tables if table in counts]
                if not present:
                    copies.append(CopyDisposition(copy_id=identifier, disposition="absent_by_layout", count=0))
                    continue
                count = sum(counts[table] for table in present)
            copies.append(CopyDisposition(copy_id=identifier,
                disposition="rebuild" if identifier in _REBUILD else "classify", count=count))
        unavailable = ["external_backup_inventory_unknown", "offline_copy_revocation_unavailable",
                       "physical_media_erasure_unavailable"]
        if not protected:
            unavailable.extend(("legacy_history_requires_explicit_disposition", "legacy_source_checksum_retained_only_in_original"))
            if counts["events"] <= MAX_ROWS:
                for row in await query(f"SELECT session_id FROM events WHERE session_id IS NOT NULL LIMIT {MAX_ROWS + 1}"):
                    try:
                        # Match admission's parsing acceptance, not a canonical
                        # spelling check. Original identifiers remain untouched.
                        UUID(row["session_id"])
                    except (TypeError, ValueError):
                        unavailable.append("legacy_non_uuid_sessions_unsupported")
                        break
            # Report feature names only; serialized messages never enter a dry-run.
            for table in ("events", "nodes"):
                if counts[table] > MAX_ROWS:
                    continue
                for row in await query(f"SELECT metadata FROM {table} LIMIT {MAX_ROWS + 1}"):
                    try:
                        metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                        keys, _, _ = _history_metadata(metadata)
                        features = []
                        if _HISTORY_MESSAGE in keys:
                            features.append("lossless_framework_history_requires_explicit_disposition")
                        if _HISTORY_CONTROL in keys:
                            features.append("legacy_chat_controls_require_explicit_disposition")
                    except (ValueError, TypeError, KeyError):
                        features = ["legacy_framework_history_unclassified"]
                    unavailable.extend(feature for feature in features if feature not in unavailable)
        if counts.get("blobs", 0):
            unavailable.append("visual_conversion_requires_explicit_removal")
        if any(count > MAX_ROWS for count in counts.values()):
            unavailable.append("migration_capacity_exceeded")
        if layout == "postgres":
            retained = ("selected_postgres_source", "operator_pg_dumps", "deployment_wal_and_replicas")
        else:
            paths = (config.pack_path,) if config.pack_path else (config.db_path, config.vector_path, config.lexical_path)
            retained = tuple(str(Path(str(path) + ".enc" if not Path(path).exists() and Path(str(path) + ".enc").exists() else path).absolute()) for path in paths)
        policy = policy or config.privacy_policy
        purposes = ["store"] if config.encryption_enabled else []
        if policy is not None:
            purposes.append("retry_commitment")
            if policy.capture != "none":
                purposes.append("evidence")
            if policy.durable_processing_input:
                purposes.append("processing")
            if any(action.action == "pseudonymize" for action in policy.actions()):
                purposes.append("pseudonym")
        else:
            unavailable.append("explicit_target_policy_required")
        return PrivacyMigrationPlan(source_format="protected_v2" if protected else "legacy",
            source_layout=layout, copies=tuple(copies), source_events=counts["events"], admitted_nodes=counts["nodes"],
            required_key_purposes=tuple(purposes), policy_changes=(() if policy is None else (
                "explicit_sanitized_representations", "saved_policy_and_deadlines", "independent_current_erasure_ledger")),
            retained_copies=(*retained, "operator_exports_and_backups_unknown"), unavailable_features=tuple(unavailable),
            legacy_plaintext_present=not protected)


@dataclass(frozen=True)
class _LegacySnapshot:
    tables: dict
    fingerprint: str
    layout: str


async def _read_snapshot(config):
    """Read fixed native tables with a finite total, retaining bytes only in RAM."""
    total, tables = 0, {}
    async with _source(config) as query:
        counts = await _inventory(query)
        if "privacy_format" in counts:
            raise PrivacyPortabilityError("privacy_format_unsupported")
        for name, count in counts.items():
            if count > MAX_ROWS:
                raise PrivacyPortabilityError("privacy_source_capacity")
            rows = await query(f'SELECT * FROM "{name}" LIMIT {MAX_ROWS + 1}')
            # This internal source-generation fingerprint never enters a public
            # result, provenance record or purported original-source checksum.
            encoded = json.dumps(rows, sort_keys=True, default=lambda value: value.hex() if isinstance(value, bytes) else str(value)).encode()
            total += len(encoded)
            if total > MAX_BYTES:
                raise PrivacyPortabilityError("privacy_source_capacity")
            tables[name] = rows
    normalized = {name: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str)) for name, rows in tables.items()}
    if config.backend != "postgres" and not config.pack_path:
        from prme.storage.privacy_source import _read_bounded, _quiescent
        vector = Path(config.vector_path)
        if not vector.exists() and Path(str(vector) + ".enc").exists():
            vector = Path(str(vector) + ".enc")
        if vector.exists():
            _quiescent(vector)
            normalized["_native_vector_artifact"] = hashlib.sha256(_read_bounded(vector, MAX_BYTES)).hexdigest()
    fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=lambda value: value.hex() if isinstance(value, bytes) else str(value)).encode()).hexdigest()
    return _LegacySnapshot(tables, fingerprint,
        "postgres" if config.backend == "postgres" else "pack" if config.pack_path else "directory")


class _MigrationJob(FrozenModel):
    """Private frozen input, authenticated with the existing retry capability."""
    schema_version: int = 1
    job_id: UUID
    snapshot: PolicySnapshot
    source_generation: str
    target: str
    event_ids: tuple[UUID, ...]
    remove_event_ids: tuple[UUID, ...]
    remove_node_ids: tuple[UUID, ...]
    unavailable_legacy_history: bool
    allow_reembedding: bool


def _target(config):
    if config.backend == "postgres":
        # Credentials never become a path, journal identity, or error message.
        from urllib.parse import urlsplit, parse_qsl
        parts = urlsplit(config.database_url.get_secret_value())
        return json.dumps({"host": parts.hostname, "port": parts.port, "database": parts.path,
            "search_path": dict(parse_qsl(parts.query)).get("search_path"), "namespace": str(config.namespace_id)})
    return str(Path(config.pack_path or config.db_path).absolute())


def _legacy_objects(source, *, remove_event_ids, remove_node_ids, unavailable_history):
    try:
        return _decode_legacy_objects(source, remove_event_ids=remove_event_ids,
            remove_node_ids=remove_node_ids, unavailable_history=unavailable_history)
    except (PrivacyDenied, PrivacyPortabilityError):
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PrivacyPortabilityError("privacy_legacy_unclassified") from None


def _meaning(node):
    """Saved content, qualifiers and truth clocks; mutable usage/state is separate."""
    return node.model_dump(include={"id", "content", "metadata", "evidence_refs", "user_id", "scope",
        "session_id", "node_type", "epistemic_type", "source_type", "valid_from", "valid_to", "event_time"})


def _decode_legacy_objects(source, *, remove_event_ids, remove_node_ids, unavailable_history):
    from prme.models import Event
    from prme.models.direct_store import DirectStoreRecord
    from prme.models.extraction import ExtractionRecord
    from prme.storage.derivation_registry import decode_plan
    from prme.storage.pg.graph_store import PgGraphStore
    if source.tables.get("privacy_admissions"):
        raise PrivacyPortabilityError("privacy_legacy_unclassified")
    if source.tables.get("operations") and not unavailable_history:
        raise PrivacyPortabilityError("privacy_legacy_unclassified")
    events = {}
    for row in source.tables["events"]:
        values = dict(row)
        if isinstance(values.get("metadata"), str):
            values["metadata"] = json.loads(values["metadata"])
        event = Event.model_validate(values)
        if str(event.id) != str(row["id"]) or hashlib.sha256(event.content.encode()).hexdigest() != event.content_hash:
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        UUID(event.user_id)
        events[event.id] = event
    if not set(remove_event_ids) <= events.keys():
        raise PrivacyPortabilityError("privacy_legacy_unclassified")
    candidates, extractions, generated = {}, {}, {}
    for operation in source.tables["operations"]:
        if operation["op_type"] == "EXTRACTION_VALIDATED":
            body = json.loads(operation["payload"]) if isinstance(operation["payload"], str) else operation["payload"]
            record = ExtractionRecord.model_validate(body)
            event = events[record.event_id]
            record.verify_source(event.user_id, event.scope.value, event.content_hash)
            if record.operation_id != str(operation["id"]) or str(event.id) != str(operation["target_id"]):
                raise PrivacyPortabilityError("privacy_legacy_unclassified")
            extractions[event.id] = record
    for operation in source.tables["operations"]:
        body = json.loads(operation["payload"]) if isinstance(operation["payload"], str) else operation["payload"]
        if operation["op_type"] == "DIRECT_STORE_REQUESTED":
            record = DirectStoreRecord.from_operation_payload(body)
            record.verify_source(events[record.event_id])
            if record.operation_id != str(operation["id"]):
                raise PrivacyPortabilityError("privacy_legacy_unclassified")
            candidates.setdefault(record.node.id, []).append((record.event_id, "direct", record.node))
        elif operation["op_type"] == "DERIVATION_PREPARED":
            plan = decode_plan(body)
            event = events[plan.event_id]
            plan.verify_source(event.user_id, event.scope.value, event.content_hash)
            if plan.prepared_operation_id != str(operation["id"]) or plan.event_id not in extractions:
                raise PrivacyPortabilityError("privacy_legacy_unclassified")
            for node in plan.nodes:
                candidates.setdefault(node.id, []).append((event.id, "extraction", node))
        elif operation["op_type"] in {"PROFILE_PUBLISHED", "CONSOLIDATION_PUBLISHED"}:
            if operation["op_type"] == "PROFILE_PUBLISHED":
                from prme.storage.profile_registry import decode
                plan = decode(tuple(operation[name] for name in ("id", "op_type", "target_id", "payload", "actor_id", "namespace_id")))
                kind = "profile"
            else:
                from prme.storage.consolidation_publication import _unpack, _validate_operation
                plan = _unpack(body)
                _validate_operation(plan, tuple(operation[name] for name in ("op_type", "target_id", "payload", "actor_id", "namespace_id")), "CONSOLIDATION_PUBLISHED")
                kind = "summary"
            if plan.schema_version != 1 or plan.privacy_manifest is not None:
                raise PrivacyPortabilityError("privacy_legacy_unclassified")
            generated[plan.node.id] = (kind, plan)
    nodes = {node.id: node for node in map(PgGraphStore._record_to_node, source.tables["nodes"])}
    if not set(remove_node_ids) <= nodes.keys():
        raise PrivacyPortabilityError("privacy_legacy_unclassified")
    by_event = {identifier: [] for identifier in events}
    kinds = {identifier: "extraction" if identifier in extractions else "direct" for identifier in events}
    retained_generated = {}
    for node in nodes.values():
        if node.id in remove_node_ids or any(value in remove_event_ids for value in node.evidence_refs):
            continue
        if node.id in generated:
            kind, plan = generated[node.id]
            if (_meaning(node) != _meaning(plan.node) or any(
                    dep.id not in nodes or _meaning(dep) != _meaning(nodes[dep.id])
                    for dep in (*plan.sources, *plan.previous))):
                raise PrivacyPortabilityError("privacy_legacy_unclassified")
            retained_generated[node.id] = (node, kind, plan)
            continue
        matching = [(event_id, kind) for event_id, kind, saved in candidates.get(node.id, ())
                    if _meaning(node) == _meaning(saved)]
        if len(set(matching)) != 1:
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        event_id, kind = matching[0]
        if any(ref not in events or (events[ref].user_id, events[ref].scope) != (node.user_id, node.scope) for ref in node.evidence_refs):
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        if by_event[event_id] and kinds[event_id] != kind:
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        kinds[event_id] = kind
        by_event[event_id].append(node)
    if any(len(values) > 1 and kinds[identifier] == "direct" for identifier, values in by_event.items()):
        raise PrivacyPortabilityError("privacy_legacy_unclassified")
    if source.tables.get("blobs"):
        # A visual source and all dependent nodes must be explicitly selected
        # for removal; no description/hash/thumbnail is silently treated as text.
        visual = {identifier for identifier, event in events.items()
            if any(key in (event.metadata or {}) for key in ("frame_sha256", "thumbnail", "observation_id", "frame_id"))}
        if not visual or not visual <= set(remove_event_ids):
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
    # Preserve every original source/checksum and journal/node comparison above.
    # Only the new admitted copy receives the explicitly unavailable projection.
    events, by_event = _project_legacy_history(events, by_event, nodes, kinds,
        unavailable_history=unavailable_history)
    return events, by_event, kinds, retained_generated


def _legacy_vectors(source, config):
    """Native exact vectors, including intact historical file-only snapshots."""
    import numpy as np
    from prme.models.derivation import PreparedEmbedding
    output = {}
    nodes = {str(row["id"]): row for row in source.tables["nodes"]}
    if source.layout == "postgres":
        for identifier, row in nodes.items():
            if row.get("embedding") is not None:
                values = json.loads(row["embedding"]) if isinstance(row["embedding"], str) else row["embedding"]
                output[UUID(identifier)] = PreparedEmbedding(node_id=identifier, content=row["content"], values=tuple(values),
                    model=row["embedding_model"], version=row["embedding_version"], dimension=len(values))
        return output
    payloads = {row["vector_key"]: row["vector_data"] for row in source.tables.get("vector_payloads", ())}
    native = None
    for row in source.tables.get("vector_metadata", ()):
        identifier = str(row["node_id"])
        if identifier not in nodes:
            continue
        dimension = row["embedding_dim"]
        data = payloads.get(row["vector_key"])
        if data is not None:
            values = np.frombuffer(data, dtype="<f4")
        else:
            if native is None:
                from usearch.index import Index
                from prme.storage.privacy_source import read_legacy_bytes
                snapshots = source.tables.get("vector_snapshot", ())
                if snapshots:
                    raw = snapshots[0]["data"]
                else:
                    path = Path(config.vector_path)
                    if not path.exists() and Path(str(path) + ".enc").exists():
                        path = Path(str(path) + ".enc")
                    raw = read_legacy_bytes(path, encryption_key=_password(config))
                native = Index(ndim=dimension, metric="cos", dtype="f32")
                native.load(raw)
            values = native.get(row["vector_key"])
        if values is None or len(values) != dimension:
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        output[UUID(identifier)] = PreparedEmbedding(node_id=identifier, content=nodes[identifier]["content"],
            values=tuple(map(float, values)), model=row["embedding_model"], version=row["embedding_version"], dimension=dimension)
    return output


def _migration_coordinator(conn, config, runtime):
    import asyncio
    from types import SimpleNamespace
    from prme.privacy.admission import AdmissionCoordinator
    from prme.storage.event_store import EventStore
    from prme.epistemic.matrix import ConfidenceMatrix
    engine = SimpleNamespace(_config=config, _event_store=EventStore(conn, asyncio.Lock(), protected=True),
                             _confidence_matrix=ConfidenceMatrix())
    engine._privacy = AdmissionCoordinator(engine, runtime)
    return engine


async def _event_nodes(coordinator, prepared, nodes, vectors, *, allow_reembedding):
    """Use the existing closed projections; never admit a supporting paragraph."""
    from prme.privacy.records import PRIVACY_KEY
    from prme.models.derivation import PreparedEmbedding
    from prme.types import LifecycleState
    if not nodes:
        return (), ()
    if prepared.record.kind == "direct":
        admitted = []
        for original in nodes:
            projected = coordinator.direct_node(prepared, node_type=original.node_type,
                epistemic_type=original.epistemic_type, source_type=original.source_type,
                confidence=original.confidence, ttl_days=original.ttl_days,
                valid_from=original.valid_from, valid_to=original.valid_to)
            admitted.append(original.model_copy(update={"content": projected.content, "metadata": projected.metadata}))
    else:
        from prme.ingestion.admitted_statements import project_admitted_node
        admitted = [project_admitted_node(node, prepared.source) for node in nodes]
        fields = {"nodes": [{"content": node.content, "metadata": node.metadata} for node in admitted],
                  "vectors": [node.content for node in admitted], "lexical": [node.content for node in admitted]}
        values, report = await coordinator.final_generated_bundle(prepared, fields)
        if set(values) != set(fields) or any(len(values[key]) != len(fields[key]) for key in fields):
            raise PrivacyDenied("privacy_invalid_result")
        for index, node in enumerate(admitted):
            value = values["nodes"][index]
            if value["content"] != values["vectors"][index] or value["content"] != values["lexical"][index]:
                raise PrivacyDenied("privacy_invalid_result")
            metadata = dict(value["metadata"])
            metadata[PRIVACY_KEY] = prepared.record.reference()
            if report is not None:
                metadata[PRIVACY_KEY]["sanitization"] = report.model_dump(mode="json")
            admitted[index] = node.model_copy(update={"content": value["content"], "metadata": metadata})
    embeddings = []
    runtime = coordinator.require_runtime()
    for node in admitted:
        if node.lifecycle_state == LifecycleState.ARCHIVED:
            # Archive deliberately evicts indexes while retaining source/node
            # history. Keep that validated terminal state without recreating it.
            continue
        existing = vectors.get(node.id)
        if existing is None:
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
        if existing.content == node.content:
            embeddings.append(existing)
        elif allow_reembedding:
            encoded = await coordinator.encode((node.content,), prepared.record.snapshot, records=(prepared.record,))
            embeddings.append(PreparedEmbedding(node_id=node.id, content=node.content, values=encoded[0],
                model=runtime.embedder.model_name, version=runtime.embedder.model_version, dimension=runtime.embedder.dimension))
        else:
            raise PrivacyPortabilityError("privacy_legacy_unclassified")
    return tuple(node.model_dump() for node in admitted), tuple(embeddings)


class _GeneratedMigration:
    """Only already-admitted, exact migration dependencies enter this adapter.

    This implements the existing #324 generated-output capability shape. It
    does not use or replace the ordinary-read contributor classifier.
    """
    def __init__(self, coordinator, records):
        from prme.privacy.capabilities import OperationBudget
        self.coordinator = coordinator
        self.records = {record.event_id: record for record in records}
        self.budget = OperationBudget(uuid4())

    async def generated_sources(self, values):
        from prme.privacy.admission import _fields
        from prme.privacy.sanitizer import canonical
        snapshots = {record.snapshot for record in self.records.values()}
        return tuple([await self.coordinator.sanitize(_fields(canonical(values), None), saved,
            self.budget, mode="query") for saved in snapshots])

    async def generated(self, fields, *, sources):
        from prme.privacy.sanitizer import canonical
        values = [await self.coordinator.sanitize(canonical(fields), source.source_snapshot,
            self.budget, mode="generated_output", source=source) for source in sources]
        if not values or len({value.fields_json for value in values}) != 1:
            raise PrivacyDenied("privacy_policy_conflict")
        return json.loads(values[0].fields_json)


def _dependency_records(nodes, known_nodes, records):
    from prme.privacy.journals import JOURNAL_KEY, GeneratedNodeReference
    from prme.privacy.records import PRIVACY_KEY
    pending, seen, event_ids = list(nodes), set(), set()
    while pending:
        node = pending.pop()
        if node.id in seen:
            continue
        seen.add(node.id)
        metadata = node.metadata or {}
        event_ids.add(UUID(metadata[PRIVACY_KEY]["event_id"]))
        if JOURNAL_KEY in metadata:
            pending.extend(known_nodes[identifier] for identifier in GeneratedNodeReference.model_validate(metadata[JOURNAL_KEY]).source_node_ids)
        for identifier in node.evidence_refs:
            if identifier in records:
                event_ids.add(identifier)
            elif identifier in known_nodes:
                pending.append(known_nodes[identifier])
            else:
                raise PrivacyPortabilityError("privacy_legacy_unclassified")
    return tuple(records[identifier] for identifier in sorted(event_ids, key=str))


async def _generated_nodes(engine, generated, vectors, *, removed_nodes, allow_reembedding, fault):
    """Admitted memory survives; old executable publication identity does not.

    The existing typed generated reference records *all* source and previous
    publication nodes. Current ledger traversal therefore retains the full
    declared dependency closure without inventing a new executable journal.
    """
    from prme.models.derivation import PreparedEmbedding
    from prme.privacy.journals import admit_generated_node
    from prme.privacy.portability import ProtectedExport, ProtectedExportManifest, ProtectedStoreFormat
    from prme.storage.pg.graph_store import PgGraphStore, _NODE_COLUMNS
    from prme.storage.privacy_export import _load_export, _node
    coordinator = engine._privacy
    runtime = coordinator.require_runtime()
    pending, omitted = dict(generated), set(removed_nodes)
    while pending:
        progressed = False
        # Read the actual committed batches on every resume; never regenerate
        # an already persisted generated node or its stored numerical vector.
        async with coordinator.erasure.repository.session() as session:
            known = {node.id: node for node in map(PgGraphStore._record_to_node,
                await session.fetch(f"SELECT {_NODE_COLUMNS} FROM nodes"))}
        records = {record.event_id: record for record in await coordinator.erasure.current_records(
            await coordinator.repository._read("1=1", ()))}
        for identifier, (original, kind, plan) in tuple(pending.items()):
            if identifier in known:
                pending.pop(identifier)
                progressed = True
                continue
            dependencies = tuple(dict.fromkeys(node.id for node in (*plan.sources, *plan.previous)))
            if any(identity in pending for identity in dependencies):
                continue
            if any(identity in omitted for identity in dependencies):
                # A deliberate removal or current erasure invalidates the whole
                # generated result; no surviving fragment justifies its text.
                omitted.add(identifier)
                pending.pop(identifier)
                progressed = True
                continue
            if any(identity not in known for identity in dependencies):
                raise PrivacyPortabilityError("privacy_legacy_unclassified")
            sources = tuple(known[identity] for identity in dependencies)
            selected = _dependency_records(sources, known, records)
            try:
                async with coordinator.erasure.guard(selected, node_ids=(identifier, *dependencies)):
                    metadata = dict(original.metadata or {})
                    if kind == "profile":
                        metadata["profile_format_version"] = 2
                    else:
                        # The new opaque lineage is an existing opaque node ID,
                        # never the old content-derived request hash or name.
                        metadata["consolidation_key"] = str(identifier)
                        if metadata.get("summary_publication_kind") == "hierarchical_source_excerpts_v2":
                            metadata["summary_publication_key"] = str(identifier)
                        else:
                            metadata["consolidation_format_version"] = 2
                    admitted, manifest = await admit_generated_node(_GeneratedMigration(coordinator, selected),
                        original.model_copy(update={"metadata": metadata}), sources, kind=kind, lineage_id=identifier)
                    _node(admitted, manifest)
                    existing = vectors.get(identifier)
                    if existing is None:
                        raise PrivacyPortabilityError("privacy_legacy_unclassified")
                    if existing.content == admitted.content:
                        embedding = existing
                    elif allow_reembedding:
                        encoded = await coordinator.encode((admitted.content,), selected[0].snapshot, records=selected)
                        embedding = PreparedEmbedding(node_id=identifier, content=admitted.content, values=encoded[0],
                            model=runtime.embedder.model_name, version=runtime.embedder.model_version, dimension=runtime.embedder.dimension)
                    else:
                        raise PrivacyPortabilityError("privacy_legacy_unclassified")
                    # Internal append, not a standalone export. The complete
                    # typed graph is validated before any external publication.
                    descriptor = ProtectedStoreFormat.model_validate_json((await coordinator.repository._query("SELECT body FROM privacy_format", ()))[0][0])
                    head = (await coordinator.erasure.repository.state())[1]
                    batch = ProtectedExport(manifest=ProtectedExportManifest(mode="protected", format=descriptor,
                        created_at=runtime.clock(), ledger_checkpoint=head, omitted=("legacy_executable_publication",)),
                        admissions=(), events=(), nodes=(admitted.model_dump(),), edges=(), embeddings=(embedding,), operations=(), work=())
                    async with coordinator.publication_guards(selected, node_ids=(identifier, *dependencies)):
                        await _load_export(coordinator.erasure.repository, batch)
                    known[identifier] = admitted
                if fault:
                    await fault("after_generated", len(generated) - len(pending), engine)
            except PrivacyDenied as exc:
                if exc.code != "privacy_erased":
                    raise
                omitted.add(identifier)
            pending.pop(identifier)
            progressed = True
        if not progressed:
            raise PrivacyPortabilityError("privacy_legacy_unclassified")


async def _published_target(config, runtime, provider, expected):
    """A lost acknowledgment can only resume this exact published lineage/job."""
    from prme import MemoryEngine
    from prme.privacy.portability import ProtectedStoreFormat
    if config.backend != "postgres" and not Path(config.pack_path or config.db_path).exists():
        return False
    async with _source(config) as query:
        tables = await query("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'")
        if not tables:
            return False
        if "privacy_format" not in {row["table_name"] for row in tables}:
            raise PrivacyPortabilityError("privacy_destination_occupied")
        actual = ProtectedStoreFormat.model_validate_json((await query("SELECT body FROM privacy_format"))[0]["body"])
        if actual != expected.model_copy(update={"state": "complete"}):
            raise PrivacyPortabilityError("privacy_destination_occupied")
    # Normal current-ledger/expiry-before-recovery startup is still mandatory;
    # the job's completion bit is not permission to serve stale target data.
    async with MemoryEngine.open(config, privacy_runtime=runtime, embedding_provider=provider) as engine:
        coordinator = engine._privacy
        records = await coordinator.erasure.current_records(await coordinator.repository._read("1=1", ()))
        nodes = await coordinator.repository._query("SELECT id FROM nodes", ())
        async with coordinator.publication_guards(records, node_ids=tuple(row[0] for row in nodes)):
            return True


def migration_status(job_path) -> MigrationStatus:
    """Read-only job status is progress, never independent serving permission."""
    import duckdb
    path = Path(job_path) / "migration.duckdb"
    if path.is_symlink() or not path.is_file():
        raise PrivacyPortabilityError("privacy_migration_incomplete")
    with duckdb.connect(str(path), read_only=True) as conn:
        body, completed, state = conn.execute("SELECT body,completed,state FROM privacy_migration_job WHERE singleton=1").fetchone()
        job = _MigrationJob.model_validate_json(body)
        return MigrationStatus(job_id=job.job_id, state=state, completed=completed, total=len(job.event_ids))


async def migrate_privacy_pack(source_config, target_config, *, privacy_runtime, embedding_provider,
                               job_path, resume=False, unavailable_legacy_history=False,
                               remove_event_ids=(), remove_node_ids=(), allow_reembedding=False,
                               _fault=None) -> MigrationStatus:
    """Explicit conversion. The quarantined job has no ordinary serving path.

    A new versioned representation retains opaque event/node IDs; original
    version/checksum bytes stay in the unchanged source. Unknown legacy graph
    kinds are refused unless deliberately removed. No model runs implicitly.
    """
    import duckdb
    from filelock import FileLock, Timeout
    from types import SimpleNamespace
    from prme.privacy.admission import preflight_runtime
    from prme.privacy.portability import ProtectedStoreFormat, ProtectedExport, ProtectedExportManifest, ExportEvent
    from prme.storage.privacy_export import _load_export, _capture, _publish_bytes, restore_protected_export
    from prme.storage.erasure import prepare_store
    from prme.storage.schema import initialize_database
    from prme.storage.vector_index import VectorIndex
    from prme.storage.privacy_format import install_duck_format
    from prme.models.privacy_reads import ProtectedOperationView
    runtime = privacy_runtime
    await preflight_runtime(target_config, runtime)
    if runtime is None or runtime.retry_committer is None or target_config.privacy_policy is None:
        raise PrivacyDenied("privacy_retry_unavailable")
    source = await _read_snapshot(source_config)
    remove_events = tuple(sorted(map(UUID, map(str, remove_event_ids)), key=str))
    remove_nodes = tuple(sorted(map(UUID, map(str, remove_node_ids)), key=str))
    events, by_event, kinds, generated = _legacy_objects(source, remove_event_ids=remove_events,
        remove_node_ids=remove_nodes, unavailable_history=unavailable_legacy_history)
    vectors = _legacy_vectors(source, source_config)
    path = Path(job_path).absolute()
    if path.is_symlink() or (resume and not path.is_dir()) or (not resume and path.exists()):
        raise PrivacyPortabilityError("privacy_destination_occupied")
    # Never permit a job under an original directory or over an original pack.
    original = Path(source_config.pack_path or source_config.db_path).absolute() if source_config.backend != "postgres" else None
    if original is not None and (path == original or (not source_config.pack_path and path.is_relative_to(original.parent))):
        raise PrivacyPortabilityError("privacy_destination_occupied")
    if not resume:
        path.mkdir(mode=0o700)
    conn = None
    lock = FileLock(str(path / "migration.lock"))
    try:
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise PrivacyPortabilityError("privacy_migration_incomplete") from None
        database = path / "migration.duckdb"
        if resume and (not database.is_file() or database.is_symlink()):
            raise PrivacyPortabilityError("privacy_migration_incomplete")
        conn = duckdb.connect(str(database), config={"threads": 1, "temp_directory": ""})
        working = target_config.model_copy(update={"database_url": None, "pack_path": None,
            "db_path": str(database), "vector_path": str(path / "vectors.usearch"), "lexical_path": str(path / "lexical_index")})
        if not resume:
            await prepare_store(conn=conn, runtime=runtime, namespace_id=working.namespace_id)
            initialize_database(conn, protected=True)
            VectorIndex._init_metadata_table(SimpleNamespace(_conn=conn, _index_path=None))
        engine = _migration_coordinator(conn, working, runtime)
        coordinator = engine._privacy
        current = coordinator.effective(None, user_id=str(target_config.privacy_policy.owner_id), scope=target_config.privacy_policy.scope)
        if resume:
            body, authentication, completed, state = conn.execute("SELECT body,authentication,completed,state FROM privacy_migration_job WHERE singleton=1").fetchone()
            job = _MigrationJob.model_validate_json(body)
            commitment = RetryCommitment.model_validate_json(authentication)
            if (job.schema_version != 1 or job.source_generation != source.fingerprint or job.target != _target(target_config)
                    or job.snapshot.policy != current.policy or job.snapshot.references != current.references
                    or job.remove_event_ids != remove_events or job.remove_node_ids != remove_nodes
                    or job.unavailable_legacy_history != unavailable_legacy_history or job.allow_reembedding != allow_reembedding
                    or commitment.context != RetryContext(request_id=job.job_id, snapshot=job.snapshot)):
                raise PrivacyPortabilityError("privacy_source_changed")
            async with coordinator.guard(job.snapshot, "admission"):
                if not await runtime.retry_committer.verify(job.model_dump_json().encode(), commitment):
                    raise PrivacyDenied("privacy_retry_conflict")
            await prepare_store(conn=conn, runtime=runtime, namespace_id=working.namespace_id)
        else:
            job = _MigrationJob(job_id=uuid4(), snapshot=current, source_generation=source.fingerprint,
                target=_target(target_config), event_ids=tuple(sorted(events, key=str)), remove_event_ids=remove_events,
                remove_node_ids=remove_nodes, unavailable_legacy_history=unavailable_legacy_history, allow_reembedding=allow_reembedding)
            async with coordinator.guard(current, "admission"):
                commitment = await runtime.retry_committer.commit(job.model_dump_json().encode(), RetryContext(request_id=job.job_id, snapshot=current))
                conn.execute("BEGIN TRANSACTION")
                try:
                    install_duck_format(conn, state="staging", migration_id=job.job_id)
                    current_format = ProtectedStoreFormat.model_validate_json(conn.execute("SELECT body FROM privacy_format").fetchone()[0])
                    conn.execute("UPDATE privacy_format SET body=?", [current_format.model_copy(update={"state": "staging", "migration_id": job.job_id}).model_dump_json()])
                    conn.execute("CREATE TABLE privacy_migration_job(singleton INTEGER PRIMARY KEY,body VARCHAR,authentication VARCHAR,completed INTEGER,state VARCHAR)")
                    conn.execute("INSERT INTO privacy_migration_job VALUES(1,?,?,0,'staging')", [job.model_dump_json(), commitment.model_dump_json()])
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            completed = 0
        descriptor = ProtectedStoreFormat.model_validate_json(conn.execute("SELECT body FROM privacy_format").fetchone()[0])
        if descriptor.migration_id != job.job_id or descriptor.state != "staging":
            raise PrivacyPortabilityError("privacy_migration_incomplete")
        if resume and completed == len(job.event_ids):
            if await _published_target(target_config, runtime, embedding_provider, descriptor):
                conn.execute("UPDATE privacy_migration_job SET state='complete' WHERE singleton=1")
                return MigrationStatus(job_id=job.job_id, state="complete", completed=completed, total=len(job.event_ids))
            if state == "complete":
                raise PrivacyPortabilityError("privacy_migration_incomplete")
        from prme.privacy.records import PrivacyRecord
        subjects = {identifier: PrivacyRecord(event_id=identifier, source_id=uuid4(), representation_id=uuid4(),
            snapshot=job.snapshot, kind=kinds[identifier]) for identifier in events}
        for index, event_id in enumerate(job.event_ids):
            if index < completed:
                continue
            if event_id in remove_events:
                conn.execute("UPDATE privacy_migration_job SET completed=? WHERE singleton=1", [index + 1])
                continue
            event = events[event_id]
            subject = subjects[event_id]
            if not await coordinator.erasure.current_records((subject,)):
                conn.execute("UPDATE privacy_migration_job SET completed=? WHERE singleton=1", [index + 1])
                continue
            nodes = []
            for node in by_event[event_id]:
                # Keep the exact full evidence set or omit the whole node when
                # a current event/node fence invalidates it; never trim refs.
                try:
                    async with coordinator.erasure.guard(tuple(subjects[ref] for ref in node.evidence_refs), node_ids=(node.id,)):
                        nodes.append(node)
                except PrivacyDenied as exc:
                    if exc.code != "privacy_erased":
                        raise
            dependencies = tuple(subjects[identifier] for identifier in sorted(
                {event_id, *(ref for node in nodes for ref in node.evidence_refs)}, key=str))
            async with coordinator.erasure.guard(dependencies, node_ids=tuple(node.id for node in nodes)):
                original_node = nodes[0] if nodes else None
                prepared = await coordinator.prepare(event.content, user_id=event.user_id, scope=event.scope, policy=None,
                    kind=kinds[event_id], asynchronous=False, session_id=event.session_id, role=event.role,
                    metadata=original_node.metadata if original_node and kinds[event_id] == "direct" else event.metadata, event_time=event.event_time,
                    retrieval_content=original_node.content if original_node and kinds[event_id] == "direct" else None,
                    _migration_identity=(event_id, job.snapshot))
                from dataclasses import replace
                prepared = replace(prepared, record=prepared.record.model_copy(update={"retry": commitment, "request_index": index}))
                admitted_nodes, embeddings = await _event_nodes(coordinator, prepared, nodes, vectors,
                    allow_reembedding=allow_reembedding)
                head = (await coordinator.erasure.repository.state())[1]
                batch = ProtectedExport(manifest=ProtectedExportManifest(mode="protected", format=descriptor.model_copy(update={"state": "complete"}),
                    created_at=runtime.clock(), ledger_checkpoint=head, omitted=("legacy_history_checksum_and_replay",)),
                    admissions=(prepared.record,), events=(ExportEvent(id=event.id, timestamp=event.timestamp, role=event.role,
                        user_id=event.user_id, session_id=event.session_id, scope=event.scope, created_at=event.created_at, event_time=event.event_time),),
                    nodes=admitted_nodes, edges=(), embeddings=embeddings, operations=(), work=())
                # The job may contain an admitted node referring to a later event
                # batch. It remains non-serving until complete dependency closure
                # validates below. Each persisted node still passes the existing
                # typed projection under its exact primary record before this write.
                from prme.storage.privacy_export import _node, _manifest
                manifest = _manifest((prepared.record,), runtime.clock())
                for node in batch.nodes:
                    _node(node, manifest)
                if _fault:
                    await _fault("before_batch", index, engine)
                async with coordinator.publication_guards((prepared.record, *dependencies), node_ids=tuple(node["id"] for node in admitted_nodes)), coordinator.writer_guard((prepared.record,)):
                    await _load_export(coordinator.erasure.repository, batch, _cursor=index + 1)
            if _fault:
                await _fault("after_batch", index, engine)
        # Metadata-only historical references use the existing unavailable view;
        # raw records, checksums and pre-sanitation bytes remain only at source.
        async with coordinator.erasure.guard():
            conn.execute("CREATE TABLE IF NOT EXISTS privacy_migration_history(id VARCHAR PRIMARY KEY,body VARCHAR NOT NULL)")
            for operation in source.tables["operations"]:
                try:
                    target_id = UUID(str(operation["target_id"])) if operation["target_id"] else None
                except ValueError:
                    target_id = None
                view = ProtectedOperationView(id=operation["id"], target_id=target_id, created_at=operation["created_at"])
                conn.execute("INSERT INTO privacy_migration_history VALUES(?,?) ON CONFLICT DO NOTHING", [str(view.id), view.model_dump_json()])
        await prepare_store(conn=conn, runtime=runtime, namespace_id=working.namespace_id)
        all_source_nodes = {UUID(str(row["id"])) for row in source.tables["nodes"]}
        existing_nodes = {UUID(str(row[0])) for row in conn.execute("SELECT id FROM nodes").fetchall()}
        await _generated_nodes(engine, generated, vectors,
            removed_nodes=all_source_nodes - existing_nodes - generated.keys(),
            allow_reembedding=allow_reembedding, fault=_fault)
        from prme.storage.pg.graph_store import PgGraphStore, _EDGE_COLUMNS
        from prme.ingestion.admitted_statements import project_admitted_edge
        remaining_nodes = {UUID(str(row[0])) for row in conn.execute("SELECT id FROM nodes").fetchall()}
        remaining_events = {UUID(str(row[0])) for row in conn.execute("SELECT event_id FROM privacy_admissions").fetchall()}
        edges = []
        for row in source.tables["edges"]:
            edge = PgGraphStore._record_to_edge(row)
            if edge.source_id not in remaining_nodes or edge.target_id not in remaining_nodes or (edge.provenance_event_id is not None and edge.provenance_event_id not in remaining_events):
                continue  # Explicit removal or the real current erasure fence.
            edges.append(project_admitted_edge(edge))
        records = await coordinator.erasure.current_records(await coordinator.repository._read("1=1", ()))
        async with coordinator.publication_guards(records, node_ids=tuple(remaining_nodes)):
            names = tuple(name.strip() for name in _EDGE_COLUMNS.split(","))
            for edge in edges:
                data = edge.model_dump(mode="json")
                data["metadata"] = json.dumps(data["metadata"]) if data["metadata"] else None
                conn.execute(f"INSERT INTO edges({_EDGE_COLUMNS}) VALUES({','.join('?' for _ in names)}) ON CONFLICT DO NOTHING", [data[name] for name in names])
        bundle = await _capture(engine, "protected", _migration_id=job.job_id)
        # A changed source aborts before external publication, never folds a new
        # source generation into this job's previously committed identities.
        if (await _read_snapshot(source_config)).fingerprint != job.source_generation:
            raise PrivacyPortabilityError("privacy_source_changed")
        if _fault:
            await _fault("before_publication", len(job.event_ids), engine)
        artifact = path / "protected-export.json"
        if artifact.exists():
            artifact.unlink()  # Exactly this job-owned, previously staged output.
        records = await coordinator.erasure.current_records(await coordinator.repository._read("1=1", ()))
        async with coordinator.publication_guards(records, node_ids=tuple(node.id for node in bundle.nodes)):
            _publish_bytes(artifact, bundle.model_dump_json().encode())
        await restore_protected_export(artifact, target_config, privacy_runtime=runtime, embedding_provider=embedding_provider)
        if _fault:
            await _fault("after_publication", len(job.event_ids), engine)
        conn.execute("UPDATE privacy_migration_job SET state='complete' WHERE singleton=1")
        return MigrationStatus(job_id=job.job_id, state="complete", completed=len(job.event_ids), total=len(job.event_ids))
    except BaseException:
        if conn is not None:
            try:
                conn.execute("UPDATE privacy_migration_job SET state='interrupted' WHERE singleton=1")
            except duckdb.Error:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()
        if lock.is_locked:
            lock.release()
