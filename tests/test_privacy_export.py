"""Actual closed exports; component evidence, not full EM08 restoration proof."""
import asyncio
import base64
from contextlib import asynccontextmanager
import copy
import hashlib
import json
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy.erasure import ErasureRequest
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import PrivacyPortabilityError
from prme.storage.privacy_export import export_protected_pack, read_protected_export, restore_protected_export
from tests.privacy_portability_support import fresh_config
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture, logical_dump,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM03_export_effect_failure_and_current_ledger(tmp_path, layout, analyzer, native, monkeypatch, capsys):
    """Actual destination fault and erasure serialization at export publication."""
    from prme.storage import privacy_export

    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    source_only = b"EM03_EXPORT_ONLY_SOURCE"
    markers = [source_only, b"BEGIN PRIVATE KEY", b"BEGIN ENCRYPTED PRIVATE KEY"]
    tasks = []
    try:
        wrapper = next(iter(runtime.protector.registry._wrappers.values()))
        wrap = wrapper.wrap_key
        async def observed_wrap(key, descriptor, context):
            markers.extend((key, base64.b64encode(key), key.hex().encode()))
            return await wrap(key, descriptor, context)
        with monkeypatch.context() as patch:
            patch.setattr(wrapper, "wrap_key", observed_wrap)
            event = await engine.store("Mara likes tea. " + source_only.decode(),
                retrieval_content="Mara likes tea.", user_id=owner)
        assert len(markers) > 3
        record = await engine._privacy.repository.get(event, user_id=owner)
        before = await logical_dump(engine, tmp_path / "source-before.jsonl")
        native_reads = native.reads

        failed = tmp_path / "failed-export.json"
        temporary_seen = []
        def failed_link(source, destination):
            assert destination == failed and source.suffix == ".export"
            assert source.parent == failed.parent and not source.stat().st_mode & 0o077
            data = source.read_bytes()
            assert data and all(marker not in data for marker in markers)
            temporary_seen.append(source)
            raise OSError("synthetic export publication failure")
        with monkeypatch.context() as patch:
            patch.setattr(privacy_export.os, "link", failed_link)
            with pytest.raises(OSError, match="^synthetic export publication failure$"):
                await export_protected_pack(engine, failed)
        assert len(temporary_seen) == 1 and not temporary_seen[0].exists()
        assert not failed.exists() and not list(tmp_path.glob(".*.export"))
        assert await engine._privacy.repository.get(event, user_id=owner) == record
        assert await logical_dump(engine, tmp_path / "source-after-fault.jsonl") == before
        assert native.reads == native_reads

        captured, release, attempted, acquired = (asyncio.Event() for _ in range(4))
        capture, publish, ledger_guard = privacy_export._capture, privacy_export._publish_bytes, runtime.erasure_ledger.guard
        order = []
        erasing = None
        async def paused_capture(*args, **kwargs):
            bundle = await capture(*args, **kwargs)
            captured.set()
            await release.wait()
            return bundle
        @asynccontextmanager
        async def observed_guard():
            is_request = asyncio.current_task() is erasing
            if is_request:
                attempted.set()
            async with ledger_guard():
                if is_request:
                    acquired.set()
                yield
        def observed_publish(path, data):
            assert attempted.is_set() and not acquired.is_set()
            assert all(marker not in data for marker in markers)
            publish(path, data)
            order.append("published")
        async def erase():
            status = await engine.request_erasure(ErasureRequest(request_id=uuid4(),
                scope=policy.scope, event_ids=(UUID(event),)), user_id=owner)
            order.append("erasure_acknowledged")
            return status
        retained = tmp_path / "pre-acknowledgement-export.json"
        with monkeypatch.context() as patch:
            patch.setattr(privacy_export, "_capture", paused_capture)
            patch.setattr(privacy_export, "_publish_bytes", observed_publish)
            patch.setattr(runtime.erasure_ledger, "guard", observed_guard)
            exporting = asyncio.create_task(export_protected_pack(engine, retained))
            tasks.append(exporting)
            await asyncio.wait_for(captured.wait(), 5)
            erasing = asyncio.create_task(erase())
            tasks.append(erasing)
            await asyncio.wait_for(attempted.wait(), 5)
            assert not acquired.is_set() and not erasing.done() and not retained.exists()
            release.set()
            manifest = await asyncio.wait_for(exporting, 5)
            status = await asyncio.wait_for(erasing, 5)
        assert acquired.is_set() and order == ["published", "erasure_acknowledged"]
        assert status.state == "blocked_from_access"
        retained_hash = hashlib.sha256(retained.read_bytes()).hexdigest()
        assert read_protected_export(retained).admissions[0].evidence == record.evidence
        async with runtime.erasure_ledger.guard():
            current = await runtime.erasure_ledger.current_head(manifest.format.store_id)
        assert current.sequence > manifest.ledger_checkpoint.sequence
        after = tmp_path / "after-acknowledgement-export.json"
        with pytest.raises(PrivacyDenied, match="^privacy_erased$"):
            await export_protected_pack(engine, after)
        assert not after.exists() and not list(tmp_path.glob(".*.export"))
        assert hashlib.sha256(retained.read_bytes()).hexdigest() == retained_hash
        assert native.reads == native_reads
        await logical_dump(engine, tmp_path / "source-after-fence.jsonl")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await engine.close()
    # Local native database/WAL/index files, fixture logical SQL dumps, ledger,
    # public registry and export are all task-owned. PostgreSQL server files and
    # external backups are deliberately outside this fresh scan's scope.
    for path in tmp_path.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert all(marker not in path.read_bytes() for marker in markers), path.name
    captured_output = capsys.readouterr()
    assert all(marker not in (captured_output.out + captured_output.err).encode() for marker in markers)


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_closed_default_and_active_exports(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer)
    source_only, memory = "SOURCE_ONLY_EXPORT_326", "Mara likes tea."
    try:
        event = await engine.store(memory + " " + source_only, retrieval_content=memory, user_id=str(policy.owner_id))
        record = await engine._privacy.repository.get(event, user_id=str(policy.owner_id))
        native_reads = native.reads
        async def forbidden(*args, **kwargs):
            pytest.fail("ordinary export attempted private unwrap")
        monkeypatch.setattr(runtime.protector, "open", forbidden)
        for mode in ("protected", "active_memory_only"):
            path = tmp_path / (mode + ".json")
            manifest = await export_protected_pack(engine, path, mode=mode)
            bundle = read_protected_export(path)
            raw = path.read_bytes()
            assert source_only.encode() not in raw and b"BEGIN PRIVATE KEY" not in raw
            assert native.reads == native_reads
            assert manifest.admitted_memory_is_sensitive and not manifest.contains_private_keys
            assert [node.content for node in bundle.nodes] == [memory]
            if mode == "protected":
                assert bundle.admissions[0].evidence == record.evidence
                assert len(bundle.events) == 1 and bundle.operations and bundle.embeddings
            else:
                assert not bundle.events and not bundle.operations and not bundle.work and not bundle.embeddings
                assert bundle.admissions[0].evidence is None and "restoration" in manifest.omitted
            before = raw
            with pytest.raises(PrivacyPortabilityError, match="^privacy_destination_occupied$"):
                await export_protected_pack(engine, path, mode=mode)
            assert path.read_bytes() == before
        # Unknown payload fields and copied source paragraphs are not silently
        # admitted by a permissive generic JSON exporter.
        await engine._privacy.repository._query(
            "UPDATE operations SET payload=? WHERE op_type='DIRECT_STORE_REQUESTED'",
            (json.dumps({"unexpected_source": source_only}),))
        refused = tmp_path / "refused.json"
        with pytest.raises(PrivacyPortabilityError):
            await export_protected_pack(engine, refused)
        assert not refused.exists()
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_terminal_and_alias_history_export_restore(tmp_path, layout, analyzer, native):
    """Actual existing history writers; no held public reader or replay claim."""
    from prme.storage import _snapshot_json
    from prme.storage.alias_proposal import AliasProposalRecord, ProtectedAliasProposalRecord, read_record
    from prme.storage.alias_review import ProtectedAliasReviewRecord, read_review_record
    from prme.types import NodeType

    engine, runtime, policy, *_ = await journal_engine(tmp_path, layout, analyzer, processing_ceiling_seconds=60)
    owner = str(policy.owner_id)
    source_only = "SOURCE_ONLY_HISTORY_EXPORT_326"
    nodes = set()
    proposals, reviews = [], []
    try:
        for decision in ("accepted", "rejected"):
            pair = []
            for name in (f"Acme {decision} Pro", f"Acme {decision} Professional"):
                await engine.store(f"{name}. {source_only}.", retrieval_content=name,
                                   user_id=owner, node_type=NodeType.ENTITY)
                present = {str(row[0]) for row in await engine._privacy.repository._query("SELECT id FROM nodes", ())}
                created = present - nodes
                assert len(created) == 1
                pair.extend(created)
                nodes = present
            proposal = await engine._graph_store.propose_alias(*pair, user_id=owner, alias_type="semantic", score=0.92)
            assert proposal.applied
            proposals.append(UUID(proposal.operation_id))
            review = await engine._graph_store.review_alias_proposal(proposal.operation_id, user_id=owner,
                decision=decision, reviewer_id=str(uuid4()), reason="Catalog reviewed.")
            assert review.applied
            reviews.append(UUID(review.operation_id))

        # Real queued claims/slot transactions produce both existing terminal
        # records. No extraction/model send or reconstructed source is needed.
        cancelled = await engine.ingest("Mara likes tea. " + source_only, user_id=owner)
        claim = await engine._event_store.extraction_work.claim(user_id=owner, event_id=cancelled)
        assert await engine._privacy.processing.terminate(claim, state="cancelled")
        expired = await engine.ingest("Mara likes apples. " + source_only, user_id=owner)
        pending = await engine._privacy.repository.get(expired, user_id=owner)
        runtime.clock.now = pending.snapshot.processing_deadline
        assert await engine._privacy.processing.expire(user_id=owner) == 1
        assert not runtime.extractor.sends
        records = {record.event_id: record for record in await engine._privacy.repository.for_owner(owner)}
        assert records[UUID(cancelled)].processing_state == "cancelled" and records[UUID(cancelled)].processing is None
        assert records[UUID(expired)].processing_state == "expired" and records[UUID(expired)].processing is None
        original = {str(row[0]): row[1] for row in await engine._privacy.repository._query("SELECT id,payload FROM operations", ())}
        native_reads = native.reads
        artifact = tmp_path / "history-protected.json"
        await export_protected_pack(engine, artifact)
        bundle = read_protected_export(artifact)
        assert native.reads == native_reads
        assert {str(op.id): op.payload for op in bundle.operations} == original
        assert len(bundle.nodes) == 4 and len(bundle.edges) == 3
        assert source_only not in artifact.read_text() and "PRIVATE KEY" not in artifact.read_text()
        by_operation = {op.id: op for op in bundle.operations}
        for identifier in proposals:
            record = read_record(by_operation[identifier].payload)
            assert isinstance(record, ProtectedAliasProposalRecord) and record.diagnostics == "not_retained"
        for identifier in reviews:
            record = read_review_record(by_operation[identifier].payload)
            assert isinstance(record, ProtectedAliasReviewRecord) and record.diagnostics == "not_retained"
        terminals = [op for op in bundle.operations if op.op_type == "PRIVACY_PROCESSING_TERMINAL"]
        assert {json.loads(op.payload)["state"] for op in terminals} == {"cancelled", "expired"}
        assert all(json.loads(op.payload)["replay"] == "unavailable" for op in terminals)
        terminal_work = {work.event_id: work for work in bundle.work if work.kind == "extraction"}
        assert all(terminal_work[identifier].status == "failed" and terminal_work[identifier].generation > 0
                   for identifier in (UUID(cancelled), UUID(expired)))
        active_path = tmp_path / "history-active.json"
        await export_protected_pack(engine, active_path, mode="active_memory_only")
        active = read_protected_export(active_path)
        assert {node.id for node in active.nodes} == {node.id for node in bundle.nodes}
        assert not active.edges and not active.operations and not active.work
        assert "alias_relationships" in active.manifest.omitted and "restoration" in active.manifest.omitted
        assert native.reads == native_reads
        source_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        config = engine._config
        await engine.close()

        async with fresh_config(config, tmp_path / "restored-history", layout, restore=True) as destination:
            await restore_protected_export(artifact, destination, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            restored = await MemoryEngine.create(destination, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            try:
                roundtrip_path = tmp_path / "history-roundtrip.json"
                await export_protected_pack(restored, roundtrip_path)
                roundtrip = read_protected_export(roundtrip_path)
                assert {op.id: op.payload for op in roundtrip.operations} == {op.id: op.payload for op in bundle.operations}
                assert roundtrip.edges == bundle.edges
                # Restore invalidates every incoming extraction claim exactly
                # once. It preserves the saved attempt/order/status/deadlines;
                # a snapshot lease is not authority in the restored store.
                expected_work = tuple(work.model_copy(update={"generation": work.generation + 1,
                    "lease_expires_at": None, "status": "pending" if work.status == "running" else work.status})
                    if work.kind == "extraction" else work for work in bundle.work)
                assert roundtrip.work == expected_work
                assert roundtrip.admissions == bundle.admissions
                with pytest.raises(PrivacyDenied, match="^privacy_processing_unavailable$"):
                    await restored._privacy.processing.begin(claim)
                assert not await restored._event_store.extraction_work.retry(cancelled, user_id=owner)
                assert not await restored._event_store.extraction_work.retry(expired, user_id=owner)

                # Six finite refusals inside this same family/layout selection.
                # Restore parses before touching this already-restored target.
                invalid = []
                def changed(identifier):
                    data = bundle.model_dump(mode="json")
                    return data, next(op for op in data["operations"] if op["id"] == str(identifier))
                data, operation = changed(proposals[0])
                body = json.loads(operation["payload"])
                body["sha256"] = "0" * 64
                operation["payload"] = json.dumps(body)
                invalid.append(("checksum", data))
                data, operation = changed(reviews[0])
                operation["actor_id"] = str(uuid4())
                invalid.append(("context", data))
                data, operation = changed(terminals[0].id)
                body = json.loads(operation["payload"])
                body["unknown_source"] = source_only
                operation["payload"] = json.dumps(body)
                invalid.append(("terminal-extra", data))
                data, operation = changed(terminals[0].id)
                body = json.loads(operation["payload"])
                body["source_id"] = str(uuid4())
                operation["payload"] = json.dumps(body)
                invalid.append(("terminal-context", data))
                data, operation = changed(proposals[0])
                operation["payload"] = '{"record":"' + source_only + '",' + operation["payload"][1:]
                invalid.append(("duplicate-hidden-content", data))
                data, operation = changed(proposals[0])
                protected = read_record(operation["payload"])
                edge = protected.edge.model_copy(update={"metadata": {"relation": "alias",
                    "alias_type": protected.alias_type, "identity_verified": False,
                    "alias_operation_id": str(protected.operation_id)}})
                legacy = AliasProposalRecord(operation_id=protected.operation_id, alias_type=protected.alias_type,
                    score=protected.score, left_before=protected.left_before, right_before=protected.right_before, edge=edge)
                raw = _snapshot_json.dumps(legacy.model_dump(mode="python"))
                legacy_payload = {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
                assert type(read_record(legacy_payload)) is AliasProposalRecord
                operation["payload"] = json.dumps(legacy_payload)
                invalid.append(("legacy-family", data))
                for name, data in invalid:
                    rejected = tmp_path / (name + ".json")
                    rejected.write_text(json.dumps(copy.deepcopy(data)))
                    with pytest.raises(PrivacyPortabilityError) as denied:
                        await restore_protected_export(rejected, destination, privacy_runtime=runtime, embedding_provider=runtime.embedder)
                    assert denied.value.code in {"privacy_export_invalid", "privacy_legacy_unclassified"}
                after = {str(row[0]): row[1] for row in await restored._privacy.repository._query("SELECT id,payload FROM operations", ())}
                assert after == original
            finally:
                await restored.close()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == source_hash
        assert native.reads == native_reads and not runtime.extractor.sends
    finally:
        if not engine._closed:
            await engine.close()
