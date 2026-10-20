"""EM07/AC2 real migration components; full public parity remains held on #323."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.privacy.erasure import ErasureRequest
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import PrivacyPortabilityError
from prme.privacy.retry_commitments import NativeRetryCommitter
from prme.storage.privacy_migration import inspect_privacy_migration, migrate_privacy_pack, migration_status, _read_snapshot
from tests.privacy_portability_support import fresh_config
from tests.privacy_journal_support import journal_engine, isolated_privacy_postgres, local_analyzer_fixture  # noqa: F401
from tests.privacy_journal_support import native as native_fixture  # noqa: F401

FIXTURE = json.loads((Path(__file__).parent / "fixtures/privacy/migration-v1.json").read_text())


def hashes(path):
    return {str(file.relative_to(path)): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in path.rglob("*") if file.is_file()}


@pytest.mark.parametrize("layout", ["directory", "pack", "postgres", "closed_fernet"])
async def test_EM07_direct_migration_resume_exact_identity_current_erasure(tmp_path, layout, analyzer, native):
    template, runtime, policy, custody, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    base = template._config
    await template.close()
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    owner = str(policy.owner_id)
    local_layout = "directory" if layout == "closed_fernet" else layout
    async with fresh_config(base, tmp_path / "legacy", local_layout) as original, fresh_config(base, tmp_path / "migrated", local_layout, restore=True) as target:
        original = original.model_copy(update={"privacy_policy": None})
        if layout == "closed_fernet":
            original = type(original).model_validate({**original.model_dump(), "encryption_enabled": True, "encryption_key": "passphrase:task-owned-migration-source"})
        source = await MemoryEngine.create(original, embedding_provider=runtime.embedder)
        try:
            event_ids = []
            for memory in FIXTURE["admitted"]:
                event_ids.append(await source.store(memory + " " + FIXTURE["source_only"] + " " + FIXTURE["prohibited"],
                    retrieval_content=memory, user_id=owner))
            if layout == "closed_fernet":
                source._conn.execute("DELETE FROM vector_payloads")
        finally:
            await source.close()
        before = await _read_snapshot(original)
        source_files = hashes(tmp_path / "legacy") if local_layout == "directory" else None
        plan = await inspect_privacy_migration(original, policy=policy)
        assert not plan.protected and plan.legacy_plaintext_present
        assert [row.copy_id for row in plan.copies] == [f"C{number:02}" for number in range(1, 46)]
        assert "retry_commitment" in plan.required_key_purposes
        assert "legacy_history_requires_explicit_disposition" in plan.unavailable_features
        assert FIXTURE["source_only"] not in plan.model_dump_json() and FIXTURE["prohibited"] not in plan.model_dump_json()
        assert (await _read_snapshot(original)).fingerprint == before.fingerprint
        if source_files is not None:
            assert hashes(tmp_path / "legacy") == source_files
        with pytest.raises(PrivacyPortabilityError, match="^privacy_legacy_unclassified$"):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=tmp_path / "refused")
        assert not (tmp_path / "refused").exists()
        saved = {}
        async def interrupt(stage, index, engine):
            if stage == "after_batch" and index == 1:
                records = await engine._privacy.repository._read("1=1", ())
                records = sorted(records, key=lambda record: str(record.event_id))
                saved["erased"], saved["survives"] = records
                await engine._privacy.erasure.request(ErasureRequest(request_id=uuid4(), event_ids=(records[0].event_id,)), user_id=owner)
                raise RuntimeError("synthetic migration interruption")
        job_path = tmp_path / "migration-job"
        with pytest.raises(RuntimeError, match="synthetic migration interruption"):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=job_path, unavailable_legacy_history=True, _fault=interrupt)
        partial = migration_status(job_path)
        assert partial.state == "interrupted" and partial.completed == 2 and partial.total == 3
        result = await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
            job_path=job_path, unavailable_legacy_history=True, resume=True)
        assert result.job_id == partial.job_id and result.state == "complete"
        assert (await _read_snapshot(original)).fingerprint == before.fingerprint
        if source_files is not None:
            assert hashes(tmp_path / "legacy") == source_files
        restored = await MemoryEngine.create(target, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        try:
            record = await restored._privacy.repository.get(str(saved["survives"].event_id), user_id=owner)
            assert record == saved["survives"]
            assert record.evidence.context.event_id in set(map(UUID, event_ids))
            assert record.snapshot == saved["erased"].snapshot
            rows = await restored._privacy.repository._query("SELECT id,content,evidence_refs FROM nodes", ())
            assert len(rows) == 2
            assert all(str(saved["erased"].event_id) not in str(row[2]) for row in rows)
            original_node_ids = {str(row["id"]) for row in before.tables["nodes"]}
            assert all(str(row[0]) in original_node_ids and row[1] in FIXTURE["admitted"] for row in rows)
            payload = (job_path / "protected-export.json").read_bytes()
            assert FIXTURE["source_only"].encode() not in payload and FIXTURE["prohibited"].encode() not in payload
            assert b'"payload_status":"unavailable"' in payload
            assert b"BEGIN PRIVATE KEY" not in payload
        finally:
            await restored.close()
        # Resume authentication rejects edits to the saved lifetime even when
        # the edited JSON is structurally valid; no new retry key is provisioned.
        import duckdb
        from datetime import timedelta
        from prme.privacy.policy import snapshot
        with duckdb.connect(str(job_path / "migration.duckdb")) as conn:
            body, authentication = map(json.loads, conn.execute("SELECT body,authentication FROM privacy_migration_job").fetchone())
            changed = snapshot(policy, None, saved["survives"].snapshot.admitted_at - timedelta(seconds=1)).model_dump(mode="json")
            body["snapshot"] = changed
            authentication["context"]["snapshot"] = changed
            conn.execute("UPDATE privacy_migration_job SET body=?,authentication=?", [json.dumps(body), json.dumps(authentication)])
        with pytest.raises((PrivacyDenied, PrivacyPortabilityError)):
            await migrate_privacy_pack(original, target, privacy_runtime=runtime, embedding_provider=runtime.embedder,
                job_path=job_path, unavailable_legacy_history=True, resume=True)
