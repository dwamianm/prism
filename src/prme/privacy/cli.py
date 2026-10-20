"""Explicit key provisioning and finite diagnostics; no implicit native unlock."""

from __future__ import annotations

import asyncio
import getpass
import importlib
import inspect
import json
import os
from contextlib import ExitStack, asynccontextmanager
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
from prme.types import Scope


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


async def _factory(reference):
    # Explicit operator-supplied capability, never an allow-all offline default.
    if reference is None or ":" not in reference:
        raise PrivacyDenied("privacy_capability_unavailable")
    module, name = reference.split(":", 1)
    if not all(part.isidentifier() for part in module.split(".")) or not name.isidentifier():
        raise PrivacyDenied("privacy_configuration_invalid")
    result = getattr(importlib.import_module(module), name)()
    return await result if inspect.isawaitable(result) else result


async def _authority(reference):
    result = await _factory(reference)
    if not callable(getattr(result, "guard", None)):
        raise PrivacyDenied("privacy_capability_unavailable")
    return result


@asynccontextmanager
async def _engine(reference):
    """The factory transfers one open engine to this command for its lifetime."""
    from prme.storage.engine import MemoryEngine
    from prme.storage._threading import run_async_to_completion
    engine = await _factory(reference)
    if not isinstance(engine, MemoryEngine):
        raise PrivacyDenied("privacy_configuration_invalid")
    try:
        if engine._closed or engine._privacy.require_runtime().erasure_ledger is None:
            raise PrivacyDenied("privacy_ledger_unavailable")
        yield engine
    finally:
        await run_async_to_completion(engine.close())


@asynccontextmanager
async def _store(args, *, writable=False):
    if args.engine_factory is not None:
        async with _engine(args.engine_factory) as engine:
            yield engine._privacy.repository, engine._privacy
        return
    async with _repository(args, writable=writable) as repository:
        # Raw repository diagnostics remain available for legacy stores only.
        # Protected state requires the engine's current ledger and policy path.
        tables = await repository._query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() "
            "AND table_name IN ('privacy_store_state','privacy_admissions')", ())
        names = {row[0] for row in tables}
        if "privacy_store_state" in names or ("privacy_admissions" in names and
                await repository._query("SELECT event_id FROM privacy_admissions LIMIT 1", ())):
            raise PrivacyDenied("privacy_ledger_unavailable")
        yield repository, None


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
    async with _store(args, writable=command in {"rotate", "resume"}) as (repository, coordinator):
        boundary = KeyBoundary(project_id=args.project, owner_id=args.owner, scope=args.scope)
        @asynccontextmanager
        async def selected_guard(row, effect="disclosure"):
            if coordinator is not None:
                def current():
                    deadline = row.snapshot.evidence_deadline
                    if deadline is not None and coordinator.require_runtime().clock() >= deadline:
                        raise PrivacyDenied("privacy_expired")
                async with coordinator.guard(row.snapshot, effect, records=(row,), purpose="evidence"):
                    current()
                    yield
                    current()
            else:
                async with policy_authority.guard(row.snapshot, effect):
                    yield
        def boundary_guard(effect):
            if coordinator is None:
                raise PrivacyDenied("privacy_capability_unavailable")
            saved = coordinator.effective(None, user_id=str(args.owner), scope=Scope(boundary.scope))
            if KeyBoundary.from_snapshot(saved) != boundary:
                raise PrivacyDenied("privacy_boundary_mismatch")
            return coordinator.guard(saved, effect)
        if command == "rotation-status":
            async def emit_status():
                job = await repository.rotation_job(args.job)
                if job is None or job.boundary != boundary:
                    raise PrivacyDenied("privacy_boundary_mismatch")
                print(job.model_dump_json())
            if coordinator is None:
                await emit_status()
            else:
                async with boundary_guard("disclosure"):
                    await emit_status()
            return
        policy_authority = coordinator.require_runtime().authority if coordinator else await _authority(args.authority_factory)
        now = coordinator.require_runtime().clock if coordinator else utc_now
        reader = LocalAccessAuthority(FileAuditSink(args.audit), clock=now)
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
                        async with selected_guard(row):
                            print(json.dumps({"event_id": str(row.event_id), "evidence": "not_retained"}))
                    else:
                        metadata = await protector.inspect_metadata(row.evidence, original_snapshot=row.snapshot,
                            boundary=boundary, guard=lambda: selected_guard(row))
                        async with selected_guard(row):
                            print(json.dumps(metadata, sort_keys=True))
                return
            if any(row.evidence is None for row in records):
                raise PrivacyDenied("privacy_capability_unavailable")
            expiry = now() + timedelta(seconds=args.seconds)
            from prme.privacy.crypto import decode_envelope
            custody = NativeKeyCustody(args.registry, clock=now)
            for row in records:
                with ExitStack() as private:
                    # Each native key load and each plaintext effect has the
                    # same exact-record current policy/ledger approval. The
                    # closed private handle is never returned to the caller.
                    async with selected_guard(row):
                        grant = await reader.grant("diagnostic", ((row.evidence.context, row.snapshot),), expires_at=expiry)
                        ref = decode_envelope(row.evidence, row.snapshot).wrapper.ref
                        wrapper = private.enter_context(custody.unlocked((ref,), expires_at=expiry))
                    protector.registry = public_registry(args.registry, private_wrapper=wrapper)
                    async with protector.open_held(row.evidence, grant, original_snapshot=row.snapshot,
                        guard=lambda: selected_guard(row)) as plaintext:
                        # Actual output stays inside the held authorization lease.
                        if args.action == "reveal":
                            print(json.dumps({"event_id": str(row.event_id), "source": json.loads(plaintext)}), flush=True)
                        else:
                            print(json.dumps({"event_id": str(row.event_id), "authenticated": True}), flush=True)
            return
        custody = NativeKeyCustody(args.registry, clock=now)
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
            return boundary_guard("publication") if coordinator else policy_authority.guard(exemplar.snapshot, "publication")
        with ExitStack() as private:
            async with activation():
                wrapper = private.enter_context(custody.unlocked((source,), expires_at=now() + timedelta(seconds=args.seconds)))
            protector = CryptoProtector(public_registry(args.registry, private_wrapper=wrapper), authority=reader)
            manager = RotationManager(repository, protector, custody)
            if command == "rotate":
                descriptor = protector.registry.exact(source)
                if descriptor.boundary != boundary or descriptor.purpose != "evidence":
                    raise PrivacyDenied("privacy_boundary_mismatch")
                job = await manager.start(source, target, guard=activation)
            async def access_for(row):
                return await reader.grant("diagnostic", ((row.evidence.context, row.snapshot),),
                    expires_at=now() + timedelta(seconds=args.seconds), actions=("rewrap",))
            job = await manager.step(job.job_id, activation_guard=activation, access_for=access_for,
                guard_for=lambda row: lambda: selected_guard(row, "publication"), limit=args.limit)
            async with activation():
                print(job.model_dump_json())


async def command(args):
    try:
        await _run(args)
    except PrivacyDenied:
        raise
    except Exception:
        # No provider/DB/path/password exception is printed by the outer CLI.
        raise PrivacyDenied("privacy_capability_unavailable") from None


async def erasure_command(args):
    from prme.privacy.erasure import ErasureRequest
    try:
        async with _engine(args.engine_factory) as engine:
            owner = str(args.owner)
            if args.erasure_command == "erase":
                result = await engine.request_erasure(ErasureRequest(
                    request_id=args.request, mode=args.mode, scope=Scope(args.scope),
                    owner_all=args.all, event_ids=tuple(args.event or ()),
                    node_ids=tuple(args.node or ())), user_id=owner)
            elif args.erasure_command == "status":
                result = await engine.get_erasure_status(args.request, user_id=owner)
            else:
                method = engine.expire_privacy if args.erasure_command == "expire" else engine.process_erasures
                result = await method(user_id=owner, limit=args.limit, budget_ms=args.budget_ms)
            value = ([item.model_dump(mode="json") for item in result] if isinstance(result, tuple)
                     else result.model_dump(mode="json") if result is not None else None)
            print(json.dumps(value, sort_keys=True))
    except PrivacyDenied:
        raise
    except Exception:
        raise PrivacyDenied("privacy_capability_unavailable") from None


def _portability_config(args, selection, *, policy=None, provider=None):
    """Explicit target selection; ambient database/pack/key settings cannot win."""
    from prme.config import PRMEConfig
    from prme.storage.embedding import validate_embedding_provider
    path, variable = getattr(args, selection, None), getattr(args, selection + "_database_env", None)
    if bool(path) == bool(variable):
        raise PrivacyDenied("privacy_configuration_invalid")
    password_variable = getattr(args, selection + "_key_env", None)
    password = os.environ.get(password_variable) if password_variable else None
    if password_variable and not password:
        raise PrivacyDenied("privacy_configuration_invalid")
    values = {"database_url": None, "namespace_id": None, "pack_path": None, "privacy_policy": policy,
              "encryption_enabled": bool(password), "encryption_key": password, "duckdb_threads": 1}
    if variable:
        values["database_url"] = os.environ.get(variable)
        if not values["database_url"] or password_variable:
            raise PrivacyDenied("privacy_configuration_invalid")
    else:
        selected = Path(path).absolute()
        if selected.suffix == ".prme":
            values["pack_path"] = str(selected)
        else:
            # A directory or explicit memory.duckdb[.enc] names the same
            # existing directory layout; closed Fernet is read in RAM only.
            database = selected if selected.suffix in {".duckdb", ".enc"} else selected / "memory.duckdb"
            if database.suffix == ".enc":
                database = database.with_suffix("")
            values.update(db_path=str(database), vector_path=str(database.parent / "vectors.usearch"),
                          lexical_path=str(database.parent / "lexical_index"))
    config = PRMEConfig(**values)
    if provider is not None:
        model, _, dimension = validate_embedding_provider(provider)
        config = config.model_copy(update={"embedding": config.embedding.model_copy(update={
            "provider": "custom", "model_name": model, "dimension": dimension, "api_key": None})})
    return config


async def portability_command(args):
    from prme.privacy.portability import PrivacyPortabilityError
    from prme.storage.privacy_migration import inspect_privacy_migration, migrate_privacy_pack, migration_status
    from prme.storage.privacy_export import restore_protected_export
    try:
        name = args.erasure_command
        if name == "migration-status":
            result = migration_status(args.job)
        elif name in {"export", "policy"}:
            async with _engine(args.engine_factory) as engine:
                result = (await engine.export_protected(args.output, mode=args.mode) if name == "export"
                          else await engine.inspect_privacy_policy(user_id=str(args.owner), scope=Scope(args.scope)))
        else:
            policy = None
            if args.policy_file is not None:
                from prme.privacy.policy import PrivacyPolicy
                from prme.storage.privacy_source import _read_bounded
                policy = PrivacyPolicy.model_validate_json(_read_bounded(Path(args.policy_file), 65536))
            if name == "dry-run":
                result = await inspect_privacy_migration(_portability_config(args, "source"), policy=policy)
            else:
                from prme.privacy.capabilities import PrivacyRuntime
                runtime = await _factory(args.runtime_factory)
                provider = await _factory(args.embedding_factory)
                if not isinstance(runtime, PrivacyRuntime):
                    raise PrivacyDenied("privacy_configuration_invalid")
                target = _portability_config(args, "target", policy=policy, provider=provider)
                if name == "restore":
                    result = await restore_protected_export(args.artifact, target,
                        privacy_runtime=runtime, embedding_provider=provider)
                else:
                    result = await migrate_privacy_pack(_portability_config(args, "source"), target,
                        privacy_runtime=runtime, embedding_provider=provider, job_path=args.job, resume=args.resume,
                        unavailable_legacy_history=args.unavailable_legacy_history,
                        remove_event_ids=tuple(args.remove_event or ()), remove_node_ids=tuple(args.remove_node or ()),
                        allow_reembedding=args.allow_reembedding)
        print(json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result, sort_keys=True))
    except (PrivacyDenied, PrivacyPortabilityError):
        raise
    except Exception:
        raise PrivacyDenied("privacy_capability_unavailable") from None


def failure_guidance(error):
    """Static guidance only; never interpolate source/provider/credential text."""
    from prme.privacy.portability import PrivacyPortabilityError
    if not isinstance(error, (PrivacyDenied, PrivacyPortabilityError)):
        return None
    return {
        "privacy_source_key_unavailable": "Use the original store key; source files were not decrypted to disk.",
        "privacy_provider_unavailable": "Check the exact saved key version. A permanently lost private key requires a separately retained recovery artifact; there is no plaintext fallback.",
        "privacy_capability_unavailable": "Supply the explicit current runtime and required keys. Permanent key loss cannot be repaired from an ordinary pack export.",
        "privacy_ledger_unavailable": "Supply the current independent erasure ledger; a snapshot checkpoint is not current authority.",
        "privacy_ledger_stale": "Use the current independent erasure authority before restoring or serving this copy.",
        "privacy_format_unsupported": "Use a supported format and explicit privacy migration; opening does not convert legacy content.",
        "privacy_migration_required": "Inspect with privacy dry-run, then explicitly migrate to a separate empty destination.",
        "privacy_legacy_unclassified": "Inspect the legacy copy dispositions and explicitly resolve unsupported history, visual data, or identities before migration.",
        "privacy_migration_incomplete": "Resume the same owned migration job; incomplete output is not a serving store.",
        "privacy_source_not_quiescent": "Close/checkpoint the selected source and preserve its WAL; migration will not ignore it.",
        "privacy_source_capacity": "The selected artifact exceeds the bounded reader; no plaintext spill or disk fallback is enabled.",
        "privacy_destination_occupied": "Select an empty destination. This command never overwrites an existing store.",
        "privacy_source_changed": "The source or authenticated job changed; resume requires the original frozen inputs.",
        "privacy_export_invalid": "Use a complete, compatible protected export. Active-memory-only exports are not restorable snapshots.",
    }.get(error.code, "Inspect the explicit policy, selected boundary, saved deadlines and required capabilities.")


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
            target.add_argument("--engine-factory", help="Explicit module:factory transferring an open protected MemoryEngine to this command")
            child.add_argument("--store-key-env", help="Existing local store-key environment variable")
            child.add_argument("--authority-factory", help="Legacy-only explicit module:factory for current-policy authority")
        if name in {"inspect", "unlock", "rotate", "resume"}:
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
    privacy = subparsers.add_parser("privacy", help="Explicit policy, portability, managed erasure and expiry")
    actions = privacy.add_subparsers(dest="erasure_command", required=True)
    for name in ("erase", "status", "purge", "expire"):
        child = actions.add_parser(name)
        child.set_defaults(func=erasure_command)
        child.add_argument("--engine-factory", required=True, help="module:factory; command owns and closes the returned protected engine")
        child.add_argument("--owner", type=UUID, required=True)
        if name in {"erase", "status"}:
            child.add_argument("--request", type=UUID, required=True)
        if name == "erase":
            child.add_argument("--mode", choices=("memory", "evidence", "processing"), default="memory")
            child.add_argument("--scope", choices=("personal", "project", "org"), default="personal")
            child.add_argument("--all", action="store_true", help="Only admissions at or before this request's fence")
            child.add_argument("--event", type=UUID, action="append")
            child.add_argument("--node", type=UUID, action="append")
        if name in {"purge", "expire"}:
            child.add_argument("--limit", type=int, choices=range(1, 129), default=16, metavar="1..128")
            child.add_argument("--budget-ms", type=int, default=1000)
    for name in ("policy", "export", "dry-run", "migrate", "migration-status", "restore"):
        child = actions.add_parser(name)
        child.set_defaults(func=portability_command)
        if name in {"policy", "export"}:
            child.add_argument("--engine-factory", required=True, help="module:factory; command owns and closes the returned protected engine")
        if name == "policy":
            child.add_argument("--owner", type=UUID, required=True)
            child.add_argument("--scope", choices=("personal", "project", "org"), default="personal")
        if name == "export":
            child.add_argument("--output", required=True)
            child.add_argument("--mode", choices=("protected", "active_memory_only"), default="protected")
        if name in {"dry-run", "migrate", "restore"}:
            child.add_argument("--policy-file", required=name != "dry-run", help="Explicit target PrivacyPolicy JSON")
        for selected, included in (("source", {"dry-run", "migrate"}), ("target", {"migrate", "restore"})):
            if name not in included:
                continue
            choice = child.add_mutually_exclusive_group(required=True)
            choice.add_argument("--" + selected, help="Explicit local directory, memory.duckdb[.enc], or .prme path")
            choice.add_argument("--" + selected + "-database-env", help="Name of explicitly selected PostgreSQL URL environment variable")
            child.add_argument("--" + selected + "-key-env", help="Name of the existing local store-key environment variable")
        if name in {"migrate", "restore"}:
            child.add_argument("--runtime-factory", required=True, help="module:factory supplying current policy, ledger, sanitizer and key capabilities")
            child.add_argument("--embedding-factory", required=True, help="module:factory supplying the exact saved model/version/dimension")
        if name in {"migrate", "migration-status"}:
            child.add_argument("--job", required=True)
        if name == "migrate":
            child.add_argument("--resume", action="store_true")
            child.add_argument("--unavailable-legacy-history", action="store_true", help="Explicitly retain original history only in the source; migrated history is typed unavailable")
            child.add_argument("--remove-event", type=UUID, action="append")
            child.add_argument("--remove-node", type=UUID, action="append")
            child.add_argument("--allow-reembedding", action="store_true", help="Permit the explicit provider to encode changed admitted statements")
        if name == "restore":
            child.add_argument("--artifact", required=True)
