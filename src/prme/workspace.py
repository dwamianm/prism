"""Bounded, identity-checked namespaces for trusted Python applications."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import wraps
import inspect
from pathlib import Path
import sqlite3
from typing import AsyncIterator
from uuid import UUID, uuid4

from filelock import FileLock, Timeout

from prme.config import PRMEConfig
from prme.storage._threading import run_async_to_completion
from prme.storage.embedding import EmbeddingProvider, create_embedding_provider
from prme.storage.engine import MemoryEngine


class WorkspaceError(RuntimeError):
    """Workspace ownership, capacity, or lifetime prevents an operation."""


@dataclass(frozen=True)
class NamespaceInfo:
    name: str
    id: UUID


@dataclass
class _Entry:
    engine: MemoryEngine
    references: int = 0
    used: int = 0
    owners: dict[asyncio.Task, int] = field(default_factory=dict)


class NamespaceMemory(MemoryEngine):
    """Lease-scoped MemoryEngine API; the workspace owns open/close.

    Public method signatures and result types are inherited from MemoryEngine.
    A released lease cannot be reused, including through a saved bound method.
    Private engine internals are deliberately not forwarded.
    """

    def __init__(self, engine: MemoryEngine, namespace: NamespaceInfo):
        self._engine = engine
        self._namespace = namespace
        self._loop = asyncio.get_running_loop()
        self._accepting = True
        self._operations = 0
        self._drained = asyncio.Event()
        self._drained.set()

    @property
    def namespace(self) -> NamespaceInfo:
        return self._namespace

    def _check(self):
        if not self._accepting:
            raise WorkspaceError("Namespace lease has been released")
        if asyncio.get_running_loop() is not self._loop:
            raise WorkspaceError("Namespace lease must be used on its owning event loop")

    def _begin(self):
        self._check()
        self._operations += 1
        self._drained.clear()

    def _end(self):
        self._operations -= 1
        if not self._operations:
            self._drained.set()

    def __getattribute__(self, name):
        if name.startswith("_") or name == "namespace":
            return object.__getattribute__(self, name)
        self._check()
        if name in {"open", "create", "close", "lock", "unlock"}:
            raise WorkspaceError("The workspace owns namespace lifecycle; exit the namespace context")
        method = getattr(self._engine, name)
        if inspect.isasyncgenfunction(method):
            @wraps(method)
            async def iterate(*args, **kwargs):
                self._begin()
                iterator = method(*args, **kwargs)
                try:
                    async for item in iterator:
                        yield item
                finally:
                    try:
                        await iterator.aclose()
                    finally:
                        self._end()
            return iterate
        if inspect.iscoroutinefunction(method):
            @wraps(method)
            async def call(*args, **kwargs):
                self._begin()
                try:
                    return await method(*args, **kwargs)
                finally:
                    self._end()
            return call
        if callable(method):
            @wraps(method)
            def call_sync(*args, **kwargs):
                self._check()
                return method(*args, **kwargs)
            return call_sync
        return method


class MemoryWorkspace:
    """Own a bounded LRU cache of physically separate local memory engines.

    One workspace instance owns a directory until close, enforced by a process
    file lock. Each instance belongs to one event loop. Names are case-sensitive
    application keys, not paths or authorization grants. Registry names/IDs are
    plaintext metadata even when the individual packs use encryption.
    """

    def __init__(self, directory: str | Path, *, config: PRMEConfig | None = None,
                 embedding_provider: EmbeddingProvider | None = None, max_open: int = 4):
        self._config = (config or PRMEConfig()).model_copy(deep=True)
        if self._config.backend != "duckdb" or self._config.namespace_id is not None:
            raise ValueError("Workspace requires local configuration without a preselected namespace_id")
        self._directory = Path(directory).resolve()
        self._initialize_state(embedding_provider, max_open)

    def _initialize_state(self, embedding_provider, max_open):
        if type(max_open) is not int or max_open < 1:
            raise ValueError("max_open must be a positive integer")
        self._provider = (create_embedding_provider(self._config.embedding)
                          if embedding_provider is None else embedding_provider)
        self._max_open = max_open
        self._condition = asyncio.Condition()
        self._entries: dict[UUID, _Entry] = {}
        self._clock = 0
        self._started = False
        self._closing = False
        self._closed = False
        self._failure: Exception | None = None
        self._close_task: asyncio.Task | None = None

    @classmethod
    @asynccontextmanager
    async def open(cls, directory: str | Path, *, config: PRMEConfig | None = None,
                   embedding_provider: EmbeddingProvider | None = None,
                   max_open: int = 4) -> AsyncIterator[MemoryWorkspace]:
        async with cls(directory, config=config, embedding_provider=embedding_provider, max_open=max_open) as workspace:
            yield workspace

    @classmethod
    @asynccontextmanager
    async def open_postgres(cls, config: PRMEConfig, *, name: str = "default",
                            embedding_provider: EmbeddingProvider | None = None,
                            max_open: int = 4, min_connections: int = 1,
                            max_connections: int = 10) -> AsyncIterator[MemoryWorkspace]:
        """Open named PostgreSQL projects using one bounded connection pool.

        ``name`` identifies a workspace in this database. Project names are
        scoped by that workspace; both names are application keys, not grants.
        """
        async with _PostgresWorkspace(config, name=name, embedding_provider=embedding_provider,
                                      max_open=max_open, min_connections=min_connections,
                                      max_connections=max_connections) as workspace:
            yield workspace

    def _path(self, relative: str) -> Path:
        path = self._directory / relative
        # Workspace storage is trusted, but pre-existing redirections must not
        # silently route a named project into some other pack.
        current = path
        while current != self._directory:
            if current.is_symlink():
                raise WorkspaceError("Workspace paths must not contain symbolic links")
            current = current.parent
        return path

    async def __aenter__(self) -> MemoryWorkspace:
        if self._started or self._closed:
            raise WorkspaceError("Workspace instances cannot be reopened")
        self._directory.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self._path("workspace.lock")), timeout=0, thread_local=False)
        try:
            self._lock.acquire()
        except Timeout as exc:
            raise WorkspaceError("Workspace is already open; share its existing instance") from exc
        try:
            registry = self._path("workspace.sqlite3")
            fresh = not registry.exists()
            self._registry = sqlite3.connect(registry)
            if fresh:
                with self._registry:
                    self._registry.execute("CREATE TABLE metadata (version INTEGER NOT NULL)")
                    self._registry.execute("INSERT INTO metadata VALUES (1)")
                    self._registry.execute("CREATE TABLE namespaces (name TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, "
                                           "initialized INTEGER NOT NULL DEFAULT 0)")
            if self._registry.execute("SELECT version FROM metadata").fetchall() != [(1,)]:
                raise WorkspaceError("Unsupported workspace registry version")
            self._registry.execute("SELECT name, id, initialized FROM namespaces LIMIT 1")
        except BaseException:
            if hasattr(self, "_registry"):
                self._registry.close()
            self._lock.release()
            raise
        self._loop = asyncio.get_running_loop()
        self._started = True
        return self

    async def __aexit__(self, *_):
        await self.close()

    def _check(self):
        if not self._started or self._closing or self._closed:
            raise WorkspaceError("Workspace is not open")
        if asyncio.get_running_loop() is not self._loop:
            raise WorkspaceError("Workspace must be used on its owning event loop")
        if self._failure is not None:
            raise WorkspaceError("An engine failed to close; close the workspace before reopening") from self._failure

    async def _resolve(self, name: str, create: bool) -> NamespaceInfo:
        _validate_name(name)
        row = self._registry.execute("SELECT id FROM namespaces WHERE name = ?", [name]).fetchone()
        if row is None:
            if not create:
                raise KeyError(name)
            identifier = uuid4()
            with self._registry:
                self._registry.execute("INSERT INTO namespaces (name, id) VALUES (?, ?)", [name, str(identifier)])
            return NamespaceInfo(name, identifier)
        return NamespaceInfo(name, UUID(row[0]))

    async def list_namespaces(self) -> list[NamespaceInfo]:
        self._check()
        return [NamespaceInfo(name, UUID(identifier)) for name, identifier in
                self._registry.execute("SELECT name, id FROM namespaces ORDER BY name").fetchall()]

    def _pack_config(self, namespace: NamespaceInfo) -> PRMEConfig:
        relative = f"packs/{namespace.id}"
        directory = self._path(relative)
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise WorkspaceError("Namespace packs must not contain symbolic links")
        return self._config.model_copy(update={
            "namespace_id": namespace.id, "db_path": str(directory / "memory.duckdb"),
            "vector_path": str(directory / "vectors.usearch"),
            "lexical_path": str(directory / "lexical_index"),
        }, deep=True)

    async def _publish_open(self, namespace: NamespaceInfo):
        config = self._pack_config(namespace)
        initialized = self._registry.execute("SELECT initialized FROM namespaces WHERE id = ?",
                                             [str(namespace.id)]).fetchone()[0]
        if initialized and not (Path(config.db_path).exists() or Path(config.db_path + ".enc").exists()):
            raise WorkspaceError("Initialized namespace database is missing; restore its pack before opening")
        engine = await MemoryEngine.create(config, embedding_provider=self._provider)
        try:
            with self._registry:
                self._registry.execute("UPDATE namespaces SET initialized = 1 WHERE id = ?", [str(namespace.id)])
        except BaseException:
            await engine.close()
            raise
        self._entries[namespace.id] = _Entry(engine)
        self._condition.notify_all()

    async def _evict(self, identifier: UUID):
        entry = self._entries[identifier]
        try:
            await entry.engine.close()
        except Exception as exc:
            self._failure = exc
            raise
        del self._entries[identifier]

    @asynccontextmanager
    async def namespace(self, name: str, *, create: bool = True) -> AsyncIterator[NamespaceMemory]:
        self._check()
        owner = asyncio.current_task()
        assert owner is not None
        async with self._condition:
            self._check()
            namespace = await self._resolve(name, create)
            while namespace.id not in self._entries:
                self._check()
                if len(self._entries) < self._max_open:
                    # Publish an idle entry before a cancelled opener unwinds.
                    # No capacity slot or native engine is lost on cancellation.
                    await run_async_to_completion(self._publish_open(namespace))
                    break
                idle = [(entry.used, str(identifier), identifier) for identifier, entry in self._entries.items()
                        if entry.references == 0]
                if idle:
                    await run_async_to_completion(self._evict(min(idle)[2]))
                    continue
                if all(owner in entry.owners for entry in self._entries.values()):
                    raise WorkspaceError("Nested namespace request exceeds max_open; release a lease or increase capacity")
                await self._condition.wait()
            self._check()
            entry = self._entries[namespace.id]
            entry.references += 1
            entry.owners[owner] = entry.owners.get(owner, 0) + 1
        memory = NamespaceMemory(entry.engine, namespace)
        try:
            yield memory
        finally:
            memory._accepting = False
            await run_async_to_completion(self._release(memory, entry, owner))

    async def _release(self, memory: NamespaceMemory, entry: _Entry, owner: asyncio.Task):
        await memory._drained.wait()
        async with self._condition:
            entry.references -= 1
            entry.owners[owner] -= 1
            if not entry.owners[owner]:
                del entry.owners[owner]
            self._clock += 1
            entry.used = self._clock
            self._condition.notify_all()

    async def close(self):
        if not self._started:
            self._closed = True
            return
        if asyncio.get_running_loop() is not self._loop:
            raise WorkspaceError("Workspace must close on its owning event loop")
        owner = asyncio.current_task()
        if any(owner in entry.owners for entry in self._entries.values()):
            raise WorkspaceError("Release this task's namespace leases before closing the workspace")
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._finish_close())
        task = self._close_task

        async def wait():
            await asyncio.shield(task)
        await run_async_to_completion(wait())

    async def _finish_close(self):
        errors = []
        async with self._condition:
            self._condition.notify_all()
            while any(entry.references for entry in self._entries.values()):
                await self._condition.wait()
            try:
                for entry in self._entries.values():
                    try:
                        await entry.engine.close()
                    except Exception as exc:
                        errors.append(exc)
                if self._failure is not None:
                    errors.append(self._failure)
            finally:
                self._entries.clear()
                try:
                    await self._close_storage()
                finally:
                    self._closed = True
        if errors:
            raise ExceptionGroup("Workspace engine cleanup failed", errors)


    async def _close_storage(self):
        self._registry.close()
        self._lock.release()


def _validate_name(name: str):
    if not isinstance(name, str) or not name or name != name.strip() or len(name.encode("utf-8")) > 512:
        raise ValueError("Namespace name must be 1–512 UTF-8 bytes without surrounding whitespace")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("Namespace name must not contain control characters")


class _PostgresWorkspace(MemoryWorkspace):
    """PostgreSQL storage hooks using the same lease/cache lifecycle as local packs."""

    def __init__(self, config, *, name, embedding_provider, max_open, min_connections, max_connections):
        self._config = config.model_copy(deep=True)
        if self._config.backend != "postgres" or self._config.namespace_id is not None:
            raise ValueError("PostgreSQL workspace requires database_url without a preselected namespace_id")
        if self._config.encryption_enabled:
            raise ValueError("Pack encryption is local-only; configure PostgreSQL transport and storage encryption separately")
        for value in (min_connections, max_connections):
            if type(value) is not int or value < 1:
                raise ValueError("Connection limits must be positive integers")
        if min_connections > max_connections:
            raise ValueError("min_connections must not exceed max_connections")
        _validate_name(name)
        self._name = name
        self._min_connections = min_connections
        self._max_connections = max_connections
        self._initialize_state(embedding_provider, max_open)
        from prme.storage.embedding import validate_embedding_provider
        model, _, dimension = validate_embedding_provider(self._provider)
        self._config = self._config.model_copy(update={"embedding": self._config.embedding.model_copy(update={
            "provider": "custom", "model_name": model, "dimension": dimension, "api_key": None,
        })}, deep=True)

    async def __aenter__(self) -> MemoryWorkspace:
        if self._started or self._closed:
            raise WorkspaceError("Workspace instances cannot be reopened")
        # Settle acquisition even if the context opener is cancelled. If no
        # context is delivered, close the acquired root pool before unwinding.
        try:
            await run_async_to_completion(self._start())
        except BaseException:
            if hasattr(self, "_pool"):
                await run_async_to_completion(self._pool.close())
            self._closed = True
            raise
        return self

    async def _start(self):
        from prme.storage.pg.pool import create_pool
        from prme.storage.pg.workspace_catalog import WorkspaceCatalog
        assert self._config.database_url is not None
        self._pool = await create_pool(self._config.database_url.get_secret_value(),
                                       min_size=self._min_connections, max_size=self._max_connections)
        self._catalog = WorkspaceCatalog(self._pool, self._name)
        await self._catalog.initialize()
        self._loop = asyncio.get_running_loop()
        self._started = True

    async def _resolve(self, name: str, create: bool) -> NamespaceInfo:
        _validate_name(name)
        return NamespaceInfo(name, await self._catalog.resolve(name, create=create))

    async def list_namespaces(self) -> list[NamespaceInfo]:
        self._check()
        return [NamespaceInfo(name, identifier) for name, identifier in await self._catalog.list_namespaces()]

    async def _publish_open(self, namespace: NamespaceInfo):
        pool = await self._catalog.prepare(namespace.name, namespace.id, embedding_dim=self._config.embedding.dimension)
        config = self._config.model_copy(update={"namespace_id": namespace.id}, deep=True)
        engine = await MemoryEngine._create_postgres(config, embedding_provider=self._provider, namespace_pool=pool)
        self._entries[namespace.id] = _Entry(engine)
        self._condition.notify_all()

    async def _close_storage(self):
        await self._pool.close()
