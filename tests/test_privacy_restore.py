"""Real snapshot restore/current-ledger integration, with explicit read limits.

These checks use stored admitted rows and metadata availability. The held #323
public contributor classifier is not repaired or re-tested here. Its public
surviving-memory gate remains pending, so these are not complete ER07/EM08.
"""
import hashlib
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy.erasure import ErasureRequest
from prme.privacy.portability import PrivacyPortabilityError
from prme.storage.privacy_export import export_protected_pack, restore_protected_export
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_same_lineage_restore_applies_current_ledger_before_recovery(tmp_path, layout, analyzer, native):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    admin, target_schema, restored = None, None, None
    try:
        erased = await engine.store("Mara likes oranges.", user_id=owner)
        survives = await engine.store("Mara prefers tea.", user_id=owner)
        rows = await engine._privacy.repository._query("SELECT id,content FROM nodes ORDER BY id", ())
        survivor_id = str(next(row[0] for row in rows if row[1] == "Mara prefers tea."))
        erased_id = str(next(row[0] for row in rows if row[1] == "Mara likes oranges."))
        artifact = tmp_path / "snapshot.json"
        exported = await export_protected_pack(engine, artifact)
        original = hashlib.sha256(artifact.read_bytes()).hexdigest()
        await engine.request_erasure(ErasureRequest(request_id=uuid4(), scope=policy.scope, event_ids=(UUID(erased),)), user_id=owner)
        config = engine._config
        if layout == "postgres":
            import asyncpg
            url = os.environ["PRME_TEST_RESTORE_DATABASE_URL"]
            admin = await asyncpg.connect(url)
            target_schema = "pv326_restore_" + uuid4().hex
            await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            await admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
            await admin.execute(f'CREATE SCHEMA "{target_schema}"')
            parts = urlsplit(url)
            query = [(k, v) for k, v in parse_qsl(parts.query) if k != "search_path"] + [("search_path", target_schema)]
            target_url = urlunsplit(parts._replace(query=urlencode(query)))
            config = type(config).model_validate({**config.model_dump(), "database_url": target_url})
        else:
            destination = tmp_path / "restored"
            config = config.model_copy(update={
                "pack_path": str(tmp_path / "restored.prme") if layout == "pack" else None,
                "db_path": str(destination / "memory.duckdb"),
                "vector_path": str(destination / "vectors.usearch"),
                "lexical_path": str(destination / "lexical_index"),
            })
        result = await restore_protected_export(artifact, config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        assert result["store_id"] == str(exported.format.store_id)
        assert result["ledger_sequence"] > exported.ledger_checkpoint.sequence
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == original
        restored = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        assert await restored._graph_store.get_node(erased_id, include_superseded=True) is None
        assert (await restored._graph_store.get_node(survivor_id)).content == "Mara prefers tea."
        assert (await restored.inspect_source(survives, user_id=owner)).state == "sealed"
        payload = (await restored._privacy.repository.get(survives, user_id=owner)).evidence
        assert payload == (await engine._privacy.repository.get(survives, user_id=owner)).evidence
        with pytest.raises(PrivacyPortabilityError, match="^privacy_destination_occupied$"):
            await restore_protected_export(artifact, config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == original
    finally:
        if restored is not None:
            await restored.close()
        await engine.close()
        if admin is not None:
            if target_schema is not None:
                await admin.execute(f'DROP SCHEMA "{target_schema}" CASCADE')
            await admin.close()
