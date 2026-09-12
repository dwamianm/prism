"""Static consumer contract; checked by mypy rather than executed."""

from typing import assert_type

from prme import MemoryClient, RetrievalResponse
from prme.models import Event, MemoryNode, ProcessingResult
from prme.organizer.models import OrganizeResult


def consume(client: MemoryClient) -> None:
    assert_type(client.retrieve("preferences", user_id="alice"), RetrievalResponse)
    assert_type(client.get_node("node-id"), MemoryNode | None)
    assert_type(client.query_nodes(user_id="alice"), list[MemoryNode])
    assert_type(client.get_events("alice"), list[Event])
    assert_type(client.process_pending(user_id="alice"), ProcessingResult)
    assert_type(client.organize(user_id="alice"), OrganizeResult)
    for node in client.iter_nodes(user_id="alice"):
        assert_type(node, MemoryNode)
