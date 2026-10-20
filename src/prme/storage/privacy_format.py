"""Explicit protected-format preflight, before schema mutation or recovery.

V2 removes the legacy content_hash column/index. Released readers pinned in
PV-07 encounter their incompatible index DDL before recovery. New readers use
this positive declaration and validate the actual schema; a missing column is
never itself evidence of protection. Legacy schema/bytes are not rewritten.
"""
from __future__ import annotations

import hashlib

from prme.privacy.erasure import LedgerHead
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import PrivacyPortabilityError, ProtectedStoreFormat
from prme.privacy.records import PRIVACY_KEY
from prme.storage.extraction_work import ExtractionWorkRepository

DDL = "CREATE TABLE IF NOT EXISTS privacy_format(singleton INTEGER PRIMARY KEY CHECK(singleton=1),body TEXT NOT NULL)"
EVENT_COLUMNS = (
    "id", "timestamp", "role", "content", "user_id", "session_id", "scope",
    "metadata", "created_at", "event_time",
)
# A private adapter for the empty Event compatibility object only. It is never
# persisted as an original source checksum or used to authenticate evidence.
_STUB_HASH = hashlib.sha256(b"").hexdigest()


def event_hash_sql(protected: bool, prefix: str = "") -> str:
    if prefix not in ("", "e."):
        raise ValueError("Unsupported event alias")
    return f"'{_STUB_HASH}' AS content_hash" if protected else prefix + "content_hash"


def validate_stub(event) -> None:
    try:
        metadata = event.metadata or {}
        reference = metadata[PRIVACY_KEY]
        if event.content or event.content_hash != _STUB_HASH or reference["event_id"] != str(event.id):
            raise ValueError()
        if set(metadata) - {PRIVACY_KEY, "erasure"}:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise PrivacyDenied("privacy_invalid_result") from None


async def preflight_format(*, conn=None, pool=None, runtime=None, namespace_id=None,
                           conn_lock=None, allow_staging=False) -> bool:
    repository = ExtractionWorkRepository(conn=conn, pool=pool, conn_lock=conn_lock)
    async with repository.session() as session:
        tables = {row["table_name"] for row in await session.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
        if "privacy_format" not in tables:
            if "events" not in tables:
                # No source content exists to reinterpret. The ordinary factory
                # has already checked capabilities before creating the backend.
                return runtime is not None
            columns = {row["column_name"] for row in await session.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='events'")}
            if "content_hash" not in columns:
                raise PrivacyPortabilityError("privacy_format_unsupported")
            if runtime is not None:
                raise PrivacyPortabilityError("privacy_migration_required")
            return False
        if runtime is None:
            raise PrivacyDenied("privacy_capability_unavailable")
        try:
            rows = await session.fetch("SELECT body FROM privacy_format WHERE singleton=1")
            if len(rows) != 1:
                raise ValueError()
            declared = ProtectedStoreFormat.model_validate_json(rows[0]["body"])
            if declared.state != "complete" and not allow_staging:
                raise PrivacyPortabilityError("privacy_migration_incomplete")
            if declared.project_id != runtime.project_id or (namespace_id is not None and declared.store_id != namespace_id):
                raise PrivacyDenied("privacy_boundary_mismatch")
            if not {"events", "privacy_admissions", "privacy_store_state"} <= tables:
                raise ValueError()
            columns = {row["column_name"] for row in await session.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='events'")}
            if columns != set(EVENT_COLUMNS):
                raise ValueError()
            indexes = await session.fetch(
                "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND indexname='idx_events_hash'"
                if session.postgres else
                "SELECT index_name FROM duckdb_indexes() WHERE database_name=current_database() AND schema_name=current_schema() AND index_name='idx_events_hash'")
            if indexes:
                raise ValueError()
            states = await session.fetch("SELECT project_id,body FROM privacy_store_state WHERE singleton=1")
            if len(states) != 1:
                raise ValueError()
            head = LedgerHead.model_validate_json(states[0]["body"])
            if str(states[0]["project_id"]) != str(declared.project_id) or head.store_id != declared.store_id:
                raise ValueError()
            # No content read, admission, recovery or native-index access here.
            marker = (f"e.metadata ? '{PRIVACY_KEY}'" if session.postgres else
                      f"json_extract(e.metadata,'$.{PRIVACY_KEY}') IS NOT NULL")
            if await session.fetch(
                "SELECT e.id FROM events e LEFT JOIN privacy_admissions p ON p.event_id=e.id "
                f"WHERE e.content IS NULL OR length(e.content)>0 OR p.event_id IS NULL OR NOT COALESCE(({marker}),false) LIMIT 1"):
                raise ValueError()
        except (PrivacyDenied, PrivacyPortabilityError):
            raise
        except Exception:
            raise PrivacyPortabilityError("privacy_format_unsupported") from None
    return True


def install_duck_format(conn, *, state="complete", migration_id=None):
    project, body = conn.execute("SELECT project_id,body FROM privacy_store_state WHERE singleton=1").fetchone()
    head = LedgerHead.model_validate_json(body)
    record = ProtectedStoreFormat(project_id=project, store_id=head.store_id, state=state, migration_id=migration_id)
    conn.execute(DDL)
    conn.execute("INSERT INTO privacy_format VALUES(1,?) ON CONFLICT DO NOTHING", [record.model_dump_json()])


async def install_pg_format(conn, *, state="complete", migration_id=None):
    row = await conn.fetchrow("SELECT project_id,body FROM privacy_store_state WHERE singleton=1")
    head = LedgerHead.model_validate_json(row["body"])
    record = ProtectedStoreFormat(project_id=row["project_id"], store_id=head.store_id, state=state, migration_id=migration_id)
    await conn.execute(DDL)
    await conn.execute("INSERT INTO privacy_format VALUES(1,$1) ON CONFLICT DO NOTHING", record.model_dump_json())


async def protected_session(session) -> bool:
    return bool(await session.fetch(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='privacy_format'"))
