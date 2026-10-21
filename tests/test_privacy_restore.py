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
from tests.privacy_portability_support import fresh_config


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


def _pg_environment(url, database):
    parts = urlsplit(url)
    assert parts.hostname == "127.0.0.1" and parts.port == 55432
    assert parts.path == "/" + database
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
    url = os.environ["PRME_TEST_RESTORE_DATABASE_URL"]
    _pg_environment(url, "privacy_engine_326_restore")
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
    restore_url = os.environ["PRME_TEST_RESTORE_DATABASE_URL"]
    source_env = _pg_environment(source_url, "privacy_engine_326")
    restore_env = _pg_environment(restore_url, "privacy_engine_326_restore")
    admin = await asyncpg.connect(restore_url)
    try:
        assert not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)", schema)
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        await _pg_tool(["pg_dump", "--no-owner", "--no-acl", "--format=custom", "--schema", schema, "--file", str(backup)], source_env)
        before = _hashes(backup)
        await _pg_tool(["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", "privacy_engine_326_restore", str(backup)], restore_env)
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


async def _assert_survivor(engine, owner, erased_id, survivor_id, survives, evidence):
    assert await engine._graph_store.get_node(erased_id, include_superseded=True) is None
    assert (await engine._graph_store.get_node(survivor_id)).content == "Mara prefers tea."
    assert (await engine.inspect_source(survives, user_id=owner)).state == "sealed"
    assert (await engine._privacy.repository.get(survives, user_id=owner)).evidence == evidence


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
        await _assert_survivor(restored, owner, erased_id, survivor_id, survives, evidence)
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
        await _assert_survivor(native_restored, owner, erased_id, survivor_id, survives, evidence)
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
            await _assert_survivor(native_restored, owner, erased_id, survivor_id, survives, evidence)
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
