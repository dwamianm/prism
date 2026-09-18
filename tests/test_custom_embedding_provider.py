"""Public provider injection and asymmetric encoding preserve storage identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prme import CachedEmbeddingProvider, EmbeddingProvider, MemoryClient, MemoryEngine, QueryEmbeddingProvider
from prme.config import PRMEConfig
from prme.storage.embedding import encode_query
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


class Documents:
    model_name = "authored-custom-space"
    model_version = "document-query-recipe-1"
    dimension = 384

    def __init__(self):
        self.documents = []
        self.queries = []
        self.closed = False

    async def embed(self, texts):
        self.documents.extend(texts)
        return [[float(text == "left"), float(text != "left"), *([0.] * (self.dimension - 2))] for text in texts]

    async def close(self):
        self.closed = True


class Asymmetric(Documents):
    async def embed_query(self, text):
        self.queries.append(text)
        return [0., 1., *([0.] * (self.dimension - 2))]


@pytest.mark.parametrize("cached", [False, True])
async def test_public_custom_provider_reopens_and_records_real_identity(config, user, monkeypatch, cached):
    def forbidden(_):
        raise AssertionError("Explicit provider must bypass configured factory")
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", forbidden)
    underlying = Asymmetric()
    provider = CachedEmbeddingProvider(underlying) if cached else underlying
    assert isinstance(provider, EmbeddingProvider) and isinstance(provider, QueryEmbeddingProvider)
    original = config.model_dump_json()
    async with MemoryEngine.open(config, embedding_provider=provider) as engine:
        assert engine._config.embedding.provider == "custom"
        assert engine._config.embedding.model_name == underlying.model_name
        assert config.model_dump_json() == original
        await engine.store("left", user_id=user)
        await engine.store("right", user_id=user)
        response = await engine.retrieve("left", user_id=user, include_cross_scope=False)
        candidates = {c.node.content: c for c in response.results}
        assert candidates["right"].semantic_score > candidates["left"].semantic_score
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert receipt.execution.features["embedding"]["model"] == underlying.model_name
        assert receipt.execution.features["embedding"]["version"] == underlying.model_version
        assert receipt.execution.features["embedding"]["query_encoding"] == "embed_query"
        effective = engine._config
    assert not underlying.closed
    fresh = Asymmetric()
    async with MemoryEngine.open(effective, embedding_provider=fresh) as engine:
        assert len(await engine.query_nodes(user_id=user)) == 2
        hits = await engine._vector_index.search("left", user)
        assert (await engine.get_node(hits[0]["node_id"])).content == "right"
        assert fresh.documents == [] and fresh.queries == ["left"]
    with pytest.raises(ValueError, match="requires embedding_provider"):
        await MemoryEngine.create(effective)


def test_sync_client_accepts_custom_provider(config, user):
    provider = Asymmetric()
    with MemoryClient(config=config, embedding_provider=provider) as client:
        client.store("left", user_id=user)
        client.store("right", user_id=user)
        response = client.retrieve("choose", user_id=user, include_cross_scope=False)
        assert response.results[0].node.content == "right"
        assert client._config.embedding.model_name == provider.model_name
    assert provider.queries and not provider.closed


async def test_query_cache_is_separate_and_results_have_private_buffers():
    provider = Asymmetric()
    cached = CachedEmbeddingProvider(provider)
    document = await cached.embed(["left"])
    query = await encode_query(cached, "left")
    assert document[0][:2] == [1., 0.] and query[:2] == [0., 1.]
    query[0] = 99
    assert (await encode_query(cached, "left"))[:2] == [0., 1.]
    assert (await cached.embed(["left"]))[0][:2] == [1., 0.]
    assert provider.documents == provider.queries == ["left"]


async def test_legacy_provider_reuses_document_cache_for_queries():
    provider = Documents()
    assert isinstance(provider, EmbeddingProvider) and not isinstance(provider, QueryEmbeddingProvider)
    cached = CachedEmbeddingProvider(provider)
    vector = (await cached.embed(["left"]))[0]
    assert await encode_query(cached, "left") == vector
    assert provider.documents == ["left"]


async def test_query_encoder_failure_never_silently_uses_document_encoding():
    provider = Asymmetric()
    provider.embed_query = AsyncMock(side_effect=RuntimeError("authored query outage"))
    with pytest.raises(RuntimeError, match="query outage"):
        await encode_query(CachedEmbeddingProvider(provider), "left")
    assert provider.documents == []


@pytest.mark.parametrize("change", [{"dimension": 0}, {"dimension": True}, {"model_name": ""},
                                    {"model_version": " "}, {"embed": None}, {"embed_query": "wrong"}])
async def test_invalid_provider_rejected_before_database_creation(tmp_path, change):
    values = {"model_name": "authored", "model_version": "1", "dimension": 2, "embed": AsyncMock()}
    provider = SimpleNamespace(**{**values, **change})
    path = tmp_path / "never-created" / "memory.duckdb"
    with pytest.raises(ValueError, match="Embedding|embedding|embed_query"):
        await MemoryEngine.create(PRMEConfig(db_path=str(path), database_url=None), embedding_provider=provider)
    assert not path.parent.exists()


async def test_provider_dimension_controls_new_local_index(tmp_path):
    provider = Documents()
    provider.dimension = 2
    cfg = PRMEConfig(db_path=str(tmp_path / "memory.duckdb"), vector_path=str(tmp_path / "vectors.usearch"),
                     lexical_path=str(tmp_path / "lexical"), database_url=None,
                     organizer={"opportunistic_enabled": False})
    async with MemoryEngine.open(cfg, embedding_provider=provider) as engine:
        assert engine._config.embedding.dimension == 2 and engine._vector_index._index.ndim == 2
        await engine.store("left", user_id="u")
        assert len(await engine._vector_index.search("left", "u")) == 1


async def test_custom_encoding_version_change_reports_mismatch(config, user):
    async with MemoryEngine.open(config, embedding_provider=Documents()) as engine:
        await engine.store("left", user_id=user)
    changed = Documents()
    changed.model_version = "document-query-recipe-2"
    async with MemoryEngine.open(config, embedding_provider=changed) as engine:
        response = await engine.retrieve("left", user_id=user, include_cross_scope=False)
        assert response.metadata.embedding_mismatch
        assert response.results and response.results[0].node.content == "left"
