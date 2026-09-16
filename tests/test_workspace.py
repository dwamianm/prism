"""Named physical partitions preserve isolation and bounded engine ownership."""

import asyncio
from contextlib import asynccontextmanager
import shutil
import sqlite3
import subprocess
import sys
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import MemoryEngine, PRMEConfig, RelevanceSubmission
from prme.storage.namespace_identity import NamespaceIdentityError
from prme.types import NodeType, Scope
from prme.workspace import MemoryWorkspace, NamespaceMemory, WorkspaceError
from tests.test_durable_ingestion import MockEmbeddingProvider


@pytest.fixture
def config():
    return PRMEConfig(database_url=None, namespace_id=None, duckdb_threads=1,
                      organizer={"opportunistic_enabled": False}, materialization_budget_ms=5000)


@asynccontextmanager
async def workspace(path, config, max_open=1):
    async with MemoryWorkspace.open(path, config=config, embedding_provider=MockEmbeddingProvider(),
                                    max_open=max_open) as value:
        yield value


async def test_same_owner_project_names_preserve_sources_receipts_and_recovery_across_eviction(tmp_path, config):
    saved = {}
    async with workspace(tmp_path, config) as ws:
        for name, days in [("Aurora", 7), ("Aurora/experimental", 30)]:
            async with ws.namespace(name) as memory:
                assert isinstance(memory, NamespaceMemory)
                events = [await memory.store("Aurora", user_id="owner", scope=Scope.PROJECT,
                                             node_type=NodeType.ENTITY, metadata={"entity_type": "project"})
                          for _ in range(2)]
                fact = f"Aurora retains records for {days} days"
                merged = await memory.organize(jobs=["deduplicate"], budget_ms=30000)
                assert merged.per_job["deduplicate"].nodes_modified == 1
                pending = await memory.ingest_fast(fact, user_id="owner", scope=Scope.PROJECT)
                assert (await memory.processing_status(pending, user_id="owner")).status == "pending"
                saved[name] = (memory.namespace, events, pending, fact)
        for name, (info, events, pending, fact) in saved.items():
            async with ws.namespace(name, create=False) as memory:
                assert memory.namespace == info
                assert (await memory.processing_status(pending, user_id="owner")).status == "pending"
                result = await memory.retrieve("Aurora retains records", user_id="owner", scope=Scope.PROJECT)
                assert fact in [c.node.content for c in result.results]
                assert (await memory.processing_status(pending, user_id="owner")).status == "complete"
                submission = RelevanceSubmission(request_id=result.metadata.request_id,
                                                 labels={result.results[0].node.id: True})
                label = await memory.record_relevance(submission, user_id="owner")
                for foreign_name, (_, foreign_events, foreign_pending, foreign_fact) in saved.items():
                    if foreign_name != name:
                        assert foreign_fact not in [c.node.content for c in result.results]
                        assert await memory.get_event(foreign_events[0], user_id="owner") is None
                        assert await memory.processing_status(foreign_pending, user_id="owner") is None
                for event in events:
                    assert (await memory.get_event(event, user_id="owner")).content == "Aurora"
                saved[name] = (info, events, pending, fact)
                if name == "Aurora":
                    request_id, feedback_id = str(result.metadata.request_id), str(label.feedback_id)
        async with ws.namespace("Aurora/experimental") as memory:
            assert await memory.get_retrieval_receipt(request_id, user_id="owner") is None
            assert await memory.get_relevance(feedback_id, user_id="owner") is None
    async with workspace(tmp_path, config) as ws:
        assert [n.name for n in await ws.list_namespaces()] == sorted(saved)
        async with ws.namespace("Aurora", create=False) as memory:
            assert await memory.get_retrieval_receipt(request_id, user_id="owner") is not None
            assert await memory.get_relevance(feedback_id, user_id="owner") is not None


async def test_leased_operations_finish_before_eviction_and_saved_methods_expire(tmp_path, config, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    original = MemoryEngine.store

    async def delayed(self, *args, **kwargs):
        started.set()
        await finish.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(MemoryEngine, "store", delayed)
    async with workspace(tmp_path, config) as ws:
        entered = asyncio.Event()
        holders = {}

        async def first():
            async with ws.namespace("first") as memory:
                holders["memory"] = memory
                holders["saved"] = memory.store
                holders["operation"] = asyncio.create_task(memory.store("retained", user_id="owner"))
                await started.wait()
            holders["released"] = True

        async def second():
            async with ws.namespace("second"):
                entered.set()

        task = asyncio.create_task(first())
        await started.wait()
        other = asyncio.create_task(second())
        await asyncio.sleep(0.01)
        assert not entered.is_set() and not task.done()
        task.cancel()
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await other
        assert await holders["operation"]
        with pytest.raises(WorkspaceError, match="released"):
            await holders["saved"]("invalid", user_id="owner")
        with pytest.raises(WorkspaceError, match="released"):
            await holders["memory"].count_nodes()
        async with ws.namespace("first") as memory:
            assert await memory.count_nodes(user_id="owner") == 1


async def test_same_project_leases_share_capacity_but_have_independent_lifetimes(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("same") as first:
            async with ws.namespace("same") as second:
                await second.store("source", user_id="owner")
                assert len(ws._entries) == 1
                with pytest.raises(WorkspaceError, match="lifecycle"):
                    await second.close()
            assert await first.count_nodes(user_id="owner") == 1
            with pytest.raises(WorkspaceError, match="Nested"):
                async with ws.namespace("another"):
                    pytest.fail("Capacity deadlock must not be entered")
            with pytest.raises(WorkspaceError, match="Release"):
                await ws.close()


async def test_cancelled_open_publishes_owned_idle_engine_and_retry_reuses_it(tmp_path, config, monkeypatch):
    entered, resume = asyncio.Event(), asyncio.Event()
    original = MemoryEngine.create.__func__
    calls = 0

    async def delayed(cls, *args, **kwargs):
        nonlocal calls
        engine = await original(cls, *args, **kwargs)
        calls += 1
        entered.set()
        await resume.wait()
        return engine

    monkeypatch.setattr(MemoryEngine, "create", classmethod(delayed))
    async with workspace(tmp_path, config) as ws:
        async def borrow():
            async with ws.namespace("opening"):
                pytest.fail("Cancelled caller must not acquire a lease")
        task = asyncio.create_task(borrow())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        assert not task.done()
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with ws.namespace("opening") as memory:
            assert await memory.count_nodes() == 0
        assert calls == 1


async def test_cancelled_waiter_does_not_reserve_capacity(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("first"):
            async def borrow():
                async with ws.namespace("second"):
                    pytest.fail("No slot available")
            waiter = asyncio.create_task(borrow())
            await asyncio.sleep(0.01)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        async with ws.namespace("second") as memory:
            assert await memory.count_nodes() == 0


async def test_close_waits_for_leases_despite_cancellation_and_releases_process_lock(tmp_path, config):
    ws = await MemoryWorkspace(tmp_path, config=config, embedding_provider=MockEmbeddingProvider()).__aenter__()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def borrower():
        async with ws.namespace("project") as memory:
            entered.set()
            await finish.wait()
            await memory.store("before close", user_id="owner")

    task = asyncio.create_task(borrower())
    await entered.wait()
    closer = asyncio.create_task(ws.close())
    await asyncio.sleep(0.01)
    closer.cancel()
    await asyncio.sleep(0)
    closer.cancel()
    assert not closer.done()
    with pytest.raises(WorkspaceError, match="not open"):
        async with ws.namespace("new"):
            pass
    finish.set()
    await task
    with pytest.raises(asyncio.CancelledError):
        await closer
    await ws.close()
    async with workspace(tmp_path, config) as reopened:
        async with reopened.namespace("project") as memory:
            assert await memory.count_nodes(user_id="owner") == 1


async def test_identity_rejects_pack_swap_before_schema_startup(tmp_path, config, monkeypatch):
    root = tmp_path / "original"
    async with workspace(root, config) as ws:
        for name in ["a", "b"]:
            async with ws.namespace(name) as memory:
                await memory.store(name, user_id="owner")
        ids = {n.name: n.id for n in await ws.list_namespaces()}
    shutil.copyfile(root / "packs" / str(ids["b"]) / "memory.duckdb",
                    root / "packs" / str(ids["a"]) / "memory.duckdb")
    monkeypatch.setattr("prme.storage.engine.initialize_database", lambda _: pytest.fail("Wrong identity reached startup"))
    async with workspace(root, config) as ws:
        with pytest.raises(NamespaceIdentityError, match="does not match"):
            async with ws.namespace("a"):
                pytest.fail("Swapped pack was opened")


async def test_closed_workspace_copy_preserves_namespace_and_source_identity(tmp_path, config):
    root = tmp_path / "original"
    async with workspace(root, config) as ws:
        async with ws.namespace("a") as memory:
            info = memory.namespace
            event = await memory.store("copied source", user_id="owner")
    target = tmp_path / "copy"
    shutil.copytree(root, target)
    async with workspace(target, config) as copied:
        async with copied.namespace("a", create=False) as memory:
            assert memory.namespace == info
            assert (await memory.get_event(event, user_id="owner")).content == "copied source"


async def test_unbound_existing_pack_requires_explicit_import(tmp_path, config):
    config = config.model_copy(update={"db_path": str(tmp_path / "old.duckdb"),
        "vector_path": str(tmp_path / "old.usearch"), "lexical_path": str(tmp_path / "lexical")})
    async with MemoryEngine.open(config, embedding_provider=MockEmbeddingProvider()) as memory:
        await memory.store("legacy", user_id="owner")
    config.namespace_id = uuid4()
    with pytest.raises(NamespaceIdentityError, match="explicit import"):
        await MemoryEngine.create(config, embedding_provider=MockEmbeddingProvider())


async def test_second_workspace_cannot_bypass_open_ownership(tmp_path, config):
    async with workspace(tmp_path, config):
        with pytest.raises(WorkspaceError, match="already open"):
            async with workspace(tmp_path, config):
                pass
    async with workspace(tmp_path, config):
        pass


async def test_registry_symlink_failure_does_not_leak_workspace_lock(tmp_path, config):
    other = tmp_path / "other"
    other.touch()
    (tmp_path / "workspace.sqlite3").symlink_to(other)
    with pytest.raises(WorkspaceError, match="symbolic"):
        async with workspace(tmp_path, config):
            pass
    (tmp_path / "workspace.sqlite3").unlink()
    async with workspace(tmp_path, config):
        pass


async def test_cleanup_failure_is_reported_and_not_reused(tmp_path, config, monkeypatch):
    ws = await MemoryWorkspace(tmp_path, config=config, embedding_provider=MockEmbeddingProvider(), max_open=1).__aenter__()
    async with ws.namespace("first"):
        pass
    engine = next(iter(ws._entries.values())).engine
    original = engine.close

    async def failing():
        await original()
        raise RuntimeError("authored close failure")
    monkeypatch.setattr(engine, "close", failing)
    with pytest.raises(RuntimeError, match="authored"):
        async with ws.namespace("second"):
            pass
    with pytest.raises(WorkspaceError, match="failed to close"):
        async with ws.namespace("first"):
            pass
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        await ws.close()
    async with workspace(tmp_path, config):
        pass


@pytest.mark.parametrize("name", ["", " ", " leading", "trailing ", "line\nbreak", "\x00", "x" * 513, None])
async def test_bad_names_never_create_registry_entries(tmp_path, config, name):
    async with workspace(tmp_path, config) as ws:
        with pytest.raises(ValueError):
            async with ws.namespace(name):
                pass
        assert await ws.list_namespaces() == []


async def test_missing_name_and_unsupported_database_are_explicit(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        with pytest.raises(KeyError):
            async with ws.namespace("absent", create=False):
                pass
        assert await ws.list_namespaces() == []
    with pytest.raises(ValueError, match="local"):
        MemoryWorkspace(tmp_path, config=PRMEConfig(database_url="postgresql://localhost/unused"))
    with pytest.raises(ValueError, match="isolated local"):
        await MemoryEngine.create(PRMEConfig(database_url="postgresql://localhost/unused", namespace_id=uuid4()))


async def test_existing_registry_is_never_silently_reinitialized(tmp_path, config):
    with sqlite3.connect(tmp_path / "workspace.sqlite3"):
        pass
    with pytest.raises(sqlite3.OperationalError):
        async with workspace(tmp_path, config):
            pass


async def test_initialized_namespace_missing_database_is_not_recreated(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("important") as memory:
            info = memory.namespace
            await memory.store("durable source", user_id="owner")
    path = tmp_path / "packs" / str(info.id) / "memory.duckdb"
    path.unlink()
    async with workspace(tmp_path, config) as ws:
        with pytest.raises(WorkspaceError, match="database is missing"):
            async with ws.namespace("important"):
                pass
    assert not path.exists()


async def test_structured_ingestion_derivations_and_foreign_mutations_stay_in_project(tmp_path, config, monkeypatch):
    from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult

    async def extract(content, **kwargs):
        language = "Rust" if "Rust" in content else "Python"
        return ExtractionResult(entities=[ExtractedEntity(name="Alice", entity_type="person")],
            facts=[ExtractedFact(subject="Alice", predicate="uses", object=language, evidence_quote=content)])
    provider = type("Extractor", (), {"provider_name": "authored", "model_name": "fixed-test-extraction"})()
    provider.extract = AsyncMock(side_effect=extract)
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: provider)
    async with workspace(tmp_path, config) as ws:
        records = {}
        for name, language in [("a", "Rust"), ("b", "Python")]:
            async with ws.namespace(name) as memory:
                event = await memory.ingest(f"Alice uses {language}.", user_id="owner", scope=Scope.PROJECT,
                                            wait_for_extraction=True)
                nodes = await memory.get_event_nodes(event, user_id="owner")
                entity = next(n for n in nodes if n.node_type == NodeType.ENTITY)
                records[name] = (event, entity.id)
        assert records["a"][1] != records["b"][1]
        async with ws.namespace("b") as memory:
            foreign = str(records["a"][1])
            assert await memory.get_node(foreign, user_id="owner") is None
            assert await memory.get_extraction(records["a"][0], user_id="owner") is None
            with pytest.raises(ValueError):
                await memory.archive(foreign, user_id="owner")
        async with ws.namespace("a") as memory:
            assert await memory.get_node(str(records["a"][1]), user_id="owner") is not None


async def test_failed_indexing_recovers_after_eviction_without_touching_other_project(tmp_path, config):
    class Provider(MockEmbeddingProvider):
        broken = True
        async def embed(self, texts):
            if self.broken:
                raise RuntimeError("authored embedding outage")
            return await super().embed(texts)
    provider = Provider()
    async with MemoryWorkspace.open(tmp_path, config=config, embedding_provider=provider, max_open=1) as ws:
        async with ws.namespace("broken") as memory:
            event = await memory.store("recoverable source", user_id="owner")
            assert (await memory.processing_status(event, user_id="owner")).status == "pending"
        async with ws.namespace("other") as memory:
            foreign = await memory.ingest_fast("other pending source", user_id="owner")
            assert await memory.processing_status(event, user_id="owner") is None
        provider.broken = False
        async with ws.namespace("broken") as memory:
            assert (await memory.process_pending(user_id="owner", budget_ms=5000)).processed == 1
            assert (await memory.processing_status(event, user_id="owner")).status == "complete"
        async with ws.namespace("other") as memory:
            assert (await memory.processing_status(foreign, user_id="owner")).status == "pending"


async def test_encrypted_eviction_and_workspace_copy_keep_binding(tmp_path, config):
    from cryptography.fernet import Fernet
    from pydantic import SecretStr
    config.encryption_enabled = True
    config.encryption_key = SecretStr("raw_key:" + Fernet.generate_key().decode())
    root = tmp_path / "original"
    async with workspace(root, config) as ws:
        async with ws.namespace("encrypted") as memory:
            info = memory.namespace
            event = await memory.store("encrypted memory source", user_id="owner")
        async with ws.namespace("other"):
            directory = root / "packs" / str(info.id)
            assert (directory / "memory.duckdb.enc").exists()
            assert not (directory / "memory.duckdb").exists()
    shutil.copytree(root, tmp_path / "copy")
    async with workspace(tmp_path / "copy", config) as ws:
        async with ws.namespace("encrypted", create=False) as memory:
            assert memory.namespace == info
            assert (await memory.get_event(event, user_id="owner")).content == "encrypted memory source"


async def test_cancelled_eviction_finishes_native_close_before_capacity_is_reused(tmp_path, config, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("first"):
            pass
        original = next(iter(ws._entries.values())).engine.close
        async def delayed():
            started.set()
            await finish.wait()
            await original()
        monkeypatch.setattr(next(iter(ws._entries.values())).engine, "close", delayed)
        async def borrow():
            async with ws.namespace("second"):
                pytest.fail("Cancelled caller acquired a lease")
        task = asyncio.create_task(borrow())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        assert len(ws._entries) == 1 and not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not ws._entries
        async with ws.namespace("second") as memory:
            assert await memory.count_nodes() == 0


async def test_iterator_lifetime_is_tracked_and_iteration_after_release_rejected(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("stream") as memory:
            await memory.store("one", user_id="owner")
            iterator = memory.iter_nodes(user_id="owner")
            assert (await anext(iterator)).content == "one"
            assert memory._operations == 1
            await iterator.aclose()
            assert memory._operations == 0
            stale = memory.iter_nodes(user_id="owner")
        with pytest.raises(WorkspaceError, match="released"):
            await anext(stale)


async def test_process_exit_releases_lock_and_preserves_registered_source(tmp_path, config):
    script = '''
import asyncio, os, sys
from prme.workspace import MemoryWorkspace
from prme import PRMEConfig
class Provider:
    model_name = 'process-authored'
    model_version = '1'
    dimension = 384
    async def embed(self, texts):
        return [[1.] * 384 for _ in texts]
async def run():
    cfg = PRMEConfig(database_url=None, namespace_id=None, duckdb_threads=1,
                     organizer={'opportunistic_enabled': False})
    async with MemoryWorkspace.open(sys.argv[1], config=cfg, embedding_provider=Provider()) as ws:
        async with ws.namespace('project') as memory:
            await memory.ingest_fast('source before process exit', user_id='owner')
            os._exit(42)
asyncio.run(run())
'''
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, str(tmp_path)],
                                     capture_output=True, text=True, timeout=30)
    assert result.returncode == 42, result.stderr
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("project", create=False) as memory:
            events = await memory.get_events("owner")
            assert [e.content for e in events] == ["source before process exit"]
            response = await memory.retrieve("source before process exit", user_id="owner")
            assert response.results


async def test_live_process_cannot_open_owned_workspace(tmp_path, config):
    script = '''
import asyncio, sys
from prme import PRMEConfig
from prme.workspace import MemoryWorkspace, WorkspaceError
async def run():
    try:
        async with MemoryWorkspace.open(sys.argv[1], config=PRMEConfig(database_url=None, namespace_id=None)):
            raise SystemExit(1)
    except WorkspaceError:
        raise SystemExit(42)
asyncio.run(run())
'''
    async with workspace(tmp_path, config):
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, str(tmp_path)],
                                         capture_output=True, text=True, timeout=30)
        assert result.returncode == 42, result.stderr


async def test_idle_lru_eviction_keeps_recent_and_leased_engines(tmp_path, config):
    async with workspace(tmp_path, config, max_open=2) as ws:
        ids = {}
        for name in ["a", "b", "a", "c"]:
            async with ws.namespace(name) as memory:
                ids[name] = memory.namespace.id
        assert set(ws._entries) == {ids["a"], ids["c"]}
        async with ws.namespace("a"):
            async with ws.namespace("b"):
                assert set(ws._entries) == {ids["a"], ids["b"]}


async def test_startup_failure_keeps_fixed_identity_and_frees_capacity_for_retry(tmp_path, config, monkeypatch):
    from prme.storage import engine as module
    original = module.VectorIndex
    async with workspace(tmp_path, config) as ws:
        with monkeypatch.context() as failure:
            failure.setattr(module, "VectorIndex", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("authored startup failure")))
            with pytest.raises(RuntimeError, match="authored startup"):
                async with ws.namespace("retry"):
                    pass
        assert not ws._entries
        info = (await ws.list_namespaces())[0]
        assert module.VectorIndex is original
        async with ws.namespace("retry", create=False) as memory:
            assert memory.namespace == info
            await memory.store("retry works", user_id="owner")


async def test_lease_rejects_foreign_event_loop(tmp_path, config):
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("loop") as memory:
            async def read():
                with pytest.raises(WorkspaceError, match="owning event loop"):
                    await memory.count_nodes()
            await asyncio.to_thread(asyncio.run, read())


async def test_background_extraction_evicted_mid_call_remains_explicitly_recoverable(tmp_path, config, monkeypatch):
    from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
    started, resume = asyncio.Event(), asyncio.Event()
    class Extractor:
        provider_name = "authored"
        model_name = "paused-extraction"
        async def extract(self, content, **kwargs):
            started.set()
            await resume.wait()
            return ExtractionResult(entities=[ExtractedEntity(name="Alice", entity_type="person")],
                facts=[ExtractedFact(subject="Alice", predicate="uses", object="Rust", evidence_quote=content)])
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: Extractor())
    async with workspace(tmp_path, config) as ws:
        async with ws.namespace("first") as memory:
            event = await memory.ingest("Alice uses Rust.", user_id="owner")
            await asyncio.wait_for(started.wait(), 5)
        async with ws.namespace("other") as memory:
            assert await memory.extraction_status(event, user_id="owner") is None
        resume.set()
        async with ws.namespace("first") as memory:
            assert (await memory.extraction_status(event, user_id="owner")).status != "complete"
            result = await memory.process_extractions(user_id="owner", budget_ms=5000)
            assert result.processed == 1
            assert (await memory.extraction_status(event, user_id="owner")).status == "complete"
