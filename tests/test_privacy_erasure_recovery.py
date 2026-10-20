"""ER04–06 fault/recovery checks using real ledger, SQL and native indexes."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import shutil
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy import PrivacyDenied
from prme.privacy.erasure import ErasureRequest
from prme.privacy.ledger import LocalErasureLedger
from prme.privacy.local_keys import public_registry
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.test_privacy_source_routing import selection


async def reopen(engine, runtime):
    config = engine._config
    await engine.close()
    runtime = replace(runtime, erasure_ledger=LocalErasureLedger(runtime.erasure_ledger.path))
    return await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime), runtime


@pytest.mark.parametrize("layout", ["directory", "postgres"])
async def test_ER04_scope_bound_maintenance_does_not_scan_foreign_jobs(tmp_path, layout, analyzer, native):
    """The existing three-source family under separately authorized defaults."""
    from prme.privacy.keys import KeyBoundary
    from prme.types import Scope
    from tests.privacy_fakes import policy_for

    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner, project_engine = str(policy.owner_id), None
    try:
        personal = await engine.store("Mara likes tea.", user_id=owner)
        later = await engine.store("Mara prefers oranges.", user_id=owner)
        personal_request = ErasureRequest(request_id=UUID(int=1), scope=Scope.PERSONAL,
                                         mode="evidence", event_ids=(UUID(personal),))
        await engine.request_erasure(personal_request, user_id=owner)
        [personal_status] = await engine.process_erasures(user_id=owner, limit=1, budget_ms=60000)
        assert personal_status.request_id == personal_request.request_id and personal_status.state == "live_purged"
        personal_config = engine._config
        await engine.close()
        project_policy = policy_for(runtime, owner=policy.owner_id, scope=Scope.PROJECT,
                                    capture="full", evidence_lifetime_seconds=3600)
        boundary = KeyBoundary(project_id=runtime.project_id, owner_id=policy.owner_id, scope=Scope.PROJECT.value)
        await custody.provision_wrapping(boundary, "evidence")
        await custody.provision_wrapping(boundary, "processing")
        runtime.protector.registry = public_registry(custody.path)
        project_engine = await MemoryEngine.create(personal_config.model_copy(update={"privacy_policy": project_policy}),
                                                  privacy_runtime=runtime, embedding_provider=runtime.embedder)
        project = await project_engine.store("A project color is blue.", user_id=owner, scope=Scope.PROJECT)
        project_request = ErasureRequest(request_id=UUID(int=2), scope=Scope.PROJECT,
                                        mode="evidence", event_ids=(UUID(project),))
        await project_engine.request_erasure(project_request, user_id=owner)
        # Before the correction, this completes the project job and then raises
        # on the already-completed personal job selected by the owner-wide query.
        [project_status] = await project_engine.process_erasures(user_id=owner, budget_ms=60000)
        assert project_status.request_id == project_request.request_id and project_status.state == "live_purged"
        assert await project_engine.get_erasure_status(personal_request.request_id, user_id=owner) == personal_status
        assert (await project_engine._privacy.repository.get(later, user_id=owner)).evidence is not None
        assert (await project_engine._privacy.repository.get(project, user_id=owner)).evidence is None

        foreign_request = ErasureRequest(request_id=UUID(int=0), scope=Scope.PROJECT,
                                        mode="evidence", event_ids=(UUID(project),))
        await project_engine.request_erasure(foreign_request, user_id=owner)
        foreign_before = await project_engine.get_erasure_status(foreign_request.request_id, user_id=owner)
        await project_engine.close()
        project_engine = None
        engine = await MemoryEngine.create(personal_config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        later_request = ErasureRequest(request_id=UUID(int=3), scope=Scope.PERSONAL,
                                      mode="evidence", event_ids=(UUID(later),))
        await engine.request_erasure(later_request, user_id=owner)
        [completed] = await engine.process_erasures(user_id=owner, limit=1, budget_ms=60000)
        assert completed.request_id == later_request.request_id and completed.state == "live_purged"
        assert await engine.get_erasure_status(foreign_request.request_id, user_id=owner) == foreign_before
        assert (await engine._privacy.repository.get(later, user_id=owner)).evidence is None
        assert all(record.snapshot.policy.scope == Scope.PERSONAL for record in (
            await engine._privacy.repository.get(personal, user_id=owner),
            await engine._privacy.repository.get(later, user_id=owner),
        ))
        assert (await engine._privacy.repository.get(project, user_id=owner)).snapshot.policy.scope == Scope.PROJECT
        with pytest.raises(PrivacyDenied, match="^privacy_boundary_mismatch$"):
            await engine.process_erasures(user_id=str(uuid4()), limit=1)
        repository = engine._privacy.repository
        await repository._query("UPDATE privacy_erasure_jobs SET scope=? WHERE request_id=?",
                                (Scope.PERSONAL.value, str(foreign_request.request_id)))
        try:
            with pytest.raises(PrivacyDenied, match="^privacy_boundary_mismatch$"):
                await engine.process_erasures(user_id=owner, limit=1)
            assert await engine.get_erasure_status(foreign_request.request_id, user_id=owner) == foreign_before
        finally:
            await repository._query("UPDATE privacy_erasure_jobs SET scope=? WHERE request_id=?",
                                    (Scope.PROJECT.value, str(foreign_request.request_id)))
    finally:
        if project_engine is not None:
            await project_engine.close()
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER04_intent_and_each_stage_survive_interruption_and_failure(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, custody, evidence, _ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner = str(policy.owner_id)
    try:
        event = await engine.store("Mara likes tea. Source maroon.", retrieval_content="Mara likes tea.", user_id=owner)
        positive = await engine.store("Mara likes apples.", user_id=owner)
        selected = (await engine.get_event_nodes(event, user_id=owner))[0]
        grant = await selection(engine, runtime, owner, (event,), kind="diagnostic")
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            assert (await engine.read_source(event, user_id=owner, access=grant)).content == "Mara likes tea. Source maroon."
        runtime.protector.registry = public_registry(custody.path)
        request = ErasureRequest(request_id=uuid4(), node_ids=(selected.id,))
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
                assert pending[0].intent.source_event_ids == (UUID(event),)
        assert apply is not lost_fence
        engine, runtime = await reopen(engine, runtime)
        assert (await engine.request_erasure(request, user_id=owner)).state == "blocked_from_access"
        assert (await engine.inspect_source(event, user_id=owner)).state == "erased"
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            with pytest.raises(PrivacyDenied):
                await engine.read_source(event, user_id=owner, access=grant)
        runtime.protector.registry = public_registry(custody.path)
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
        erased = await engine._privacy.repository.get(event, user_id=owner)
        assert erased.evidence is None and erased.processing is None and erased.retry is None
        assert await engine._privacy.repository._query("SELECT content,content_hash FROM events WHERE id=?", (event,)) == [("", "")]
        # A completed node-only selection no longer has a node row to resolve.
        # Its exact persisted request must still be safely retryable.
        selected = (await engine.get_event_nodes(positive, user_id=owner))[0]
        node_request = ErasureRequest(request_id=uuid4(), node_ids=(selected.id,))
        await engine.request_erasure(node_request, user_id=owner)
        results = await engine.process_erasures(user_id=owner, budget_ms=60000)
        node_status = next(value for value in results if value.request_id == node_request.request_id)
        assert node_status.state == "live_purged"
        assert await engine._graph_store.get_node(str(selected.id)) is None
        assert await engine.request_erasure(node_request, user_id=owner) == node_status
        with pytest.raises(PrivacyDenied, match="privacy_erasure_conflict"):
            await engine.request_erasure(ErasureRequest(request_id=node_request.request_id, event_ids=(UUID(positive),)), user_id=owner)
        # A completed low-ID request cannot starve later work with limit=1.
        another = ErasureRequest(request_id=uuid4(), event_ids=(UUID(positive),))
        await engine.request_erasure(another, user_id=owner)
        [last] = await engine.process_erasures(user_id=owner, limit=1, budget_ms=60000)
        assert last.request_id == another.request_id and last.state == "live_purged"
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER06_stale_native_saves_close_restore_and_rebuild_keep_survivor(tmp_path, layout, analyzer, native, monkeypatch):
    from prme.storage.erasure_indexes import _documents
    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner, stale = str(policy.owner_id), None
    control, _, control_policy, *_ = await journal_engine(tmp_path / "unaffected-store", "directory", analyzer)
    control_owner = str(control_policy.owner_id)
    control_event = await control.store("Ravi grows plums.", user_id=control_owner)
    await control._vector_index.save()
    control_snapshot = Path(control._config.vector_path).read_bytes()
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
            later = await engine.store("Mara likes pears.", user_id=owner)
            later_node = (await engine.get_event_nodes(later, user_id=owner))[0]
            entered, release = asyncio.Event(), asyncio.Event()
            get_node = engine._graph_store.get_node
            async def paused(identifier, *args, **kwargs):
                value = await get_node(identifier, *args, **kwargs)
                if identifier == str(later_node.id):
                    entered.set()
                    await release.wait()
                return value
            with monkeypatch.context() as patch:
                patch.setattr(engine._graph_store, "get_node", paused)
                rebuild = asyncio.create_task(engine.rebuild_indexes(user_id=owner))
                erasure = None
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    erasure = asyncio.create_task(engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(later),)), user_id=owner))
                    await asyncio.sleep(0.02)
                    assert not erasure.done()
                    release.set()
                    assert (await asyncio.wait_for(rebuild, 15))["nodes_indexed"] == 2
                    assert (await asyncio.wait_for(erasure, 15)).state == "blocked_from_access"
                finally:
                    release.set()
                    await asyncio.gather(*(task for task in (rebuild, erasure) if task is not None), return_exceptions=True)
            assert all(status.state == "live_purged" for status in await engine.process_erasures(user_id=owner, budget_ms=60000))
            assert engine._vector_index._index.size == 1
            assert set(_documents(engine._lexical_index, set())) == {str(survivor.id)}
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
        assert Path(control._config.vector_path).read_bytes() == control_snapshot
        assert (await control.get_event_nodes(control_event, user_id=control_owner))[0].content == "Ravi grows plums."
        assert len(_documents(control._lexical_index, set())) == 1
    finally:
        if stale is not None:
            await stale.close()
        await engine.close()
        await control.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER05_missing_work_recovery_cannot_restore_erased_preparations(tmp_path, layout, analyzer, native, monkeypatch):
    from tests.privacy_journal_support import SOURCE, processing_keys
    engine, runtime, policy, custody, evidence, processing = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody)
    engine._privacy.runtime = runtime
    owner = str(policy.owner_id)
    try:
        source = await engine.ingest(SOURCE, user_id=owner)
        with processing_keys(runtime, custody, processing):
            assert (await engine.process_extractions(user_id=owner)).processed == 1
        async def interrupt(*args, **kwargs):
            raise RuntimeError("synthetic interruption after real native preparation")
        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "publish_profile", interrupt)
            with pytest.raises(PrivacyDenied):
                await engine.consolidate_knowledge(user_id=owner, entity_names=["Mara"])
        profile_id = (await engine.profile_jobs(user_id=owner))[0]["plan_id"]
        profile = await engine._profile_work.get(profile_id, user_id=owner)
        assert profile.privacy_manifest and profile.node.content
        extraction = await engine.ingest(SOURCE, user_id=owner)
        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "commit_derivation", interrupt)
            with processing_keys(runtime, custody, processing):
                assert (await engine.process_extractions(user_id=owner)).processed == 0
        plan = await engine._event_store.get_derivation_plan(extraction, user_id=owner)
        assert plan and plan.nodes
        direct = await engine.ingest_fast("Nora collects shells. Pending source coral.", user_id=owner)
        assert await engine._event_store.get_direct_store(direct, user_id=owner) is not None
        positive = await engine.store("Nora bakes bread.", user_id=owner)
        if layout != "postgres":
            assert await engine._privacy.repository._query("SELECT node_id FROM vector_staging WHERE node_id=?", (profile_id,))
        await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=tuple(map(UUID, (source, extraction, direct)))), user_id=owner)
        # Remove the mutable trackers after acknowledgement. Immutable copies
        # existed above, and startup must consume the current independent fence
        # before the existing registry reconstruction sees those preparations.
        repository = engine._privacy.repository
        await repository._query("DELETE FROM profile_work WHERE plan_id=?", (profile_id,))
        await repository._query("DELETE FROM event_extractions WHERE event_id=?", (extraction,))
        await repository._query("DELETE FROM event_materializations WHERE event_id=?", (direct,))
        sends = len(runtime.extractor.sends), len(runtime.embedder.sends)
        engine, runtime = await reopen(engine, runtime)
        assert not await engine.profile_jobs(user_id=owner)
        assert (await engine.process_extractions(user_id=owner)).processed == 0
        assert (await engine.process_pending(user_id=owner)).processed == 0
        assert (await engine.process_profiles(user_id=owner))["processed"] == 0
        assert sends == (len(runtime.extractor.sends), len(runtime.embedder.sends))
        assert await engine._event_store.get_derivation_plan(extraction, user_id=owner) is None
        assert await engine._event_store.get_direct_store(direct, user_id=owner) is None
        assert await engine._profile_work.get(profile_id, user_id=owner) is None
        assert await engine._graph_store.get_node(profile_id) is None
        assert (await engine.get_event_nodes(positive, user_id=owner))[0].content == "Nora bakes bread."
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None, status
    finally:
        await engine.close()
