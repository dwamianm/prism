"""Full-pipeline lexical experiments preserve product boundaries and identities."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from benchmarks.diagnostics.hybrid_lexical import capture, policy_scope, raw_config, QueryProxy
from benchmarks.diagnostics.packing_reader import canonical
from benchmarks.evidence import longmemeval_sources
from prme.retrieval.config import PackingConfig
from prme.storage.lexical_index import LexicalIndex
from tests.test_durable_ingestion import MockEmbeddingProvider
from tests.test_evidence_evaluation import question


async def test_complete_product_capture_excludes_gold_and_repeats_controls(monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    turns, gold = longmemeval_sources(question())
    result = await capture(turns, question()["question"], datetime(2025, 1, 3, tzinfo=timezone.utc),
                           raw_config(), case_identity="authored", budgets=[512, 1024])
    assert len(result["arms"]) == 8
    assert result["repeated_control_identical"] and result["source_memories_unchanged"]
    for key, arm in result["arms"].items():
        policy, order, budget = key.split(":")
        assert arm["packing"]["multipath_ordering"] == order
        assert arm["execution"]["features"]["experimental_lexical_query"]["policy"] == policy
        assert arm["measurement"]["tokens"] <= int(budget) - 100
        assert arm["measurement"]["evidence_recall"] is None
    assert set(result["candidates"]) == {"parser", "literal_stopwords"}
    raw = canonical(result)
    for secret in [b"secret-answer-label", b"answer-labelled-session", b"has_answer", b"answer_session_ids"]:
        assert secret not in raw


async def test_query_proxy_retains_native_scope_and_owner_filters(tmp_path):
    index = LexicalIndex(str(tmp_path))
    try:
        for nid, owner, scope, kind in [("wanted", "alice", "project", "fact"),
            ("foreign", "bob", "project", "fact"), ("personal", "alice", "personal", "fact"),
            ("note", "alice", "project", "note")]:
            await index.index(nid, "The cobalt telescope", owner, kind, scope)
        await index.flush()
        native = index._index
        index._index = QueryProxy(index)
        rows = await index.search("Tell me about the cobalt telescope", "alice", scope=["project"], node_type="fact")
        assert [row["node_id"] for row in rows] == ["wanted"]
        with pytest.raises(ValueError, match="content"):
            index._index.parse_query_lenient("alice", ["user_id"])
        index._index = native
    finally:
        await index.close()


def test_failed_arm_restores_parser_packing_and_receipt_identity():
    original = object()
    config = PackingConfig()
    identity = {"baseline": True}
    engine = SimpleNamespace(_lexical_index=SimpleNamespace(_index=original),
        _retrieval_pipeline=SimpleNamespace(_packing_config=config, _feature_identity=identity))
    with pytest.raises(RuntimeError):
        with policy_scope(engine, "literal_stopwords", "score"):
            assert isinstance(engine._lexical_index._index, QueryProxy)
            assert engine._retrieval_pipeline._packing_config.multipath_ordering == "score"
            raise RuntimeError("authored failure")
    assert engine._lexical_index._index is original
    assert engine._retrieval_pipeline._packing_config is config
    assert engine._retrieval_pipeline._feature_identity is identity
