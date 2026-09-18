"""Batched lexical commits preserve durable per-source recovery boundaries."""
import asyncio
from datetime import datetime, timezone
import shutil
import subprocess
import sys
import threading
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.storage.lexical_index import LexicalIndex
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("boundary", ["second_add", "before_commit", "after_commit"])
async def test_atomic_replacement_batch_preserves_all_or_none(tmp_path, monkeypatch, boundary):
    index = LexicalIndex(str(tmp_path))
    try:
        await index.replace_many((("a", "Old telescope", "alice", "fact", "personal"),
                                  ("b", "Old telescope", "alice", "fact", "project")))
        original = index._ensure_writer
        class Fault:
            def __init__(self, writer):
                self.writer, self.adds = writer, 0
            def __getattr__(self, name):
                return getattr(self.writer, name)
            def add_document(self, document):
                self.adds += 1
                if boundary == "second_add" and self.adds == 2:
                    raise OSError("authored second-add failure")
                return self.writer.add_document(document)
            def commit(self):
                if boundary == "before_commit":
                    raise OSError("authored commit failure")
                self.writer.commit()
                if boundary == "after_commit":
                    raise OSError("authored lost acknowledgement")
        replacement = (("a", "New microscope", "alice", "note", "personal"),
                       ("b", "New microscope", "alice", "note", "project"))
        with monkeypatch.context() as fault:
            fault.setattr(index, "_ensure_writer", lambda: Fault(original()))
            with pytest.raises(OSError):
                await index.replace_many(replacement)
        query = "microscope" if boundary == "after_commit" else "telescope"
        assert {row['node_id'] for row in await index.search(query, 'alice')} == {'a', 'b'}
        assert await index.search('telescope' if query == 'microscope' else 'microscope', 'alice') == []
        await index.replace_many(replacement)
        await index.replace_many(replacement)
        assert [row['node_id'] for row in await index.search('microscope', 'alice', scope=['project'], node_type='note')] == ['b']
        assert await index.search('microscope', 'bob') == []
        assert len(await index.search('microscope', 'alice')) == 2
    finally:
        await index.close()
    reopened = LexicalIndex(str(tmp_path))
    try:
        assert len(await reopened.search('microscope', 'alice')) == 2
    finally:
        await reopened.close()


async def test_batch_rejects_duplicate_identity_before_touching_old_document(tmp_path):
    index = LexicalIndex(str(tmp_path))
    try:
        await index.index('a', 'Old telescope', 'alice', replace=True)
        with pytest.raises(ValueError, match='duplicate'):
            await index.replace_many((('a', 'New microscope', 'alice', 'note', None),
                                      ('a', 'Another microscope', 'bob', 'note', None)))
        assert len(await index.search('telescope', 'alice')) == 1
        assert await index.search('microscope', 'alice') == []
        assert await index.search('microscope', 'bob') == []
    finally:
        await index.close()


async def test_batch_cancellation_keeps_native_lock_until_commit_finishes(tmp_path, monkeypatch):
    index = LexicalIndex(str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    original = index._ensure_writer
    class BlockingWriter:
        def __init__(self, writer):
            self.writer = writer
        def __getattr__(self, name):
            return getattr(self.writer, name)
        def commit(self):
            entered.set()
            assert release.wait(5)
            return self.writer.commit()
    task = None
    try:
        monkeypatch.setattr(index, '_ensure_writer', lambda: BlockingWriter(original()))
        task = asyncio.create_task(index.replace_many((('a', 'Telescope', 'alice', 'note', None),)))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert index._write_lock.locked() and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not index._write_lock.locked()
        assert len(await index.search('telescope', 'alice')) == 1
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        await index.close()


async def test_pending_sources_use_one_commit_before_any_acknowledgement(config, user, monkeypatch):
    if config.backend != 'duckdb':
        pytest.skip('Native Tantivy batch contract')
    async with MemoryEngine.open(config) as engine:
        ids = [await engine.ingest_fast(f'Telescope calibration note {i}', user_id=user) for i in range(4)]
        foreign = await engine.ingest_fast('Foreign telescope', user_id=user+'-other')
        batch = engine._lexical_index.replace_many
        completed_batch = False
        calls = 0
        async def observed_batch(documents):
            nonlocal completed_batch, calls
            calls += 1
            assert {str(node.id) for node in await engine.query_nodes(user_id=user)} == set(ids)
            for eid in ids:
                assert (await engine.processing_status(eid, user_id=user)).status == 'pending'
            await batch(documents)
            completed_batch = True
        acknowledge = engine._event_store.finish_materialization
        async def observed_ack(*args, **kwargs):
            assert completed_batch
            await acknowledge(*args, **kwargs)
        monkeypatch.setattr(engine._lexical_index, 'replace_many', observed_batch)
        monkeypatch.setattr(engine._event_store, 'finish_materialization', observed_ack)
        result = await engine.process_pending(user_id=user, budget_ms=5000)
        assert (result.processed, result.pending, result.failed) == (4, 0, 0)
        assert calls == 1
        assert {row['node_id'] for row in await engine._lexical_index.search('telescope', user)} == set(ids)
        assert (await engine.processing_status(foreign, user_id=user+'-other')).status == 'pending'


async def test_batch_and_serial_recovery_return_identical_product_contexts(config, user, tmp_path):
    if config.backend != 'duckdb':
        pytest.skip('Local artifact replay')
    from pathlib import Path
    from prme.types import Scope

    async with MemoryEngine.open(config) as engine:
        for i in range(8):
            await engine.ingest_fast(f'Telescope {i} calibration uses a cobalt filter.', user_id=user,
                                     scope=Scope.PROJECT if i % 2 else Scope.PERSONAL,
                                     role='assistant' if i % 3 else 'user',
                                     session_id=f'episode-{i // 2}')
        await engine.ingest_fast('A cobalt telescope owned by someone else', user_id=user+'-other')
    original = Path(config.db_path).parent
    copies = []
    for name in ('serial', 'batch'):
        destination = tmp_path.parent / (tmp_path.name + '-' + name)
        shutil.copytree(original, destination)
        copies.append(config.model_copy(update={'db_path':str(destination / 'memory.duckdb'),
                      'lexical_path':str(destination / 'lexical'),
                      'vector_path':str(destination / 'vectors.usearch')}))
    clock = datetime.now(timezone.utc)
    outcomes = []
    for number, current in enumerate(copies):
        async with MemoryEngine.open(current) as engine:
            if number:
                assert (await engine.process_pending(user_id=user, budget_ms=5000)).processed == 8
            else:
                for event in await engine._event_store.pending_materializations(user_id=user):
                    await engine._materialization_queue.process_one(engine, event)
            response = await engine.retrieve('Which telescope uses a cobalt filter?', user_id=user,
                                             scope=Scope.PROJECT, include_cross_scope=False,
                                             reference_time=clock, token_budget=2048)
            assert response.results
            outcomes.append(([candidate.model_dump(mode='json') for candidate in response.results], response.bundle.render()))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize('failure', ['vector', 'lexical'])
async def test_failed_item_does_not_block_healthy_batch_sources(config, user, monkeypatch, failure):
    async with MemoryEngine.open(config) as engine:
        ids = [await engine.ingest_fast(content, user_id=user) for content in
               ['Bad telescope', 'Healthy telescope', 'Another healthy telescope']]
        index = getattr(engine, f'_{failure}_index')
        original = index.index
        async def selective(node_id, content, *args, **kwargs):
            if content.startswith('Bad'):
                raise OSError('authored item failure')
            await original(node_id, content, *args, **kwargs)
        with monkeypatch.context() as fault:
            fault.setattr(index, 'index', selective)
            if failure == 'lexical' and hasattr(index, 'replace_many'):
                fault.setattr(index, 'replace_many', AsyncMock(side_effect=OSError('authored batch failure')))
            result = await engine.process_pending(user_id=user, budget_ms=5000)
        assert (result.processed, result.pending, result.failed) == (2, 1, 1)
        assert (await engine.processing_status(ids[0], user_id=user)).last_error == 'OSError'
        assert {row['node_id'] for row in await engine._lexical_index.search('healthy', user)} == set(ids[1:])
        assert (await engine.process_pending(user_id=user, budget_ms=5000)).processed == 1
        assert {row['node_id'] for row in await engine._lexical_index.search('telescope', user)} == set(ids)


@pytest.mark.parametrize('boundary', ['before_commit', 'after_commit'])
async def test_process_exit_during_batch_replays_every_source(config, user, boundary):
    if config.backend != 'duckdb':
        pytest.skip('Native process exit recovery')
    script = '''
import asyncio, json, os, sys
from unittest.mock import patch
from prme import MemoryEngine, PRMEConfig
from tests.test_durable_ingestion import MockEmbeddingProvider
async def main():
    with patch('prme.storage.engine.create_embedding_provider', lambda _: MockEmbeddingProvider()):
        engine = await MemoryEngine.create(PRMEConfig.model_validate_json(sys.argv[1]))
        for i in range(4):
            await engine.ingest_fast(f'Authored telescope {i}', user_id=sys.argv[2])
        original = engine._lexical_index._ensure_writer
        class ExitWriter:
            def __init__(self, writer): self.writer = writer
            def __getattr__(self, name): return getattr(self.writer, name)
            def commit(self):
                if sys.argv[3] == 'before_commit': os._exit(29)
                self.writer.commit()
                os._exit(29)
        engine._lexical_index._ensure_writer = lambda: ExitWriter(original())
        await engine.process_pending(user_id=sys.argv[2], budget_ms=5000)
asyncio.run(main())
'''
    child = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script, config.model_dump_json(), user, boundary],
                                    capture_output=True, timeout=30)
    assert child.returncode == 29
    async with MemoryEngine.open(config) as engine:
        events = await engine.get_events(user)
        assert len(events) == 4
        for event in events:
            assert (await engine.processing_status(str(event.id), user_id=user)).status == 'pending'
        result = await engine.process_pending(user_id=user, budget_ms=5000)
        assert (result.processed, result.pending, result.failed) == (4, 0, 0)
        ids = {str(event.id) for event in events}
        assert {row['node_id'] for row in await engine._lexical_index.search('telescope', user)} == ids
        assert {row['node_id'] for row in await engine._vector_index.search('telescope', user)} == ids
        assert (await engine.process_pending(user_id=user)).processed == 0


@pytest.mark.parametrize('boundary', ['vector', 'lexical_commit'])
async def test_cancelled_public_drain_leaves_sources_retryable(config, user, monkeypatch, boundary):
    if config.backend != 'duckdb':
        pytest.skip('Local batched drain cancellation')
    async with MemoryEngine.open(config) as engine:
        ids = [await engine.ingest_fast(f'Telescope source {i}', user_id=user) for i in range(4)]
        entered, release = asyncio.Event(), asyncio.Event()
        target, name = ((engine._vector_index, 'index') if boundary == 'vector'
                        else (engine._lexical_index, 'replace_many'))
        original = getattr(target, name)
        async def paused(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()
        monkeypatch.setattr(target, name, paused)
        task = asyncio.create_task(engine.process_pending(user_id=user, budget_ms=5000))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for event_id in ids:
                assert (await engine.processing_status(event_id, user_id=user)).status == 'pending'
        finally:
            release.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
        # Wait for the cancelled caller's already accepted native write to finish.
        await engine._write_queue.submit(AsyncMock())
        monkeypatch.setattr(target, name, original)
        result = await engine.process_pending(user_id=user, budget_ms=5000)
        assert (result.processed, result.pending, result.failed) == (4, 0, 0)
        assert {row['node_id'] for row in await engine._lexical_index.search('telescope', user)} == set(ids)
        assert {row['node_id'] for row in await engine._vector_index.search('telescope', user)} == set(ids)
