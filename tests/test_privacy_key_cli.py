"""CLI actions use actual ciphertext, explicit guards and selected IDs."""

import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from prme.cli import build_parser
from prme.privacy.cli import command
from prme.privacy.local_keys import NativeKeyCustody
from prme.privacy.policy import PrivacyDenied
from tests.privacy_fakes import FakeAuthority
from tests.test_privacy_ciphertext_storage import MEMORY, SOURCE, real_engine
from tests.test_privacy_keys import native as native_fixture  # noqa: F401


def authority_factory():
    # Component current-policy authority. Hosted/lifecycle authority is separately gated.
    return FakeAuthority()


def engine_factory():
    raise AssertionError("test supplies an explicit configured engine factory")


def parse(values):
    return build_parser().parse_args(["privacy-keys", *values])


async def test_provision_status_public_subprocess_and_fixed_errors(tmp_path, native, capsys, monkeypatch):
    registry = tmp_path / "keys.json"
    project, owner = uuid4(), uuid4()
    args = parse(["provision", "--registry", str(registry), "--backend", "macos", "--purpose", "evidence",
                  "--project", str(project), "--owner", str(owner)])
    await command(args)
    descriptor = json.loads(capsys.readouterr().out)
    assert descriptor["kind"] == "local-rsa-oaep-sha256-v1"
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary status touched private native custody")
    monkeypatch.setattr("prme.privacy.local_keys._open_native", forbidden)
    await command(parse(["status", "--registry", str(registry)]))
    result = json.loads(capsys.readouterr().out)
    assert result["native_private_checked"] is False
    # Separate interpreter has no fake/native private keys and still reads status.
    result = subprocess.run([sys.executable, "-m", "prme.cli", "privacy-keys", "status",
                             "--registry", str(registry)], text=True, capture_output=True, check=True)
    assert not json.loads(result.stdout)["native_private_checked"]
    assert "PRIVATE KEY" not in result.stdout + result.stderr
    registry.write_text("SYNTHETIC_PRIVATE_EXCEPTION_MUST_NOT_ESCAPE")
    with pytest.raises(PrivacyDenied) as error:
        await command(parse(["status", "--registry", str(registry)]))
    assert "SYNTHETIC_PRIVATE" not in str(error.value)


async def test_selected_cli_inspection_and_unlock(tmp_path, native, capsys, monkeypatch):
    import structlog
    # Match prme.cli.main: diagnostics go to stderr, command JSON to stdout.
    monkeypatch.setattr(structlog._config._CONFIG, "logger_factory", structlog.PrintLoggerFactory(file=sys.stderr))
    engine, runtime, policy, custody, evidence, _ = await real_engine(tmp_path, "pack")
    event_id = await engine.store(SOURCE, retrieval_content=MEMORY, user_id=str(policy.owner_id))
    path = engine._config.pack_path
    config = engine._config
    await engine.close()
    opened = []
    async def factory():
        from prme import MemoryEngine
        value = await MemoryEngine.create(config, embedding_provider=runtime.embedder, privacy_runtime=runtime)
        opened.append(value)
        return value
    monkeypatch.setattr("tests.test_privacy_key_cli.engine_factory", factory)
    capsys.readouterr()
    base = ["--registry", str(custody.path), "--project", str(policy.project_id), "--owner", str(policy.owner_id),
            "--engine-factory", "tests.test_privacy_key_cli:engine_factory", "--event", event_id, "--audit", str(tmp_path / "audit.jsonl")]
    raw = list(base)
    raw[raw.index("--engine-factory"):raw.index("--engine-factory") + 2] = ["--store", str(path)]
    with pytest.raises(PrivacyDenied, match="privacy_ledger_unavailable"):
        await command(parse(["inspect", *raw]))
    before = native.reads
    await command(parse(["inspect", *base]))
    metadata = json.loads(capsys.readouterr().out)
    assert not metadata["private_key_checked"] and native.reads == before
    await command(parse(["unlock", *base, "--action", "verify"]))
    output = capsys.readouterr().out
    assert json.loads(output)["authenticated"] and SOURCE not in output
    audit = (tmp_path / "audit.jsonl").read_text()
    assert SOURCE not in audit and "authorized" in audit and "finished" in audit
    await command(parse(["unlock", *base, "--action", "reveal"]))
    assert SOURCE in capsys.readouterr().out
    # Wrong selected ID refuses before private native lookup.
    base[base.index(event_id)] = str(uuid4())
    before = native.reads
    with pytest.raises(PrivacyDenied):
        await command(parse(["unlock", *base, "--action", "verify"]))
    assert native.reads == before
    assert all(value._closed for value in opened)
    target = await custody.provision_wrapping(evidence.boundary, "evidence", key_id=evidence.ref.key_id)
    rotation = ["--registry", str(custody.path), "--project", str(policy.project_id), "--owner", str(policy.owner_id),
                "--engine-factory", "tests.test_privacy_key_cli:engine_factory"]
    await command(parse(["rotate", *rotation, "--audit", str(tmp_path / "audit.jsonl"),
        "--source-version", str(evidence.ref.key_version), "--target-version", str(target.ref.key_version)]))
    job = json.loads(capsys.readouterr().out)
    assert job["state"] == "complete" and job["changed"] == 1
    await command(parse(["rotation-status", *rotation, "--job", job["job_id"]]))
    assert json.loads(capsys.readouterr().out) == job
    assert all(value._closed for value in opened)


async def test_recovery_cli_uses_inherited_fd_and_exclusive_artifact(tmp_path, native, capsys):
    registry = tmp_path / "keys.json"
    custody = NativeKeyCustody.create(registry, backend="macos")
    from prme.privacy.keys import KeyBoundary
    descriptor = await custody.provision_wrapping(KeyBoundary(project_id=uuid4(), owner_id=uuid4(), scope="personal"), "evidence")
    pack = tmp_path / "pack"
    pack.mkdir()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"synthetic-cli-passphrase-321\n")
        os.close(write_fd)
        args = parse(["recovery-export", "--registry", str(registry), "--key-version", str(descriptor.ref.key_version),
                      "--artifact", str(tmp_path / "recovery.pem"), "--pack", str(pack), "--passphrase-fd", str(read_fd)])
        await command(args)
        assert "passphrase" not in capsys.readouterr().out
        assert (tmp_path / "recovery.pem").read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    finally:
        os.close(read_fd)
