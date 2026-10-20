"""Declared-format preflight and real backend event dispatch, before recovery.

The native OS storage double is inherited for these format-component checks;
native custody proof remains the exact PV-03 conformance evidence. Sanitization,
crypto, policy composition, ledger, event storage and native indexes are real.
"""
import json
import traceback
import os
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path

import pytest

from prme import MemoryEngine
from prme.privacy.portability import PrivacyPortabilityError
from prme.privacy.records import PRIVACY_KEY
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("reader", ["v0.15.0", "v0.16.0a4"])
@pytest.mark.parametrize("layout,adapter", [(layout, adapter) for layout in LAYOUTS
    for adapter in ("engine", "client", "cli", "http", "mcp", "workspace") if (layout, adapter) != ("postgres", "cli")])
async def test_pinned_historical_reader_control_and_format_refusal(tmp_path, reader, layout, adapter, analyzer, native):
    """AC2 bounded actual readers. No key/dependency/config failure is success."""
    import asyncio
    import hashlib
    from tests.privacy_portability_support import fresh_config, HISTORICAL_PROBE
    from prme.storage.privacy_migration import _source
    from prme import MemoryWorkspace
    reader_dir = Path(__file__).parents[1] / ".github-ticket-loop/privacy-326/historical-readers" / reader
    if not (reader_dir / ".venv/bin/python").exists():
        pytest.skip("explicit isolated historical environment required; no acceptance success")
    fixture = json.loads((Path(__file__).parent / "fixtures/privacy/historical-readers-v1.json").read_text())
    pin = next(row for row in fixture["readers"] if row["tag"] == reader)
    assert hashlib.sha256((reader_dir / "uv.lock").read_bytes()).hexdigest() == pin["lock_sha256"]
    template, runtime, policy, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    async def content_snapshot(config):
        async with _source(config) as query:
            tables = await query("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'")
            names = sorted(row["table_name"] for row in tables)
            result = {}
            for name in names:
                if name.startswith("privacy_") or name in {"events", "nodes", "edges", "operations", "event_materializations", "event_extractions",
                        "vector_metadata", "vector_payloads", "vector_staging", "derivation_registered_plans", "profile_work",
                        "profile_publication_heads", "profile_preparation_heads", "consolidation_publication_heads"}:
                    result[name] = sorted(json.dumps(row, sort_keys=True, default=str) for row in await query(f'SELECT * FROM "{name}"'))
            return names, result
    for protected in (False, True):
        async with fresh_config(base, tmp_path / ("protected" if protected else "legacy"), layout) as config:
            config = config.model_copy(update={"privacy_policy": policy if protected else None})
            selected_runtime = runtime if protected else None
            workspace_dir = tmp_path / ("protected-workspace" if protected else "legacy-workspace")
            workspace_name = "pv326_reader_" + uuid4().hex
            admin = None
            namespace_id = policy.project_id if protected else uuid4()
            try:
                if adapter == "workspace":
                    if layout == "postgres":
                        import asyncpg
                        admin = await asyncpg.connect(os.environ["PRME_TEST_DATABASE_URL"])
                        context = MemoryWorkspace.open_postgres(config, name=workspace_name, embedding_provider=runtime.embedder,
                            privacy_runtime_factory=(lambda _: runtime) if protected else None)
                    else:
                        context = MemoryWorkspace.open(workspace_dir, config=config, embedding_provider=runtime.embedder,
                            privacy_runtime_factory=(lambda _: runtime) if protected else None, single_file_packs=layout == "pack")
                    async with context as workspace:
                        if admin is None:
                            with workspace._registry:
                                workspace._registry.execute("INSERT INTO namespaces(name,id) VALUES('existing',?)", [str(namespace_id)])
                        else:
                            await admin.execute("INSERT INTO prme_workspace.namespaces(workspace,name,id) VALUES($1,'existing',$2)", workspace_name, namespace_id)
                        async with workspace.namespace("existing") as memory:
                            event_id = await memory.store("Historical reader usable control.", user_id=str(policy.owner_id))
                            actual = memory._engine._config
                    if layout == "postgres":
                        from prme.storage.pg.namespace import namespace_schema
                        from urllib.parse import parse_qsl, urlencode
                        parts = urlsplit(actual.database_url.get_secret_value())
                        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "search_path"] + [("search_path", namespace_schema(namespace_id))]
                        inspection = type(actual).model_validate({**actual.model_dump(), "database_url": urlunsplit(parts._replace(query=urlencode(query)))})
                    else:
                        inspection = actual
                else:
                    engine = await MemoryEngine.create(config, privacy_runtime=selected_runtime, embedding_provider=runtime.embedder)
                    try:
                        event_id = await engine.store("Historical reader usable control.", user_id=str(policy.owner_id))
                        actual = inspection = engine._config
                    finally:
                        await engine.close()
                before_names, before = await content_snapshot(inspection)
                # The same supported opening configuration for both pinned
                # releases; newer unrelated feature flags are not reader input.
                serializable = actual.model_dump(mode="json", include={"database_url", "namespace_id", "db_path", "vector_path",
                    "lexical_path", "pack_path", "embedding", "duckdb_threads", "encryption_enabled", "encryption_key"})
                if actual.database_url is not None:
                    serializable["database_url"] = actual.database_url.get_secret_value()
                # Historical HTTP/MCP do not expose provider injection. Select
                # a supported factory-backed configuration; the isolated probe
                # replaces only the external model factory, preserving exact
                # model/version/dimension and all actual storage opening code.
                serializable["embedding"]["provider"] = "fastembed"
                spec = {"config": serializable, "owner": str(policy.owner_id), "event_id": event_id,
                    "adapter": adapter, "protected": protected, "workspace_dir": str(workspace_dir), "workspace_name": workspace_name}
                spec_path = tmp_path / ("protected-spec.json" if protected else "legacy-spec.json")
                spec_path.write_text(json.dumps(spec))
                spec_path.chmod(0o600)
                environment = {key: value for key, value in os.environ.items() if not key.startswith("PRME_") and key != "PYTHONPATH"}
                process = await asyncio.create_subprocess_exec(str(reader_dir / ".venv/bin/python"), "-c", HISTORICAL_PROBE, str(spec_path),
                    cwd=reader_dir, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
                spec_path.with_suffix(".stdout").write_bytes(stdout)
                spec_path.with_suffix(".stderr").write_bytes(stderr)
                assert stdout.decode().splitlines(), stderr.decode()[-2000:]
                result = json.loads(stdout.decode().splitlines()[-1])
                print(json.dumps({"reader": reader, "layout": layout, **result}))
                assert process.returncode == 0, (result, stderr.decode()[-2000:])
                after_names, after = await content_snapshot(inspection)
                if protected:
                    assert after == before
                    assert result["format_refusal"] and result["provider_creations"] == 0
                    print(json.dumps({"harmless_created_tables": sorted(set(after_names) - set(before_names)), "record_mutation": False}))
            finally:
                if admin is not None:
                    from prme.storage.pg.namespace import namespace_schema
                    await admin.execute(f'DROP SCHEMA IF EXISTS "{namespace_schema(namespace_id)}" CASCADE')
                    await admin.execute("DELETE FROM prme_workspace.namespaces WHERE workspace=$1", workspace_name)
                    await admin.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_v2_real_admission_reopen_and_no_durable_source_hash(tmp_path, layout, analyzer, native):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    try:
        try:
            event_id = await engine.store("Mara likes tea. Private source indigo.", retrieval_content="Mara likes tea.", user_id=owner)
        except Exception as exc:
            # Content-free diagnosis survives the intentional public exception
            # normalization. No exception messages, locals or source are printed.
            cause = exc
            while cause is not None:
                print(type(cause).__name__, [(frame.name, frame.lineno) for frame in traceback.extract_tb(cause.__traceback__)])
                cause = cause.__context__
            raise
        repo = engine._privacy.repository
        columns = {row[0] for row in await repo._query(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='events'", ())}
        assert "content_hash" not in columns
        stub = await engine._event_store.get(event_id)
        assert stub.content == "" and stub.metadata == {PRIVACY_KEY: (await repo.get(event_id, user_id=owner)).reference()}
        assert (await engine.inspect_source(event_id, user_id=owner)).state == "sealed"
        before = await repo._query("SELECT payload FROM operations ORDER BY id", ())
        config = engine._config
    finally:
        await engine.close()
    engine = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
    try:
        assert (await engine.inspect_source(event_id, user_id=owner)).state == "sealed"
        assert await engine._privacy.repository._query("SELECT payload FROM operations ORDER BY id", ()) == before
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_unknown_format_refuses_before_recovery(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    try:
        await engine.store("Mara likes apples.", user_id=str(policy.owner_id))
        repo = engine._privacy.repository
        body = json.loads((await repo._query("SELECT body FROM privacy_format", ()))[0][0])
        body["schema_version"] = 1000
        await repo._query("UPDATE privacy_format SET body=?", (json.dumps(body),))
        config = engine._config
    finally:
        await engine.close()
    def forbidden(*args, **kwargs):
        pytest.fail("recovery/index/provider reached before format refusal")
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", forbidden)
    monkeypatch.setattr("prme.storage.derivation_registry.initialize_duck", forbidden)
    monkeypatch.setattr("prme.storage.derivation_registry.initialize_pg", forbidden)
    with pytest.raises(PrivacyPortabilityError, match="^privacy_format_unsupported$"):
        await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)


@pytest.mark.parametrize("layout", ["postgres"])
@pytest.mark.parametrize("protected", [False, True])
async def test_existing_namespace_runtime_login_without_ddl(tmp_path, layout, protected, analyzer, native):
    """#281 HS01/HS11 prerequisite: real login, no table/catalog ownership."""
    import asyncpg
    from prme import MemoryWorkspace
    from prme.storage.namespace_identity import NamespaceIdentityError
    from prme.storage.pg.namespace import namespace_schema
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    config = engine._config.model_copy(update={"privacy_policy": policy if protected else None, "namespace_id": None})
    await engine.close()
    name, role, password = "pv326_" + uuid4().hex, "pv326_rt_" + uuid4().hex, uuid4().hex
    authority = runtime if protected else None
    factory = (lambda _: authority) if protected else None
    admin = await asyncpg.connect(os.environ["PRME_TEST_DATABASE_URL"])
    identifier = None
    created_role = False
    try:
        async with MemoryWorkspace.open_postgres(config, name=name, embedding_provider=runtime.embedder,
                privacy_runtime_factory=factory) as workspace:
            # The project's durable catalog UUID is its policy boundary. The
            # privileged test provisioner binds it before protected bootstrap.
            identifier = policy.project_id
            await admin.execute("INSERT INTO prme_workspace.namespaces(workspace,name,id) VALUES($1,'existing',$2)", name, identifier)
            async with workspace.namespace("existing") as memory:
                identifier = memory.namespace.id
                event = await memory.store("Mara likes tea.", user_id=str(policy.owner_id))
        schema = namespace_schema(identifier)
        await admin.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT')
        created_role = True
        await admin.execute(f'GRANT USAGE ON SCHEMA prme_workspace,"{schema}" TO "{role}"')
        await admin.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA prme_workspace TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        await admin.execute(f'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"')
        authorities = ["prme_namespace_identity", "prme_runtime_schema", "profile_publication_heads",
                       "profile_preparation_heads", "consolidation_publication_heads", "consolidation_stage_fences",
                       "event_extractions", "privacy_admissions"]
        if protected:
            authorities += ["privacy_format", "privacy_store_state", "privacy_erasure_jobs", "privacy_erasure_epoch",
                            "privacy_erasure_targets", "privacy_erasure_progress", "privacy_erasure_artifacts"]
        for table in authorities:
            await admin.execute(f'REVOKE INSERT,UPDATE,DELETE ON "{schema}"."{table}" FROM "{role}"')
        parts = urlsplit(config.database_url.get_secret_value())
        login_url = urlunsplit(parts._replace(netloc=f"{role}:{password}@{parts.hostname}:{parts.port}"))
        runtime_config = type(config).model_validate({**config.model_dump(), "database_url": login_url})
        async def open_runtime():
            return MemoryWorkspace.open_postgres(runtime_config, name=name, embedding_provider=runtime.embedder,
                privacy_runtime_factory=factory, initialize=False)
        async with await open_runtime() as workspace:
            async with workspace.namespace("existing", create=False) as memory:
                if protected:
                    assert (await memory.inspect_source(event, user_id=str(policy.owner_id))).state == "sealed"
                else:
                    assert (await memory.get_event(event, user_id=str(policy.owner_id))).content == "Mara likes tea."
            with pytest.raises(NamespaceIdentityError, match="create=False"):
                async with workspace.namespace("new"):
                    pytest.fail("runtime created a namespace")
            with pytest.raises(KeyError):
                async with workspace.namespace("missing", create=False):
                    pytest.fail("runtime recreated missing namespace")
        login = await asyncpg.connect(login_url)
        try:
            assert not await login.fetchval("SELECT has_schema_privilege(current_user,$1,'CREATE')", schema)
            assert not await login.fetchval("SELECT has_table_privilege(current_user,$1,'UPDATE')", "prme_workspace.namespaces")
            for table in authorities:
                assert not await login.fetchval("SELECT has_table_privilege(current_user,$1,'UPDATE')", f"{schema}.{table}")
        finally:
            await login.close()
        # Structural drift and pending recovery refuse; no auto-repair writes.
        await admin.execute(f'ALTER TABLE "{schema}".events ADD COLUMN unintended TEXT')
        expected = "privacy_format_unsupported" if protected else "postgres_schema_bootstrap_required"
        with pytest.raises(ValueError, match=f"^{expected}$"):
            async with await open_runtime() as workspace:
                async with workspace.namespace("existing", create=False):
                    pytest.fail("incompatible schema served")
        await admin.execute(f'ALTER TABLE "{schema}".events DROP COLUMN unintended')
        # A quarantined corrupt/unregistered journal must not be silently
        # skipped by runtime startup. This is a recovery refusal control.
        await admin.execute(f'INSERT INTO "{schema}".operations(id,op_type,target_id,payload,actor_id) '
                            "VALUES($1,'DERIVATION_PREPARED',$2,'{}'::jsonb,'fixture')", str(uuid4()), event)
        with pytest.raises(ValueError, match="^postgres_recovery_required$"):
            async with await open_runtime() as workspace:
                async with workspace.namespace("existing", create=False):
                    pytest.fail("pending recovery served")
    finally:
        if identifier is not None:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{namespace_schema(identifier)}" CASCADE')
            await admin.execute("DELETE FROM prme_workspace.namespaces WHERE workspace=$1", name)
        if created_role:
            await admin.execute(f'DROP OWNED BY "{role}"')
            await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()
