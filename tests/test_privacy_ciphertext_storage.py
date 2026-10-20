"""Real AES/RSA at both actual storage boundaries; no production model calls."""

from dataclasses import replace
from datetime import timedelta
import json
from uuid import uuid4

import pytest

from prme.privacy.crypto import CryptoProtector, decode_envelope
from prme.privacy.keys import KeyBoundary, LocalAccessAuthority, StoredProcessingAuthorizer
from prme.privacy.local_keys import NativeKeyCustody, public_registry
from prme.privacy.policy import PrivacyDenied
from tests.privacy_fakes import (
    config_for, isolated_privacy_postgres, make_runtime, open_engine, policy_for,  # noqa: F401
)
from tests.test_privacy_crypto import Audit
from tests.test_privacy_keys import native as native_fixture  # noqa: F401

LAYOUTS = ["directory", "pack", "postgres"]
SOURCE = "PV321_ONLY_SOURCE_9cb19a84_tea"
MEMORY = "PV321_ADMITTED_MEMORY_46f20a5b"


async def real_engine(tmp_path, layout):
    runtime = make_runtime()
    policy = policy_for(runtime, capture="full", evidence_lifetime_seconds=3600)
    custody = NativeKeyCustody.create(tmp_path / "public.json", backend="macos", clock=runtime.clock)
    boundary = KeyBoundary(project_id=policy.project_id, owner_id=policy.owner_id, scope=policy.scope.value)
    evidence = await custody.provision_wrapping(boundary, "evidence")
    processing = await custody.provision_wrapping(boundary, "processing")
    reader = LocalAccessAuthority(Audit(), clock=runtime.clock)
    protector = CryptoProtector(public_registry(custody.path), authority=reader)
    runtime = replace(runtime, protector=protector)
    engine, runtime, policy = await open_engine(tmp_path / "pack", runtime=runtime, policy=policy, layout=layout)
    reader.processing = StoredProcessingAuthorizer(engine._privacy.repository, engine._event_store.extraction_work)
    return engine, runtime, policy, custody, evidence, processing


async def test_EM03_missing_public_keys_refuse_before_protected_creation(tmp_path):
    from prme import MemoryEngine
    from prme.privacy.keys import KeyRegistry
    runtime = make_runtime()
    policy = policy_for(runtime, capture="full", evidence_lifetime_seconds=3600)
    runtime = replace(runtime, protector=CryptoProtector(KeyRegistry((), active=(), wrappers={})))
    target = tmp_path / "must-not-exist"
    with pytest.raises(PrivacyDenied):
        await MemoryEngine.create(config_for(target, policy=policy), embedding_provider=runtime.embedder,
                                  privacy_runtime=runtime)
    assert not target.exists() and not runtime.embedder.sends and not runtime.extractor.sends


async def dump_rows(engine):
    repo = engine._privacy.repository
    tables = ("events", "privacy_admissions", "operations", "nodes", "event_extractions")
    return b"\n".join([repr(await repo._query(f"SELECT * FROM {table}", ())).encode() for table in tables])


def scan_files(root, markers):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            assert not any(marker in data for marker in markers), f"private marker in {path.name}"


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("stage", ["before_aes", "after_wrap", "atomic_write"])
async def test_EM03_failure_boundaries_do_not_persist_plaintext(tmp_path, layout, stage, native, monkeypatch):
    engine, runtime, policy, custody, *_ = await real_engine(tmp_path, layout)
    repo = engine._privacy.repository
    def fail():
        raise RuntimeError("synthetic fixed failure")
    if stage == "before_aes":
        monkeypatch.setattr("prme.privacy.crypto.AESGCM.generate_key", lambda **kwargs: fail())
    elif stage == "after_wrap":
        async def refuse(*args, **kwargs):
            fail()
        monkeypatch.setattr(repo, "accept", refuse)
    elif layout == "postgres":
        await repo._query("""CREATE FUNCTION pv321_fail() RETURNS trigger LANGUAGE plpgsql AS
            $$ BEGIN RAISE EXCEPTION 'synthetic fixed failure'; END $$""", ())
        await repo._query("""CREATE TRIGGER pv321_atomic BEFORE INSERT ON privacy_admissions
            FOR EACH ROW EXECUTE FUNCTION pv321_fail()""", ())
    else:
        append = engine._event_store._append_sync
        def interrupted(event):
            append(event)
            fail()
        monkeypatch.setattr(engine._event_store, "_append_sync", interrupted)
    try:
        with pytest.raises(Exception):
            await engine.store(SOURCE, retrieval_content=MEMORY, user_id=str(policy.owner_id))
        assert not await repo.for_owner(str(policy.owner_id))
        assert not await repo._query("SELECT id FROM events WHERE user_id=?", (str(policy.owner_id),))
        assert SOURCE.encode() not in await dump_rows(engine)
        scan_files(tmp_path, (SOURCE.encode(), b"BEGIN PRIVATE KEY"))
    finally:
        await engine.close()
    scan_files(tmp_path, (SOURCE.encode(), b"BEGIN PRIVATE KEY"))


@pytest.mark.parametrize("layout", ["postgres"])
async def test_EM03_owned_postgres_physical_marker_scan(tmp_path, layout, native, monkeypatch):
    """Read only this dedicated database's base files and owned server WAL."""
    import base64
    import os
    import subprocess
    from prme.storage._threading import run_to_completion
    container = os.getenv("PRME_OWNED_TEST_CONTAINER_321")
    if container != "prme-privacy-engine-tests-20261019":
        pytest.skip("explicit task-owned container required for physical marker scan")
    label = subprocess.run(["docker", "inspect", "--format", '{{index .Config.Labels "prme.task"}}',
                            container], check=True, capture_output=True, text=True).stdout.strip()
    assert label == "privacy-318-279"
    engine, runtime, policy, custody, *_ = await real_engine(tmp_path, layout)
    markers = [b"PV321_ONLY_PHYSICAL_SOURCE_c81adc38"]
    wrapper = next(iter(runtime.protector.registry._wrappers.values()))
    wrap = wrapper.wrap_key
    async def capture_key(key, descriptor, context):
        markers.extend((key, base64.b64encode(key), key.hex().encode()))
        return await wrap(key, descriptor, context)
    monkeypatch.setattr(wrapper, "wrap_key", capture_key)
    try:
        await engine.store(markers[0].decode(), retrieval_content=MEMORY, user_id=str(policy.owner_id))
        repo = engine._privacy.repository
        database = (await repo._query("SELECT current_database(),oid FROM pg_database WHERE datname=current_database()", ()))[0]
        assert database[0] == "privacy_engine_321"
        directory = (await repo._query("SHOW data_directory", ()))[0][0]
        def scan():
            command = ["docker", "exec", container, "tar", "-cf", "-", "-C", directory,
                       f"base/{database[1]}", "pg_wal"]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            total, tail, matched = 0, b"", False
            try:
                while chunk := process.stdout.read(1024 * 1024):
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        raise AssertionError("physical scan exceeded frozen 512 MiB safety bound")
                    data = tail + chunk
                    matched |= any(marker in data for marker in markers)
                    tail = data[-256:]
                error = process.stderr.read()
                status = process.wait(timeout=10)
                # Live PG files may change during tar; no truncated-read claim.
                assert status == 0 or (status == 1 and b"file changed as we read it" in error)
                assert total > 0 and not matched, "private marker found in owned database/WAL"
                logs = subprocess.run(["docker", "logs", "--tail", "2000", container],
                                       capture_output=True, check=True)
                assert not any(marker in logs.stdout + logs.stderr for marker in markers)
                return total
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
        total = await run_to_completion(scan)
        print(f"PV321 owned database/WAL scanned {total} bytes; no source/key markers.")
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM03_real_ciphertext_storage_and_private_diagnostic(tmp_path, layout, native):
    engine, runtime, policy, custody, evidence, processing = await real_engine(tmp_path, layout)
    try:
        event_id = await engine.store(SOURCE, retrieval_content=MEMORY, user_id=str(policy.owner_id))
        record = await engine._privacy.repository.get(event_id, user_id=str(policy.owner_id))
        payload = record.evidence
        assert payload is not None and record.processing is None
        assert SOURCE not in (await dump_rows(engine)).decode()
        parsed = decode_envelope(payload, record.snapshot)
        assert parsed.wrapper.ref == evidence.ref and payload.key_version != processing.ref.key_version
        assert (await engine.get_event_nodes(event_id, user_id=str(policy.owner_id)))[0].content == MEMORY
        grant = await runtime.protector.authority.grant("diagnostic", ((payload.context, record.snapshot),),
                                                       expires_at=runtime.clock() + timedelta(minutes=1))
        def guard():
            return runtime.authority.guard(record.snapshot, "disclosure")
        before = native.reads
        meta = await runtime.protector.inspect_metadata(payload, original_snapshot=record.snapshot,
                                                       boundary=evidence.boundary, guard=guard)
        assert not meta["private_key_checked"] and not meta["ciphertext_authenticated"]
        assert native.reads == before
        with pytest.raises(PrivacyDenied):
            await runtime.protector.open(payload, grant, original_snapshot=record.snapshot, guard=guard)
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=1)) as private:
            reader = CryptoProtector(public_registry(custody.path, private_wrapper=private),
                                     authority=runtime.protector.authority)
            async with reader.open_held(payload, grant, original_snapshot=record.snapshot, guard=guard) as plaintext:
                assert SOURCE.encode() in plaintext and MEMORY.encode() in plaintext
        await custody.retire(evidence.ref.key_version)
        with pytest.raises(PrivacyDenied):
            with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=1)):
                pass
        assert (await engine.get_event_nodes(event_id, user_id=str(policy.owner_id)))[0].content == MEMORY
        scan_files(tmp_path, (SOURCE.encode(), b"BEGIN PRIVATE KEY"))
    finally:
        await engine.close()
    scan_files(tmp_path, (SOURCE.encode(), b"BEGIN PRIVATE KEY"))


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM03_real_processing_consumed_only_by_actual_claim(tmp_path, layout, native, monkeypatch):
    engine, runtime, policy, custody, evidence, processing = await real_engine(tmp_path, layout)
    monkeypatch.setattr(engine._pipeline, "queue_extraction", lambda *args: None)
    try:
        event_id = await engine.ingest(SOURCE, user_id=str(policy.owner_id))
        pending = await engine._privacy.repository.get(event_id, user_id=str(policy.owner_id))
        assert pending.processing and pending.evidence
        assert pending.processing.key_version == processing.ref.key_version
        assert SOURCE.encode() not in await dump_rows(engine)
        with custody.unlocked((processing.ref,), expires_at=runtime.clock() + timedelta(minutes=1)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            result = await engine.process_extractions(user_id=str(policy.owner_id))
            assert result.processed == 1
        completed = await engine._privacy.repository.get(event_id, user_id=str(policy.owner_id))
        assert completed.processing is None and completed.processing_state == "complete"
        assert completed.evidence == pending.evidence
        assert SOURCE.encode() not in await dump_rows(engine)
    finally:
        await engine.close()
    scan_files(tmp_path, (SOURCE.encode(), b"BEGIN PRIVATE KEY"))


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM03_failure_after_commit_preserves_exact_retry_ciphertext(tmp_path, layout, native, monkeypatch):
    engine, runtime, policy, custody, *_ = await real_engine(tmp_path, layout)
    owner, request_id = str(policy.owner_id), uuid4()
    repository = engine._privacy.repository
    original = repository.accept
    async def lost_ack(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic acknowledgement lost")
    monkeypatch.setattr(repository, "accept", lost_ack)
    admit = runtime.sanitizer.admit
    async def admitted_memory(request):
        result = await admit(request)
        data = json.loads(result.fields_json)
        data["retrieval_content"] = MEMORY
        return replace(result, fields_json=json.dumps(data))
    monkeypatch.setattr(runtime.sanitizer, "admit", admitted_memory)
    items = [{"content": SOURCE}]
    try:
        with pytest.raises(RuntimeError, match="acknowledgement"):
            await engine.ingest_fast_many(items, user_id=owner, request_id=request_id)
        saved = await repository.request(request_id, user_id=owner)
        assert len(saved) == 1
        monkeypatch.setattr(repository, "accept", original)
        assert await engine.ingest_fast_many(items, user_id=owner, request_id=request_id) == [str(saved[0].event_id)]
        assert await repository.request(request_id, user_id=owner) == saved
        assert SOURCE.encode() not in await dump_rows(engine)
    finally:
        await engine.close()
    scan_files(tmp_path, (SOURCE.encode(), b"BEGIN PRIVATE KEY"))
