"""Real snapshot restore/current-ledger integration, with explicit read limits.

These checks use stored admitted rows and metadata availability. The held #323
public contributor classifier is not repaired or re-tested here. Its public
surviving-memory gate remains pending, so these are not complete ER07/EM08.
"""
import asyncio
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, closing
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy.erasure import ErasureRequest
from prme.privacy.ledger import LocalErasureLedger
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import PrivacyPortabilityError
from prme.storage.privacy_export import export_protected_pack, restore_protected_export
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.privacy_portability_support import fresh_config, restore_database_url


def _local_target(config, path, *, single=False):
    return config.model_copy(update={
        "pack_path": str(path.with_suffix(".prme")) if single else None,
        "db_path": str(path / "memory.duckdb"),
        "vector_path": str(path / "vectors.usearch"),
        "lexical_path": str(path / "lexical_index"),
    })


def _schema_url(url, schema):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "search_path"]
    return urlunsplit(parts._replace(query=urlencode([*query, ("search_path", schema)])))


def _hashes(path):
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    return {str(file.relative_to(path.parent)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in files if file.is_file()}


def _database(url):
    return urlsplit(url).path.lstrip("/")


def _pg_environment(url):
    parts = urlsplit(url)
    assert parts.hostname in {"127.0.0.1", "localhost", "::1"} and _database(url)
    database = _database(url)
    # Native tools receive credentials only through their environment, never
    # command arguments or captured output. Ignore ambient connection options.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGHOST=parts.hostname, PGPORT=str(parts.port), PGDATABASE=database,
               PGUSER=unquote(parts.username), PGPASSWORD=unquote(parts.password), PGSSLMODE="disable")
    return env


async def _pg_tool(command, env):
    result = await asyncio.to_thread(subprocess.run, command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@asynccontextmanager
async def _postgres_target(config):
    import asyncpg
    url = await restore_database_url()
    _pg_environment(url)
    admin = await asyncpg.connect(url)
    schema = "pv326_restore_" + uuid4().hex
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        yield type(config).model_validate({**config.model_dump(), "database_url": _schema_url(url, schema)})
    finally:
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@asynccontextmanager
async def _native_snapshot(config, tmp_path, layout):
    """The caller closes its source engine before native backup/copy operations."""
    backup = tmp_path / "retained-native-backup"
    if layout != "postgres":
        source = Path(config.pack_path) if layout == "pack" else Path(config.db_path).parent
        assert source.is_relative_to(tmp_path)
        target = _local_target(config, tmp_path / "native-restored", single=layout == "pack")
        destination = Path(target.pack_path) if layout == "pack" else Path(target.db_path).parent
        if source.is_dir():
            target = config.model_copy(update={"pack_path": None, **{
                field: str(destination / Path(getattr(config, field)).relative_to(source))
                for field in ("db_path", "vector_path", "lexical_path")
            }})
            shutil.copytree(source, backup)
            shutil.copytree(backup, destination)
        else:
            shutil.copy2(source, backup)
            shutil.copy2(backup, destination)
        before = _hashes(backup)
        assert before
        yield target, backup, before
        assert _hashes(backup) == before
        return

    import asyncpg
    source_url = config.database_url.get_secret_value()
    schema = dict(parse_qsl(urlsplit(source_url).query))["search_path"]
    assert re.fullmatch(r"privacy_component_[0-9a-f]{32}", schema)
    restore_url = await restore_database_url()
    source_env = _pg_environment(source_url)
    restore_env = _pg_environment(restore_url)
    admin = await asyncpg.connect(restore_url)
    try:
        assert not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)", schema)
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        await _pg_tool(["pg_dump", "--no-owner", "--no-acl", "--format=custom", "--schema", schema, "--file", str(backup)], source_env)
        before = _hashes(backup)
        await _pg_tool(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", _database(restore_url), str(backup)], restore_env)
        target = type(config).model_validate({**config.model_dump(), "database_url": _schema_url(restore_url, schema)})
        print(f"Native schema archive: {schema}, {backup.stat().st_size} bytes, sha256={next(iter(before.values()))}")
        yield target, backup, before
        assert _hashes(backup) == before
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _copied_rows(config):
    """Read selected native rows without invoking engine initialization/recovery."""
    statements = [f"SELECT * FROM {table} ORDER BY 1" for table in ("events", "nodes", "privacy_admissions", "operations")]
    if config.backend == "postgres":
        import asyncpg
        conn = await asyncpg.connect(config.database_url.get_secret_value())
        try:
            return [tuple(tuple(row) for row in await conn.fetch(sql)) for sql in statements]
        finally:
            await conn.close()
    import duckdb
    with duckdb.connect(config.pack_path or config.db_path, read_only=True, config={"threads": 1}) as conn:
        return [conn.execute(sql).fetchall() for sql in statements]


def _er08_engine_factory():
    raise AssertionError("explicit task-owned engine factory required")


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER08_archive_clear_retained_copies(tmp_path, layout, analyzer, native, monkeypatch, capsys):
    """Actual framework clears, explicit migration, live purge and retained copies."""
    import copy
    import sys
    import structlog
    from langchain_core.messages import HumanMessage
    from llama_index.core.base.llms.types import ChatMessage
    from prme.cli import build_parser
    from prme.integrations.langchain import PRMEChatMessageHistory
    from prme.integrations.llamaindex import PRMEChatStore
    from prme.privacy.cli import erasure_command
    from prme.privacy.local_keys import public_registry
    from prme.privacy.retry_commitments import NativeRetryCommitter
    from prme.storage.privacy_export import read_protected_export
    from prme.storage.privacy_migration import inspect_privacy_migration, migrate_privacy_pack, _read_snapshot

    monkeypatch.setattr(structlog._config._CONFIG, "logger_factory", structlog.PrintLoggerFactory(file=sys.stderr))
    template, runtime, policy, custody, evidence, _ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    runtime = replace(runtime, key_custody=custody,
        retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    owner, scope = str(policy.owner_id), policy.scope
    langchain_session, llamaindex_session = str(uuid4()), str(uuid4())
    async with fresh_config(base, tmp_path / "legacy", layout) as original, fresh_config(base, tmp_path / "migrated", layout, restore=True) as target:
        original = original.model_copy(update={"privacy_policy": None})
        source = await MemoryEngine.create(original, embedding_provider=runtime.embedder)
        try:
            archived_event = await source.store("Mara likes tea.", user_id=owner, metadata={"kept": "archive metadata"})
            [archived_node] = await source.get_event_nodes(archived_event, user_id=owner)
            await source.archive(str(archived_node.id), user_id=owner)
            assert (await source.get_node(str(archived_node.id), user_id=owner, include_superseded=True)).lifecycle_state.value == "archived"
            assert (await source.get_events(owner))[0].content == "Mara likes tea."
        finally:
            await source.close()

        def write_and_clear():
            history = PRMEChatMessageHistory(config=original, embedding_provider=runtime.embedder,
                user_id=owner, session_id=langchain_session, scope=scope)
            try:
                history.add_messages([HumanMessage(content="Hello")])
                [message] = history._client.get_events(owner, session_id=langchain_session)
                assert history.messages[0].content == "Hello"
                history.clear()
                assert history.messages == []
                retained = history._client.get_events(owner, session_id=langchain_session)
                assert len(retained) == 2 and message in retained
                assert all(event.content_hash for event in retained)
            finally:
                history.close()
            store = PRMEChatStore(config=original, embedding_provider=runtime.embedder, scope=scope)
            key = owner + ":" + llamaindex_session
            try:
                store.add_message(key, ChatMessage(role="user", content="one"))
                store.add_message(key, ChatMessage(role="assistant", content="two"))
                before = store._client.get_events(owner, session_id=llamaindex_session)
                assert [item.content for item in store.delete_messages(key)] == ["one", "two"]
                assert store.get_messages(key) == [] and key not in store.get_keys()
                retained = store._client.get_events(owner, session_id=llamaindex_session)
                assert len(retained) == 3 and all(event in retained for event in before)
            finally:
                store.close()
            return message.metadata
        message_metadata = await asyncio.to_thread(write_and_clear)
        before = await _read_snapshot(original)
        assert len(before.tables["events"]) == 6 and len(before.tables["nodes"]) == 4
        expected = {str(row["id"]): row["content"] for row in before.tables["events"] if row["content"]}
        assert set(expected.values()) == {"Mara likes tea.", "Hello", "one", "two"}
        control_ids = {str(row["id"]) for row in before.tables["events"] if not row["content"]}
        assert len(control_ids) == 2
        original_path = Path(original.pack_path or original.db_path).parent if layout == "directory" else Path(original.pack_path or original.db_path)
        original_hashes = _hashes(original_path) if layout != "postgres" else None
        plan = await inspect_privacy_migration(original, policy=policy)
        assert {"lossless_framework_history_requires_explicit_disposition",
            "legacy_chat_controls_require_explicit_disposition"} <= set(plan.unavailable_features)
        assert plan.retained_copies and plan.legacy_plaintext_present and not plan.protected
        assert "Hello" not in plan.model_dump_json() and "archive metadata" not in plan.model_dump_json()

        async def unpublished(job):
            assert not job.exists()
            if layout == "postgres":
                import asyncpg
                conn = await asyncpg.connect(target.database_url.get_secret_value())
                try:
                    assert not await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")
                finally:
                    await conn.close()
            else:
                assert not Path(target.pack_path or target.db_path).exists()
        job = tmp_path / "default-refused"
        with pytest.raises(PrivacyPortabilityError, match="^privacy_legacy_unclassified$"):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder, job_path=job)
        await unpublished(job)

        # These are the fixed ER08 negative subchecks, each an actual legacy
        # source/journal. The successful source artifact is never patched.
        for variant in ("message_version", "message_format", "message_shape", "control_shape", "foreign_control"):
            async with fresh_config(base, tmp_path / variant, layout) as invalid:
                invalid = invalid.model_copy(update={"privacy_policy": None})
                source = await MemoryEngine.create(invalid, embedding_provider=runtime.embedder)
                try:
                    if variant.startswith("message"):
                        metadata = copy.deepcopy(message_metadata)
                        envelope = metadata["_prme_chat_message"]
                        if variant == "message_version":
                            envelope["version"] = 2
                        elif variant == "message_format":
                            envelope["format"] = "unknown-v1"
                        else:
                            envelope["message"] = []
                        await source.store("Hello", user_id=owner, metadata=metadata)
                    else:
                        control_session = str(uuid4())
                        foreign = await source.store("Hello", user_id=str(uuid4()), session_id=control_session)
                        await source._append_control_event("", user_id=owner, session_id=control_session, scope=scope,
                            metadata={"_prme_chat_history_control": {"version": 1, "operation": "delete",
                                "target_event_id": "invalid" if variant == "control_shape" else foreign}})
                finally:
                    await source.close()
                job = tmp_path / (variant + "-refused")
                with pytest.raises(PrivacyPortabilityError, match="^privacy_legacy_unclassified$"):
                    await migrate_privacy_pack(invalid, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                        job_path=job, unavailable_legacy_history=True)
                await unpublished(job)

        migrated = await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
            job_path=tmp_path / "migration-job", unavailable_legacy_history=True)
        assert migrated.state == "complete" and migrated.completed == 6
        assert (await _read_snapshot(original)).fingerprint == before.fingerprint
        if original_hashes is not None:
            assert _hashes(original_path) == original_hashes
        engine = await MemoryEngine.create(target, embedding_provider=runtime.embedder, privacy_runtime=runtime)
        artifact = tmp_path / "retained-operator-export.json"
        try:
            with pytest.raises(PrivacyDenied, match="^privacy_unsupported_input$"):
                await engine.require_legacy_history(user_id=owner)
            records = [await engine._privacy.repository.get(identifier, user_id=owner) for identifier in expected]
            expiry = runtime.clock() + timedelta(minutes=1)
            with ExitStack() as private, monkeypatch.context() as selected:
                async with engine._privacy.guard(records[0].snapshot, "disclosure", records=tuple(records), purpose="evidence"):
                    grant = await runtime.protector.authority.grant("diagnostic",
                        tuple((record.evidence.context, record.snapshot) for record in records), expires_at=expiry)
                    wrapper = private.enter_context(custody.unlocked((evidence.ref,), expires_at=expiry))
                selected.setattr(runtime.protector, "registry", public_registry(custody.path, private_wrapper=wrapper))
                for identifier, content in expected.items():
                    assert (await engine.inspect_source(identifier, user_id=owner)).state == "sealed"
                    view = await engine.read_source(identifier, user_id=owner, scope=scope, access=grant)
                    assert str(view.event_id) == identifier and view.content == content
                    record = next(value for value in records if str(value.event_id) == identifier)
                    def selected_guard():
                        return engine._privacy.guard(record.snapshot, "disclosure", records=(record,), purpose="evidence")
                    async with runtime.protector.open_held(record.evidence, grant,
                            original_snapshot=record.snapshot, guard=selected_guard) as plaintext:
                        fields = json.loads(plaintext)
                        original_row = next(row for row in before.tables["events"] if str(row["id"]) == identifier)
                        metadata = original_row["metadata"]
                        metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
                        retained_metadata = {key: value for key, value in (metadata or {}).items()
                            if key not in {"_prme_chat_message", "_prme_chat_history_control"}}
                        assert fields["metadata"] == retained_metadata
                        if identifier == archived_event:
                            assert fields["metadata"]["kept"] == "archive metadata"
            await export_protected_pack(engine, artifact)
            bundle = read_protected_export(artifact)
            assert "lossless_framework_history" in bundle.manifest.omitted
            assert {str(event.id) for event in bundle.events} == set(expected) | control_ids
            assert {str(node.id) for node in bundle.nodes} == {str(row["id"]) for row in before.tables["nodes"]}
            assert {node.content for node in bundle.nodes} == set(expected.values())
            archived = next(node for node in bundle.nodes if node.id == archived_node.id)
            assert archived.lifecycle_state.value == "archived"
            assert archived.id not in {value.node_id for value in bundle.embeddings}
            if layout == "postgres":
                [row] = await engine._privacy.repository._query("SELECT embedding FROM nodes WHERE id=?", (str(archived.id),))
                assert row[0] is None
            else:
                assert not await engine._privacy.repository._query(
                    "SELECT vector_key FROM vector_metadata WHERE node_id=?", (str(archived.id),))
            assert all(not {"_prme_chat_message", "_prme_chat_history_control"}.intersection(node.metadata or {}) for node in bundle.nodes)
            original_operations = {str(row["id"]) for row in before.tables["operations"]}
            assert {str(row.id) for row in bundle.unavailable_history} == original_operations
            assert not control_ids.intersection(str(row.id) for row in bundle.unavailable_history)
            assert all(record.evidence is not None for record in bundle.admissions)
            assert b"PRIVATE KEY" not in artifact.read_bytes()
        finally:
            await engine.close()
        artifact_hash = _hashes(artifact)

        # Closed native backup is deliberately retained outside the current
        # ledger domain. No restore or physical media deletion is claimed here.
        backup = tmp_path / "retained-native-backup"
        if layout == "postgres":
            url = target.database_url.get_secret_value()
            schema = dict(parse_qsl(urlsplit(url).query))["search_path"]
            assert re.fullmatch(r"pv326_[0-9a-f]{32}", schema)
            await _pg_tool(["pg_dump", "--no-owner", "--no-acl", "--inserts", "--schema", schema,
                "--file", str(backup)], _pg_environment(url))
            native_rows = backup.read_bytes()
            assert all(identifier.encode() in native_rows for identifier in expected)
            assert all(content.encode() in native_rows for content in expected.values())
        else:
            backup_config = _local_target(target, backup, single=layout == "pack")
            source_path = Path(target.pack_path) if layout == "pack" else Path(target.db_path).parent
            backup = Path(backup_config.pack_path) if layout == "pack" else backup
            if layout == "pack":
                shutil.copy2(source_path, backup)
            else:
                shutil.copytree(source_path, backup)
            native_rows = await _copied_rows(backup_config)
            assert all(identifier in repr(native_rows) for identifier in expected)
            assert all(content in repr(native_rows) for content in expected.values())
        backup_hashes = _hashes(backup)
        assert backup_hashes
        request = ErasureRequest(request_id=uuid4(), event_ids=tuple(map(UUID, expected)), scope=scope)
        opened = []
        async def fresh():
            value = await MemoryEngine.create(target, embedding_provider=runtime.embedder, privacy_runtime=runtime)
            opened.append(value)
            return value
        monkeypatch.setattr("tests.test_privacy_restore._er08_engine_factory", fresh)
        engine = await fresh()
        try:
            assert (await engine.request_erasure(request, user_id=owner)).state == "blocked_from_access"
            assert (await engine.get_erasure_status(request.request_id, user_id=owner)).logical_live_copies == "pending"
        finally:
            await engine.close()
        cli = ["--owner", owner, "--engine-factory", "tests.test_privacy_restore:_er08_engine_factory"]
        capsys.readouterr()
        await erasure_command(build_parser().parse_args(["privacy", "status", *cli, "--request", str(request.request_id)]))
        assert json.loads(capsys.readouterr().out)["state"] == "blocked_from_access"
        await erasure_command(build_parser().parse_args(["privacy", "purge", *cli, "--budget-ms", "60000"]))
        [purged] = json.loads(capsys.readouterr().out)
        assert purged["state"] == "live_purged" and purged["logical_live_copies"] == "purged"
        assert purged["backup_copies"] == purged["external_copies"] == "not_managed"
        assert purged["failure"] is None and not purged["physical_wipe"]
        engine = await fresh()
        try:
            status = await engine.get_erasure_status(request.request_id, user_id=owner)
            assert status.model_dump(mode="json") == purged
            for identifier in expected:
                with pytest.raises(PrivacyDenied):
                    await engine.read_source(identifier, user_id=owner, scope=scope, access=grant)
            assert not await engine._privacy.repository._query("SELECT id FROM nodes", ())
        finally:
            await engine.close()
        assert all(value._closed for value in opened)
        assert _hashes(artifact) == artifact_hash and read_protected_export(artifact) == bundle
        assert _hashes(backup) == backup_hashes
        assert (backup.read_bytes() if layout == "postgres" else await _copied_rows(backup_config)) == native_rows
        assert (await _read_snapshot(original)).fingerprint == before.fingerprint
        if original_hashes is not None:
            assert _hashes(original_path) == original_hashes
        (tmp_path / "ER08-evidence.json").write_text(json.dumps({"layout": layout,
            "source_fingerprint": before.fingerprint, "events": len(before.tables["events"]), "nodes": len(before.tables["nodes"]),
            "erased_events": sorted(expected), "retained_controls": sorted(control_ids), "status": purged,
            "backup_hashes": backup_hashes, "operator_export_hashes": artifact_hash,
            "unavailable_features": plan.unavailable_features, "omitted": bundle.manifest.omitted}, indent=2) + "\n")


async def _assert_survivor(engine, owner, erased_id, survivor_id, survives, evidence, *,
                           retrieve=None, unavailable_ids=()):
    assert await engine._graph_store.get_node(erased_id, include_superseded=True) is None
    assert (await engine._graph_store.get_node(survivor_id)).content == "Mara prefers tea."
    assert (await engine.inspect_source(survives, user_id=owner)).state == "sealed"
    assert (await engine._privacy.repository.get(survives, user_id=owner)).evidence == evidence
    # Public usability is separate from the retained storage/availability checks.
    retrieve = retrieve or engine.retrieve
    response = await asyncio.wait_for(retrieve("Mara prefers tea.", user_id=owner,
        scope=engine._privacy.default.scope, reference_time=engine._privacy.require_runtime().clock(),
        include_cross_scope=False), 20)
    result_ids = {str(item.node.id) for item in response.results}
    packed_ids = {str(item.node.id) for section in response.bundle.sections.values() for item in section}
    assert survivor_id in result_ids and survivor_id in packed_ids
    assert "Mara prefers tea." in response.bundle.render()
    denied_ids = {erased_id, *unavailable_ids}
    assert not denied_ids.intersection(result_ids | packed_ids)
    assert not denied_ids.intersection(str(item.node.id) for item in response.cross_scope_hints)
    assert "Mara likes oranges." not in response.bundle.render()
    assert "Mara paints murals." not in response.bundle.render()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM02_exported_context_and_wrapped_key_refusals(tmp_path, layout, analyzer, native, monkeypatch):
    """Artifact context refusal and real unwrap after metadata-only restoration."""
    from prme.privacy.crypto import CryptoProtector, canonical, decode_envelope
    from prme.privacy.local_keys import public_registry
    from prme.storage.privacy_export import read_protected_export

    engine, runtime, policy, custody, evidence, _ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    source_only = ("EM02_EXPORTED_SOURCE_FIRST", "EM02_EXPORTED_SOURCE_SECOND")
    restored = None
    try:
        identifiers = [await engine.store(f"Mara likes tea. {marker}.",
            retrieval_content="Mara likes tea.", user_id=owner) for marker in source_only]
        artifact = tmp_path / "original-export.json"
        await export_protected_pack(engine, artifact)
        original = artifact.read_bytes()
        original_hash = hashlib.sha256(original).hexdigest()
        bundle = read_protected_export(artifact)
        indexes = [next(i for i, record in enumerate(bundle.admissions) if str(record.event_id) == identifier)
                   for identifier in identifiers]
        first, second = indexes
        config = engine._config
        await engine.close()
        engine = None

        lookups = []
        get_password = native.get_password
        def recorded_lookup(service, username):
            lookups.append(username)
            return get_password(service, username)
        monkeypatch.setattr(native, "get_password", recorded_lookup)
        native_before = dict(native.values)
        registry_before = custody.path.read_bytes()
        async with fresh_config(config, tmp_path / "restored", layout, restore=True) as target:
            async def assert_empty():
                if layout == "postgres":
                    import asyncpg
                    conn = await asyncpg.connect(target.database_url.get_secret_value())
                    try:
                        assert not await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")
                    finally:
                        await conn.close()
                else:
                    assert not Path(target.pack_path or target.db_path).exists()
                    assert not (tmp_path / "restored").exists()
                    assert not list(tmp_path.glob(".*.restore"))

            for variant in ("source_swap", "owner", "scope", "purpose", "key_version"):
                body = json.loads(original)
                payload = body["admissions"][first]["evidence"]
                if variant == "source_swap":
                    body["admissions"][first]["evidence"] = body["admissions"][second]["evidence"]
                elif variant == "owner":
                    payload["context"]["owner_id"] = str(uuid4())
                elif variant == "scope":
                    payload["context"]["scope"] = "project" if policy.scope.value != "project" else "personal"
                elif variant == "purpose":
                    payload["context"]["purpose"] = "processing"
                else:
                    payload["key_version"] = str(uuid4())
                altered = tmp_path / (variant + ".json")
                altered.write_bytes(canonical(body))
                with pytest.raises((PrivacyDenied, PrivacyPortabilityError)):
                    await restore_protected_export(altered, target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
                await assert_empty()
                assert not lookups and native.values == native_before
                assert custody.path.read_bytes() == registry_before

            # The bounded public parser verifies metadata, never opaque
            # ciphertext authenticity. The private diagnostic operation must
            # reject a DEK wrapped for a different immutable payload context.
            body = json.loads(original)
            wrong = json.loads(body["admissions"][first]["evidence"]["envelope"])
            other = json.loads(body["admissions"][second]["evidence"]["envelope"])
            wrong["wrapped_key"] = other["wrapped_key"]
            body["admissions"][first]["evidence"]["envelope"] = canonical(wrong).decode("ascii")
            swapped = tmp_path / "wrapped-key-swap.json"
            swapped.write_bytes(canonical(body))
            parsed = read_protected_export(swapped)
            assert parsed.admissions[second] == bundle.admissions[second]
            await restore_protected_export(swapped, target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            assert not lookups and native.values == native_before
            restored = await MemoryEngine.create(target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            assert not lookups
            for index, identifier in enumerate(identifiers):
                record = await restored._privacy.repository.get(identifier, user_id=owner)
                ref = decode_envelope(record.evidence, record.snapshot).wrapper.ref
                assert ref == evidence.ref
                expiry = runtime.clock() + timedelta(minutes=1)
                def selected_guard():
                    return restored._privacy.guard(record.snapshot, "disclosure", records=(record,), purpose="evidence")
                with ExitStack() as private:
                    async with selected_guard():
                        grant = await runtime.protector.authority.grant("diagnostic",
                            ((record.evidence.context, record.snapshot),), expires_at=expiry)
                        wrapper = private.enter_context(custody.unlocked((ref,), expires_at=expiry))
                    protector = CryptoProtector(public_registry(custody.path, private_wrapper=wrapper),
                        authority=runtime.protector.authority)
                    if index == 0:
                        with pytest.raises(PrivacyDenied, match="^privacy_capability_unavailable$"):
                            async with protector.open_held(record.evidence, grant,
                                original_snapshot=record.snapshot, guard=selected_guard):
                                pytest.fail("swapped wrapped key disclosed plaintext")
                    else:
                        async with protector.open_held(record.evidence, grant,
                            original_snapshot=record.snapshot, guard=selected_guard) as plaintext:
                            assert source_only[1].encode() in plaintext
                            assert source_only[0].encode() not in plaintext
                    # The public API receives the same finite custody/grant;
                    # no private capability is installed beyond this lease.
                    with monkeypatch.context() as selected:
                        selected.setattr(runtime.protector, "registry", protector.registry)
                        if index == 0:
                            with pytest.raises(PrivacyDenied, match="^privacy_capability_unavailable$"):
                                await asyncio.wait_for(restored.read_source(identifier,
                                    user_id=owner, scope=policy.scope, access=grant), 20)
                        else:
                            public = await asyncio.wait_for(restored.read_source(identifier,
                                user_id=owner, scope=policy.scope, access=grant), 20)
                            assert str(public.event_id) == identifier
                            assert source_only[1] in public.content and source_only[0] not in public.content
                assert not wrapper._private_keys
            assert lookups and all(name.startswith(str(evidence.ref.key_version) + ":") for name in lookups)
            assert native.values == native_before
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == original_hash
            assert all(marker.encode() not in original for marker in source_only)
            assert b"PRIVATE KEY" not in original
            await restored.close()
            restored = None
    finally:
        if restored is not None:
            await restored.close()
        if engine is not None:
            await engine.close()


async def _assert_eligible_restored_work(artifact, config, path, layout, runtime, custody, processing,
                                        owner, ids, originals, exported_work, native):
    """Publish the untouched job from the same artifact before its expiry arm."""
    from prme.privacy.keys import StoredProcessingAuthorizer
    from tests.privacy_fakes import FakeEmbedding, FakeExtractor
    from tests.privacy_journal_support import processing_keys

    original = originals[ids[1]]
    observed = {}

    class ObservedExtractor(FakeExtractor):
        async def extract(self, source, snapshot, *, guard):
            observed["source"], observed["snapshot"] = source, snapshot
            observed["claim"] = await work.status(ids[1], user_id=owner)
            observed["work_order"] = await work.work_order(user_id=owner, event_id=ids[1])
            return await super().extract(source, snapshot, guard=guard)

    positive = replace(runtime, embedder=FakeEmbedding(), extractor=ObservedExtractor())
    positive.extractor.result = {
        "entities": [{"name": "Mara", "entity_type": "person"}],
        "facts": [{"subject": "Mara", "predicate": "likes", "object": "tea", "polarity": "positive",
                   "evidence_quote": "Mara likes tea. RESTORED_PENDING_1."}],
        "relationships": [], "summary": None,
    }
    original_bytes = artifact.read_bytes()
    async with fresh_config(config, path, layout, restore=True) as target:
        await restore_protected_export(artifact, target, privacy_runtime=positive, embedding_provider=positive.embedder)
        async with MemoryEngine.open(target, privacy_runtime=positive, embedding_provider=positive.embedder) as restored:
            work = restored._event_store.extraction_work
            positive.protector.authority.processing = StoredProcessingAuthorizer(restored._privacy.repository, work)
            assert await restored._privacy.repository.get(ids[1], user_id=owner) == original
            waiting = await work.status(ids[1], user_id=owner)
            assert waiting.status == "pending" and waiting.generation == exported_work[ids[1]].generation + 1
            assert waiting.attempts == exported_work[ids[1]].attempts and waiting.lease_expires_at is None
            assert await work.work_order(user_id=owner, event_id=ids[1]) == exported_work[ids[1]].work_order

            # The consumed reservation is not made reusable by restoring it.
            first = originals[ids[0]]
            cancelled_claim = await work.claim(user_id=owner, event_id=ids[0], lease_seconds=86400)
            assert cancelled_claim.lease_expires_at == first.snapshot.processing_deadline
            async with restored._privacy.guard(first.snapshot, "admission", processing=True, records=(first,)):
                assert await restored._privacy.processing.terminate(cancelled_claim, state="cancelled")
            cancelled = await work.status(ids[0], user_id=owner)
            assert cancelled.status == "failed" and cancelled.last_error == "privacy_cancelled"
            assert (await restored._privacy.repository.get(ids[0], user_id=owner)).processing_state == "cancelled"
            assert not await work.retry(ids[0], user_id=owner)

            with processing_keys(positive, custody, processing):
                result = await asyncio.wait_for(restored.process_extractions(user_id=owner, limit=1), 20)
            assert result.processed == 1 and result.pending == 0 and result.failed == 1  # Explicitly cancelled first job.
            assert len(positive.extractor.sends) == 1
            sent = json.loads(positive.extractor.sends[0])
            assert sent["content"] == "Mara likes tea. RESTORED_PENDING_1."
            assert "RESTORED_PENDING_0" not in positive.extractor.sends[0]
            claim = observed["claim"]
            assert claim.status == "running" and claim.generation == waiting.generation + 1
            assert claim.attempts == waiting.attempts + 1
            assert observed["work_order"] == exported_work[ids[1]].work_order
            assert claim.lease_expires_at == original.snapshot.processing_deadline
            source = observed["source"]
            assert (source.source_id, source.representation_id) == (original.source_id, original.representation_id)
            assert observed["snapshot"] == original.snapshot and source.source_snapshot == original.snapshot
            assert source.continuation is not None and source.continuation.input_bytes > 0
            finished = await restored._privacy.repository.get(ids[1], user_id=owner)
            assert finished.snapshot == original.snapshot and finished.reference() == original.reference()
            assert finished.evidence == original.evidence and finished.processing is None
            assert finished.processing_state == "complete" and finished.processing_attempt == 0
            completed = await work.status(ids[1], user_id=owner)
            assert completed.status == "complete" and completed.generation == claim.generation
            assert completed.attempts == claim.attempts
            assert await work.work_order(user_id=owner, event_id=ids[1]) == exported_work[ids[1]].work_order
            plan = await restored._event_store.get_derivation_plan(ids[1], user_id=owner)
            receipt = await restored._event_store.get_derivation_receipt(ids[1], user_id=owner)
            assert receipt.event_id == original.event_id and receipt.plan_id == plan.id
            assert receipt.plan_checksum == plan.checksum and receipt.generation == claim.generation
            assert set(receipt.node_ids) == {node.id for node in plan.nodes}
            assert all(node.evidence_refs == [original.event_id] for node in plan.nodes)
            assert await restored._event_store.get_derivation_receipt(ids[0], user_id=owner) is None
            fact = next(node for node in plan.nodes if node.content == "Mara likes tea.")
            private_reads = native.reads
            response = await asyncio.wait_for(restored.retrieve("Mara likes tea.", user_id=owner,
                scope=original.snapshot.policy.scope, reference_time=positive.clock(), include_cross_scope=False), 20)
            assert fact.id in {item.node.id for item in response.results}
            assert fact.id in {item.node.id for section in response.bundle.sections.values() for item in section}
            assert "Mara likes tea." in response.bundle.render()
            assert "RESTORED_PENDING_" not in response.model_dump_json()
            assert native.reads == private_reads
            sends = len(positive.extractor.sends), len(positive.embedder.sends)
            assert (await restored.process_extractions(user_id=owner, limit=1)).processed == 0
            assert sends == (len(positive.extractor.sends), len(positive.embedder.sends))
            assert await restored._event_store.get_derivation_receipt(ids[1], user_id=owner) == receipt
    assert artifact.read_bytes() == original_bytes
    return observed["source"].continuation


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_restored_pending_work_keeps_deadline_budget_and_claim_fences(tmp_path, layout, analyzer, native):
    """Original denial/expiry arm plus eligible work from the same export."""
    from prme.privacy.admission import AdmissionCoordinator
    from prme.privacy.capabilities import ProcessingAccess
    from prme.privacy.journals import load_checkpoint
    from prme.privacy.keys import StoredProcessingAuthorizer
    from prme.privacy.sanitizer import DETECTOR_SECONDS, MAX_INPUT_BYTES, MAX_LEAVES, restore_budget
    from prme.storage.privacy_export import read_protected_export
    from tests.privacy_journal_support import processing_keys

    engine, runtime, policy, custody, _, processing = await journal_engine(
        tmp_path, layout, analyzer, processing_ceiling_seconds=180)
    owner = str(policy.owner_id)
    restored = None
    try:
        ids = [await engine.ingest(f"Mara likes tea. RESTORED_PENDING_{index}.", user_id=owner) for index in range(2)]
        work = engine._event_store.extraction_work
        original_claim = await work.claim(user_id=owner, event_id=ids[0], lease_seconds=86400)
        first = await engine._privacy.repository.get(ids[0], user_id=owner)
        assert original_claim.lease_expires_at == first.snapshot.processing_deadline
        async with engine._privacy.guard(first.snapshot, "admission", processing=True, records=(first,)):
            consumed = await engine._privacy.processing.begin(original_claim)
        assert consumed.processing_attempt == original_claim.generation
        assert await work.claim(user_id=owner, event_id=ids[1]) is None  # Retain queue order.
        artifact = tmp_path / "pending-work.json"
        await export_protected_pack(engine, artifact)
        original_bytes = artifact.read_bytes()
        bundle = read_protected_export(artifact)
        originals = {str(row.event_id): row for row in bundle.admissions}
        assert originals[ids[0]] == consumed and originals[ids[1]].processing_attempt == 0
        exported_work = {str(row.event_id): row for row in bundle.work if row.kind == "extraction"}
        assert [exported_work[event].status for event in ids] == ["running", "pending"]
        assert exported_work[ids[0]].work_order < exported_work[ids[1]].work_order
        config = engine._config
        await engine.close()
        engine = None
        published_continuation = await _assert_eligible_restored_work(
            artifact, config, tmp_path / "restored-eligible-work", layout, runtime, custody, processing,
            owner, ids, originals, exported_work, native)
        native_reads = native.reads
        async with fresh_config(config, tmp_path / "restored-work", layout, restore=True) as target:
            await restore_protected_export(artifact, target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            restored = await MemoryEngine.create(target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            roundtrip = tmp_path / "restored-pending-work.json"
            await export_protected_pack(restored, roundtrip)
            after = read_protected_export(roundtrip)
            assert after.admissions == bundle.admissions
            expected = tuple(row.model_copy(update={"generation": row.generation + 1,
                "lease_expires_at": None, "status": "pending" if row.status == "running" else row.status})
                if row.kind == "extraction" else row for row in bundle.work)
            assert after.work == expected
            assert native.reads == native_reads
            work = restored._event_store.extraction_work
            runtime.protector.authority.processing = StoredProcessingAuthorizer(restored._privacy.repository, work)
            with pytest.raises(PrivacyDenied, match="^privacy_processing_unavailable$"):
                await restored._privacy.processing.begin(original_claim)
            replacement = await work.claim(user_id=owner, event_id=ids[0], lease_seconds=86400)
            assert replacement.generation == original_claim.generation + 2
            assert replacement.attempts == original_claim.attempts + 1
            assert replacement.lease_expires_at == consumed.snapshot.processing_deadline
            with pytest.raises(PrivacyDenied, match="^privacy_processing_unavailable$"):
                async with restored._privacy.guard(consumed.snapshot, "admission", processing=True, records=(consumed,)):
                    await restored._privacy.processing.begin(replacement)
            assert await restored._privacy.repository.get(ids[0], user_id=owner) == consumed
            async with restored._privacy.guard(consumed.snapshot, "admission", processing=True, records=(consumed,)):
                assert await restored._privacy.processing.terminate(replacement, state="cancelled")
            assert not await work.retry(ids[0], user_id=owner)

            original = originals[ids[1]]
            claim = await work.claim(user_id=owner, event_id=ids[1], lease_seconds=86400)
            assert claim.generation == exported_work[ids[1]].generation + 2
            assert claim.attempts == exported_work[ids[1]].attempts + 1
            assert claim.lease_expires_at == original.snapshot.processing_deadline
            async with restored._privacy.guard(original.snapshot, "admission", processing=True, records=(original,)):
                reserved = await restored._privacy.processing.begin(claim)
            assert reserved.processing == original.processing and reserved.snapshot == original.snapshot
            access = ProcessingAccess(work_id=original.event_id, work_generation=claim.generation,
                lease_deadline=claim.lease_expires_at, processing_deadline=original.snapshot.processing_deadline,
                claimed_event_ids=(original.event_id,))
            def selected_guard():
                return restored._privacy.guard(original.snapshot, "disclosure", processing=True, records=(original,))
            with processing_keys(runtime, custody, processing):
                async with runtime.protector.open_held(original.processing, access,
                    original_snapshot=original.snapshot, guard=selected_guard) as raw:
                    source_bytes, checkpoint = load_checkpoint(raw)
                    source = AdmissionCoordinator.load_source(source_bytes)
                    assert checkpoint is None
                    assert (source.source_id, source.representation_id) == (original.source_id, original.representation_id)
                    continuation = source.continuation
                    assert 0 < continuation.input_bytes < MAX_INPUT_BYTES
                    assert 0 < continuation.leaves < MAX_LEAVES
                    assert 0 <= continuation.detector_nanoseconds < int(DETECTOR_SECONDS * 1e9)
                    budget = restore_budget(continuation)
                    assert budget.operation_id == continuation.operation_id
                    assert budget.state.continuation() == continuation
                # No subsequent claim or restore grants a new original lifetime.
                runtime.clock.now = original.snapshot.processing_deadline
                assert await restored._privacy.processing.expire(user_id=owner) == 1
                with pytest.raises(PrivacyDenied):
                    await runtime.protector.open(original.processing, access,
                        original_snapshot=original.snapshot, guard=selected_guard)
            ended = await restored._privacy.repository.get(ids[1], user_id=owner)
            assert ended.snapshot == original.snapshot and ended.processing is None
            assert ended.processing_state == "expired"
            assert await work.claim(user_id=owner, event_id=ids[1]) is None
            assert not await work.retry(ids[1], user_id=owner)
            assert not await work.renew(claim, lease_seconds=86400)
            assert not runtime.extractor.sends and not runtime.embedder.sends
            assert artifact.read_bytes() == original_bytes
            await restored.close()
            restored = None
        assert published_continuation == continuation
    finally:
        if restored is not None:
            await restored.close()
        if engine is not None:
            await engine.close()


async def test_bound_postgres_restore_current_catalog_and_recovery_lease(tmp_path, analyzer, native, monkeypatch):
    """One real PG variant; hosted capability composition/public reads stay separate."""
    import asyncpg
    from prme import MemoryWorkspace
    from prme.privacy.portability import PostgresRestoreTarget
    from prme.storage import privacy_export
    from prme.storage.pg.namespace import namespace_schema
    from tests.privacy_portability_support import HeldLocalLedger, PgRestoreAuthority, PgRestoreCapability

    template, runtime, policy, custody, evidence, _ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    primary_url, quarantine_url = os.environ["PRME_TEST_DATABASE_URL"], await restore_database_url()
    primary_env = _pg_environment(primary_url)
    quarantine_env = _pg_environment(quarantine_url)
    primary = await asyncpg.connect(primary_url)
    quarantine = await asyncpg.connect(quarantine_url)
    control_pool = await asyncpg.create_pool(primary_url, min_size=1, max_size=3)
    namespace_id, other_id = policy.project_id, uuid4()
    schema, other_schema = namespace_schema(namespace_id), namespace_schema(other_id)
    workspace_name, control_schema = "pv326_recovery_" + uuid4().hex, "pv326_control_" + uuid4().hex
    table = f'"{control_schema}".recovery_control'
    config = type(base).model_validate({**base.model_dump(), "database_url": primary_url})
    quarantine_config = type(base).model_validate({**base.model_dump(), "database_url": quarantine_url})
    task = None
    try:
        await primary.execute(f'CREATE SCHEMA "{control_schema}"')
        await primary.execute(f"CREATE TABLE {table}(namespace_id UUID PRIMARY KEY,policy TEXT NOT NULL,"
            "state TEXT NOT NULL,operation_id UUID,artifact_hash TEXT,sentinel TEXT NOT NULL)")
        await primary.execute(f"INSERT INTO {table} VALUES($1,$2,'active',NULL,NULL,'current-control')",
            namespace_id, policy.model_dump_json())
        ledger = HeldLocalLedger(runtime.erasure_ledger)
        authority = PgRestoreAuthority(control_pool, table, ledger)
        runtime = replace(runtime, erasure_ledger=ledger, authority=authority)
        def opened(current_config, *, initialize=True):
            return MemoryWorkspace.open_postgres(current_config, name=workspace_name, initialize=initialize,
                embedding_provider=runtime.embedder, privacy_runtime_factory=lambda _: runtime)
        owner = str(policy.owner_id)
        async with opened(config) as workspace:
            # Explicit fixture bootstrap uses the real current catalog. Its ID
            # is the already provisioned privacy/key boundary, never a path.
            await primary.execute("INSERT INTO prme_workspace.namespaces(workspace,name,id) VALUES($1,'A',$2)", workspace_name, namespace_id)
            await primary.execute("INSERT INTO prme_workspace.namespaces(workspace,name,id,initialized) VALUES($1,'other',$2,true)", workspace_name, other_id)
            await primary.execute(f'CREATE SCHEMA "{other_schema}"')
            await primary.execute(f'CREATE TABLE "{other_schema}".sentinel(value TEXT NOT NULL)')
            await primary.execute(f'INSERT INTO "{other_schema}".sentinel VALUES(\'other-project\')')
            async with workspace.namespace("A", create=False) as memory:
                erased = await memory.store("Mara likes oranges.", user_id=owner)
                survives = await memory.store("Mara prefers tea.", user_id=owner)
                engine = memory._engine
                rows = await engine._privacy.repository._query("SELECT id,content FROM nodes", ())
                erased_id = str(next(row[0] for row in rows if row[1] == "Mara likes oranges."))
                survivor_id = str(next(row[0] for row in rows if row[1] == "Mara prefers tea."))
                original_evidence = (await engine._privacy.repository.get(survives, user_id=owner)).evidence
        # All source leases are closed before the real native archive.
        archive = tmp_path / "namespace.dump"
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        await _pg_tool(["pg_dump", "--no-owner", "--no-acl", "--format=custom", "--schema", schema, "--file", str(archive)], primary_env)
        archive_hashes = _hashes(archive)
        async with opened(quarantine_config):
            # A quarantine fixture registry binds the saved UUID. No public
            # authorization/control table is copied from the archive.
            await quarantine.execute("INSERT INTO prme_workspace.namespaces(workspace,name,id,initialized) VALUES($1,'A',$2,true)", workspace_name, namespace_id)
        await _pg_tool(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", _database(quarantine_url), str(archive)], quarantine_env)
        artifact = tmp_path / "quarantined-namespace.json"
        async with opened(quarantine_config, initialize=False) as workspace:
            async with workspace.namespace("A", create=False) as memory:
                manifest = await export_protected_pack(memory._engine, artifact)
        assert manifest.format.store_id == namespace_id
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        async with opened(config, initialize=False) as workspace:
            async with workspace.namespace("A", create=False) as memory:
                await memory.request_erasure(ErasureRequest(request_id=uuid4(), scope=policy.scope,
                    event_ids=(UUID(erased),)), user_id=owner)
        async with ledger.guard():
            current_head = await ledger.current_head(namespace_id)
        assert current_head.sequence > manifest.ledger_checkpoint.sequence
        # Explicit task-owned loss simulation, after the engine/leases closed.
        # The restoration API itself never drops or replaces this schema.
        await primary.execute(f'DROP SCHEMA "{schema}" CASCADE')
        target = PostgresRestoreTarget(workspace=workspace_name, namespace="A", namespace_id=namespace_id,
            operation_id=uuid4(), artifact_sha256=artifact_hash)
        capability = PgRestoreCapability(authority, target)
        await primary.execute(f"UPDATE {table} SET state='recovering',operation_id=$1,artifact_hash=$2",
            target.operation_id, artifact_hash)
        catalog_before = await primary.fetch("SELECT * FROM prme_workspace.namespaces WHERE workspace=$1 ORDER BY name", workspace_name)
        control_before = await primary.fetch(f"SELECT * FROM {table}")

        async def restore(selected=target, permit=capability):
            return await restore_protected_export(artifact, config, privacy_runtime=runtime,
                embedding_provider=runtime.embedder, postgres_target=selected, recovery=permit)
        with pytest.raises(PrivacyDenied, match="^privacy_capability_unavailable$"):
            await restore(permit=None)
        with pytest.raises(PrivacyDenied, match="^privacy_boundary_mismatch$"):
            await restore(target.model_copy(update={"namespace_id": other_id}))
        wrong_hash = target.model_copy(update={"artifact_sha256": "0" * 64})
        with pytest.raises(PrivacyPortabilityError, match="^privacy_source_changed$"):
            await restore(wrong_hash, PgRestoreCapability(authority, wrong_hash))
        assert not await primary.fetchval("SELECT oid FROM pg_namespace WHERE nspname=$1", schema)

        # An unrelated occupied target must survive unchanged even with a
        # currently authorized recovery operation.
        await primary.execute(f'CREATE SCHEMA "{schema}"')
        await primary.execute(f'CREATE TABLE "{schema}".occupied(value TEXT)')
        await primary.execute(f'INSERT INTO "{schema}".occupied VALUES(\'retained\')')
        with pytest.raises(PrivacyPortabilityError, match="^privacy_destination_occupied$"):
            await restore()
        assert await primary.fetchval(f'SELECT value FROM "{schema}".occupied') == "retained"
        await primary.execute(f'DROP SCHEMA "{schema}" CASCADE')
        load = privacy_export._load_export
        async def fail_load(*args, **kwargs):
            await load(*args, **kwargs)
            raise RuntimeError("synthetic bound restore interruption")
        with monkeypatch.context() as patch:
            patch.setattr(privacy_export, "_load_export", fail_load)
            with pytest.raises(RuntimeError, match="^synthetic bound restore interruption$"):
                await restore()
        assert not await primary.fetchval("SELECT oid FROM pg_namespace WHERE nspname=$1", schema)
        assert await primary.fetch(f"SELECT * FROM {table}") == control_before

        entered, release = asyncio.Event(), asyncio.Event()
        restore_database = privacy_export._restore_database
        async def paused_database(*args, **kwargs):
            result = await restore_database(*args, **kwargs)
            async def inherited_child():
                with pytest.raises(PrivacyDenied):
                    async with ledger.guard():
                        pytest.fail("child inherited the recovery lease")
            await asyncio.create_task(inherited_child())
            entered.set()
            await release.wait()
            return result
        capability.fail_acknowledgement = True
        with monkeypatch.context() as patch:
            patch.setattr(privacy_export, "_restore_database", paused_database)
            task = asyncio.create_task(restore())
            await asyncio.wait_for(entered.wait(), 10)
            assert not await primary.fetchval("SELECT oid FROM pg_namespace WHERE nspname=$1", schema)
            # A real conflicting transaction cannot alter current recovery
            # authority while the engine's load/publication transaction runs.
            async with primary.transaction():
                await primary.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(asyncpg.LockNotAvailableError):
                    await primary.execute(f"UPDATE {table} SET state='active'")
            release.set()
            with pytest.raises(RuntimeError, match="^synthetic lost restore acknowledgement$"):
                await asyncio.wait_for(task, 10)
        assert await primary.fetchval("SELECT oid FROM pg_namespace WHERE nspname=$1", schema)
        receipt_before = await primary.fetchval(f'SELECT body FROM "{schema}".privacy_restore_receipt')
        rows_before = await primary.fetch(f'SELECT * FROM "{schema}".nodes ORDER BY id')
        assert [str(row["id"]) for row in rows_before] == [survivor_id]
        async def no_reload(*args, **kwargs):
            pytest.fail("completed operation reloaded snapshot content")
        with monkeypatch.context() as patch:
            patch.setattr(privacy_export, "_load_export", no_reload)
            result = await restore()
        assert result["store_id"] == str(namespace_id) and result["operation_id"] == str(target.operation_id)
        assert result["artifact_sha256"] == artifact_hash and result["ledger_sequence"] == current_head.sequence
        assert await primary.fetchval(f'SELECT body FROM "{schema}".privacy_restore_receipt') == receipt_before
        assert await primary.fetch(f'SELECT * FROM "{schema}".nodes ORDER BY id') == rows_before
        # New operation identity never turns an occupied destination into an
        # authorized replacement, even when the caller owns its new lease.
        other_target = target.model_copy(update={"operation_id": uuid4()})
        await primary.execute(f"UPDATE {table} SET operation_id=$1", other_target.operation_id)
        with pytest.raises(PrivacyPortabilityError, match="^privacy_destination_occupied$"):
            await restore(other_target, PgRestoreCapability(authority, other_target))
        await primary.execute(f"UPDATE {table} SET operation_id=$1", target.operation_id)
        assert await primary.fetch(f"SELECT * FROM {table}") == control_before
        assert await primary.fetch("SELECT * FROM prme_workspace.namespaces WHERE workspace=$1 ORDER BY name", workspace_name) == catalog_before
        assert await primary.fetchval(f'SELECT value FROM "{other_schema}".sentinel') == "other-project"
        # The operator completes current recovery only after the engine commit;
        # ordinary startup uses the existing public workspace API.
        await primary.execute(f"UPDATE {table} SET state='active'")
        async with opened(config, initialize=False) as workspace:
            async with workspace.namespace("A", create=False) as memory:
                await _assert_survivor(memory._engine, owner, erased_id, survivor_id, survives, original_evidence,
                    retrieve=memory.retrieve)
        assert {entry.version: entry for entry in custody.inventory()}[evidence.ref.key_version].managed_lineages == (namespace_id,)
        assert _hashes(archive) == archive_hashes and hashlib.sha256(artifact.read_bytes()).hexdigest() == artifact_hash
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await control_pool.close()
        for conn in (primary, quarantine):
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            if conn is primary:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{other_schema}" CASCADE')
                await conn.execute(f'DROP SCHEMA IF EXISTS "{control_schema}" CASCADE')
            if await conn.fetchval("SELECT pg_catalog.to_regclass('prme_workspace.namespaces')"):
                await conn.execute("DELETE FROM prme_workspace.namespaces WHERE workspace=$1", workspace_name)
            await conn.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_same_lineage_restore_applies_current_ledger_before_recovery(tmp_path, layout, analyzer, native):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    restored, native_restored, shared = None, None, None
    stack = AsyncExitStack()
    try:
        erased = await engine.store("Mara likes oranges.", user_id=owner)
        survives = await engine.store("Mara prefers tea.", user_id=owner)
        rows = await engine._privacy.repository._query("SELECT id,content FROM nodes ORDER BY id", ())
        survivor_id = str(next(row[0] for row in rows if row[1] == "Mara prefers tea."))
        erased_id = str(next(row[0] for row in rows if row[1] == "Mara likes oranges."))
        artifact = tmp_path / "snapshot.json"
        exported = await export_protected_pack(engine, artifact)
        original = hashlib.sha256(artifact.read_bytes()).hexdigest()
        evidence = (await engine._privacy.repository.get(survives, user_id=owner)).evidence
        source_config = engine._config
        await engine.close()
        native_config, backup, backup_hashes = await stack.enter_async_context(_native_snapshot(source_config, tmp_path, layout))
        engine = await MemoryEngine.create(source_config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        # Same lineage, deliberately different live documents after the backup.
        # Native cleanup must never union this later row into restored copies.
        await engine.store("Mara paints murals.", user_id=owner)
        source_only_id = str((await engine._privacy.repository._query("SELECT id FROM nodes WHERE content=?", ("Mara paints murals.",)))[0][0])
        old_ledger_path = tmp_path / "pre-erasure-independent-ledger.sqlite"
        async with runtime.erasure_ledger.guard():
            with closing(sqlite3.connect(runtime.erasure_ledger.path.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(old_ledger_path)) as destination:
                    source.backup(destination)
        old_ledger_path.chmod(0o600)
        old_runtime = replace(runtime, erasure_ledger=LocalErasureLedger(old_ledger_path))
        await engine.request_erasure(ErasureRequest(request_id=uuid4(), scope=policy.scope, event_ids=(UUID(erased),)), user_id=owner)
        await engine.process_erasures(user_id=owner)
        current_artifact = tmp_path / "current-checkpoint.json"
        current = await export_protected_pack(engine, current_artifact)
        assert current.ledger_checkpoint.sequence > exported.ledger_checkpoint.sequence
        if layout == "postgres":
            config = await stack.enter_async_context(_postgres_target(source_config))
        else:
            config = _local_target(source_config, tmp_path / "restored", single=layout == "pack")

        foreign = LocalErasureLedger.create(tmp_path / "foreign-independent-ledger.sqlite")
        async with foreign.guard():
            await foreign.register_store(exported.format.store_id, runtime.project_id)
        # A serialized checkpoint inside the snapshot is not an independent
        # authority. Missing and foreign providers must not serve native copies
        # or publish a logical restoration. No exact exception text is the AC.
        copied_before = await _copied_rows(native_config)
        for denied_runtime in (replace(runtime, erasure_ledger=None), replace(runtime, erasure_ledger=foreign)):
            with pytest.raises(PrivacyDenied):
                await restore_protected_export(artifact, config, privacy_runtime=denied_runtime, embedding_provider=runtime.embedder)
            with pytest.raises(PrivacyDenied):
                await MemoryEngine.create(native_config, privacy_runtime=denied_runtime, embedding_provider=runtime.embedder)
            assert await _copied_rows(native_config) == copied_before
        # This is a real newer applied checkpoint against the earlier authority,
        # not a claim to detect rolling back BOTH a snapshot and its ledger.
        with pytest.raises(PrivacyDenied, match="^privacy_ledger_stale$"):
            await restore_protected_export(current_artifact, config, privacy_runtime=old_runtime, embedding_provider=runtime.embedder)
        ambiguous = tmp_path / "ambiguous-lineage.json"
        body = json.loads(artifact.read_bytes())
        body["manifest"]["format"]["store_id"] = str(uuid4())
        ambiguous.write_text(json.dumps(body))
        with pytest.raises(PrivacyPortabilityError, match="^privacy_export_invalid$"):
            await restore_protected_export(ambiguous, config, privacy_runtime=runtime, embedding_provider=runtime.embedder)

        result = await restore_protected_export(artifact, config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        assert result["store_id"] == str(exported.format.store_id)
        assert result["ledger_sequence"] > exported.ledger_checkpoint.sequence
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == original
        restored = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        await _assert_survivor(restored, owner, erased_id, survivor_id, survives, evidence,
            unavailable_ids=(source_only_id,))
        assert await restored._graph_store.get_node(source_only_id) is None
        if layout != "postgres":
            from prme.storage.erasure_indexes import _documents
            shared = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            if layout == "pack":
                assert shared._lexical_index is restored._lexical_index
            else:
                assert shared._lexical_index is not restored._lexical_index
                assert shared._lexical_index._index_path == restored._lexical_index._index_path
            assert shared._lexical_index._index is restored._lexical_index._index
            assert engine._lexical_index._index is not restored._lexical_index._index
            assert set(_documents(engine._lexical_index, set())) == {survivor_id, source_only_id}
            assert set(_documents(restored._lexical_index, set())) == {survivor_id}
            assert set(_documents(shared._lexical_index, set())) == {survivor_id}
            for handle in (engine, restored, shared):
                assert not await handle._privacy.repository._query("SELECT request_id FROM privacy_erasure_artifacts WHERE state<>'complete'", ())
            await shared.close()
            shared = None
        with pytest.raises(PrivacyPortabilityError, match="^privacy_destination_occupied$"):
            await restore_protected_export(artifact, config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        native_restored = await MemoryEngine.create(native_config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        await _assert_survivor(native_restored, owner, erased_id, survivor_id, survives, evidence,
            unavailable_ids=(source_only_id,))
        assert await native_restored._graph_store.get_node(source_only_id) is None
        if layout != "postgres":
            assert set(_documents(native_restored._lexical_index, set())) == {survivor_id}
            assert native_restored._lexical_index._index is not restored._lexical_index._index
            assert set(_documents(engine._lexical_index, set())) == {survivor_id, source_only_id}
        assert (await native_restored._privacy.erasure.repository.state())[1] == current.ledger_checkpoint
        await native_restored.close()
        native_restored = None
        copied_after = await _copied_rows(native_config)
        with pytest.raises(PrivacyDenied, match="^privacy_ledger_stale$"):
            await MemoryEngine.create(native_config, privacy_runtime=old_runtime, embedding_provider=runtime.embedder)
        assert await _copied_rows(native_config) == copied_after
        if layout == "directory":
            single = _local_target(source_config, tmp_path / "directory-to-single", single=True)
            await restore_protected_export(artifact, single, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            native_restored = await MemoryEngine.create(single, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            await _assert_survivor(native_restored, owner, erased_id, survivor_id, survives, evidence,
                unavailable_ids=(source_only_id,))
        assert _hashes(backup) == backup_hashes
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == original
    finally:
        if shared is not None:
            await shared.close()
        if native_restored is not None:
            await native_restored.close()
        if restored is not None:
            await restored.close()
        await engine.close()
        await stack.aclose()
