"""AP01–AP07: real backends and evidence selection with explicit component doubles."""

import base64
import asyncio
import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.privacy import PrivacyDenied
from tests.privacy_fakes import config_for, make_runtime, open_engine, policy_for
from tests.privacy_fakes import isolated_privacy_postgres as isolated_privacy_postgres

SOURCE = "Alex does not like tea unless it is cold."


async def test_product_defaults_require_explicit_protected_group_override(tmp_path, monkeypatch):
    from prme import PRMEConfig
    from tests.conftest import product_defaults

    product_defaults(monkeypatch)
    r = make_runtime()
    p = policy_for(r)
    config = PRMEConfig(db_path=str(tmp_path / "memory.duckdb"),
                        vector_path=str(tmp_path / "vectors.usearch"),
                        lexical_path=str(tmp_path / "lexical"), privacy_policy=p)
    assert config.enable_owner_group_links and config.scoring.rrf_graph_seed_count
    with pytest.raises(PrivacyDenied, match="privacy_configuration_invalid"):
        await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
    assert not list(tmp_path.iterdir())
    assert not r.sanitizer.calls and not r.protector.calls and not r.embedder.sends

    engine = await MemoryEngine.create(config.model_copy(update={"enable_owner_group_links": False}),
                                      embedding_provider=r.embedder, privacy_runtime=r)
    try:
        event_id = await engine.store(SOURCE, user_id=str(p.owner_id))
        [node] = await engine.get_event_nodes(event_id, user_id=str(p.owner_id))
        saved = await engine._privacy.repository.get(event_id, user_id=str(p.owner_id))
        assert node.content == SOURCE and saved.snapshot.policy == p
        assert saved.processing is None and saved.evidence is None
        assert engine._config.scoring.rrf_graph_seed_count
    finally:
        await engine.close()


@pytest.mark.parametrize("window", ["naive", "reversed", "equal", "end_only", "ordered"])
async def test_protected_validity_window_precedes_all_content_effects(tmp_path, window):
    engine, r, p = await open_engine(tmp_path)
    start, end = r.clock(), r.clock() + timedelta(days=1)
    windows = {"naive": (start.replace(tzinfo=None), end), "reversed": (end, start),
               "equal": (start, start), "end_only": (None, end), "ordered": (start, end)}
    valid_from, valid_to = windows[window]
    try:
        if window == "ordered":
            event_id = await engine.store(SOURCE, user_id=str(p.owner_id), valid_from=valid_from, valid_to=valid_to)
            [node] = await engine.get_event_nodes(event_id, user_id=str(p.owner_id))
            assert (node.valid_from, node.valid_to) == (start, end)
        else:
            with pytest.raises(PrivacyDenied, match="privacy_unsupported_input"):
                await engine.store(SOURCE, user_id=str(p.owner_id), valid_from=valid_from, valid_to=valid_to)
            assert r.sanitizer.calls == [] and r.embedder.sends == [] and r.extractor.sends == []
            assert await engine._event_store.get_by_user(str(p.owner_id)) == []
            assert await engine._graph_store.query_nodes(user_id=str(p.owner_id)) == []
    finally:
        await engine.close()


async def test_owner_group_default_is_rejected_before_store_creation(tmp_path):
    r = make_runtime()
    p = policy_for(r)
    config = config_for(tmp_path, policy=p).model_copy(update={"enable_owner_group_links": True})
    with pytest.raises(PrivacyDenied, match="privacy_configuration_invalid"):
        engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
        await engine.close()
    assert not list(tmp_path.iterdir()) and r.sanitizer.calls == [] and r.protector.calls == []


@pytest.fixture(params=["directory", "pack", "postgres"])
def layout(request):
    if request.param == "postgres" and not os.getenv("PRME_TEST_DATABASE_URL"):
        pytest.skip("isolated PostgreSQL not configured")
    return request.param


@pytest.mark.parametrize("capture,use", [("none", "diagnostics_only"), ("minimal", "diagnostics_only"), ("full", "runtime")])
async def test_AP01_valid_profiles_retain_memory_and_immutable_policy(tmp_path, layout, capture, use):
    runtime = make_runtime()
    policy = policy_for(runtime, capture=capture, evidence_use=use,
                        evidence_lifetime_seconds=3600 if capture != "none" else None)
    engine, _, _ = await open_engine(tmp_path, runtime=runtime, policy=policy, layout=layout)
    owner = str(policy.owner_id)
    try:
        event_id = await engine.store(SOURCE, user_id=owner)
        nodes = await engine.get_event_nodes(event_id, user_id=owner)
        assert len(nodes) == 1 and nodes[0].content == SOURCE
        saved = await engine._privacy.repository.get(event_id, user_id=owner)
        assert saved.snapshot.policy == policy and saved.processing is None
        assert (saved.evidence is None) == (capture == "none")
        event = await engine.get_event(event_id, user_id=owner)
        assert event.content == "" and SOURCE not in event.model_dump_json()
        assert saved.source_id != saved.representation_id
        inspection = await engine.inspect_privacy_policy(user_id=owner)
        assert inspection["unlimited_memory"] is True and not inspection["protected_product_ready"]
        assert inspection["providers"]["embedding"] == runtime.embedder.model_name
        assert SOURCE not in json.dumps(inspection)
    finally:
        await engine.close()


async def test_AP02_whole_batch_preflight_before_write_or_provider(tmp_path, layout):
    engine, runtime, policy = await open_engine(tmp_path, layout=layout)
    owner = str(policy.owner_id)
    try:
        with pytest.raises(PrivacyDenied):
            await engine.ingest_fast_many([{"content": SOURCE}, {"content": "[reject]"}], user_id=owner)
        assert await engine.get_events(owner) == []
        assert runtime.embedder.sends == [] and runtime.extractor.sends == []
        runtime.authority.denied.add("admission")
        with pytest.raises(PrivacyDenied):
            await engine.store("denied", user_id=owner)
        assert await engine._event_store.get_by_user(owner) == []
    finally:
        runtime.authority.denied.clear()
        await engine.close()


async def test_AP04_complete_minimal_capture_keeps_attribution_time_and_negation(tmp_path, layout):
    r = make_runtime()
    p = policy_for(r, capture="minimal", evidence_use="diagnostics_only", evidence_lifetime_seconds=3600)
    engine, _, _ = await open_engine(tmp_path, runtime=r, policy=p, layout=layout)
    try:
        event_id = await engine.ingest(SOURCE, speaker="Alex", user_id=str(p.owner_id), event_time=r.clock(), wait_for_extraction=True)
        record = await engine._privacy.repository.get(event_id, user_id=str(p.owner_id))
        evidence = json.loads(base64.b64decode(record.evidence.envelope))
        assert evidence["content"] == SOURCE and evidence["speaker"] == "Alex"
        assert evidence["event_time"] == r.clock().isoformat() and evidence["representation"] == "sanitized"
        nodes = await engine.get_event_nodes(event_id, user_id=str(p.owner_id))
        fact = next(n for n in nodes if n.metadata and "polarity" in n.metadata)
        assert fact.content.startswith("It is not true that ") and "unless it is cold" in fact.content
        assert fact.metadata["condition"] == "unless it is cold" and fact.metadata["polarity"] == "negative"
        assert "evidence_quote" not in fact.metadata
    finally:
        await engine.close()


@pytest.mark.parametrize("text,bound", [(SOURCE, 16), ("[ambiguous] " + SOURCE, 4096)])
async def test_AP05_unsafe_minimal_never_truncates_or_upgrades(tmp_path, text, bound):
    r = make_runtime()
    p = policy_for(r, capture="minimal", evidence_use="diagnostics_only", evidence_lifetime_seconds=3600, minimal_max_bytes=bound)
    engine, _, _ = await open_engine(tmp_path, runtime=r, policy=p)
    try:
        with pytest.raises(PrivacyDenied):
            await engine.store(text, user_id=str(p.owner_id))
        assert await engine.get_events(str(p.owner_id)) == [] and not r.embedder.sends
    finally:
        await engine.close()


async def test_AP06_no_evidence_async_work_removed_on_actual_commit(tmp_path, layout, monkeypatch):
    from prme.models.privacy_reads import ProtectedExtractionView

    engine, r, p = await open_engine(tmp_path, layout=layout)
    owner = str(p.owner_id)
    monkeypatch.setattr(engine._pipeline, "queue_extraction", lambda *args: None)
    try:
        event_id = await engine.ingest(SOURCE, user_id=owner)
        pending = await engine._privacy.repository.get(event_id, user_id=owner)
        assert pending.evidence is None and pending.processing is not None
        assert SOURCE not in pending.model_dump_json()
        result = await engine.process_extractions(user_id=owner)
        assert result.processed == 1
        complete = await engine._privacy.repository.get(event_id, user_id=owner)
        assert complete.processing is None and complete.evidence is None
        assert complete.processing_state == "complete" and complete.snapshot == pending.snapshot
        assert await engine.get_event_nodes(event_id, user_id=owner)
        assert await engine._event_store.get_extraction(event_id, user_id=owner) is None
        view = await engine.get_extraction(event_id, user_id=owner)
        assert isinstance(view, ProtectedExtractionView) and str(view.event_id) == event_id
        assert view.status == "unavailable" and view.source_availability.state == "not_captured"
        assert SOURCE not in view.model_dump_json()
    finally:
        await engine.close()


async def test_AP07_visual_refusal_before_decoder_blob_or_provider(tmp_path, monkeypatch):
    from prme.vision.observe import observe_source
    engine, r, p = await open_engine(tmp_path)
    monkeypatch.setattr(engine, "vision_provider", lambda: pytest.fail("vision provider initialized"))
    try:
        for call in (engine.observe, lambda source, **kw: observe_source(engine, source, **kw)):
            with pytest.raises(PrivacyDenied, match="unsupported"):
                await call(b"unsupported image", user_id=str(p.owner_id))
        assert await engine.get_events(str(p.owner_id)) == []
    finally:
        await engine.close()


async def test_missing_capability_preflight_precedes_store_creation(tmp_path):
    r = make_runtime()
    p = policy_for(r)
    config = config_for(tmp_path, policy=p)
    with pytest.raises(PrivacyDenied, match="capability_unavailable"):
        await MemoryEngine.create(config, embedding_provider=r.embedder)
    assert not list(tmp_path.iterdir())


async def test_retry_uses_saved_snapshot_and_private_request_commitment(tmp_path, layout):
    engine, r, p = await open_engine(tmp_path, layout=layout)
    owner, request = str(p.owner_id), uuid4()
    try:
        first = await engine.ingest_fast_many([{"content": SOURCE}], user_id=owner, request_id=request)
        saved = await engine._privacy.repository.get(first[0], user_id=owner)
        from datetime import timedelta
        r.clock.now += timedelta(seconds=20)
        assert await engine.ingest_fast_many([{"content": SOURCE}], user_id=owner, request_id=request) == first
        again = await engine._privacy.repository.get(first[0], user_id=owner)
        assert again == saved
        with pytest.raises(PrivacyDenied, match="retry_conflict"):
            await engine.ingest_fast_many([{"content": "changed"}], user_id=owner, request_id=request)
        r.retry_committer.keys.clear()
        with pytest.raises(PrivacyDenied, match="retry_unavailable"):
            await engine.ingest_fast_many([{"content": SOURCE}], user_id=owner, request_id=request)
    finally:
        await engine.close()


async def test_concurrent_retry_and_reopen_retain_original_default(tmp_path, layout):
    engine, r, p = await open_engine(tmp_path, layout=layout)
    owner, request = str(p.owner_id), uuid4()
    try:
        first, second = await asyncio.gather(*(
            engine.ingest_fast_many([{"content": SOURCE}], user_id=owner, request_id=request) for _ in range(2)))
        assert first == second
        saved = await engine._privacy.repository.get(first[0], user_id=owner)
        assert len(await engine._event_store.get_by_user(owner)) == 1
        config = engine._config.model_copy(update={"privacy_policy": None})
    finally:
        await engine.close()
    reopened = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
    try:
        assert await reopened.ingest_fast_many([{"content": SOURCE}], user_id=owner, request_id=request) == first
        assert await reopened._privacy.repository.get(first[0], user_id=owner) == saved
    finally:
        await reopened.close()


async def test_capability_errors_are_content_free_before_durable_effect(tmp_path, monkeypatch):
    r = make_runtime()
    p = policy_for(r, capture="full", evidence_use="diagnostics_only", evidence_lifetime_seconds=300)
    engine, _, _ = await open_engine(tmp_path, runtime=r, policy=p)

    async def fail(*args, **kwargs):
        raise ValueError("private capability error sentinel")

    monkeypatch.setattr(r.protector, "seal", fail)
    try:
        with pytest.raises(PrivacyDenied) as error:
            await engine.store(SOURCE, user_id=str(p.owner_id))
        assert str(error.value) == "privacy_capability_unavailable"
        assert await engine._event_store.get_by_user(str(p.owner_id)) == []
        assert not r.embedder.sends
    finally:
        await engine.close()
