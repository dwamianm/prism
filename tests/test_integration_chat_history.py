"""Framework-independent tests for append-only chat history controls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prme.integrations._chat_history import (
    CHAT_CONTROL_METADATA_KEY,
    CHAT_HISTORY_FORMAT_VERSION,
    visible_chat_events,
)
from prme import MemoryClient
from prme.models import Event
from prme.types import Scope


class _PagedEvents:
    def __init__(self, events: list[Event]) -> None:
        self.events = sorted(
            events, key=lambda event: (event.timestamp, str(event.id)), reverse=True
        )
        self.calls: list[tuple[int, int]] = []

    def get_events(
        self,
        user_id: str,
        *,
        session_id: str,
        limit: int,
        offset: int,
    ) -> list[Event]:
        self.calls.append((limit, offset))
        return self.events[offset : offset + limit]


def _event(index: int, *, metadata: dict | None = None) -> Event:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)
    return Event(
        content=f"message-{index}",
        user_id="alice",
        session_id="session",
        role="user",
        scope=Scope.PERSONAL,
        metadata=metadata,
        timestamp=timestamp,
        created_at=timestamp,
    )


def test_visible_chat_events_pages_past_one_thousand() -> None:
    client = _PagedEvents([_event(index) for index in range(1001)])

    visible = visible_chat_events(
        client,  # type: ignore[arg-type]
        user_id="alice",
        session_id="session",
        scope=Scope.PERSONAL,
    )

    assert len(visible) == 1001
    assert visible[0].content == "message-0"
    assert visible[-1].content == "message-1000"
    assert client.calls == [(1000, 0), (1000, 1000)]


def test_visible_chat_events_stops_at_latest_clear_marker() -> None:
    marker = _event(
        995,
        metadata={
            CHAT_CONTROL_METADATA_KEY: {
                "version": CHAT_HISTORY_FORMAT_VERSION,
                "operation": "clear",
            }
        },
    )
    events = [_event(index) for index in range(1100) if index != 995] + [marker]
    client = _PagedEvents(events)

    visible = visible_chat_events(
        client,  # type: ignore[arg-type]
        user_id="alice",
        session_id="session",
        scope=Scope.PERSONAL,
    )

    assert [event.content for event in visible] == [
        f"message-{index}" for index in range(996, 1100)
    ]
    assert client.calls == [(1000, 0)]


def test_visible_chat_events_rejects_unknown_control_versions() -> None:
    client = _PagedEvents([
        _event(
            1,
            metadata={
                CHAT_CONTROL_METADATA_KEY: {
                    "version": 2,
                    "operation": "clear",
                }
            },
        )
    ])

    with pytest.raises(ValueError, match="Unsupported PRME chat-history control"):
        visible_chat_events(
            client,  # type: ignore[arg-type]
            user_id="alice",
            session_id="session",
            scope=Scope.PERSONAL,
        )


def test_qa_pairing_does_not_cross_scope(tmp_path) -> None:
    with MemoryClient(str(tmp_path)) as client:
        client.store(
            "Personal question",
            user_id="alice",
            session_id="session",
            role="user",
            scope=Scope.PERSONAL,
        )
        event_id = client.store(
            "Project answer",
            user_id="alice",
            session_id="session",
            role="assistant",
            scope=Scope.PROJECT,
        )

        nodes = client.get_event_nodes(event_id, user_id="alice")
        assert len(nodes) == 1
        assert nodes[0].scope == Scope.PROJECT
        assert not (nodes[0].metadata or {}).get("qa_pair")


def test_chat_control_prevents_pairing_across_clear(tmp_path) -> None:
    with MemoryClient(str(tmp_path)) as client:
        client.store(
            "Old question",
            user_id="alice",
            session_id="session",
            role="user",
        )
        client._append_control_event(
            "",
            user_id="alice",
            session_id="session",
            scope=Scope.PERSONAL,
            metadata={
                CHAT_CONTROL_METADATA_KEY: {
                    "version": CHAT_HISTORY_FORMAT_VERSION,
                    "operation": "clear",
                }
            },
        )
        event_id = client.store(
            "New answer",
            user_id="alice",
            session_id="session",
            role="assistant",
        )

        nodes = client.get_event_nodes(event_id, user_id="alice")
        assert len(nodes) == 1
        assert not (nodes[0].metadata or {}).get("qa_pair")
