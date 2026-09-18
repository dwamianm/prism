"""Tests for the LangChain integration adapter."""

from __future__ import annotations

import tempfile

import pytest

pytest.importorskip("langchain_core", reason="langchain-core not installed")

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prme.integrations._chat_history import CHAT_CONTROL_METADATA_KEY
from prme.integrations.langchain import PRMEChatMessageHistory, PRMERetriever
from prme.types import Scope, SourceType


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestPRMERetriever:
    def test_retrieve_returns_documents(self, tmpdir: str):
        retriever = PRMERetriever(directory=tmpdir, user_id="test-user")
        try:
            # Store some memories
            retriever._client.store(
                "Alice prefers dark mode",
                user_id="test-user",
            )
            retriever._client.store(
                "Alice works at Acme Corp",
                user_id="test-user",
            )

            docs = retriever.invoke("What are Alice's preferences?")
            assert isinstance(docs, list)
            assert all(isinstance(d, Document) for d in docs)
            assert len(docs) > 0

            doc = docs[0]
            assert isinstance(doc.page_content, str)
            assert len(doc.page_content) > 0
            assert "node_type" in doc.metadata
            assert "composite_score" in doc.metadata
            assert doc.id is not None
        finally:
            retriever.close()

    def test_retrieve_empty(self, tmpdir: str):
        retriever = PRMERetriever(directory=tmpdir, user_id="test-user")
        try:
            docs = retriever.invoke("anything")
            assert isinstance(docs, list)
            assert len(docs) == 0
        finally:
            retriever.close()

    def test_retrieve_respects_top_k(self, tmpdir: str):
        retriever = PRMERetriever(
            directory=tmpdir, user_id="test-user", top_k=1
        )
        try:
            for i in range(5):
                retriever._client.store(
                    f"Memory number {i}", user_id="test-user"
                )
            docs = retriever.invoke("memory")
            assert len(docs) <= 1
        finally:
            retriever.close()

    def test_metadata_fields(self, tmpdir: str):
        retriever = PRMERetriever(directory=tmpdir, user_id="test-user")
        try:
            retriever._client.store(
                "Bob likes hiking", user_id="test-user"
            )
            docs = retriever.invoke("hiking")
            assert len(docs) > 0
            meta = docs[0].metadata
            assert "lifecycle_state" in meta
            assert "confidence" in meta
            assert "salience" in meta
            assert "scope" in meta
            assert "created_at" in meta
        finally:
            retriever.close()


class TestPRMEChatMessageHistory:
    def test_add_and_retrieve_messages(self, tmpdir: str):
        history = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        try:
            history.add_user_message("Hello")
            history.add_ai_message("Hi there!")

            msgs = history.messages
            assert len(msgs) == 2
            assert isinstance(msgs[0], HumanMessage)
            assert msgs[0].content == "Hello"
            assert isinstance(msgs[1], AIMessage)
            assert msgs[1].content == "Hi there!"
        finally:
            history.close()

    def test_clear_hides_messages_but_retains_source_events(self, tmpdir: str):
        history = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        try:
            history.add_user_message("Hello")
            history.clear()
            assert history.messages == []

            events = history._client.get_events("test-user", session_id="s1")
            assert len(events) == 2
            control = next(
                event
                for event in events
                if event.metadata and CHAT_CONTROL_METADATA_KEY in event.metadata
            )
            assert control.metadata[CHAT_CONTROL_METADATA_KEY]["operation"] == "clear"
            assert history._client.get_event_nodes(
                str(control.id), user_id="test-user"
            ) == []

            history.add_ai_message("Fresh start")
            assert [message.content for message in history.messages] == ["Fresh start"]
        finally:
            history.close()

    def test_clear_persists_across_instances(self, tmpdir: str):
        history = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        history.add_user_message("Old message")
        history.clear()
        history.close()

        reopened = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        try:
            assert reopened.messages == []
        finally:
            reopened.close()

    def test_preserves_structured_message_fields(self, tmpdir: str):
        history = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        message = ToolMessage(
            content=[{"type": "text", "text": "Tool output"}],
            tool_call_id="call-1",
            name="lookup",
            additional_kwargs={"source": "fixture"},
        )
        try:
            history.add_message(message)
            restored = history.messages[0]
            assert isinstance(restored, ToolMessage)
            assert restored.model_dump(mode="json") == message.model_dump(mode="json")
            event = history._client.get_events("test-user", session_id="s1")[0]
            assert event.role == "tool"
            nodes = history._client.get_event_nodes(str(event.id), user_id="test-user")
            assert nodes[0].source_type == SourceType.TOOL_OUTPUT
        finally:
            history.close()

    def test_messages_assignment_replaces_history(self, tmpdir: str):
        history = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        try:
            history.add_user_message("Old")
            history.messages = [AIMessage(content="Replacement")]
            restored = history.messages
            assert len(restored) == 1
            assert isinstance(restored[0], AIMessage)
            assert restored[0].content == "Replacement"
        finally:
            history.close()

    def test_scope_isolation(self, tmpdir: str):
        history = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        try:
            history.add_user_message("Personal")
            history._client.store(
                "Project",
                user_id="test-user",
                session_id="s1",
                scope=Scope.PROJECT,
            )
            assert [message.content for message in history.messages] == ["Personal"]
        finally:
            history.close()

    def test_session_isolation(self, tmpdir: str):
        h1 = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s1"
        )
        h2 = PRMEChatMessageHistory(
            directory=tmpdir, user_id="test-user", session_id="s2"
        )
        try:
            h1.add_user_message("Session 1 message")
            h2.add_user_message("Session 2 message")

            msgs1 = h1.messages
            msgs2 = h2.messages
            assert len(msgs1) == 1
            assert msgs1[0].content == "Session 1 message"
            assert len(msgs2) == 1
            assert msgs2[0].content == "Session 2 message"
        finally:
            h1.close()
            h2.close()
