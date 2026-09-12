"""Empty source text is still content with a canonical SHA-256 identity."""
import hashlib

import pytest

from prme import MemoryEngine
from prme.models import Event
from tests.test_durable_ingestion import config, user


@pytest.mark.parametrize("content", ["", " \n", "telescope", "観測"])
def test_event_hash_covers_every_string_including_empty(content):
    event = Event(content=content, user_id="alice", role="user")
    assert event.content == content
    assert event.content_hash == hashlib.sha256(content.encode()).hexdigest()


async def test_empty_direct_store_is_durable_and_readable_by_hash(config, user):
    expected_hash = hashlib.sha256(b"").hexdigest()
    async with MemoryEngine.open(config) as engine:
        eid = await engine.store("", user_id=user, metadata={"attachment": "source-with-no-text"})
        assert (await engine.processing_status(eid, user_id=user)).status == "complete"
        event = await engine.get_event(eid, user_id=user)
        assert event.content == "" and event.content_hash == expected_hash
        saved = await engine._event_store.get_direct_store(eid, user_id=user)
        assert saved.content_hash == expected_hash and saved.node.content == ""
        ids = [str(e.id) for e in await engine._event_store.get_by_hash(expected_hash, user_id=user)]
        assert ids == [eid]
    async with MemoryEngine.open(config) as engine:
        event = await engine.get_event(eid, user_id=user)
        assert event.content_hash == expected_hash and event.metadata == {"attachment": "source-with-no-text"}
        nodes = await engine.get_event_nodes(eid, user_id=user)
        assert len(nodes) == 1 and nodes[0].content == ""
        assert (await engine.processing_status(eid, user_id=user)).status == "complete"
        assert (await engine.process_pending(user_id=user)).processed == 0
        assert await engine.get_event(eid, user_id=user + "-other") is None
