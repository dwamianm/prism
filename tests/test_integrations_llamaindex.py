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

from prme.integrations.llamaindex import PRMEChatStore, PRMERetriever


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


class TestPRMEChatStore:
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

    def test_delete_returns_none(self, tmpdir: str):
        store = PRMEChatStore(directory=tmpdir)
        try:
            assert store.delete_messages("alice:s1") is None
            assert store.delete_message("alice:s1", 0) is None
            assert store.delete_last_message("alice:s1") is None
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
