"""Shared append-only chat-history semantics for framework adapters."""

from __future__ import annotations

from typing import Any, Literal

from prme.client import MemoryClient
from prme.models.events import Event
from prme.types import Scope

CHAT_CONTROL_METADATA_KEY = "_prme_chat_history_control"
CHAT_MESSAGE_METADATA_KEY = "_prme_chat_message"
CHAT_HISTORY_FORMAT_VERSION = 1
_EVENT_PAGE_SIZE = 1000


def visible_chat_events(
    client: MemoryClient,
    *,
    user_id: str,
    session_id: str,
    scope: Scope,
) -> list[Event]:
    """Return the logical chat history while retaining immutable source events."""
    events: list[Event] = []
    offset = 0

    while True:
        page = client.get_events(
            user_id,
            session_id=session_id,
            limit=_EVENT_PAGE_SIZE,
            offset=offset,
        )
        scoped_page = [event for event in page if event.scope == scope]
        events.extend(scoped_page)

        # Pages are newest first. Once a clear marker is present, no older
        # event can contribute to the logical history.
        if any(_control_operation(event) == "clear" for event in scoped_page):
            break
        if len(page) < _EVENT_PAGE_SIZE:
            break
        offset += len(page)

    visible: dict[str, Event] = {}
    for event in sorted(events, key=lambda item: (item.timestamp, str(item.id))):
        operation = _control_operation(event)
        if operation == "clear":
            visible.clear()
        elif operation == "delete":
            control = event.metadata[CHAT_CONTROL_METADATA_KEY]  # type: ignore[index]
            visible.pop(control["target_event_id"], None)
        elif operation is None:
            visible[str(event.id)] = event
    return list(visible.values())


def append_chat_control(
    client: MemoryClient,
    *,
    user_id: str,
    session_id: str,
    scope: Scope,
    operation: Literal["clear", "delete"],
    target_event_id: str | None = None,
) -> None:
    """Append a logical mutation without materializing it as a memory node."""
    control: dict[str, Any] = {
        "version": CHAT_HISTORY_FORMAT_VERSION,
        "operation": operation,
    }
    if operation == "delete":
        if target_event_id is None:
            raise ValueError("delete chat controls require target_event_id")
        control["target_event_id"] = target_event_id

    client._append_control_event(
        "",
        user_id=user_id,
        session_id=session_id,
        scope=scope,
        metadata={CHAT_CONTROL_METADATA_KEY: control},
    )


def serialized_chat_message(event: Event, *, format_name: str) -> dict[str, Any] | None:
    """Read one adapter-owned serialized message from event metadata."""
    metadata = event.metadata
    if not isinstance(metadata, dict):
        return None
    envelope = metadata.get(CHAT_MESSAGE_METADATA_KEY)
    if (
        not isinstance(envelope, dict)
        or envelope.get("version") != CHAT_HISTORY_FORMAT_VERSION
        or envelope.get("format") != format_name
        or not isinstance(envelope.get("message"), dict)
    ):
        return None
    return envelope["message"]


def chat_message_metadata(*, format_name: str, message: dict[str, Any]) -> dict[str, Any]:
    """Build a versioned, framework-specific message envelope."""
    return {
        CHAT_MESSAGE_METADATA_KEY: {
            "version": CHAT_HISTORY_FORMAT_VERSION,
            "format": format_name,
            "message": message,
        }
    }


def _control_operation(event: Event) -> Literal["clear", "delete"] | None:
    metadata = event.metadata
    if not isinstance(metadata, dict) or CHAT_CONTROL_METADATA_KEY not in metadata:
        return None
    control = metadata.get(CHAT_CONTROL_METADATA_KEY)
    if not isinstance(control, dict):
        raise ValueError("Malformed PRME chat-history control event")
    version = control.get("version")
    if version != CHAT_HISTORY_FORMAT_VERSION:
        raise ValueError(f"Unsupported PRME chat-history control version: {version!r}")
    operation = control.get("operation")
    if operation == "clear":
        return "clear"
    if operation == "delete":
        target = control.get("target_event_id")
        if isinstance(target, str) and target:
            return "delete"
    raise ValueError("Malformed PRME chat-history control event")
