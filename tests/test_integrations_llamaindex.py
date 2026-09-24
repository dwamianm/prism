"""Tests for the LlamaIndex integration adapter."""

from __future__ import annotations

import tempfile

import pytest

pytest.importorskip("llama_index.core", reason="llama-index-core not installed")

from llama_index.core.schema import NodeWithScore, TextNode

try:
    from llama_index.core.llms import ChatMessage
except ImportError:
    from llama_index.core.base.llms.types import ChatMessage

from prme.client import config_from_directory
from prme.integrations.llamaindex import PRMEChatStore, PRMERetriever
from prme.retrieval.config import ScoringWeights
from prme.types import SourceType


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestPRMERetriever:
    def test_retrieve_returns_nodes(self, tmpdir: str):
        retriever = PRMERetriever(directory=tmpdir, user_id="test-user")
        try:
            retriever._client.store(
                "Alice prefers dark mode",
                user_id="test-user",
            )
            retriever._client.store(
                "Alice works at Acme Corp",
                user_id="test-user",
            )

            results = retriever.retrieve("What are Alice's preferences?")
            assert isinstance(results, list)
            assert all(isinstance(r, NodeWithScore) for r in results)
            assert len(results) > 0

            result = results[0]
            assert isinstance(result.node, TextNode)
            assert isinstance(result.score, float)
            assert len(result.text) > 0
            assert "node_type" in result.metadata
        finally:
            retriever.close()

    def test_retrieve_empty(self, tmpdir: str):
        retriever = PRMERetriever(directory=tmpdir, user_id="test-user")
        try:
            results = retriever.retrieve("anything")
            assert isinstance(results, list)
            assert len(results) == 0
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
            results = retriever.retrieve("memory")
            assert len(results) <= 1
        finally:
            retriever.close()

    def test_score_is_composite(self, tmpdir: str):
        retriever = PRMERetriever(directory=tmpdir, user_id="test-user")
        try:
            retriever._client.store(
                "Bob likes hiking", user_id="test-user"
            )
            results = retriever.retrieve("hiking")
            assert len(results) > 0
            assert results[0].score >= 0.0
        finally:
            retriever.close()


    def test_rank_fusion_reports_semantic_relevance(self, tmpdir: str):
        for fusion in ("rrf", "weighted"):
            config = config_from_directory(tmpdir).model_copy(
                update={"scoring": ScoringWeights(fusion=fusion)}
            )
            retriever = PRMERetriever(directory=tmpdir, user_id="test-user", config=config)
            try:
                if fusion == "rrf":
                    retriever._client.store("Bob likes hiking", user_id="test-user")
                results = retriever.retrieve("hiking")
                assert results
                if fusion == "rrf":
                    assert all(0 <= r.metadata["semantic_relevance"] <= 1 for r in results)
                else:
                    assert all("semantic_relevance" not in r.metadata for r in results)
            finally:
                retriever.close()


class TestPRMEChatStore:
    @pytest.mark.parametrize("key", ["", ":session", "alice:", "   :session"])
    def test_rejects_invalid_keys(self, tmpdir: str, key: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            with pytest.raises(ValueError, match="non-empty user_id and session_id"):
                store.get_messages(key)
        finally:
            store.close()

    def test_add_and_get_messages(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            store.add_message(
                "alice:s1",
                ChatMessage(role="user", content="Hello"),
            )
            store.add_message(
                "alice:s1",
                ChatMessage(role="assistant", content="Hi there!"),
            )

            msgs = store.get_messages("alice:s1")
            assert len(msgs) == 2
            assert msgs[0].content == "Hello"
            assert msgs[0].role.value == "user"
            assert msgs[1].content == "Hi there!"
            assert msgs[1].role.value == "assistant"
        finally:
            store.close()

    def test_key_isolation(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            store.add_message(
                "alice:s1",
                ChatMessage(role="user", content="Alice message"),
            )
            store.add_message(
                "bob:s1",
                ChatMessage(role="user", content="Bob message"),
            )

            alice_msgs = store.get_messages("alice:s1")
            bob_msgs = store.get_messages("bob:s1")
            assert len(alice_msgs) == 1
            assert alice_msgs[0].content == "Alice message"
            assert len(bob_msgs) == 1
            assert bob_msgs[0].content == "Bob message"
        finally:
            store.close()

    def test_delete_message_returns_and_hides_selected_message(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            store.add_message("alice:s1", ChatMessage(role="user", content="one"))
            store.add_message("alice:s1", ChatMessage(role="assistant", content="two"))
            store.add_message("alice:s1", ChatMessage(role="user", content="three"))

            removed = store.delete_message("alice:s1", 1)
            assert removed is not None and removed.content == "two"
            assert [message.content for message in store.get_messages("alice:s1")] == [
                "one",
                "three",
            ]

            last = store.delete_last_message("alice:s1")
            assert last is not None and last.content == "three"
            assert [message.content for message in store.get_messages("alice:s1")] == [
                "one"
            ]
            assert store.delete_message("alice:s1", 4) is None
        finally:
            store.close()

    def test_delete_messages_matches_chat_store_contract(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            assert store.delete_messages("unknown:s1") is None
            store.add_message("alice:s1", ChatMessage(role="user", content="one"))
            store.add_message("alice:s1", ChatMessage(role="assistant", content="two"))

            removed = store.delete_messages("alice:s1")
            assert removed is not None
            assert [message.content for message in removed] == ["one", "two"]
            assert store.get_messages("alice:s1") == []
            assert "alice:s1" not in store.get_keys()
        finally:
            store.close()

    def test_get_keys(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            store.add_message(
                "alice:s1",
                ChatMessage(role="user", content="Hi"),
            )
            store.add_message(
                "bob:s2",
                ChatMessage(role="user", content="Hey"),
            )
            keys = store.get_keys()
            assert "alice:s1" in keys
            assert "bob:s2" in keys
        finally:
            store.close()

    def test_set_messages(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            store.set_messages(
                "alice:s1",
                [
                    ChatMessage(role="user", content="Msg 1"),
                    ChatMessage(role="assistant", content="Msg 2"),
                ],
            )
            msgs = store.get_messages("alice:s1")
            assert len(msgs) == 2
        finally:
            store.close()

    def test_set_messages_replaces_existing_messages(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            store.add_message(
                "alice:s1", ChatMessage(role="user", content="Old message")
            )
            store.set_messages(
                "alice:s1",
                [ChatMessage(role="assistant", content="Replacement")],
            )
            messages = store.get_messages("alice:s1")
            assert [message.content for message in messages] == ["Replacement"]
        finally:
            store.close()

    def test_preserves_structured_message_fields(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        message = ChatMessage(
            role="assistant",
            content="Result",
            additional_kwargs={"tool_calls": [{"name": "lookup", "id": "call-1"}]},
        )
        try:
            store.add_message("alice:s1", message)
            restored = store.get_messages("alice:s1")[0]
            assert restored.model_dump(mode="json") == message.model_dump(mode="json")
        finally:
            store.close()

    def test_tool_role_keeps_tool_provenance(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            store.add_message(
                "alice:s1", ChatMessage(role="tool", content="Tool result")
            )
            event = store._client.get_events("alice", session_id="s1")[0]
            assert event.role == "tool"
            nodes = store._client.get_event_nodes(str(event.id), user_id="alice")
            assert nodes[0].source_type == SourceType.TOOL_OUTPUT
        finally:
            store.close()
