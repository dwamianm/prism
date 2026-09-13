"""Direct stores acknowledge durable vectors without rewriting every snapshot."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from prme import MemoryEngine
from tests.test_durable_ingestion import MockEmbeddingProvider
from tests import test_fresh_pack_paths

fresh_config = test_fresh_pack_paths.fresh_config


async def test_store_respects_configured_snapshot_interval(fresh_config, monkeypatch):
    config = fresh_config.model_copy(update={"vector_save_interval": 4})
    async with MemoryEngine.open(config) as engine:
        snapshot = Mock(wraps=engine._vector_index._save_snapshot)
        monkeypatch.setattr(engine._vector_index, "_save_snapshot", snapshot)
        events = [await engine.store(f"Cobalt telescope observation {i}", user_id="alice") for i in range(10)]
        assert snapshot.call_count == 2
        assert engine._vector_index._unsaved_inserts == 2
        for eid in events:
            assert (await engine.processing_status(eid, user_id="alice")).status == "complete"
        assert len(await engine._vector_index.search("Cobalt telescope", "alice", k=20)) == 10
        assert engine._conn.execute("SELECT count(*) FROM vector_payloads").fetchone()[0] == 10
    # Closing still explicitly checkpoints the derived snapshot.
    assert snapshot.call_count == 3


@pytest.mark.parametrize("prior_snapshot", [False, True])
async def test_acknowledged_stores_survive_exit_with_unsaved_snapshot(fresh_config, tmp_path, monkeypatch, prior_snapshot):
    config = fresh_config.model_copy(update={"vector_save_interval": 100})
    script = '''
import asyncio, hashlib, json, os, sys
from pathlib import Path
from prme import MemoryEngine, PRMEConfig, NodeType
import prme.storage.engine as module
class Provider:
    model_name = 'durability-test'
    model_version = '1'
    dimension = 384
    async def embed(self, texts):
        return [[hashlib.sha256(t.encode()).digest()[i % 32] / 255 for i in range(384)] for t in texts]
async def main():
    config = PRMEConfig.model_validate_json(sys.argv[1])
    module.create_embedding_provider = lambda _: Provider()
    engine = await MemoryEngine.create(config)
    accepted = []
    for i in range(3):
        eid = await engine.store(f'Cobalt telescope observation {i}', user_id='alice', node_type=NodeType.NOTE)
        assert (await engine.processing_status(eid, user_id='alice')).status == 'complete'
        accepted.append(eid)
        if i == 0 and sys.argv[3] == 'yes':
            await engine._vector_index.save()
    assert engine._vector_index._unsaved_inserts == (2 if sys.argv[3] == 'yes' else 3)
    Path(sys.argv[2]).write_text(json.dumps(accepted))
    os._exit(42)
asyncio.run(main())
'''
    accepted_path = tmp_path / "accepted.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, config.model_dump_json(), str(accepted_path),
                         "yes" if prior_snapshot else "no"], capture_output=True, env=env, timeout=30,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    accepted = json.loads(accepted_path.read_text())

    class OfflineProvider(MockEmbeddingProvider):
        async def embed(self, texts):
            raise AssertionError("Opening an acknowledged pack must not regenerate embeddings")

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: OfflineProvider())
    async with MemoryEngine.open(config) as engine:
        node_ids = set()
        for eid in accepted:
            assert (await engine.processing_status(eid, user_id="alice")).status == "complete"
            node_ids.add(str((await engine.get_event_nodes(eid, user_id="alice"))[0].id))
        assert (await engine.process_pending(user_id="alice")).processed == 0
        query_vector = (await MockEmbeddingProvider().embed(["Cobalt telescope"]))[0]
        hits = await engine._vector_index.search_by_vector(query_vector, "alice", k=10)
        assert {h["node_id"] for h in hits} == node_ids
        assert await engine._vector_index.search_by_vector(query_vector, "bob", k=10) == []
        assert {h["node_id"] for h in await engine._lexical_index.search("Cobalt telescope", "alice", limit=10)} == node_ids
        assert engine._conn.execute("SELECT count(*) FROM vector_payloads").fetchone()[0] == 3


async def test_write_work_diagnostic_checks_acknowledgments_and_restores_provider():
    from benchmarks.diagnostics.store_snapshots import run
    from prme.storage import engine as engine_module

    original = engine_module.create_embedding_provider
    report = await run(5, 2)
    assert report["passed"]
    assert report["store_snapshot_count"] == 2 and report["close_snapshot_count"] == 1
    assert report["payload_count"] == 5 and report["unsaved_before_close"] == 1
    assert report["store_snapshot_bytes"] == sum(row["bytes"] for row in report["snapshots"][:2])
    assert engine_module.create_embedding_provider is original
