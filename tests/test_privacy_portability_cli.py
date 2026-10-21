"""AC3/AC6 actual parser/actions; only external provider/native OS boundaries fake.

These are CLI subchecks, not complete frozen cases or held #323 retrieval proof.
"""
from dataclasses import replace
from datetime import timedelta
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.cli import build_parser
from prme.privacy.cli import failure_guidance
from prme.privacy.crypto import decode64, decode_envelope
from prme.privacy.erasure import ErasureRequest
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import PrivacyPortabilityError
from prme.privacy.retry_commitments import NativeRetryCommitter
from prme.storage.privacy_export import export_protected_pack, read_protected_export, restore_protected_export
from prme.storage.privacy_migration import _read_snapshot
from tests.privacy_portability_support import fresh_config
from tests.privacy_journal_support import LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture  # noqa: F401
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


def engine_factory():
    raise AssertionError("explicit test factory required")


def runtime_factory():
    raise AssertionError("explicit test factory required")


def embedding_factory():
    raise AssertionError("explicit test factory required")


async def run(values):
    args = build_parser().parse_args(values)
    await args.func(args)


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_AC6_EM05_EM08_cli_selected_diagnostics_keys_export_and_erasure(tmp_path, layout, analyzer, native, monkeypatch, capsys):
    import structlog
    monkeypatch.setattr(structlog._config._CONFIG, "logger_factory", structlog.PrintLoggerFactory(file=sys.stderr))
    engine, runtime, policy, custody, evidence, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    source = "Mara likes tea. CLI_ONLY_SOURCE_326. private326@example.com"
    event = await engine.store(source, retrieval_content="Mara likes tea.", user_id=owner)
    other = await engine.store("Mara likes apples.", user_id=owner)
    config = engine._config
    await engine.close()
    opened = []
    async def fresh():
        value = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        opened.append(value)
        return value
    monkeypatch.setattr(__name__ + ".engine_factory", fresh)
    reference = __name__ + ":engine_factory"
    selected = ["--engine-factory", reference]
    capsys.readouterr()
    await run(["privacy", "policy", *selected, "--owner", owner])
    inspection = json.loads(capsys.readouterr().out)
    assert "CLI_ONLY_SOURCE_326" not in json.dumps(inspection)
    public_reads = native.reads
    export_directory = tmp_path / "ordinary-exports"
    export_directory.mkdir()
    artifact = export_directory / "protected.json"
    await run(["privacy", "export", *selected, "--output", str(artifact)])
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["mode"] == "protected" and manifest["contains_private_keys"] is False
    assert native.reads == public_reads
    assert "CLI_ONLY_SOURCE_326" not in artifact.read_text() and "private326@example.com" not in artifact.read_text()
    assert len(read_protected_export(artifact).admissions) == 2
    active = export_directory / "active.json"
    await run(["privacy", "export", *selected, "--output", str(active), "--mode", "active_memory_only"])
    assert json.loads(capsys.readouterr().out)["admitted_memory_is_sensitive"]
    assert all(row.evidence is None for row in read_protected_export(active).admissions)
    base = ["--registry", str(custody.path), "--project", str(policy.project_id), "--owner", owner,
            *selected, "--audit", str(tmp_path / "audit.jsonl")]
    await run(["privacy-keys", "unlock", *base, "--event", event, "--event", other, "--action", "verify"])
    verified = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {row["event_id"] for row in verified} == {event, other} and all(row["authenticated"] for row in verified)
    await run(["privacy-keys", "unlock", *base, "--event", event, "--action", "reveal"])
    revealed = json.loads(capsys.readouterr().out)
    assert revealed["event_id"] == event and revealed["source"]["representation"] == "sanitized"
    assert "CLI_ONLY_SOURCE_326" in json.dumps(revealed) and "private326@example.com" not in json.dumps(revealed)
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "CLI_ONLY_SOURCE_326" not in audit and "private326@example.com" not in audit
    assert "authorized" in audit and "finished" in audit
    recovery = tmp_path / "separate-recovery.pem"
    # The deliberate key artifact is outside both ordinary exports and pack.
    for action in ("recovery-export", "recovery-import"):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"task-owned-cli-recovery-326\n")
            os.close(write_fd)
            key_args = ["privacy-keys", action, "--registry", str(custody.path),
                "--key-version", str(evidence.ref.key_version), "--artifact", str(recovery), "--passphrase-fd", str(read_fd)]
            if action == "recovery-export":
                # The actual portable artifact is the selected exclusion
                # domain, including PostgreSQL's logical export. A nonexistent
                # local db_path is not a PostgreSQL pack directory.
                key_args += ["--pack", str(artifact)]
            else:
                native.values.clear()
                with pytest.raises(PrivacyDenied) as lost:
                    await run(["privacy-keys", "unlock", *base, "--event", event, "--action", "verify"])
                assert "Permanent" in failure_guidance(lost.value) or "permanently" in failure_guidance(lost.value)
                assert "CLI_ONLY_SOURCE_326" not in capsys.readouterr().out
                # Existing admitted rows remain stored under separate protection.
                current = await fresh()
                try:
                    rows = await current._privacy.repository._query("SELECT content FROM nodes", ())
                    assert sorted(row[0] for row in rows) == ["Mara likes apples.", "Mara likes tea."]
                    private_reads = native.reads
                    response = await asyncio.wait_for(current.retrieve("Mara likes tea.", user_id=owner,
                        scope=policy.scope, reference_time=runtime.clock(), include_cross_scope=False), 20)
                    assert {item.node.content for item in response.results} == {"Mara likes apples.", "Mara likes tea."}
                    context = response.bundle.render()
                    assert "Mara likes apples." in context and "Mara likes tea." in context
                    public = response.model_dump_json()
                    assert "CLI_ONLY_SOURCE_326" not in public and "private326@example.com" not in public
                    assert native.reads == private_reads
                finally:
                    await current.close()
            await run(key_args)
            assert json.loads(capsys.readouterr().out)["complete"]
        finally:
            os.close(read_fd)
    assert recovery.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    await run(["privacy-keys", "unlock", *base, "--event", event, "--action", "verify"])
    assert json.loads(capsys.readouterr().out)["authenticated"]
    request = str(uuid4())
    await run(["privacy", "erase", *selected, "--owner", owner, "--request", request, "--event", event])
    assert json.loads(capsys.readouterr().out)["state"] == "blocked_from_access"
    await run(["privacy", "status", *selected, "--owner", owner, "--request", request])
    status = json.loads(capsys.readouterr().out)
    assert not status["physical_wipe"] and status["backup_copies"] == status["external_copies"] == "not_managed"
    assert all(value._closed for value in opened)
    assert artifact.exists() and recovery.exists()  # No automatic offline disposal.


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM04_cli_rotation_real_erasure_export_reopen(tmp_path, layout, analyzer, native, monkeypatch, capsys):
    """Original three-object rotation case joined to real current-ledger effects.

    The retained recovery key deliberately demonstrates the offline-copy limit.
    It is never a current live erasure authorization or an ordinary export field.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import structlog

    monkeypatch.setattr(structlog._config._CONFIG, "logger_factory", structlog.PrintLoggerFactory(file=sys.stderr))
    engine, runtime, policy, custody, original, *_ = await journal_engine(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    source_only = "ROTATION_SOURCE_ONLY_326"
    opened = [engine]
    pending = None
    config = engine._config

    async def fresh():
        nonlocal pending
        if pending is not None:
            value, pending = pending, None
        else:
            value = await MemoryEngine.create(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            opened.append(value)
        return value

    def unwrap(private, envelope):
        return private.decrypt(decode64(envelope.wrapped_key, minimum=1, maximum=6144), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=envelope.wrapper.bytes()))

    monkeypatch.setattr(__name__ + ".engine_factory", fresh)
    selected = ["--engine-factory", __name__ + ":engine_factory"]
    base = ["--registry", str(custody.path), "--project", str(policy.project_id), "--owner", owner, *selected]
    audited = [*base, "--audit", str(tmp_path / "rotation-audit.jsonl")]
    try:
        for index in range(3):
            await engine.store(f"Mara likes tea. {source_only}_{index}.", retrieval_content="Mara likes tea.", user_id=owner)
        before = sorted(await engine._privacy.repository.for_owner(owner), key=lambda record: record.event_id)
        assert len(before) == 3
        erased = before[1]
        store_id = (await engine._privacy.erasure.repository.state())[1].store_id
        exports = tmp_path / "rotation-exports"
        exports.mkdir()
        backup = exports / "before-rotation.json"
        await export_protected_pack(engine, backup)
        backup_hash = hashlib.sha256(backup.read_bytes()).digest()
        recovery = tmp_path / "retained-recovery.pem"
        password = b"task-owned-offline-backup-326"
        await custody.export_recovery(original.ref, recovery, passphrase=password, pack_path=backup)
        recovery_hash = hashlib.sha256(recovery.read_bytes()).digest()
        offline_private = serialization.load_pem_private_key(recovery.read_bytes(), password=password)
        target = await custody.provision_wrapping(original.boundary, "evidence", key_id=original.ref.key_id)
        await engine.close()
        capsys.readouterr()

        # A bounded CLI step commits exactly one wrapper then releases its engine.
        await run(["privacy-keys", "rotate", *audited, "--source-version", str(original.ref.key_version),
                   "--target-version", str(target.ref.key_version), "--limit", "1"])
        interrupted = json.loads(capsys.readouterr().out)
        assert interrupted["state"] == "running" and interrupted["changed"] == 1
        assert interrupted["cursor"] == str(before[0].event_id)
        job_id = interrupted["job_id"]
        engine = await fresh()
        after_one = sorted(await engine._privacy.repository.for_owner(owner), key=lambda record: record.event_id)
        assert [record.evidence.key_version for record in after_one] == [
            target.ref.key_version, original.ref.key_version, original.ref.key_version]
        request = ErasureRequest(request_id=uuid4(), mode="evidence", event_ids=(erased.event_id,))
        await engine.request_erasure(request, user_id=owner)
        assert (await engine._privacy.repository.get(str(erased.event_id), user_id=owner)).evidence == erased.evidence
        with pytest.raises(PrivacyDenied, match="^privacy_erased$"):
            async with engine._privacy.guard(erased.snapshot, "publication", records=(erased,), purpose="evidence"):
                pytest.fail("erased evidence received a publication lease")

        # Preserve the still-present envelope for this resume, so refusal comes
        # from the actual effect fence, not merely an absent SQL slot on reopen.
        pending = engine
        await run(["privacy-keys", "resume", *audited, "--job", job_id])
        blocked = json.loads(capsys.readouterr().out)
        assert blocked["state"] == "blocked" and blocked["blocked"] == 1 and blocked["changed"] == 2
        await run(["privacy-keys", "resume", *audited, "--job", job_id])
        completed = json.loads(capsys.readouterr().out)
        assert completed["state"] == "complete" and completed["blocked"] == 0 and completed["changed"] == 2
        await run(["privacy-keys", "resume", *audited, "--job", job_id])
        assert json.loads(capsys.readouterr().out) == completed
        await run(["privacy-keys", "rotation-status", *base, "--job", job_id])
        assert json.loads(capsys.readouterr().out) == completed

        engine = await fresh()
        after = sorted(await engine._privacy.repository.for_owner(owner), key=lambda record: record.event_id)
        assert [record.event_id for record in after] == [record.event_id for record in before]
        for old, current in zip(before, after, strict=True):
            assert current.snapshot == old.snapshot
            if current.event_id == erased.event_id:
                assert current.evidence is None
                assert (await engine.inspect_source(str(current.event_id), user_id=owner)).state == "erased"
                continue
            old_envelope = decode_envelope(old.evidence, old.snapshot)
            new_envelope = decode_envelope(current.evidence, current.snapshot)
            assert (new_envelope.payload, new_envelope.nonce, new_envelope.ciphertext) == (
                old_envelope.payload, old_envelope.nonce, old_envelope.ciphertext)
            assert new_envelope.wrapper.ref == target.ref
            assert new_envelope.wrapper.revision == old_envelope.wrapper.revision + 1
            async with engine._privacy.guard(current.snapshot, "disclosure", records=(current,), purpose="evidence"):
                with custody.unlocked((target.ref,), expires_at=runtime.clock() + timedelta(minutes=1)) as private:
                    assert hmac.compare_digest(unwrap(offline_private, old_envelope),
                                               unwrap(private._private_keys[target.ref], new_envelope))
        rotated = exports / "after-rotation.json"
        native_reads = native.reads
        await export_protected_pack(engine, rotated)
        assert native.reads == native_reads
        bundle = read_protected_export(rotated)
        assert all(record.evidence is None or record.evidence.key_version == target.ref.key_version for record in bundle.admissions)
        assert source_only not in rotated.read_text() and "PRIVATE KEY" not in rotated.read_text()
        await engine.close()

        # This is the supported protected layout transfer, not a generic import.
        destination_layout = "pack" if layout == "directory" else layout
        async with fresh_config(config, tmp_path / "rotation-restored", destination_layout, restore=True) as destination:
            result = await restore_protected_export(rotated, destination, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            assert UUID(result["store_id"]) == store_id and result["source_retained"]
            restored = await MemoryEngine.create(destination, privacy_runtime=runtime, embedding_provider=runtime.embedder)
            opened.append(restored)
            assert (await restored._privacy.erasure.repository.state())[1].store_id == store_id
            assert sorted(await restored._privacy.repository.for_owner(owner), key=lambda record: record.event_id) == after
            assert (await restored.inspect_source(str(erased.event_id), user_id=owner)).state == "erased"
            assert len(await restored._privacy.repository._query("SELECT id FROM nodes", ())) == 3
            await restored.close()
        entries = {entry.version: entry for entry in custody.inventory()}
        assert entries[original.ref.key_version].managed_lineages == (store_id,)
        assert entries[target.ref.key_version].managed_lineages == (store_id,)

        # A deliberate old key plus the retained old export can still decrypt
        # offline, including the live-erased object's old snapshot. This proves
        # the documented backup limit, not permission from current authority.
        for record in read_protected_export(backup).admissions:
            envelope = decode_envelope(record.evidence, record.snapshot)
            key = unwrap(offline_private, envelope)
            plaintext = AESGCM(key).decrypt(decode64(envelope.nonce, minimum=12, maximum=12),
                decode64(envelope.ciphertext, minimum=16, maximum=8 * 1024 * 1024), envelope.payload.bytes())
            assert source_only.encode() in plaintext
        assert hashlib.sha256(backup.read_bytes()).digest() == backup_hash
        assert hashlib.sha256(recovery.read_bytes()).digest() == recovery_hash
        assert source_only not in (tmp_path / "rotation-audit.jsonl").read_text()
    finally:
        for value in opened:
            if not value._closed:
                await value.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_AC1_AC6_EM08_cli_dry_run_migrate_status_restore_explicit_selection(tmp_path, layout, analyzer, native, monkeypatch, capsys):
    import structlog
    monkeypatch.setattr(structlog._config._CONFIG, "logger_factory", structlog.PrintLoggerFactory(file=sys.stderr))
    template, runtime, policy, custody, *_ = await journal_engine(tmp_path / "template", "directory", analyzer)
    config = template._config
    await template.close()
    runtime = replace(runtime, retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    monkeypatch.setattr(__name__ + ".runtime_factory", lambda: runtime)
    monkeypatch.setattr(__name__ + ".embedding_factory", lambda: runtime.embedder)
    factories = ["--runtime-factory", __name__ + ":runtime_factory", "--embedding-factory", __name__ + ":embedding_factory"]
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(policy.model_dump_json())
    async with fresh_config(config, tmp_path / "legacy", layout) as source, fresh_config(config, tmp_path / "target", layout, restore=True) as target, fresh_config(config, tmp_path / "restored", layout, restore=True) as restored:
        source = source.model_copy(update={"privacy_policy": None})
        engine = await MemoryEngine.create(source, embedding_provider=runtime.embedder)
        try:
            await engine.store("Mara likes tea. CLI_MIGRATION_SOURCE_326.", retrieval_content="Mara likes tea.", user_id=str(policy.owner_id))
        finally:
            await engine.close()
        def choice(selected, config, suffix):
            if layout == "postgres":
                variable = "PV326_CLI_" + suffix
                monkeypatch.setenv(variable, config.database_url.get_secret_value())
                return ["--" + selected + "-database-env", variable]
            return ["--" + selected, config.pack_path or str(Path(config.db_path).parent)]
        source_args, target_args, restore_args = choice("source", source, "SOURCE"), choice("target", target, "TARGET"), choice("target", restored, "RESTORE")
        # A bogus ambient URL/pack/key must never redirect explicit selection.
        monkeypatch.setenv("PRME_DATABASE_URL", "postgresql://invalid:secret@invalid.invalid/unowned")
        monkeypatch.setenv("PRME_PACK_PATH", str(tmp_path / "unowned.prme"))
        monkeypatch.setenv("PRME_ENCRYPTION_KEY", "ambient-unowned-key")
        before = (await _read_snapshot(source)).fingerprint
        capsys.readouterr()
        await run(["privacy", "dry-run", *source_args, "--policy-file", str(policy_file)])
        plan = json.loads(capsys.readouterr().out)
        assert not plan["protected"] and len(plan["copies"]) == 45
        assert not (tmp_path / "unowned.prme").exists()
        job = tmp_path / "job"
        await run(["privacy", "migrate", *source_args, *target_args, *factories, "--policy-file", str(policy_file),
            "--job", str(job), "--unavailable-legacy-history"])
        finished = json.loads(capsys.readouterr().out)
        assert finished["state"] == "complete"
        await run(["privacy", "migration-status", "--job", str(job)])
        assert json.loads(capsys.readouterr().out) == finished
        await run(["privacy", "restore", *restore_args, *factories, "--policy-file", str(policy_file), "--artifact", str(job / "protected-export.json")])
        assert json.loads(capsys.readouterr().out)["state"] == "complete"
        assert (await _read_snapshot(source)).fingerprint == before
        current = await MemoryEngine.create(restored, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        try:
            rows = await current._privacy.repository._query("SELECT content FROM nodes", ())
            assert [row[0] for row in rows] == ["Mara likes tea."]
        finally:
            await current.close()
        with pytest.raises(PrivacyPortabilityError, match="privacy_destination_occupied") as occupied:
            await run(["privacy", "restore", *restore_args, *factories, "--policy-file", str(policy_file), "--artifact", str(job / "protected-export.json")])
        assert "empty destination" in failure_guidance(occupied.value)
