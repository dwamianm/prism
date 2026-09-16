"""Malformed provider batches cannot poison retries or silently lose inputs."""

import pytest

from prme import MemoryEngine
from prme.storage.embedding import CachedEmbeddingProvider, encode_query, encode_texts
from tests import test_durable_ingestion as fixtures
from tests.test_custom_embedding_provider import Asymmetric

config = fixtures.config
user = fixtures.user


class FlakyProvider:
    model_name = "authored"
    model_version = "1"
    dimension = 2

    def __init__(self, malformed):
        self.malformed = malformed
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        return self.malformed if len(self.calls) == 1 else [[.25, .75] for _ in texts]


@pytest.mark.parametrize("malformed", [
    [[.1, .9]], [[.1, .9], [.2, .8], [.3, .7]],
    [[.1, .9], [float("nan"), .8]], [[.1, .9], [.2]],
    [[.1, .9], [".2", ".8"]], [[.1, .9], [float("inf"), .8]],
])
async def test_entire_batch_validated_before_any_cache_admission(malformed):
    provider = FlakyProvider(malformed)
    cache = CachedEmbeddingProvider(provider)
    with pytest.raises(ValueError, match="Embedding provider"):
        await cache.embed(["first", "second"])
    assert await cache.embed(["first", "second"]) == [[.25, .75], [.25, .75]]
    assert provider.calls == [["first", "second"], ["first", "second"]]
    assert await cache.embed(["second", "first"]) == [[.25, .75], [.25, .75]]
    assert len(provider.calls) == 2


async def test_optional_query_failure_does_not_poison_cache():
    class QueryProvider(FlakyProvider):
        async def embed_query(self, text):
            self.calls.append(text)
            return [float("nan"), .8] if len(self.calls) == 1 else [.25, .75]
    provider = QueryProvider(None)
    cache = CachedEmbeddingProvider(provider)
    with pytest.raises(ValueError, match="Embedding provider"):
        await cache.embed_query("question")
    assert await cache.embed_query("question") == [.25, .75]
    assert provider.calls == ["question", "question"]


@pytest.mark.parametrize("vectors", [[], [[.1, .9], [.2, .8]], [[float("nan"), .8]], [[.1]]])
async def test_uncached_legacy_query_requires_one_valid_vector(vectors):
    with pytest.raises(ValueError, match="Embedding provider"):
        await encode_query(FlakyProvider(vectors), "question")


async def test_identity_change_during_encoding_does_not_admit_vectors():
    class Changing(FlakyProvider):
        async def embed(self, texts):
            self.model_version = "2"
            return [[.25, .75] for _ in texts]
    with pytest.raises(ValueError, match="identity changed"):
        await encode_texts(Changing(None), ["source"])


async def test_numeric_arrays_are_owned_and_empty_batch_needs_no_provider_call():
    import numpy as np
    vectors = np.asarray([[.25, .75]], dtype=np.float32)
    provider = FlakyProvider(vectors)
    assert await encode_texts(provider, []) == [] and not provider.calls
    result = await encode_texts(provider, ["source"])
    vectors[0, 0] = 99
    assert result == [[.25, .75]]


@pytest.mark.parametrize("malformed", ["extra", "short", "nan", "overflow"])
async def test_backend_replacement_rejects_bad_response_and_preserves_old_vector(config, user, malformed):
    class Provider(Asymmetric):
        bad = False
        async def embed(self, texts):
            vectors = await super().embed(texts)
            if self.bad:
                if malformed == "extra":
                    return vectors + vectors
                if malformed == "short":
                    return [v[:-1] for v in vectors]
                vectors[0][0] = float("nan") if malformed == "nan" else 1e100
            return vectors
    provider = Provider()
    async with MemoryEngine.open(config, embedding_provider=provider) as engine:
        await engine.store("right", user_id=user)
        node = (await engine.query_nodes(user_id=user))[0]
        index = engine._vector_index
        before = await index.search("question", user)
        provider.bad = True
        with pytest.raises(ValueError, match="Embedding provider"):
            await index.index(str(node.id), "replacement", user, replace=True)
        assert await index.search("question", user) == before
        assert (await engine.get_node(str(node.id))).content == "right"
