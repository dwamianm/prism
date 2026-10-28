"""CL07/ER04 prescribed native interruption, abandonment and cancellation."""
import asyncio
from dataclasses import replace
from pathlib import Path
import threading
from uuid import UUID, uuid4, uuid5

import pytest

from prme.privacy import PrivacyDenied
from prme.privacy.erasure import ErasureRequest
from tests.privacy_journal_support import (
    LAYOUTS, SOURCE, SOURCE_ONLY, journal_engine, processing_keys, logical_dump,
    isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL07_ER04_abandoned_stages_persistent_failure_and_cancelled_native_cleanup(tmp_path, layout, analyzer, native, monkeypatch):
    from prme.storage import erasure_indexes
    engine, runtime, policy, custody, _, processing = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner, task, waiting = str(policy.owner_id), None, None
    released = threading.Event()
    try:
        event = await engine.ingest(SOURCE, user_id=owner)
        with processing_keys(runtime, custody, processing):
            assert (await engine.process_extractions(user_id=owner)).processed == 1
        async def interrupted(*args, **kwargs):
            raise RuntimeError("synthetic interruption after real vector staging")
        with monkeypatch.context() as patch:
            # First stop between the two native indexes. PostgreSQL's indexes
            # are atomic graph columns, so its interruption is before SQL commit.
            target = engine._lexical_index if layout != "postgres" else engine._graph_store
            name = "stage_profile" if layout != "postgres" else "publish_profile"
            patch.setattr(target, name, interrupted)
            with pytest.raises(PrivacyDenied):
                await engine.consolidate_knowledge(user_id=owner, entity_names=["Mara"])
        abandoned = (await engine.profile_jobs(user_id=owner))[0]["plan_id"]
        if layout != "postgres":
            assert await engine._privacy.repository._query("SELECT node_id FROM vector_staging WHERE node_id=?", (abandoned,))
        # Retry performs real lexical staging, then stop before graph publication.
        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "publish_profile", interrupted)
            with pytest.raises(PrivacyDenied):
                await engine.resume_profile(abandoned, user_id=owner)
        assert await engine.discard_profile(abandoned, user_id=owner)
        assert (await engine.profile_jobs(user_id=owner, status="abandoned"))[0]["plan_id"] == abandoned
        if layout != "postgres":
            docs = erasure_indexes._documents(engine._lexical_index, set())
            assert abandoned in docs and SOURCE_ONLY not in str(docs)
        assert SOURCE_ONLY.encode() not in await logical_dump(engine, tmp_path / "before-erasure.jsonl")
        positive = await engine.store("Nora bakes bread.", user_id=owner)
        survivor = (await engine.get_event_nodes(positive, user_id=owner))[0]
        runtime.extractor.result = {
            "entities": [{"name": "Nora", "entity_type": "person"}],
            "facts": [{"subject": "Nora", "predicate": "likes", "object": "pears",
                       "evidence_quote": "Nora likes pears.", "polarity": "positive"}],
            "relationships": [], "summary": None,
        }
        pending_source = await engine.ingest("Nora likes pears.", user_id=owner)
        with processing_keys(runtime, custody, processing):
            assert (await engine.process_extractions(user_id=owner)).processed == 1
        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "publish_profile", interrupted)
            with pytest.raises(PrivacyDenied):
                await engine.consolidate_knowledge(user_id=owner, entity_names=["Nora"])
        pending_id = (await engine.profile_jobs(user_id=owner))[0]["plan_id"]
        pending_plan = await engine._profile_work.get(pending_id, user_id=owner)
        assert pending_plan.node.content and pending_plan.privacy_manifest
        if layout != "postgres":
            assert pending_id in erasure_indexes._documents(engine._lexical_index, set())
            assert await engine._privacy.repository._query("SELECT node_id FROM vector_staging WHERE node_id=?", (pending_id,))
        request = ErasureRequest(request_id=uuid4(), event_ids=(UUID(event),))
        await engine.request_erasure(request, user_id=owner)
        repository = engine._privacy.repository
        if layout == "postgres":
            # A real trigger rejects the actual SQL effect twice; no internal
            # purge implementation is replaced with a successful fake.
            async with repository.pool.acquire() as connection:
                await connection.execute("CREATE FUNCTION erasure_test_unavailable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic erasure write unavailable'; END $$")
                await connection.execute("CREATE TRIGGER erasure_test_failure BEFORE DELETE ON nodes FOR EACH ROW EXECUTE FUNCTION erasure_test_unavailable()")
        with monkeypatch.context() as patch:
            if layout == "directory":
                actual_replace = erasure_indexes.os.replace
                def unavailable(source, destination):
                    if ".prme-erasure-fresh-" in str(source):
                        raise OSError("synthetic native swap unavailable")
                    return actual_replace(source, destination)
                patch.setattr(erasure_indexes.os, "replace", unavailable)
            elif layout == "pack":
                def unavailable():
                    raise OSError("synthetic snapshot persistence unavailable")
                patch.setattr(engine._vector_index, "_persist_snapshot", unavailable)
            for attempt in (1, 2):
                [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
                assert status.failure is not None and status.attempts == attempt and status.logical_live_copies == "pending"
                assert (await engine.get_erasure_status(request.request_id, user_id=owner)).failure == status.failure
                assert (await engine.get_event_nodes(positive, user_id=owner))[0].id == survivor.id
        if layout == "postgres":
            async with repository.pool.acquire() as connection:
                await connection.execute("DROP TRIGGER erasure_test_failure ON nodes")
                await connection.execute("DROP FUNCTION erasure_test_unavailable()")
        else:
            entered = threading.Event()
            compact = erasure_indexes.compact_lexical
            def paused(*args):
                entered.set()
                assert released.wait(15)
                return compact(*args)
            with monkeypatch.context() as patch:
                patch.setattr(erasure_indexes, "compact_lexical", paused)
                task = asyncio.create_task(engine.process_erasures(user_id=owner, budget_ms=60000))
                assert await asyncio.to_thread(entered.wait, 10)
                task.cancel()
                waiting = asyncio.create_task(engine.request_erasure(request, user_id=owner))
                await asyncio.sleep(0.02)
                assert not task.done() and not waiting.done()
                released.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 15)
                assert (await asyncio.wait_for(waiting, 15)).logical_live_copies == "pending"
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None
        assert await engine._profile_work.get(abandoned, user_id=owner) is None
        dumped = await logical_dump(engine, tmp_path / "after-erasure.jsonl")
        assert b"Mara likes tea." not in dumped and SOURCE_ONLY.encode() not in dumped
        assert (await engine.get_event_nodes(positive, user_id=owner))[0].id == survivor.id
        assert (await engine._profile_work.get(pending_id, user_id=owner)).checksum == pending_plan.checksum
        assert (await engine.profile_jobs(user_id=owner))[0]["plan_id"] == pending_id
        assert len(await engine.get_event_nodes(pending_source, user_id=owner)) == 2
        if layout != "postgres":
            documents = erasure_indexes._documents(engine._lexical_index, set())
            assert pending_id in documents and abandoned not in documents
            assert str(survivor.id) in documents and len(documents) == 3
            assert engine._vector_index._index.size == 4
            assert await engine._privacy.repository._query("SELECT node_id FROM vector_staging WHERE node_id=?", (pending_id,))
            await engine.rebuild_indexes(user_id=owner)
            assert set(erasure_indexes._documents(engine._lexical_index, set())) == set(documents)
            assert engine._vector_index._index.size == 4
        if layout == "directory":
            identifier = uuid5(request.request_id, "prme:lexical-erasure:v1").hex
            path = Path(engine._config.lexical_path)
            assert not path.with_name(path.name + ".prme-erasure-fresh-" + identifier).exists()
            assert not path.with_name(path.name + ".prme-erasure-retired-" + identifier).exists()
        assert status.backup_copies == status.external_copies == "not_managed" and not status.physical_wipe
    finally:
        released.set()
        await asyncio.gather(*(value for value in (task, waiting) if value is not None), return_exceptions=True)
        await engine.close()
