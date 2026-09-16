"""Invalid metadata must not diverge between a source and its durable node."""

import pytest

from prme import MemoryEngine
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("method", ["store", "ingest_fast"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_nonfinite_metadata_rejected_before_event_admission(
    config, user, method, value
):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(
            ValueError, match="Metadata must contain finite JSON-serializable values"
        ):
            await getattr(engine, method)(
                "A measurement", user_id=user, metadata={"nested": [{"value": value}]}
            )
        assert await engine.get_events(user_id=user) == []
        assert await engine.count_nodes(user_id=user) == 0


@pytest.mark.parametrize("method", ["store", "ingest_fast"])
async def test_finite_metadata_survives_source_node_and_restart(config, user, method):
    metadata = {
        "nested": [{"value": 1.25, "missing": None, "label": "NaN"}],
        "enabled": True,
    }
    async with MemoryEngine.open(config) as engine:
        source = await getattr(engine, method)(
            "A measurement", user_id=user, metadata=metadata
        )
        await engine.process_pending(user_id=user, budget_ms=5000)
    async with MemoryEngine.open(config) as engine:
        assert (await engine.get_event(source, user_id=user)).metadata == metadata
        assert (await engine.get_event_nodes(source, user_id=user))[
            0
        ].metadata == metadata


async def test_direct_node_metadata_and_deferred_extraction_are_checked_before_admission(
    config, user
):
    from prme.models.events import Event
    from prme.models.nodes import MemoryNode
    from prme.types import NodeType

    async with MemoryEngine.open(config) as engine:
        event = Event(user_id=user, role="user", content="A measurement")
        node = MemoryNode(
            user_id=user,
            node_type=NodeType.NOTE,
            content=event.content,
            evidence_refs=[event.id],
            metadata={"value": float("nan")},
        )
        with pytest.raises(ValueError, match="Metadata must contain finite"):
            await engine._event_store.append(event, store_node=node)
        assert await engine.get_event(str(event.id), user_id=user) is None
        assert await engine.processing_status(str(event.id), user_id=user) is None
        invalid = event.model_copy(update={"metadata": {"value": float("inf")}})
        with pytest.raises(ValueError, match="Metadata must contain finite"):
            await engine._event_store.append(invalid, defer_extraction=True)
        assert await engine.extraction_status(str(event.id), user_id=user) is None


async def test_legacy_raw_metadata_can_be_reinforced_and_archived_without_loss(
    config, user
):
    import math
    from uuid import uuid4
    from prme.models.events import Event
    from prme.storage import _snapshot_json
    from tests.test_atomic_lifecycle import journal_records
    from tests.test_reinforcement_atomic import records

    if config.backend != "duckdb":
        pytest.skip("PostgreSQL JSONB never admitted nonfinite numeric metadata")
    async with MemoryEngine.open(config) as engine:
        event = Event(
            user_id=user,
            role="user",
            content="An old measurement",
            metadata={"values": [float("nan"), float("inf"), float("-inf"), None]},
        )
        # Reproduce the previous durable admission path, bypassing only the new
        # public guard. New callers must not create this legacy state.
        async with engine._event_store._conn_lock:
            engine._event_store._append_with_work_sync(event, True)
        await engine.process_pending(user_id=user, budget_ms=5000)
        node = (await engine.get_event_nodes(str(event.id), user_id=user))[0]
        request_id = uuid4()
        await engine.reinforce(str(node.id), user_id=user, request_id=request_id)
        await engine.reinforce(str(node.id), user_id=user, request_id=request_id)
        confirmations = await records(engine, str(node.id))
        assert len(confirmations) == 1
        assert math.isnan(confirmations[0].before.metadata["values"][0])
        assert math.isnan(confirmations[0].after.metadata["values"][0])
        await engine.archive(str(node.id), user_id=user)
        transitions = await journal_records(engine, str(node.id))
        assert len(transitions) == 1
        assert _snapshot_json.dumps(
            transitions[0].before.metadata
        ) == _snapshot_json.dumps(event.metadata)
        assert _snapshot_json.dumps(
            transitions[0].after.metadata
        ) == _snapshot_json.dumps(event.metadata)
    async with MemoryEngine.open(config) as engine:
        assert math.isnan(
            (await engine.get_event(str(event.id), user_id=user)).metadata["values"][0]
        )
        after = await engine.get_node(
            str(node.id), user_id=user, include_superseded=True
        )
        assert _snapshot_json.dumps(after.metadata) == _snapshot_json.dumps(
            event.metadata
        )
        assert len(await journal_records(engine, str(node.id))) == 1


async def test_caller_mutation_after_validation_cannot_change_admitted_metadata(
    config, user, monkeypatch
):
    import asyncio
    from contextlib import asynccontextmanager
    from prme.models.events import Event

    reached, release = asyncio.Event(), asyncio.Event()
    async with MemoryEngine.open(config) as engine:
        store = engine._event_store
        event = Event(
            user_id=user,
            role="user",
            content="A measurement",
            metadata={"values": [1.0]},
        )
        if config.backend == "duckdb":
            original = store._conn_lock

            class Gate:
                async def __aenter__(self):
                    reached.set()
                    await release.wait()
                    return await original.__aenter__()

                async def __aexit__(self, *args):
                    return await original.__aexit__(*args)

            attribute = "_conn_lock"
        else:
            original = store._pool

            class Gate:
                @asynccontextmanager
                async def acquire(self):
                    reached.set()
                    await release.wait()
                    async with original.acquire() as connection:
                        yield connection

            attribute = "_pool"
        with monkeypatch.context() as gate:
            gate.setattr(store, attribute, Gate())
            task = asyncio.create_task(store.append(event, defer_materialization=True))
            try:
                await asyncio.wait_for(reached.wait(), 5)
                event.metadata["values"][0] = float("nan")
            finally:
                release.set()
                await task
        source = await engine.get_event(str(event.id), user_id=user)
        assert source.metadata == {"values": [1.0]}
        await engine.process_pending(user_id=user, budget_ms=5000)
        assert (await engine.get_event_nodes(str(event.id), user_id=user))[
            0
        ].metadata == source.metadata
