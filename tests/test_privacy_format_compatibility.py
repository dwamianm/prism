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

import pytest

from prme import MemoryEngine
from prme.privacy.portability import PrivacyPortabilityError
from prme.privacy.records import PRIVACY_KEY
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


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
