"""AC3/AC6 actual parser/actions; only external provider/native OS boundaries fake.

These are CLI subchecks, not complete frozen cases or held #323 retrieval proof.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.cli import build_parser
from prme.privacy.cli import failure_guidance
from prme.privacy.policy import PrivacyDenied
from prme.privacy.portability import PrivacyPortabilityError
from prme.privacy.retry_commitments import NativeRetryCommitter
from prme.storage.privacy_export import read_protected_export
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
