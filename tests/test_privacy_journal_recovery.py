"""CL07/ER05 real stage/cleanup/reconstruction, without claiming PV-06 erasure."""

import pytest

from prme import MemoryEngine
from prme.privacy.policy import PrivacyDenied, MemoryRetention
from tests.privacy_journal_support import (
    LAYOUTS,
    SOURCE,
    SOURCE_ONLY,
    journal_engine,
    processing_keys,
    operation_bytes,
    isolated_privacy_postgres,  # noqa: F401
    local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL07_ER05_component_profile_discard_reconstruct_and_expire(
    tmp_path, layout, analyzer, native, monkeypatch
):
    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path,
        layout,
        analyzer,
        memory_retention=MemoryRetention(mode="erase_after", seconds=3600),
    )
    owner = str(policy.owner_id)
    try:
        event = await engine.ingest(SOURCE, user_id=owner)
        with processing_keys(runtime, custody, processing):
            assert (await engine.process_extractions(user_id=owner)).processed == 1
        publisher = engine._graph_store.publish_profile

        async def interrupt(plan):
            raise RuntimeError("interrupted after real native staging")

        monkeypatch.setattr(engine._graph_store, "publish_profile", interrupt)
        with pytest.raises(PrivacyDenied, match="privacy_processing_unavailable"):
            await engine.consolidate_knowledge(user_id=owner, entity_names=["Mara"])
        plan_id = (await engine.profile_jobs(user_id=owner))[0]["plan_id"]
        plan = await engine._profile_work.get(plan_id, user_id=owner)
        assert (
            plan.privacy_manifest
            and await engine.get_node(plan_id, user_id=owner) is None
        )
        if layout != "postgres":
            rows = await engine._privacy.repository._query(
                "SELECT node_id FROM vector_metadata WHERE node_id=?", (plan_id,)
            )
            assert len(rows) == 1
            hits = await engine._lexical_index.search("Mara", owner, limit=100)
            assert plan_id in {item["node_id"] for item in hits}
        monkeypatch.setattr(engine._graph_store, "publish_profile", publisher)
        assert await engine.discard_profile(plan_id, user_id=owner)
        assert await engine.discard_profile(plan_id, user_id=owner)
        collected = await engine.collect_profile_staging(user_id=owner)
        assert collected == {
            "collected": 1,
            "failed": 0,
            "remaining": 0,
            "errors": {},
            "blocked_reason": None,
        }
        if layout != "postgres":
            assert not await engine._privacy.repository._query(
                "SELECT node_id FROM vector_metadata WHERE node_id=?", (plan_id,)
            )
            assert plan_id not in {
                item["node_id"]
                for item in await engine._lexical_index.search("Mara", owner, limit=100)
            }
        assert (
            await engine._profile_work.get(plan_id, user_id=owner)
        ).checksum == plan.checksum
        # Rebuild the actual missing mutable work rows from immutable history.
        await engine._privacy.repository._query(
            "DELETE FROM profile_work WHERE user_id=?", (owner,)
        )
        config = engine._config
        await engine.close()
        engine = await MemoryEngine.create(
            config, embedding_provider=runtime.embedder, privacy_runtime=runtime
        )
        jobs = await engine.profile_jobs(user_id=owner, status="abandoned")
        assert len(jobs) == 1 and jobs[0]["plan_id"] == plan_id
        with pytest.raises(PrivacyDenied):
            await engine.resume_profile(plan_id, user_id=owner)
        assert (await engine.collect_profile_staging(user_id=owner))["collected"] == 0
        # A useful live generation remains possible without either private key.
        assert (
            await engine.consolidate_knowledge(user_id=owner, entity_names=["Mara"])
            == 1
        )
        complete = (await engine.profile_jobs(user_id=owner, status="complete"))[0][
            "plan_id"
        ]
        assert await engine.get_node(complete, user_id=owner) is not None
        await engine.archive(complete, user_id=owner)
        publisher = engine._graph_store.publish_profile
        monkeypatch.setattr(engine._graph_store, "publish_profile", interrupt)
        with pytest.raises(PrivacyDenied):
            await engine.consolidate_knowledge(user_id=owner, entity_names=["Mara"])
        pending = (await engine.profile_jobs(user_id=owner))[0]["plan_id"]
        monkeypatch.setattr(engine._graph_store, "publish_profile", publisher)
        original = await engine._privacy.repository.get(event, user_id=owner)
        sends = len(runtime.embedder.sends), len(runtime.extractor.sends)
        runtime.clock.now = original.snapshot.memory_deadline
        with pytest.raises(PrivacyDenied, match="privacy_expired"):
            await engine.resume_profile(pending, user_id=owner)
        assert await engine._graph_store.get_node(pending) is None
        assert sends == (len(runtime.embedder.sends), len(runtime.extractor.sends))
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER05_component_inherited_scope_is_not_authority(
    tmp_path, layout, analyzer, native
):
    import asyncio
    from prme.privacy.journals import journal_scope, journal_deadline
    from prme.types import LifecycleState

    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    child = None
    try:
        event = await engine.store("Mara likes tea.", user_id=owner)
        node = (await engine.get_event_nodes(event, user_id=owner))[0]
        record = await engine._privacy.repository.get(event, user_id=owner)
        entered = asyncio.Event()

        async def inherited():
            entered.set()
            await engine._graph_store.promote(str(node.id))

        async with engine._privacy.publication_guards((record,)):
            with journal_scope(engine._privacy, (record,)) as held:
                child = asyncio.create_task(inherited())
                await entered.wait()
                await asyncio.sleep(0)
                assert not child.done()
                # Native deadline checks remain usable while the owner holds it.
                await asyncio.to_thread(journal_deadline)
                assert (
                    await engine._graph_store.get_node(str(node.id))
                ).lifecycle_state == LifecycleState.TENTATIVE
        assert held.active is False
        with pytest.raises(PrivacyDenied):
            held.check()
        await asyncio.wait_for(child, 5)
        assert (
            await engine.get_node(str(node.id), user_id=owner)
        ).lifecycle_state == LifecycleState.STABLE
    finally:
        if child is not None and not child.done():
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)
        await engine.close()
