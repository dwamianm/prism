"""LlamaIndex integration for PRME.

Provides:
- **PRMERetriever**: LlamaIndex-compatible retriever backed by PRME hybrid search.
- **PRMEChatStore**: Chat store backed by PRME event store.

Install with::

    pip install prme[llamaindex]

Usage::

    from prme.integrations.llamaindex import PRMERetriever, PRMEChatStore

    # As a retriever
    retriever = PRMERetriever(directory="./memories", user_id="alice")
    nodes = retriever.retrieve("What are Alice's preferences?")

    # As a chat store
    store = PRMEChatStore(directory="./memories")
    store.add_message("alice:s1", ChatMessage(role="user", content="I prefer dark mode"))
    print(store.get_messages("alice:s1"))
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
    from llama_index.core.storage.chat_store.base import BaseChatStore
except ImportError as e:
    raise ImportError(
        "LlamaIndex integration requires llama-index-core. "
        "Install with: pip install prme[llamaindex]"
    ) from e

try:
    from llama_index.core.llms import ChatMessage
except ImportError:
    from llama_index.core.base.llms.types import ChatMessage

from prme.client import MemoryClient
from prme.config import PRMEConfig
from prme.integrations._chat_history import (
    append_chat_control,
    chat_message_metadata,
    serialized_chat_message,
    visible_chat_events,
)
from prme.types import NodeType, Scope

_LLAMAINDEX_MESSAGE_FORMAT = "llamaindex-v1"


def _message_role(message: ChatMessage) -> str:
    """Map LlamaIndex roles to PRME provenance roles."""
    role = message.role.value
    if role in {"function", "tool"}:
        return "tool"
    if role in {"assistant", "chatbot", "model"}:
        return "assistant"
    if role == "system":
        return "system"
    return "user"


class PRMERetriever(BaseRetriever):
    """LlamaIndex retriever backed by PRME hybrid search.

    Wraps :class:`~prme.client.MemoryClient` and maps results
    to LlamaIndex :class:`~llama_index.core.schema.NodeWithScore` objects.

    Args:
        directory: Path to the PRME memory directory.
        user_id: User ID for scoped retrieval.
        config: Optional PRMEConfig override.
        scope: Scope filter(s) for retrieval.
        token_budget: Token budget for context packing.
        top_k: Maximum number of nodes to return.
    """

    def __init__(
        self,
        directory: str = ".",
        *,
        user_id: str,
        config: PRMEConfig | None = None,
        scope: Scope | list[Scope] | None = None,
        token_budget: int | None = None,
        top_k: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._client = MemoryClient(directory, config=config)
        self._user_id = user_id
        self._scope = scope
        self._token_budget = token_budget
        self._top_k = top_k

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        response = self._client.retrieve(
            query_bundle.query_str,
            user_id=self._user_id,
            scope=self._scope,
            token_budget=self._token_budget,
        )

        nodes_with_scores = []
        for candidate in response.results[: self._top_k]:
            node = candidate.node
            metadata: dict[str, Any] = {
                "node_type": node.node_type.value,
                "lifecycle_state": node.lifecycle_state.value,
                "confidence": node.confidence,
                "salience": node.salience,
                "scope": node.scope.value,
                "created_at": node.created_at.isoformat(),
                "updated_at": node.updated_at.isoformat(),
            }
            if node.event_time:
                metadata["event_time"] = node.event_time.isoformat()
            if node.epistemic_type:
                metadata["epistemic_type"] = node.epistemic_type.value
            if candidate.paths:
                metadata["retrieval_paths"] = candidate.paths
            if candidate.semantic_relevance is not None:
                # Rank fusion: the score is rank-based, so this cosine is the
                # value to threshold on (RFC-0005 Section 7.2).
                metadata["semantic_relevance"] = candidate.semantic_relevance

            text_node = TextNode(
                text=node.content,
                metadata=metadata,
                id_=str(node.id),
            )
            nodes_with_scores.append(
                NodeWithScore(
                    node=text_node,
                    score=candidate.composite_score,
                )
            )
        return nodes_with_scores

    def close(self) -> None:
        """Close the underlying MemoryClient."""
        self._client.close()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def _parse_key(key: str) -> tuple[str, str]:
    """Parse a 'user_id:session_id' key into components."""
    if ":" in key:
        user_id, session_id = key.split(":", 1)
    else:
        user_id, session_id = key, "default"
    if not user_id.strip() or not session_id.strip():
        raise ValueError(
            "PRME chat keys require a non-empty user_id and session_id: "
            "'user_id:session_id'"
        )
    return user_id, session_id


class PRMEChatStore(BaseChatStore):
    """LlamaIndex chat store backed by PRME event store.

    Messages are keyed by ``"user_id:session_id"`` strings.
    Stores messages via :meth:`~prme.client.MemoryClient.store` and
    reads them via :meth:`~prme.client.MemoryClient.get_events`.

    Args:
        directory: Path to the PRME memory directory.
        config: Optional PRMEConfig override.
        scope: Scope for stored messages.
    """

    def __init__(
        self,
        directory: str = ".",
        *,
        config: PRMEConfig | None = None,
        scope: Scope = Scope.PERSONAL,
    ) -> None:
        self._client = MemoryClient(directory, config=config)
        self._scope = scope
        # Track keys we've seen for get_keys()
        self._known_keys: set[str] = set()

    def set_messages(self, key: str, messages: list[ChatMessage]) -> None:
        """Replace the logical messages for a key using an append-only marker."""
        user_id, session_id = _parse_key(key)
        append_chat_control(
            self._client,
            user_id=user_id,
            session_id=session_id,
            scope=self._scope,
            operation="clear",
        )
        for msg in messages:
            self._store_message(user_id, session_id, msg)
        self._known_keys.add(key)

    def get_messages(self, key: str) -> list[ChatMessage]:
        """Retrieve all messages for a key."""
        user_id, session_id = _parse_key(key)
        events = visible_chat_events(
            self._client,
            user_id=user_id,
            session_id=session_id,
            scope=self._scope,
        )
        messages = []
        for event in events:
            messages.append(self._message_from_event(event))
        return messages

    def add_message(self, key: str, message: ChatMessage) -> None:
        """Add a single message for a key."""
        user_id, session_id = _parse_key(key)
        self._store_message(user_id, session_id, message)
        self._known_keys.add(key)

    def _store_message(
        self, user_id: str, session_id: str, message: ChatMessage
    ) -> None:
        serialized = message.model_dump(mode="json")
        self._client.store(
            message.content or "",
            user_id=user_id,
            session_id=session_id,
            role=_message_role(message),
            node_type=NodeType.NOTE,
            scope=self._scope,
            metadata=chat_message_metadata(
                format_name=_LLAMAINDEX_MESSAGE_FORMAT,
                message=serialized,
            ),
        )

    def delete_messages(self, key: str) -> Optional[list[ChatMessage]]:
        """Logically delete and return all messages for a key."""
        known = key in self._known_keys
        messages = self.get_messages(key)
        if not known and not messages:
            return None
        user_id, session_id = _parse_key(key)
        append_chat_control(
            self._client,
            user_id=user_id,
            session_id=session_id,
            scope=self._scope,
            operation="clear",
        )
        self._known_keys.discard(key)
        return messages

    def delete_message(self, key: str, idx: int) -> Optional[ChatMessage]:
        """Logically delete and return one message by index."""
        user_id, session_id = _parse_key(key)
        events = visible_chat_events(
            self._client,
            user_id=user_id,
            session_id=session_id,
            scope=self._scope,
        )
        try:
            event = events[idx]
        except IndexError:
            return None
        message = self._message_from_event(event)
        append_chat_control(
            self._client,
            user_id=user_id,
            session_id=session_id,
            scope=self._scope,
            operation="delete",
            target_event_id=str(event.id),
        )
        return message

    def delete_last_message(self, key: str) -> Optional[ChatMessage]:
        """Logically delete and return the last message for a key."""
        return self.delete_message(key, -1)

    def get_keys(self) -> list[str]:
        """Return keys written through this chat-store instance."""
        return sorted(self._known_keys)

    @staticmethod
    def _message_from_event(event: Any) -> ChatMessage:
        serialized = serialized_chat_message(
            event, format_name=_LLAMAINDEX_MESSAGE_FORMAT
        )
        if serialized is not None:
            try:
                return ChatMessage.model_validate(serialized)
            except (TypeError, ValueError):
                pass
        role = event.role if hasattr(event, "role") else "user"
        return ChatMessage(role=role, content=event.content)

    def close(self) -> None:
        """Close the underlying MemoryClient."""
        self._client.close()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
