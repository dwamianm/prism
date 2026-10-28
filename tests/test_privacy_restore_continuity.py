"""Restore keeps every acknowledged erasure, whatever the artifact declares (#326 AC5).

These cases come from the #326 review probes. Each erased or blocked memory
must stay unavailable after export and restore.
"""

import json
from uuid import UUID, uuid4

from prme import MemoryEngine
from prme.privacy.erasure import ErasureRequest
from prme.storage.privacy_export import export_protected_pack, restore_protected_export
from tests.privacy_journal_support import journal_engine, local_analyzer_fixture  # noqa: F401
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.test_privacy_restore import _local_target

ERASED = "Mara likes oranges."
KEPT = "Mara prefers tea."


async def test_restore_ignores_a_checkpoint_copied_from_a_newer_export(tmp_path, analyzer, native):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, "directory", analyzer)
    owner = str(policy.owner_id)
    erased = await engine.store(ERASED, user_id=owner)
    await engine.store(KEPT, user_id=owner)
    old = tmp_path / "old.json"
    await export_protected_pack(engine, old)
    await engine.request_erasure(ErasureRequest(request_id=uuid4(), scope=policy.scope, event_ids=(UUID(erased),)),
                                 user_id=owner)
    await engine.process_erasures(user_id=owner)
    head = (await export_protected_pack(engine, tmp_path / "current.json")).ledger_checkpoint
    config = engine._config
    await engine.close()
    # Forge: the old snapshot claims the current ledger position.
    body = json.loads(old.read_bytes())
    body["manifest"]["ledger_checkpoint"] = json.loads(head.model_dump_json())
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(body))
    target = _local_target(config, tmp_path / "restored")
    await restore_protected_export(forged, target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
    restored = await MemoryEngine.create(target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
    try:
        contents = [row[0] for row in await restored._privacy.repository._query("SELECT content FROM nodes", ())]
        assert ERASED not in contents and KEPT in contents
        live = await restored._privacy.erasure.current_records(await restored._privacy.repository._read("1=1", ()))
        assert UUID(erased) not in {record.event_id for record in live}
    finally:
        await restored.close()


async def test_export_refuses_an_accepted_erasure_until_it_is_processed(tmp_path, analyzer, native):
    import pytest
    from prme.privacy import PrivacyDenied

    engine, runtime, policy, *_ = await journal_engine(tmp_path, "directory", analyzer, capture="none",
                                                       evidence_use="diagnostics_only", evidence_lifetime_seconds=None,
                                                       unlimited_evidence=False)
    owner = str(policy.owner_id)
    erased = await engine.store(ERASED, user_id=owner)
    await engine.store(KEPT, user_id=owner)
    record = await engine._privacy.repository.get(erased, user_id=owner)
    assert record.evidence is None and record.processing is None and record.retry is None
    request = ErasureRequest(request_id=uuid4(), scope=policy.scope, event_ids=(UUID(erased),))
    assert (await engine.request_erasure(request, user_id=owner)).state == "blocked_from_access"
    artifact = tmp_path / "pending.json"
    try:
        # Blocked but not yet purged: a copy now would carry it past the block.
        with pytest.raises(PrivacyDenied, match="privacy_erased"):
            await export_protected_pack(engine, artifact)
        assert not artifact.exists()
        await engine.process_erasures(user_id=owner)
        await export_protected_pack(engine, artifact)
        contents = [node["content"] for node in json.loads(artifact.read_bytes())["nodes"]]
        assert ERASED not in contents and KEPT in contents
    finally:
        await engine.close()


async def test_an_erased_owner_reads_empty_not_damaged(tmp_path, analyzer, native):
    engine, runtime, policy, *_ = await journal_engine(tmp_path, "directory", analyzer)
    owner = str(policy.owner_id)
    erased = await engine.store(ERASED, user_id=owner)
    await engine.request_erasure(ErasureRequest(request_id=uuid4(), scope=policy.scope, event_ids=(UUID(erased),)),
                                 user_id=owner)
    await engine.process_erasures(user_id=owner)
    config = engine._config.model_copy(update={"privacy_policy": None})
    await engine.close()
    engine = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
    try:
        # The content-free tombstone row remains, but it is a completed erasure,
        # not a damaged row, so the protected-format check does not refuse it.
        rows = await engine._privacy.repository._query("SELECT content FROM events WHERE user_id=?", (owner,))
        assert rows == [("",)]
        assert await engine._privacy.repository.has_objects(owner) is False
    finally:
        await engine.close()
