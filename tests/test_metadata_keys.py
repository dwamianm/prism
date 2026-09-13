"""JSON key normalization must not silently discard caller metadata."""

import pytest

from prme import MemoryEngine
from prme.storage.metadata import snapshot_metadata
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("method", ["store", "ingest_fast"])
@pytest.mark.parametrize("key,text", [(1, "1"), (True, "true"), (None, "null"), (1.5, "1.5")])
async def test_colliding_nested_keys_publish_no_source_or_work(config, user, method, key, text):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="Metadata object keys collide"):
            await getattr(engine, method)(
                "A labelled observation", user_id=user,
                metadata={"nested": [{key: "first label", text: "second label"}]},
            )
        assert await engine.get_events(user_id=user) == []
        assert await engine.count_nodes(user_id=user) == 0
        processed = await engine.process_pending(user_id=user, budget_ms=5000)
        assert processed.processed == processed.pending == processed.failed == 0


async def test_direct_node_and_extraction_key_collisions_publish_no_work(config, user):
    from prme.models.events import Event
    from prme.models.nodes import MemoryNode
    from prme.types import NodeType

    async with MemoryEngine.open(config) as engine:
        event = Event(user_id=user, role="user", content="A labelled observation")
        collision = {"nested": {1: "first", "1": "second"}}
        node = MemoryNode(user_id=user, node_type=NodeType.NOTE, content=event.content,
                          evidence_refs=[event.id], metadata=collision)
        with pytest.raises(ValueError, match="Metadata object keys collide"):
            await engine._event_store.append(event, store_node=node)
        with pytest.raises(ValueError, match="Metadata object keys collide"):
            await engine._event_store.append(event.model_copy(update={"metadata": collision}),
                                             defer_extraction=True)
        assert await engine.get_event(str(event.id), user_id=user) is None
        assert await engine.processing_status(str(event.id), user_id=user) is None
        assert await engine.extraction_status(str(event.id), user_id=user) is None


def test_unambiguous_normalization_and_separate_object_keys_are_preserved():
    original = {"values": ({1: "integer"}, {"1": "text"}), "nested": {None: False}}
    expected = {"values": [{"1": "integer"}, {"1": "text"}], "nested": {"null": False}}
    result = snapshot_metadata(original)
    assert result == expected
    original["values"][0][1] = "later mutation"
    assert result == expected
