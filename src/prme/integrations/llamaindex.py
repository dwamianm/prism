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
from prme.types import NodeType, Scope


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
        return user_id, session_id
    return key, "default"


class PRMEChatStore(BaseChatStore):
    """LlamaIndex chat store backed by PRME event store.

    Messages are keyed by ``"user_id:session_id"`` strings.
    Stores messages via :meth:`~prme.client.MemoryClient.store` and
    queries them via :meth:`~prme.client.MemoryClient.query_nodes`.

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
        """Store messages for a key (appends; PRME is append-only)."""
        self._known_keys.add(key)
        user_id, session_id = _parse_key(key)
        for msg in messages:
            self._client.store(
                msg.content,
                user_id=user_id,
                session_id=session_id,
                role=msg.role.value,
                node_type=NodeType.NOTE,
                scope=self._scope,
                metadata={"role": msg.role.value},
            )

    def get_messages(self, key: str) -> list[ChatMessage]:
        """Retrieve all messages for a key."""
        self._known_keys.add(key)
        user_id, session_id = _parse_key(key)
        events = self._client.get_events(
            user_id,
            session_id=session_id,
            limit=1000,
        )
        messages = []
        for event in sorted(events, key=lambda e: e.timestamp):
            role = event.role if hasattr(event, "role") else "user"
            messages.append(ChatMessage(role=role, content=event.content))
        return messages

    def add_message(self, key: str, message: ChatMessage) -> None:
        """Add a single message for a key."""
        self._known_keys.add(key)
        user_id, session_id = _parse_key(key)
        self._client.store(
            message.content,
            user_id=user_id,
            session_id=session_id,
            role=message.role.value,
            node_type=NodeType.NOTE,
            scope=self._scope,
            metadata={"role": message.role.value},
        )

    def delete_messages(self, key: str) -> Optional[list[ChatMessage]]:
        """Not supported (PRME is append-only). Returns None."""
        return None

    def delete_message(self, key: str, idx: int) -> Optional[ChatMessage]:
        """Not supported (PRME is append-only). Returns None."""
        return None

    def delete_last_message(self, key: str) -> Optional[ChatMessage]:
        """Not supported (PRME is append-only). Returns None."""
        return None

    def get_keys(self) -> list[str]:
        """Return keys that have been used in this session."""
        return sorted(self._known_keys)

    def close(self) -> None:
        """Close the underlying MemoryClient."""
        self._client.close()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
