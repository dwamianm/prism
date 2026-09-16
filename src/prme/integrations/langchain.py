"""LangChain integration for PRME.

Provides:
- **PRMERetriever**: LangChain-compatible retriever backed by PRME hybrid search.
- **PRMEChatMessageHistory**: Chat message history backed by PRME event store.

Install with::

    pip install prme[langchain]

Usage::

    from prme.integrations.langchain import PRMERetriever, PRMEChatMessageHistory

    # As a retriever in a RAG chain
    retriever = PRMERetriever(directory="./memories", user_id="alice")
    docs = retriever.invoke("What are Alice's preferences?")

    # As chat message history
    history = PRMEChatMessageHistory(directory="./memories", user_id="alice", session_id="s1")
    history.add_user_message("I prefer dark mode")
    print(history.messages)
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

try:
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from langchain_core.chat_history import BaseChatMessageHistory
    from langchain_core.documents import Document
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
        messages_from_dict,
    )
    from langchain_core.retrievers import BaseRetriever
except ImportError as e:
    raise ImportError(
        "LangChain integration requires langchain-core. "
        "Install with: pip install prme[langchain]"
    ) from e

from pydantic import PrivateAttr

from prme.client import MemoryClient
from prme.config import PRMEConfig
from prme.integrations._chat_history import (
    append_chat_control,
    chat_message_metadata,
    serialized_chat_message,
    visible_chat_events,
)
from prme.types import NodeType, Scope

_LANGCHAIN_MESSAGE_FORMAT = "langchain-v1"


def _message_text(message: BaseMessage) -> str:
    """Extract indexable text while the full message stays in metadata."""
    if isinstance(message.content, str):
        return message.content
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _message_role(message: BaseMessage) -> str:
    """Map LangChain message classes to PRME provenance roles."""
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, SystemMessage):
        return "system"
    if message.type in {"tool", "function"}:
        return "tool"
    role = getattr(message, "role", None)
    return role if isinstance(role, str) and role else "user"


class PRMERetriever(BaseRetriever):
    """LangChain retriever backed by PRME hybrid search.

    Wraps :class:`~prme.client.MemoryClient` and maps
    :class:`~prme.retrieval.models.RetrievalCandidate` results
    to LangChain :class:`~langchain_core.documents.Document` objects.

    Args:
        directory: Path to the PRME memory directory.
        user_id: User ID for scoped retrieval.
        config: Optional PRMEConfig override.
        scope: Scope filter(s) for retrieval.
        token_budget: Token budget for context packing.
        top_k: Maximum number of documents to return.
    """

    directory: str = "."
    user_id: str
    config: PRMEConfig | None = None
    scope: Scope | list[Scope] | None = None
    token_budget: int | None = None
    top_k: int = 10

    _client: MemoryClient = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        self._client = MemoryClient(self.directory, config=self.config)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        response = self._client.retrieve(
            query,
            user_id=self.user_id,
            scope=self.scope,
            token_budget=self.token_budget,
        )

        docs = []
        for candidate in response.results[: self.top_k]:
            node = candidate.node
            metadata: dict[str, Any] = {
                "node_type": node.node_type.value,
                "lifecycle_state": node.lifecycle_state.value,
                "confidence": node.confidence,
                "salience": node.salience,
                "scope": node.scope.value,
                "composite_score": candidate.composite_score,
                "created_at": node.created_at.isoformat(),
                "updated_at": node.updated_at.isoformat(),
            }
            if node.event_time:
                metadata["event_time"] = node.event_time.isoformat()
            if node.epistemic_type:
                metadata["epistemic_type"] = node.epistemic_type.value
            if candidate.paths:
                metadata["retrieval_paths"] = candidate.paths

            docs.append(
                Document(
                    page_content=node.content,
                    metadata=metadata,
                    id=str(node.id),
                )
            )
        return docs

    def close(self) -> None:
        """Close the underlying MemoryClient."""
        self._client.close()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


class PRMEChatMessageHistory(BaseChatMessageHistory):
    """Chat message history backed by PRME event store.

    Stores messages via :meth:`~prme.client.MemoryClient.store` and
    retrieves them via :meth:`~prme.client.MemoryClient.get_events`.

    Args:
        directory: Path to the PRME memory directory.
        user_id: User ID for scoping.
        session_id: Session identifier for grouping messages.
        config: Optional PRMEConfig override.
        scope: Scope for stored messages.
    """

    def __init__(
        self,
        directory: str = ".",
        *,
        user_id: str,
        session_id: str | None = None,
        config: PRMEConfig | None = None,
        scope: Scope = Scope.PERSONAL,
    ) -> None:
        self._client = MemoryClient(directory, config=config)
        self._user_id = user_id
        self._session_id = session_id or str(uuid.uuid4())
        self._scope = scope

    @property
    def messages(self) -> list[BaseMessage]:
        """Retrieve all messages for this session."""
        events = visible_chat_events(
            self._client,
            user_id=self._user_id,
            session_id=self._session_id,
            scope=self._scope,
        )
        msgs: list[BaseMessage] = []
        for event in events:
            serialized = serialized_chat_message(
                event, format_name=_LANGCHAIN_MESSAGE_FORMAT
            )
            if serialized is not None:
                try:
                    msgs.append(messages_from_dict([serialized])[0])
                    continue
                except (KeyError, NotImplementedError, TypeError, ValueError):
                    # Retain readability if a future LangChain release cannot
                    # deserialize an older message envelope.
                    pass
            role = event.role if hasattr(event, "role") else "user"
            content = event.content
            if role == "assistant":
                msgs.append(AIMessage(content=content))
            elif role == "system":
                msgs.append(SystemMessage(content=content))
            else:
                msgs.append(HumanMessage(content=content))
        return msgs

    @messages.setter
    def messages(self, messages: list[BaseMessage]) -> None:
        """Replace the logical history while retaining immutable source events."""
        self.clear()
        self.add_messages(messages)

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        """Store messages in PRME."""
        for msg in messages:
            role = _message_role(msg)
            serialized = {
                "type": msg.type,
                "data": msg.model_dump(mode="json"),
            }
            self._client.store(
                _message_text(msg),
                user_id=self._user_id,
                session_id=self._session_id,
                role=role,
                node_type=NodeType.NOTE,
                scope=self._scope,
                metadata=chat_message_metadata(
                    format_name=_LANGCHAIN_MESSAGE_FORMAT,
                    message=serialized,
                ),
            )

    def clear(self) -> None:
        """Logically clear this history while retaining immutable source events."""
        append_chat_control(
            self._client,
            user_id=self._user_id,
            session_id=self._session_id,
            scope=self._scope,
            operation="clear",
        )

    def close(self) -> None:
        """Close the underlying MemoryClient."""
        self._client.close()

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
