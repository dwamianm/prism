"""Directly supplied memory types survive indexing outages and process restarts."""

from datetime import datetime, timezone
import math
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine, NodeType
from prme.models import Event, MemoryNode
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_source_typed_snapshot_and_work_are_atomic_and_scoped(config, user):
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Use UTC timestamps", user_id=user, role="user")
        node = MemoryNode(content=event.content, user_id=user, node_type=NodeType.INSTRUCTION,
                          evidence_refs=[event.id], confidence=.87, ttl_days=43, metadata={"zero": -0.0})
        await engine._event_store.append(event, store_node=node)
        saved = await engine._event_store.get_direct_store(str(event.id), user_id=user)
        assert saved.node == node
        assert math.copysign(1, saved.node.metadata["zero"]) == -1
        assert (await engine.processing_status(str(event.id), user_id=user)).status == "pending"
        assert await engine._event_store.get_direct_store(str(event.id), user_id=user + "-other") is None
        bad = Event(content="Another source", user_id=user, role="user")
        with pytest.raises(ValueError, match="source"):
            await engine._event_store.append(bad, store_node=node)
        assert await engine.get_event(str(bad.id), user_id=user) is None
        assert await engine.processing_status(str(bad.id), user_id=user) is None
    async with MemoryEngine.open(config) as engine:
        assert await engine._event_store.get_direct_store(str(event.id), user_id=user) == saved


@pytest.mark.parametrize("failed", ["vector", "lexical"])
async def test_store_retains_typed_memory_and_repairs_index_after_restart(config, user, monkeypatch, failed):
    source = "Always store timestamps in UTC"
    event_time = datetime(2025, 1, 2, tzinfo=timezone.utc)
    async with MemoryEngine.open(config) as engine:
        with monkeypatch.context() as outage:
            outage.setattr(getattr(engine, f"_{failed}_index"), "index", AsyncMock(side_effect=OSError("offline")))
            eid = await engine.store(source, user_id=user, node_type=NodeType.INSTRUCTION,
                                     confidence=.87, ttl_days=43, event_time=event_time)
        nodes = await engine.get_event_nodes(eid, user_id=user)
        assert len(nodes) == 1
        original = nodes[0]
        assert original.node_type == NodeType.INSTRUCTION
        assert (await engine.processing_status(eid, user_id=user)).status == "pending"
    async with MemoryEngine.open(config) as engine:
        assert (await engine.process_pending(user_id=user + "-other")).processed == 0
        assert (await engine.process_pending(user_id=user)).processed == 1
        assert (await engine.processing_status(eid, user_id=user)).status == "complete"
        nodes = await engine.get_event_nodes(eid, user_id=user)
        assert len(nodes) == 1 and nodes[0].id == original.id
        assert nodes[0].node_type == NodeType.INSTRUCTION
        assert nodes[0].confidence == original.confidence == pytest.approx(.87)
        assert (nodes[0].ttl_days, nodes[0].event_time) == (43, event_time)
        assert nodes[0].created_at == original.created_at
        assert [h["node_id"] for h in await engine._lexical_index.search("timestamps UTC", user)] == [str(original.id)]
        assert [h["node_id"] for h in await engine._vector_index.search(source, user)] == [str(original.id)]


async def test_graph_failure_keeps_complete_typed_request_for_retry(config, user, monkeypatch):
    from prme import MaterializationError
    async with MemoryEngine.open(config) as engine:
        with monkeypatch.context() as fault:
            fault.setattr(engine._graph_store, "create_node", AsyncMock(side_effect=OSError("private failure")))
            with pytest.raises(MaterializationError) as failure:
                await engine.store("Retain this decision", user_id=user, node_type=NodeType.DECISION, ttl_days=None)
        eid = failure.value.event_id
        assert failure.value.reason_code == "OSError" and "private" not in str(failure.value)
        assert await engine.get_event_nodes(eid, user_id=user) == []
        recorded = await engine._event_store.get_direct_store(eid, user_id=user)
        assert recorded.node.node_type == NodeType.DECISION and recorded.node.ttl_days is None
    async with MemoryEngine.open(config) as engine:
        assert (await engine.process_pending(user_id=user)).processed == 1
        node = (await engine.get_event_nodes(eid, user_id=user))[0]
        assert node.id == recorded.node.id and node.node_type == NodeType.DECISION
        assert node.created_at == recorded.node.created_at


async def test_pending_repair_does_not_reactivate_archived_node(config, user, monkeypatch):
    from prme.types import LifecycleState
    async with MemoryEngine.open(config) as engine:
        with monkeypatch.context() as fault:
            fault.setattr(engine._vector_index, "index", AsyncMock(side_effect=OSError("offline")))
            eid = await engine.store("An expired instruction", user_id=user, node_type=NodeType.INSTRUCTION)
        node = (await engine.get_event_nodes(eid, user_id=user))[0]
        await engine.archive(str(node.id), user_id=user)
    async with MemoryEngine.open(config) as engine:
        monkeypatch.setattr(engine._vector_index, "index", AsyncMock(side_effect=AssertionError("No resurrection")))
        assert (await engine.process_pending(user_id=user)).processed == 1
        assert (await engine.get_event_nodes(eid, user_id=user))[0].lifecycle_state == LifecycleState.ARCHIVED


async def test_atomic_direct_store_request_rolls_back_on_journal_conflict(config, user):
    from prme.models.direct_store import direct_store_operation_id
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Cannot partially admit", user_id=user, role="user")
        node = MemoryNode(content=event.content, user_id=user, node_type=NodeType.FACT, evidence_refs=[event.id])
        args = [direct_store_operation_id(event.id), str(event.id)]
        if engine._conn is not None:
            engine._conn.execute("INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id) VALUES (?, 'COLLISION', ?, '{}', 'test', 'personal')", args)
        else:
            async with engine._pool.acquire() as conn:
                await conn.execute("INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id) VALUES ($1, 'COLLISION', $2, '{}'::jsonb, 'test', 'personal')", *args)
        with pytest.raises(Exception):
            await engine._event_store.append(event, store_node=node)
        assert await engine.get_event(str(event.id), user_id=user) is None
        assert await engine.processing_status(str(event.id), user_id=user) is None


@pytest.mark.parametrize("initially_failed", ["vector", "lexical"])
async def test_retry_outage_does_not_erase_previously_healthy_index(config, user, monkeypatch, initially_failed):
    source = "Cobalt telescopes observe distant galaxies"
    second_failure = "lexical" if initially_failed == "vector" else "vector"
    async with MemoryEngine.open(config) as engine:
        with monkeypatch.context() as fault:
            fault.setattr(getattr(engine, f"_{initially_failed}_index"), "index", AsyncMock(side_effect=OSError("first outage")))
            eid = await engine.store(source, user_id=user)
        node = (await engine.get_event_nodes(eid, user_id=user))[0]
        with monkeypatch.context() as fault:
            fault.setattr(getattr(engine, f"_{second_failure}_index"), "index", AsyncMock(side_effect=OSError("second outage")))
            if second_failure == "lexical" and hasattr(engine._lexical_index, "replace_many"):
                fault.setattr(engine._lexical_index, "replace_many", AsyncMock(side_effect=OSError("second outage")))
            assert (await engine.process_pending(user_id=user)).pending == 1
            hits = (await engine._lexical_index.search("cobalt telescopes", user) if second_failure == "lexical"
                    else await engine._vector_index.search(source, user))
            assert [h["node_id"] for h in hits] == [str(node.id)]
        assert (await engine.process_pending(user_id=user)).processed == 1
        assert [h["node_id"] for h in await engine._lexical_index.search("cobalt telescopes", user)] == [str(node.id)]
        if engine._conn is not None:
            assert engine._conn.execute("SELECT count(*) FROM vector_metadata WHERE node_id = ?", [str(node.id)]).fetchone()[0] == 1


async def test_concurrent_vector_replacements_keep_one_searchable_generation(config, user):
    import asyncio
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store("The cobalt telescope", user_id=user)
        node = (await engine.get_event_nodes(eid, user_id=user))[0]
        await asyncio.gather(*(engine._vector_index.index(str(node.id), node.content, user, replace=True) for _ in range(5)))
        hits = await engine._vector_index.search(node.content, user)
        assert [h["node_id"] for h in hits] == [str(node.id)]
        if engine._conn is not None:
            assert engine._conn.execute("SELECT count(*) FROM vector_metadata WHERE node_id = ?", [str(node.id)]).fetchone()[0] == 1


@pytest.mark.parametrize("boundary", ["source", "node", "lexical", "vector", "complete"])
async def test_abrupt_process_exit_recovers_original_typed_request(config, user, monkeypatch, boundary):
    import asyncio
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = '''
import asyncio, hashlib, os, sys
from datetime import datetime, timezone
from prme import MemoryEngine, PRMEConfig, NodeType, Scope
import prme.storage.engine as engine_module
class Provider:
    model_name = 'durability-test'
    model_version = '1'
    dimension = 384
    async def embed(self, texts):
        return [[hashlib.sha256(text.encode()).digest()[i % 32] / 255 for i in range(384)] for text in texts]
async def main():
    config = PRMEConfig.model_validate_json(sys.argv[1])
    if config.database_url is not None:
        from pydantic import SecretStr
        config.database_url = SecretStr(os.environ['PRME_TEST_DATABASE_URL'])
    user, boundary = sys.argv[2:]
    engine_module.create_embedding_provider = lambda _: Provider()
    async with MemoryEngine.open(config) as engine:
        target, method = {
            'source': (engine._event_store, 'append'),
            'node': (engine._graph_store, 'create_node'),
            'lexical': (engine._lexical_index, 'flush' if config.backend == 'duckdb' else 'index'),
            'vector': (engine._vector_index, 'index'),
            'complete': (engine._event_store, 'finish_materialization'),
        }[boundary]
        original = getattr(target, method)
        async def interrupted(*args, **kwargs):
            await original(*args, **kwargs)
            os._exit(42)
        setattr(target, method, interrupted)
        await engine.store('Use cobalt telescopes for observation', user_id=user,
                           node_type=NodeType.INSTRUCTION, scope=Scope.PROJECT,
                           session_id='observation', confidence=.87, ttl_days=43,
                           valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
                           valid_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
                           metadata={'source': 'operator'})
asyncio.run(main())
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, config.model_dump_json(), user, boundary],
        capture_output=True, timeout=30, env=env,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    async with MemoryEngine.open(config) as engine:
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract",
                            AsyncMock(side_effect=AssertionError("Direct storage never calls the LLM")))
        event = (await engine.get_events(user))[0]
        eid = str(event.id)
        request = await engine._event_store.get_direct_store(eid, user_id=user)
        before = await engine.get_event_nodes(eid, user_id=user)
        assert len(before) == (0 if boundary == "source" else 1)
        status = await engine.processing_status(eid, user_id=user)
        assert status.status == ("complete" if boundary == "complete" else "pending")
        assert (await engine.process_pending(user_id=user)).processed == (0 if boundary == "complete" else 1)
        node = (await engine.get_event_nodes(eid, user_id=user))[0]
        for field in ("id", "node_type", "scope", "session_id", "metadata", "ttl_days",
                      "created_at", "valid_from", "valid_to", "event_time", "evidence_refs",
                      "epistemic_type", "source_type"):
            assert getattr(node, field) == getattr(request.node, field), field
        assert node.confidence == pytest.approx(request.node.confidence)
        assert (await engine.processing_status(eid, user_id=user)).status == "complete"
        assert [h["node_id"] for h in await engine._lexical_index.search("cobalt telescopes", user)] == [str(node.id)]
        assert [h["node_id"] for h in await engine._vector_index.search(node.content, user)] == [str(node.id)]
        assert (await engine.process_pending(user_id=user)).processed == 0


@pytest.mark.parametrize("failure", ["add_document", "commit"])
async def test_native_lexical_replacement_failure_preserves_committed_document(config, user, monkeypatch, failure):
    if config.backend != "duckdb":
        pytest.skip("Tantivy-specific transaction fault")
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store("Cobalt telescopes", user_id=user)
        node = (await engine.get_event_nodes(eid, user_id=user))[0]
        index = engine._lexical_index
        original = index._ensure_writer
        class FailingWriter:
            def __init__(self, writer):
                self.writer = writer
            def __getattr__(self, name):
                if name == failure:
                    def fail(*args, **kwargs):
                        raise OSError("native write unavailable")
                    return fail
                return getattr(self.writer, name)
        with monkeypatch.context() as fault:
            fault.setattr(index, "_ensure_writer", lambda: FailingWriter(original()))
            with pytest.raises(OSError, match="native write"):
                await index.index(str(node.id), "Silver microscopes", user, replace=True)
        assert [h["node_id"] for h in await index.search("cobalt telescopes", user)] == [str(node.id)]
        assert await index.search("silver microscopes", user) == []
        await index.index(str(node.id), "Silver microscopes", user, replace=True)
        assert await index.search("cobalt telescopes", user) == []
        assert [h["node_id"] for h in await index.search("silver microscopes", user)] == [str(node.id)]


async def test_native_vector_replacement_failure_preserves_previous_generation(config, user, monkeypatch):
    if config.backend != "duckdb":
        pytest.skip("USearch-specific write fault")
    from unittest.mock import Mock
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store("Cobalt telescopes", user_id=user)
        node = (await engine.get_event_nodes(eid, user_id=user))[0]
        index = engine._vector_index
        with monkeypatch.context() as fault:
            fault.setattr(index, "_add_vector", Mock(side_effect=OSError("native write unavailable")))
            with pytest.raises(OSError, match="native write"):
                await index.index(str(node.id), node.content, user, replace=True)
        assert [h["node_id"] for h in await index.search(node.content, user)] == [str(node.id)]
        await index.index(str(node.id), node.content, user, replace=True)
        assert engine._conn.execute("SELECT count(*) FROM vector_metadata WHERE node_id = ?", [str(node.id)]).fetchone()[0] == 1
        assert [h["node_id"] for h in await index.search(node.content, user)] == [str(node.id)]


async def test_corrupt_direct_store_record_stays_pending_without_creating_raw_note(config, user):
    import json
    from prme.models.direct_store import direct_store_operation_id
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Keep this instruction typed", user_id=user, role="user")
        node = MemoryNode(content=event.content, user_id=user, node_type=NodeType.INSTRUCTION, evidence_refs=[event.id])
        await engine._event_store.append(event, store_node=node)
        record = await engine._event_store.get_direct_store(str(event.id), user_id=user)
        payload = json.loads(record.operation_payload())
        payload["record"] = payload["record"].replace("Keep this instruction typed", "Untrusted mutation")
        args = [json.dumps(payload), direct_store_operation_id(event.id)]
        # Simulate storage corruption; normal APIs never mutate the journal.
        if engine._conn is not None:
            engine._conn.execute("UPDATE operations SET payload = ? WHERE id = ?", args)
        else:
            async with engine._pool.acquire() as conn:
                await conn.execute("UPDATE operations SET payload = $1::jsonb WHERE id = $2", *args)
        with pytest.raises(ValueError, match="checksum"):
            await engine._event_store.get_direct_store(str(event.id), user_id=user)
        result = await engine.process_pending(user_id=user)
        assert (result.processed, result.pending, result.failed) == (0, 1, 1)
        assert await engine.get_event_nodes(str(event.id), user_id=user) == []
        assert (await engine.processing_status(str(event.id), user_id=user)).last_error == "ValueError"
