"""Explicit native custody. Public registry reads never instantiate a keyring."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from filelock import FileLock
from pydantic import model_validator

from prme.privacy.capabilities import Guard, RetryContext
from prme.privacy.crypto import RSAKeyWrapper, canonical, decode64, encode64, policy_binding, rsa_descriptor
from prme.privacy.keys import (
    AuxiliaryKeyRef,
    KeyBoundary,
    KeyRegistry,
    SecretKeyHandle,
    WrappingKeyDescriptor,
    WrappingKeyRef,
    aware,
    utc_now,
)
from prme.privacy.policy import FrozenModel, PrivacyDenied
from prme.storage._threading import run_to_completion

NativeBackend = Literal["macos", "windows", "secretservice"]
PART_SIZE = 1000
MAX_PARTS = 16


def _open_native(name: NativeBackend) -> Any:
    """Exact classes only; never keyring.get_keyring(), plugins or fallback."""
    try:
        if name == "macos":
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        elif name == "windows":
            from keyring.backends.Windows import WinVaultKeyring
            backend = WinVaultKeyring()
            # Native Credential Manager local-machine persistence, not roaming.
            backend.persist = "local machine"
        elif name == "secretservice":
            from keyring.backends.SecretService import Keyring
            backend = Keyring()
        else:
            raise ValueError()
        expected = {
            "macos": ("keyring.backends.macOS", "Keyring"),
            "windows": ("keyring.backends.Windows", "WinVaultKeyring"),
            "secretservice": ("keyring.backends.SecretService", "Keyring"),
        }[name]
        if (type(backend).__module__, type(backend).__name__) != expected or backend.priority <= 0:
            raise ValueError()
        return backend
    except Exception:
        raise PrivacyDenied("privacy_capability_unavailable") from None


class _NativeParts:
    def __init__(self, backend: NativeBackend, provider_id: UUID) -> None:
        self._backend = _open_native(backend)
        self._service = f"prme.privacy.v1.{provider_id}"

    @staticmethod
    def _name(version: UUID, part: str | int) -> str:
        return f"{version}:{part}"

    def _get(self, version: UUID, part: str | int) -> str | None:
        try:
            value = self._backend.get_password(self._service, self._name(version, part))
            if value is not None and not isinstance(value, str):
                raise ValueError()
            return value
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    def _set(self, version: UUID, part: str | int, value: str) -> None:
        try:
            self._backend.set_password(self._service, self._name(version, part), value)
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    def read(self, version: UUID, binding: bytes) -> bytes:
        try:
            raw = self._get(version, "complete")
            if raw is None or len(raw) > PART_SIZE:
                raise ValueError()
            manifest = json.loads(raw)
            if set(manifest) != {"version", "parts", "sha256"} or manifest["version"] != 1:
                raise ValueError()
            count = manifest["parts"]
            if type(count) is not int or not 1 <= count <= MAX_PARTS:
                raise ValueError()
            parts = [self._get(version, index) for index in range(count)]
            if any(p is None or not 1 <= len(p) <= PART_SIZE or not p.isascii() for p in parts):
                raise ValueError()
            body = "".join(parts)
            if hashlib.sha256(body.encode("ascii")).hexdigest() != manifest["sha256"]:
                raise ValueError()
            item = json.loads(body)
            if set(item) != {"binding", "secret"} or item["binding"] != hashlib.sha256(binding).hexdigest():
                raise ValueError()
            return decode64(item["secret"], minimum=1, maximum=8192)
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    def write(self, version: UUID, binding: bytes, secret: bytes) -> None:
        if self._get(version, "complete") is not None:
            raise PrivacyDenied("privacy_configuration_invalid")
        body = canonical({"binding": hashlib.sha256(binding).hexdigest(), "secret": encode64(secret)}).decode("ascii")
        count = (len(body) + PART_SIZE - 1) // PART_SIZE
        if not 1 <= count <= MAX_PARTS:
            raise PrivacyDenied("privacy_configuration_invalid")
        for index in range(count):
            self._set(version, index, body[index * PART_SIZE:(index + 1) * PART_SIZE])
        self._set(version, "complete", canonical(
            {"version": 1, "parts": count, "sha256": hashlib.sha256(body.encode("ascii")).hexdigest()}).decode("ascii"))
        if self.read(version, binding) != secret:
            raise PrivacyDenied("privacy_capability_unavailable")

    def remove(self, version: UUID) -> None:
        # A fixed, bounded exact-version list. Never enumerate another user's store.
        for part in ("complete", *range(MAX_PARTS)):
            if self._get(version, part) is not None:
                try:
                    self._backend.delete_password(self._service, self._name(version, part))
                except Exception:
                    raise PrivacyDenied("privacy_capability_unavailable") from None


class KeyEntry(FrozenModel):
    wrapping: WrappingKeyDescriptor | None = None
    auxiliary: AuxiliaryKeyRef | None = None
    state: Literal["pending", "ready", "retired"] = "pending"
    active: bool = False

    @model_validator(mode="after")
    def shape(self) -> KeyEntry:
        if (self.wrapping is None) == (self.auxiliary is None) or (self.active and self.state != "ready"):
            raise ValueError("privacy_configuration_invalid")
        return self

    @property
    def version(self) -> UUID:
        return self.wrapping.ref.key_version if self.wrapping is not None else self.auxiliary.key_version

    def binding(self) -> bytes:
        reference = self.wrapping if self.wrapping is not None else self.auxiliary
        return canonical(reference.model_dump(mode="json"))


class PublicKeyStore(FrozenModel):
    schema_version: Literal[1] = 1
    provider_id: UUID
    backend: NativeBackend
    entries: tuple[KeyEntry, ...] = ()

    @model_validator(mode="after")
    def distinct(self) -> PublicKeyStore:
        if len({item.version for item in self.entries}) != len(self.entries):
            raise ValueError("privacy_configuration_invalid")
        if any(item.wrapping and item.wrapping.ref.provider_id != self.provider_id for item in self.entries):
            raise ValueError("privacy_configuration_invalid")
        return self


def read_public_store(path: str | Path) -> PublicKeyStore:
    try:
        target = Path(path)
        if target.is_symlink() or target.stat().st_size > 4 * 1024 * 1024:
            raise ValueError()
        return PublicKeyStore.model_validate_json(target.read_bytes())
    except Exception:
        raise PrivacyDenied("privacy_capability_unavailable") from None


@asynccontextmanager
async def _publication_guard(path: Path):
    # A distinct FileLock per lease avoids reentrant ownership across async tasks.
    # The same OS lock is used by all native descriptor writes/activation.
    lock = FileLock(str(path) + ".lock", timeout=10, thread_local=False)
    try:
        await run_to_completion(lock.acquire)
        yield
    finally:
        if lock.is_locked:
            await run_to_completion(lock.release)


def public_registry(path: str | Path, *, private_wrapper: RSAKeyWrapper | None = None) -> KeyRegistry:
    path = Path(path).absolute()
    state = read_public_store(path)
    def refresh():
        current = read_public_store(path)
        if current.provider_id != state.provider_id:
            raise PrivacyDenied("privacy_capability_unavailable")
        return (tuple(item.wrapping for item in current.entries if item.wrapping and item.state == "ready"),
                tuple(item.wrapping.ref for item in current.entries if item.wrapping and item.active))
    descriptors, active = refresh()
    registry = KeyRegistry(descriptors, active=active, refresh=refresh,
                       publication_guard=lambda: _publication_guard(path),
                       wrappers={state.provider_id: private_wrapper or RSAKeyWrapper()})
    registry.publication_domain = str(path)
    return registry


def _write_public(path: Path, state: PublicKeyStore) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        if path.is_symlink():
            raise ValueError()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            data = state.model_dump_json().encode()
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":  # Windows cannot open directories this way.
                parent = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    except PrivacyDenied:
        raise
    except Exception:
        raise PrivacyDenied("privacy_capability_unavailable") from None


class NativeKeyCustody:
    """Explicit private capability. Constructing a public writer never calls this."""

    isolation = "same_process"

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.path, self.clock = Path(path).absolute(), clock
        state = read_public_store(self.path)
        self._native = _NativeParts(state.backend, state.provider_id)
        self._lock = FileLock(str(self.path) + ".lock", timeout=10)

    @classmethod
    def create(cls, path: str | Path, *, backend: NativeBackend,
               clock: Callable[[], datetime] = utc_now) -> NativeKeyCustody:
        target = Path(path).absolute()
        state = PublicKeyStore(provider_id=uuid4(), backend=backend)
        _NativeParts(backend, state.provider_id)  # Availability before artifact activation.
        target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(target) + ".lock", timeout=10):
            if target.exists() or target.is_symlink():
                raise PrivacyDenied("privacy_configuration_invalid")
            _write_public(target, state)
        return cls(target, clock=clock)

    def inventory(self) -> tuple[KeyEntry, ...]:
        return read_public_store(self.path).entries

    def _entry(self, version: UUID, *, state: PublicKeyStore | None = None) -> KeyEntry:
        found = [item for item in (state or read_public_store(self.path)).entries if item.version == version]
        if len(found) != 1 or found[0].state == "retired":
            raise PrivacyDenied("privacy_capability_unavailable")
        return found[0]

    def _replace(self, state: PublicKeyStore, entry: KeyEntry) -> None:
        entries = tuple(item for item in state.entries if item.version != entry.version) + (entry,)
        _write_public(self.path, state.model_copy(update={"entries": entries}))

    def _provision(self, entry: KeyEntry, secret: bytes, *, activate: bool) -> KeyEntry:
        state = read_public_store(self.path)
        if any(item.version == entry.version for item in state.entries):
            raise PrivacyDenied("privacy_configuration_invalid")
        self._replace(state, entry)
        self._native.write(entry.version, entry.binding(), secret)
        self._verify(entry, self._native.read(entry.version, entry.binding()))
        ready = entry.model_copy(update={"state": "ready", "active": activate})
        self._replace(read_public_store(self.path), ready)
        return ready

    @staticmethod
    def _verify(entry: KeyEntry, secret: bytes) -> None:
        try:
            if entry.wrapping is not None:
                private = serialization.load_der_private_key(secret, password=None)
                if not isinstance(private, rsa.RSAPrivateKey):
                    raise ValueError()
                ref = entry.wrapping.ref
                if rsa_descriptor(private.public_key(), boundary=entry.wrapping.boundary,
                                  purpose=entry.wrapping.purpose, provider_id=ref.provider_id,
                                  key_id=ref.key_id, key_version=ref.key_version) != entry.wrapping:
                    raise ValueError()
            elif len(secret) != 32:
                raise ValueError()
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    async def provision_wrapping(self, boundary: KeyBoundary, purpose: Literal["evidence", "processing"],
                                *, key_id: UUID | None = None) -> WrappingKeyDescriptor:
        def provision():
            with self._lock:
                state = read_public_store(self.path)
                private = rsa.generate_private_key(public_exponent=65537, key_size=4096)
                descriptor = rsa_descriptor(private.public_key(), boundary=boundary, purpose=purpose,
                    provider_id=state.provider_id, key_id=key_id or uuid4(), key_version=uuid4())
                secret = private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption())
                active = not any(item.wrapping and item.active and item.wrapping.boundary == boundary
                                 and item.wrapping.purpose == purpose for item in state.entries)
                self._provision(KeyEntry(wrapping=descriptor), secret, activate=active)
                return descriptor
        return await run_to_completion(provision)

    async def resume(self, version: UUID) -> None:
        def finish():
            with self._lock:
                state = read_public_store(self.path)
                entry = self._entry(version, state=state)
                self._verify(entry, self._native.read(version, entry.binding()))
                self._replace(state, entry.model_copy(update={"state": "ready"}))
        await run_to_completion(finish)

    async def activate(self, ref: WrappingKeyRef, *, guard: Guard,
                       expected: WrappingKeyRef | None = None) -> None:
        def activate():
            with self._lock:
                state = read_public_store(self.path)
                entry = self._entry(ref.key_version, state=state)
                if entry.state != "ready" or entry.wrapping is None or entry.wrapping.ref != ref:
                    raise PrivacyDenied("privacy_capability_unavailable")
                active = tuple(item.wrapping.ref for item in state.entries
                               if item.active and item.wrapping is not None
                               and (item.wrapping.boundary, item.wrapping.purpose) ==
                               (entry.wrapping.boundary, entry.wrapping.purpose))
                if expected is not None and active not in {(expected,), (ref,)}:
                    raise PrivacyDenied("privacy_capability_unavailable")
                self._verify(entry, self._native.read(entry.version, entry.binding()))
                descriptor = entry.wrapping
                entries = tuple(item.model_copy(update={"active": item.version == ref.key_version})
                    if item.wrapping and (item.wrapping.boundary, item.wrapping.purpose) == (descriptor.boundary, descriptor.purpose)
                    else item for item in state.entries)
                _write_public(self.path, state.model_copy(update={"entries": entries}))
        async with guard():
            await run_to_completion(activate)

    async def retire(self, version: UUID) -> None:
        def retire():
            with self._lock:
                state = read_public_store(self.path)
                items = [item for item in state.entries if item.version == version]
                if len(items) != 1:
                    raise PrivacyDenied("privacy_capability_unavailable")
                self._replace(state, items[0].model_copy(update={"state": "retired", "active": False}))
                self._native.remove(version)
        await run_to_completion(retire)

    @contextmanager
    def unlocked(self, refs: tuple[WrappingKeyRef, ...], *, expires_at: datetime) -> Iterator[RSAKeyWrapper]:
        now, expiry = aware(self.clock()), aware(expires_at)
        if not 1 <= len(refs) <= 128 or not 0 < (expiry - now).total_seconds() <= 900:
            raise PrivacyDenied("privacy_configuration_invalid")
        keys = {}
        try:
            with self._lock:
                for ref in refs:
                    entry = self._entry(ref.key_version)
                    if entry.state != "ready" or entry.wrapping is None or entry.wrapping.ref != ref:
                        raise PrivacyDenied("privacy_capability_unavailable")
                    secret = self._native.read(entry.version, entry.binding())
                    self._verify(entry, secret)
                    keys[ref] = serialization.load_der_private_key(secret, password=None)
            wrapper = RSAKeyWrapper(keys, expires_at=expiry, clock=self.clock)
            try:
                yield wrapper
            finally:
                wrapper.close()
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None
        finally:
            keys.clear()

    async def provision_pseudonym_key(self, boundary: KeyBoundary, epoch: UUID) -> AuxiliaryKeyRef:
        def provision():
            with self._lock:
                if any(item.auxiliary and item.auxiliary.boundary == boundary and item.auxiliary.epoch == epoch
                       for item in read_public_store(self.path).entries):
                    raise PrivacyDenied("privacy_capability_unavailable")
                ref = AuxiliaryKeyRef(boundary=boundary, purpose="pseudonym", epoch=epoch, key_version=uuid4())
                self._provision(KeyEntry(auxiliary=ref), os.urandom(32), activate=False)
                return ref
        return await run_to_completion(provision)

    def _auxiliary(self, entry: KeyEntry) -> SecretKeyHandle:
        if entry.state != "ready" or entry.auxiliary is None:
            raise PrivacyDenied("privacy_capability_unavailable")
        key = self._native.read(entry.version, entry.binding())
        self._verify(entry, key)
        return SecretKeyHandle(entry.auxiliary, key, clock=self.clock)

    async def key_for(self, project_id: UUID, owner_id: UUID, scope: str, epoch: UUID) -> SecretKeyHandle:
        boundary = KeyBoundary(project_id=project_id, owner_id=owner_id, scope=scope)
        def lookup():
            with self._lock:
                items = [e for e in self.inventory() if e.auxiliary and e.auxiliary.purpose == "pseudonym"
                         and e.auxiliary.boundary == boundary and e.auxiliary.epoch == epoch]
                if len(items) != 1:
                    raise PrivacyDenied("privacy_capability_unavailable")
                return self._auxiliary(items[0])
        return await run_to_completion(lookup)

    async def provision_retry_key(self, context: RetryContext, *, retention_ref: UUID,
                                  expires_at: datetime) -> SecretKeyHandle:
        boundary, binding = KeyBoundary.from_snapshot(context.snapshot), policy_binding(context.snapshot)
        expiry = aware(expires_at)
        if expiry <= aware(self.clock()):
            raise PrivacyDenied("privacy_retry_unavailable")
        def provision():
            with self._lock:
                items = [e for e in self.inventory() if e.auxiliary and e.auxiliary.purpose == "retry_commitment"
                         and e.auxiliary.boundary == boundary and e.auxiliary.request_id == context.request_id]
                if items:
                    if len(items) != 1 or (items[0].auxiliary.policy_binding, items[0].auxiliary.retention_ref,
                                          items[0].auxiliary.expires_at) != (binding, retention_ref, expiry):
                        raise PrivacyDenied("privacy_retry_unavailable")
                    return self._auxiliary(items[0])
                ref = AuxiliaryKeyRef(boundary=boundary, purpose="retry_commitment", key_version=uuid4(),
                    request_id=context.request_id, retention_ref=retention_ref, expires_at=expiry, policy_binding=binding)
                entry = self._provision(KeyEntry(auxiliary=ref), os.urandom(32), activate=False)
                return self._auxiliary(entry)
        try:
            return await run_to_completion(provision)
        except Exception:
            raise PrivacyDenied("privacy_retry_unavailable") from None

    async def get_retry_key(self, context: RetryContext, *, key_version: UUID,
                            retention_ref: UUID) -> SecretKeyHandle:
        def lookup():
            with self._lock:
                entry = self._entry(key_version)
                ref = entry.auxiliary
                if (ref is None or ref.purpose != "retry_commitment" or ref.request_id != context.request_id
                        or ref.retention_ref != retention_ref or ref.boundary != KeyBoundary.from_snapshot(context.snapshot)
                        or ref.policy_binding != policy_binding(context.snapshot)
                        or aware(self.clock()) >= ref.expires_at):
                    raise PrivacyDenied("privacy_retry_unavailable")
                return self._auxiliary(entry)
        try:
            return await run_to_completion(lookup)
        except Exception:
            raise PrivacyDenied("privacy_retry_unavailable") from None

    async def export_recovery(self, ref: WrappingKeyRef, destination: str | Path, *,
                              passphrase: bytes, pack_path: str | Path) -> None:
        destination, pack = Path(destination).absolute(), Path(pack_path).absolute()
        if not isinstance(passphrase, bytes) or len(passphrase) < 16:
            raise PrivacyDenied("privacy_configuration_invalid")
        pack_directory = pack if pack.is_dir() else pack.parent
        if destination.resolve().is_relative_to(pack_directory.resolve()):
            raise PrivacyDenied("privacy_configuration_invalid")
        def export():
            with self._lock:
                entry = self._entry(ref.key_version)
                if entry.wrapping is None or entry.wrapping.ref != ref:
                    raise PrivacyDenied("privacy_capability_unavailable")
                secret = self._native.read(entry.version, entry.binding())
                self._verify(entry, secret)
                key = serialization.load_der_private_key(secret, password=None)
                encrypted = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.BestAvailableEncryption(passphrase))
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
        try:
            await run_to_completion(export)
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    async def import_recovery(self, ref: WrappingKeyRef, source: str | Path, *, passphrase: bytes) -> None:
        def restore():
            with self._lock:
                entry = self._entry(ref.key_version)
                path = Path(source)
                if entry.wrapping is None or entry.wrapping.ref != ref or path.is_symlink() or path.stat().st_size > 16384:
                    raise ValueError()
                private = serialization.load_pem_private_key(path.read_bytes(), password=passphrase)
                secret = private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption())
                self._verify(entry, secret)
                # Recovery only restores the exact provisioned version, never retired keys.
                self._native.remove(entry.version)
                self._native.write(entry.version, entry.binding(), secret)
        try:
            await run_to_completion(restore)
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None
