"""Explicit key provisioning and finite diagnostics; no implicit native unlock."""

from __future__ import annotations

import asyncio
import getpass
import importlib
import inspect
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from prme.privacy.crypto import CryptoProtector
from prme.privacy.keys import FileAuditSink, KeyBoundary, LocalAccessAuthority, utc_now
from prme.privacy.local_keys import NativeKeyCustody, public_registry, read_public_store
from prme.privacy.policy import PrivacyDenied
from prme.privacy.rotation import RotationManager
from prme.storage.privacy import PrivacyRepository


def _passphrase(args) -> bytes:
    if args.passphrase_fd is None:
        value = getpass.getpass("Recovery passphrase (at least 16 bytes): ").encode()
    else:
        if args.passphrase_fd < 3:
            raise PrivacyDenied("privacy_configuration_invalid")
        with os.fdopen(os.dup(args.passphrase_fd), "rb") as source:
            value = source.readline(1025).rstrip(b"\r\n")
    if not 16 <= len(value) <= 1024:
        raise PrivacyDenied("privacy_configuration_invalid")
    return value


async def _authority(reference):
    # Explicit operator-supplied capability, never an allow-all offline default.
    if reference is None or ":" not in reference:
        raise PrivacyDenied("privacy_capability_unavailable")
    module, name = reference.split(":", 1)
    if not all(part.isidentifier() for part in module.split(".")) or not name.isidentifier():
        raise PrivacyDenied("privacy_configuration_invalid")
    result = getattr(importlib.import_module(module), name)()
    result = await result if inspect.isawaitable(result) else result
    if not callable(getattr(result, "guard", None)):
        raise PrivacyDenied("privacy_capability_unavailable")
    return result


@asynccontextmanager
async def _repository(args, *, writable=False):
    """Open only the explicitly selected existing store; no engine/providers."""
    if args.database_env is not None:
        import asyncpg
        url = os.environ.get(args.database_env)
        if not url:
            raise PrivacyDenied("privacy_configuration_invalid")
        pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
        try:
            yield PrivacyRepository(SimpleNamespace(_pool=pool))
        finally:
            await pool.close()
        return
    path = Path(args.store).absolute()
    if path.is_symlink() or not path.is_file():
        raise PrivacyDenied("privacy_configuration_invalid")
    from prme.storage.pack_tools import _attach, _instance
    from prme.storage.encryption import derive_pack_key
    # This is the existing store-key purpose, never an evidence key.
    password = os.environ.get(args.store_key_env) if args.store_key_env else None
    key = derive_pack_key(password) if password else None
    with _instance(str(path), key) as conn:
        _attach(conn, str(path), "privacy_target", key=key, read_only=not writable)
        conn.execute("USE privacy_target")
        conn.execute("SET TimeZone='UTC'")
        yield PrivacyRepository(SimpleNamespace(_conn=conn, _conn_lock=asyncio.Lock()))


def _exact(state, version):
    matches = [item.wrapping for item in state.entries
               if item.version == version and item.wrapping is not None and item.state == "ready"]
    if len(matches) != 1:
        raise PrivacyDenied("privacy_capability_unavailable")
    return matches[0]


async def _run(args):
    command = args.key_command
    if command == "status":
        state = read_public_store(args.registry)
        print(json.dumps({"schema_version": 1, "native_private_checked": False,
                          "isolation": "same_process", "registry": state.model_dump(mode="json")}, sort_keys=True))
        return
    if command == "provision":
        path = Path(args.registry)
        custody = NativeKeyCustody(path) if path.exists() else NativeKeyCustody.create(path, backend=args.backend)
        if read_public_store(path).backend != args.backend:
            raise PrivacyDenied("privacy_configuration_invalid")
        boundary = KeyBoundary(project_id=args.project, owner_id=args.owner, scope=args.scope)
        if args.purpose == "pseudonym":
            if args.epoch is None:
                raise PrivacyDenied("privacy_configuration_invalid")
            ref = await custody.provision_pseudonym_key(boundary, args.epoch)
        else:
            if args.epoch is not None:
                raise PrivacyDenied("privacy_configuration_invalid")
            ref = (await custody.provision_wrapping(boundary, args.purpose, key_id=args.key_id)).ref
        print(ref.model_dump_json())
        return
    if command in {"recovery-export", "recovery-import"}:
        custody = NativeKeyCustody(args.registry)
        descriptor = _exact(read_public_store(args.registry), args.key_version)
        password = _passphrase(args)
        if command == "recovery-export":
            await custody.export_recovery(descriptor.ref, args.artifact, passphrase=password, pack_path=args.pack)
        else:
            await custody.import_recovery(descriptor.ref, args.artifact, passphrase=password)
        print(json.dumps({"key_version": str(args.key_version), "action": command, "complete": True}))
        return
    async with _repository(args, writable=command in {"rotate", "resume"}) as repository:
        if command == "rotation-status":
            job = await repository.rotation_job(args.job)
            if job is None or job.boundary != KeyBoundary(project_id=args.project, owner_id=args.owner, scope=args.scope):
                raise PrivacyDenied("privacy_boundary_mismatch")
            print(job.model_dump_json())
            return
        policy_authority = await _authority(args.authority_factory)
        reader = LocalAccessAuthority(FileAuditSink(args.audit))
        boundary = KeyBoundary(project_id=args.project, owner_id=args.owner, scope=args.scope)
        if command in {"inspect", "unlock"}:
            if not 1 <= len(args.event) <= 128 or len(set(args.event)) != len(args.event):
                raise PrivacyDenied("privacy_configuration_invalid")
            records = [await repository.get(str(event), user_id=str(args.owner)) for event in args.event]
            if any(row is None or KeyBoundary.from_snapshot(row.snapshot) != boundary for row in records):
                raise PrivacyDenied("privacy_boundary_mismatch")
            protector = CryptoProtector(public_registry(args.registry), authority=reader)
            if command == "inspect":
                for row in records:
                    if row.evidence is None:
                        print(json.dumps({"event_id": str(row.event_id), "evidence": "not_retained"}))
                    else:
                        metadata = await protector.inspect_metadata(row.evidence, original_snapshot=row.snapshot,
                            boundary=boundary, guard=lambda: policy_authority.guard(row.snapshot, "disclosure"))
                        print(json.dumps(metadata, sort_keys=True))
                return
            if any(row.evidence is None for row in records):
                raise PrivacyDenied("privacy_capability_unavailable")
            expiry = utc_now() + timedelta(seconds=args.seconds)
            grant = await reader.grant("diagnostic", tuple((row.evidence.context, row.snapshot) for row in records),
                                       expires_at=expiry)
            # Descriptor selection/parsing precedes the first private operation.
            from prme.privacy.crypto import decode_envelope
            refs = tuple(dict.fromkeys(decode_envelope(row.evidence, row.snapshot).wrapper.ref for row in records))
            custody = NativeKeyCustody(args.registry)
            with custody.unlocked(refs, expires_at=expiry) as wrapper:
                protector.registry = public_registry(args.registry, private_wrapper=wrapper)
                for row in records:
                    async with protector.open_held(row.evidence, grant, original_snapshot=row.snapshot,
                        guard=lambda: policy_authority.guard(row.snapshot, "disclosure")) as plaintext:
                        # Actual output stays inside the held authorization lease.
                        if args.action == "reveal":
                            print(json.dumps({"event_id": str(row.event_id), "source": json.loads(plaintext)}), flush=True)
                        else:
                            print(json.dumps({"event_id": str(row.event_id), "authenticated": True}), flush=True)
            return
        custody = NativeKeyCustody(args.registry)
        state = read_public_store(args.registry)
        if command == "resume":
            job = await repository.rotation_job(args.job)
            if job is None or job.boundary != boundary or job.purpose != "evidence":
                raise PrivacyDenied("privacy_boundary_mismatch")
            source, target = job.source, job.target
        else:
            source, target = _exact(state, args.source_version).ref, _exact(state, args.target_version).ref
        rows = await repository.page(user_id=str(args.owner), scope=args.scope, after=None, limit=128)
        exemplar = next((row for row in rows if KeyBoundary.from_snapshot(row.snapshot) == boundary), None)
        if exemplar is None:
            raise PrivacyDenied("privacy_capability_unavailable")
        def activation():
            return policy_authority.guard(exemplar.snapshot, "publication")
        with custody.unlocked((source,), expires_at=utc_now() + timedelta(seconds=args.seconds)) as wrapper:
            protector = CryptoProtector(public_registry(args.registry, private_wrapper=wrapper), authority=reader)
            manager = RotationManager(repository, protector, custody)
            if command == "rotate":
                descriptor = protector.registry.exact(source)
                if descriptor.boundary != boundary or descriptor.purpose != "evidence":
                    raise PrivacyDenied("privacy_boundary_mismatch")
                job = await manager.start(source, target, guard=activation)
            async def access_for(row):
                return await reader.grant("diagnostic", ((row.evidence.context, row.snapshot),),
                    expires_at=utc_now() + timedelta(seconds=args.seconds), actions=("rewrap",))
            job = await manager.step(job.job_id, activation_guard=activation, access_for=access_for,
                guard_for=lambda row: lambda: policy_authority.guard(row.snapshot, "publication"), limit=args.limit)
            print(job.model_dump_json())


async def command(args):
    try:
        await _run(args)
    except PrivacyDenied:
        raise
    except Exception:
        # No provider/DB/path/password exception is printed by the outer CLI.
        raise PrivacyDenied("privacy_capability_unavailable") from None


def register(subparsers):
    parser = subparsers.add_parser("privacy-keys", help="Explicit privacy key provisioning and diagnostics")
    commands = parser.add_subparsers(dest="key_command", required=True)
    for name in ("provision", "status", "inspect", "unlock", "rotate", "resume",
                 "rotation-status", "recovery-export", "recovery-import"):
        child = commands.add_parser(name)
        child.add_argument("--registry", required=True, help="Explicit public key registry JSON")
        child.set_defaults(func=command)
        if name in {"provision", "inspect", "unlock", "rotate", "resume", "rotation-status"}:
            child.add_argument("--project", type=UUID, required=True)
            child.add_argument("--owner", type=UUID, required=True)
            child.add_argument("--scope", choices=("personal", "project", "org"), default="personal")
        if name == "provision":
            child.add_argument("--backend", choices=("macos", "windows", "secretservice"), required=True)
            child.add_argument("--purpose", choices=("evidence", "processing", "pseudonym"), required=True)
            child.add_argument("--epoch", type=UUID)
            child.add_argument("--key-id", type=UUID)
        if name in {"recovery-export", "recovery-import"}:
            child.add_argument("--key-version", type=UUID, required=True)
            child.add_argument("--artifact", required=True)
            child.add_argument("--passphrase-fd", type=int)
            if name == "recovery-export":
                child.add_argument("--pack", required=True)
        if name in {"inspect", "unlock", "rotate", "resume", "rotation-status"}:
            target = child.add_mutually_exclusive_group(required=True)
            target.add_argument("--store", help="Existing local DuckDB file or .prme pack")
            target.add_argument("--database-env", help="Name of explicitly selected PostgreSQL URL environment variable")
            child.add_argument("--store-key-env", help="Existing local store-key environment variable")
        if name in {"inspect", "unlock", "rotate", "resume"}:
            child.add_argument("--authority-factory", required=True, help="Explicit module:factory for current-policy authority")
            child.add_argument("--audit", required=True, help="Content-free local audit path")
        if name in {"inspect", "unlock"}:
            child.add_argument("--event", type=UUID, action="append", required=True)
        if name == "unlock":
            child.add_argument("--action", choices=("verify", "reveal"), required=True)
        if name in {"unlock", "rotate", "resume"}:
            child.add_argument("--seconds", type=int, choices=range(1, 901), default=300, metavar="1..900")
        if name in {"rotate", "resume"}:
            child.add_argument("--limit", type=int, choices=range(1, 129), default=128, metavar="1..128")
        if name == "rotate":
            child.add_argument("--source-version", type=UUID, required=True)
            child.add_argument("--target-version", type=UUID, required=True)
        if name in {"resume", "rotation-status"}:
            child.add_argument("--job", type=UUID, required=True)
