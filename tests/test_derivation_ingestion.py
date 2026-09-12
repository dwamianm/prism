"""Public ingestion uses saved derivations across faults, restart and replay."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.errors import ExtractionError, MaterializationError
from prme.models import Event, MemoryNode
from prme.types import LifecycleState, NodeType
from tests import test_durable_ingestion
from tests.test_derivation_planning import extraction
from tests.test_extraction_updates import ingest_fact

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("boundary", ["plan", "vector", "lexical", "node", "edge", "commit"])
async def test_public_ingestion_restart_reuses_journaled_inputs(config, user, monkeypatch, boundary):
    if config.backend == "postgres" and boundary in ("vector", "lexical"):
        pytest.skip("PostgreSQL index writes are part of the graph transaction")
    source = "Alice switched from Python to Rust. Alice knows Bob."
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        pipeline = engine._pipeline
        pipeline._retry_delays = ()
        pipeline._extraction_provider.extract = AsyncMock(return_value=extraction(source))
        if boundary == "plan":
            target, method = engine._event_store, "record_derivation_plan"
        elif boundary in ("vector", "lexical"):
            target = engine._vector_index if boundary == "vector" else engine._lexical_index
            method = "stage"
        else:
            target = engine._graph_store
            method = ("commit_derivation" if boundary == "commit" else
                      f"_create_{boundary}_sync" if config.backend == "duckdb" else
                      f"_create_{boundary}_on_connection")
        original = getattr(target, method)
        if method.endswith("_sync"):
            def failed(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError("injected failure after write")
        else:
            async def failed(*args, **kwargs):
                await original(*args, **kwargs)
                raise OSError("injected failure after write")
        with monkeypatch.context() as fault:
            fault.setattr(target, method, failed)
            if boundary == "commit":
                # Atomic work completion resolves a lost acknowledgement.
                event_id = await engine.ingest(source, user_id=user, wait_for_extraction=True)
            else:
                with pytest.raises(ExtractionError) as failure:
                    await engine.ingest(source, user_id=user, wait_for_extraction=True)
                event_id = failure.value.event_id
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert plan is not None
        receipt = await engine._event_store.get_derivation_receipt(event_id, user_id=user)
        assert (receipt is not None) == (boundary == "commit")
        assert await engine._event_store.get_derivation_receipt(event_id, user_id=user + "-other") is None
        nodes = await engine.get_event_nodes(event_id, user_id=user)
        assert len(nodes) == (len(plan.nodes) if boundary == "commit" else 0)
        assert pipeline._extraction_provider.extract.await_count == 1
    async with MemoryEngine.open(config) as engine:
        for provider, method in ((engine._pipeline._extraction_provider, "extract"),
                                 (engine._vector_index._provider, "embed")):
            monkeypatch.setattr(provider, method, AsyncMock(side_effect=AssertionError("Recovery cannot infer")))
        event = await engine.get_event(event_id, user_id=user)
        await engine._pipeline._extract_and_materialize(event, event_id, raise_errors=True)
        actual = await engine._event_store.get_derivation_receipt(event_id, user_id=user)
        assert actual.plan_id == plan.id and actual.plan_checksum == plan.checksum
        if receipt is not None:
            assert actual == receipt
        assert {node.id for node in await engine.get_event_nodes(event_id, user_id=user)} == {node.id for node in plan.nodes}
        facts = await engine.query_nodes(user_id=user, node_type=NodeType.FACT,
                                         lifecycle_states=list(LifecycleState))
        assert {node.metadata["object"]: node.lifecycle_state for node in facts} == {
            "Python": LifecycleState.SUPERSEDED, "Rust": LifecycleState.TENTATIVE,
        }
        for node in plan.nodes:
            await engine.archive(str(node.id))
        if config.backend == "duckdb":
            monkeypatch.setattr(engine._vector_index, "stage", AsyncMock(side_effect=AssertionError("Completion skips staging")))
            monkeypatch.setattr(engine._lexical_index, "stage", AsyncMock(side_effect=AssertionError("Completion skips staging")))
        await engine._pipeline._extract_and_materialize(event, event_id, raise_errors=True)
        assert await engine._event_store.get_derivation_receipt(event_id, user_id=user) == actual
        retired = await engine.get_event_nodes(event_id, user_id=user)
        assert all(node.lifecycle_state == LifecycleState.ARCHIVED for node in retired)


async def test_concurrent_materialization_converges_on_one_saved_plan(config, user):
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Alice uses Rust", user_id=user, role="user")
        await engine._event_store.append(event)
        result = extraction(event.content, old=None)
        await asyncio.gather(*(engine._pipeline._materialize(result, event, str(event.id)) for _ in range(6)))
        plan = await engine._event_store.get_derivation_plan(str(event.id), user_id=user)
        receipt = await engine._event_store.get_derivation_receipt(str(event.id), user_id=user)
        assert receipt.plan_id == plan.id
        assert {node.id for node in await engine.get_event_nodes(str(event.id), user_id=user)} == {node.id for node in plan.nodes}
        if config.backend == "duckdb":
            assert engine._conn.execute("SELECT count(*) FROM vector_metadata WHERE user_id = ?", [user]).fetchone()[0] == len(plan.nodes)


async def test_legacy_partial_derivation_is_not_silently_duplicated(config, user):
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Alice uses Rust", user_id=user, role="user")
        await engine._event_store.append(event)
        existing = MemoryNode(content="Alice", user_id=user, node_type=NodeType.ENTITY,
                              evidence_refs=[event.id])
        await engine._graph_store.create_node(existing)
        with pytest.raises(MaterializationError) as failure:
            await engine._pipeline._materialize(extraction(event.content), event, str(event.id))
        assert "legacy" in str(failure.value.__cause__)
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) is None
        assert [node.id for node in await engine.get_event_nodes(str(event.id), user_id=user)] == [existing.id]


@pytest.mark.parametrize("boundary", ["plan", "vector", "lexical", "node", "commit"])
async def test_process_exit_during_public_ingestion_replays_same_plan(config, user, monkeypatch, boundary):
    if config.backend != "duckdb":
        pytest.skip("Local pack process-exit recovery; PostgreSQL has transaction fault coverage")
    script = """
import asyncio, hashlib, os, sys
from unittest.mock import AsyncMock
from prme import MemoryEngine, PRMEConfig
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
import prme.storage.engine as engine_module
class Provider:
    model_name = 'durability-test'
    model_version = '1'
    dimension = 384
    async def embed(self, texts):
        return [[hashlib.sha256(text.encode()).digest()[i % 32] / 255 for i in range(384)] for text in texts]
async def main():
    db, vector, lexical, user, boundary = sys.argv[1:]
    engine_module.create_embedding_provider = lambda _: Provider()
    config = PRMEConfig(db_path=db, vector_path=vector, lexical_path=lexical,
                        organizer={'opportunistic_enabled': False})
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._retry_delays = ()
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult(
            entities=[ExtractedEntity(name='Alice', entity_type='person')],
            facts=[ExtractedFact(subject='Alice', predicate='uses', object='Rust', evidence_quote='Alice uses Rust.')],
        ))
        target, method = {
            'plan': (engine._event_store, 'record_derivation_plan'),
            'vector': (engine._vector_index, 'stage'),
            'lexical': (engine._lexical_index, 'stage'),
            'node': (engine._graph_store, '_create_node_sync'),
            'commit': (engine._graph_store, 'commit_derivation'),
        }[boundary]
        original = getattr(target, method)
        if boundary == 'node':
            def interrupted(*args, **kwargs):
                original(*args, **kwargs)
                os._exit(42)
        else:
            async def interrupted(*args, **kwargs):
                await original(*args, **kwargs)
                os._exit(42)
        setattr(target, method, interrupted)
        await engine.ingest('Alice uses Rust.', user_id=user, wait_for_extraction=True)
asyncio.run(main())
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, config.db_path, config.vector_path,
                         config.lexical_path, user, boundary],
        capture_output=True, timeout=30, env=env,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    async with MemoryEngine.open(config) as engine:
        event = (await engine.get_events(user))[0]
        event_id = str(event.id)
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert plan is not None
        before = await engine.get_event_nodes(event_id, user_id=user)
        assert len(before) == (2 if boundary == "commit" else 0)
        if boundary != "commit":
            from datetime import datetime, timezone
            from tests.test_extraction_work import set_expiry
            # Restart cannot assume a live lease is abandoned; recovery claims
            # it after expiry, with a new fencing generation.
            assert (await engine.extraction_status(event_id, user_id=user)).status == "running"
            await set_expiry(engine, event_id, datetime(2000, 1, 1, tzinfo=timezone.utc))
        for provider, method in ((engine._pipeline._extraction_provider, "extract"),
                                 (engine._vector_index._provider, "embed")):
            monkeypatch.setattr(provider, method, AsyncMock(side_effect=AssertionError("Recovery cannot infer")))
        await engine._pipeline._extract_and_materialize(event, event_id, raise_errors=True)
        assert {node.id for node in await engine.get_event_nodes(event_id, user_id=user)} == {node.id for node in plan.nodes}
        receipt = await engine._event_store.get_derivation_receipt(event_id, user_id=user)
        assert receipt.plan_id == plan.id
