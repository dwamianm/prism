"""ER04–06 fault/recovery checks using real ledger, SQL and native indexes."""
from dataclasses import replace
from pathlib import Path
import shutil
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy import PrivacyDenied
from prme.privacy.erasure import ErasureRequest
from prme.privacy.ledger import LocalErasureLedger
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


async def reopen(engine, runtime):
    config = engine._config
    await engine.close()
    runtime = replace(runtime, erasure_ledger=LocalErasureLedger(runtime.erasure_ledger.path))
    return await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime), runtime


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER04_intent_and_each_stage_survive_interruption_and_failure(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner = str(policy.owner_id)
    try:
        event = await engine.store("Mara likes tea. Source maroon.", retrieval_content="Mara likes tea.", user_id=owner)
        positive = await engine.store("Mara likes apples.", user_id=owner)
        request = ErasureRequest(request_id=uuid4(), event_ids=(UUID(event),))
        repository = engine._privacy.erasure.repository
        apply = repository.apply
        async def lost_fence(entry):
            raise OSError("synthetic local transaction unavailable after independent intent")
        with monkeypatch.context() as patch:
            patch.setattr(repository, "apply", lost_fence)
            with pytest.raises(OSError):
                await engine.request_erasure(request, user_id=owner)
            async with runtime.erasure_ledger.guard():
                _, checkpoint = await repository.state()
                pending = await runtime.erasure_ledger.entries_since(checkpoint)
                assert len(pending) == 1 and pending[0].intent.request == request
        assert apply is not lost_fence
        engine, runtime = await reopen(engine, runtime)
        assert (await engine.request_erasure(request, user_id=owner)).state == "blocked_from_access"
        with pytest.raises(PrivacyDenied):
            await engine.get_event_nodes(event, user_id=owner)
        assert await engine.get_event_nodes(positive, user_id=owner)

        # Lose the progress acknowledgement after each real stage. Each retry
        # traverses the durable saved cursor; no successful purge is substituted.
        for following in ("sql", "indexes", "keys", "verify", "done"):
            repository = engine._privacy.erasure.repository
            save = repository.save_status
            failed = False
            async def lost_progress(status):
                nonlocal failed
                target = status.logical_live_copies == "purged" if following == "done" else status.stage == following
                if target and not failed:
                    failed = True
                    raise OSError("synthetic lost stage acknowledgement")
                return await save(status)
            with monkeypatch.context() as patch:
                patch.setattr(repository, "save_status", lost_progress)
                [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
            assert failed and status.failure is not None and status.logical_live_copies == "pending"
            persisted = await engine.get_erasure_status(request.request_id, user_id=owner)
            assert persisted.failure == status.failure and persisted.attempts >= 1
            engine, runtime = await reopen(engine, runtime)
            assert (await engine.get_erasure_status(request.request_id, user_id=owner)).failure is not None
            assert await engine.get_event_nodes(positive, user_id=owner)
        [complete] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert complete.state == "live_purged" and complete.failure is None
        assert await engine.request_erasure(request, user_id=owner) == complete
        # A completed low-ID request cannot starve later work with limit=1.
        another = ErasureRequest(request_id=uuid4(), event_ids=(UUID(positive),))
        await engine.request_erasure(another, user_id=owner)
        [last] = await engine.process_erasures(user_id=owner, limit=1, budget_ms=60000)
        assert last.request_id == another.request_id and last.state == "live_purged"
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER06_stale_native_saves_close_restore_and_rebuild_keep_survivor(tmp_path, layout, analyzer, native):
    from prme.storage.erasure_indexes import _documents
    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner, stale = str(policy.owner_id), None
    try:
        event = await engine.store("Mara likes tea. Source indigo.", retrieval_content="Mara likes tea.", user_id=owner)
        positive = await engine.store("Mara likes apples. Source amber.", retrieval_content="Mara likes apples.", user_id=owner)
        node = (await engine.get_event_nodes(event, user_id=owner))[0]
        survivor = (await engine.get_event_nodes(positive, user_id=owner))[0]
        repository = engine._privacy.repository
        if layout != "postgres":
            await engine._vector_index.save()
            stale = await MemoryEngine.create(engine._config, embedding_provider=runtime.embedder,
                privacy_runtime=replace(runtime, erasure_ledger=LocalErasureLedger(runtime.erasure_ledger.path)))
            assert str(node.id) in _documents(stale._lexical_index, set())
            assert stale._vector_index._index.size == 2
            if layout == "directory":
                snapshot = Path(engine._config.vector_path).read_bytes()
                lexical_copy = tmp_path / "old-lexical-owned-fixture"
                shutil.copytree(engine._lexical_index._index_path, lexical_copy)
            else:
                snapshot = await repository._query("SELECT * FROM vector_snapshot", ())
                assert snapshot
            # A buffered unrelated graph-backed document must survive cleanup.
            await engine._lexical_index.delete_by_node_id(str(survivor.id))
            await engine._lexical_index.index(str(survivor.id), survivor.content, owner,
                survivor.node_type.value, survivor.scope.value)
            assert engine._lexical_index._uncommitted == 1
        else:
            assert await repository._query("SELECT id FROM nodes WHERE id=? AND embedding IS NOT NULL", (str(node.id),))
            assert await repository._query("SELECT id FROM nodes WHERE id=? AND content_tsv IS NOT NULL", (str(node.id),))
        await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(event),)), user_id=owner)
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None, status
        if stale is not None:
            await stale._vector_index.save()
            assert stale._vector_index._index.size == 1
            assert set(_documents(stale._lexical_index, set())) == {str(survivor.id)}
            await stale.close()
        assert (await engine.get_event_nodes(positive, user_id=owner))[0].id == survivor.id
        config = engine._config
        await engine.close()
        if layout == "directory":
            Path(config.vector_path).write_bytes(snapshot)
            shutil.rmtree(config.lexical_path)
            shutil.copytree(lexical_copy, config.lexical_path)
        elif layout == "pack":
            import duckdb
            with duckdb.connect(str(config.pack_path)) as connection:
                connection.execute("DELETE FROM vector_snapshot")
                connection.executemany("INSERT INTO vector_snapshot VALUES (" + ",".join("?" for _ in snapshot[0]) + ")", snapshot)
        engine = await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime)
        # Reopening applies the ledger and repairs independently restored native
        # copies before public exposure; rebuilding never reruns extraction.
        sends = len(runtime.extractor.sends)
        if layout != "postgres":
            assert engine._vector_index._index.size == 1
            assert set(_documents(engine._lexical_index, set())) == {str(survivor.id)}
        if layout != "postgres":
            rebuilt = await engine.rebuild_indexes(user_id=owner)
            assert rebuilt["nodes_indexed"] == 1
        else:
            assert not await engine._privacy.repository._query("SELECT id FROM nodes WHERE id=?", (str(node.id),))
            assert await engine._privacy.repository._query("SELECT id FROM nodes WHERE id=? AND embedding IS NOT NULL AND content_tsv IS NOT NULL", (str(survivor.id),))
        assert len(runtime.extractor.sends) == sends
        with pytest.raises(PrivacyDenied):
            await engine.get_event_nodes(event, user_id=owner)
        assert (await engine.get_event_nodes(positive, user_id=owner))[0].content == survivor.content
        engine, runtime = await reopen(engine, runtime)
        assert (await engine.get_event_nodes(positive, user_id=owner))[0].id == survivor.id
        assert await engine._graph_store.get_node(str(node.id)) is None
    finally:
        if stale is not None:
            await stale.close()
        await engine.close()
