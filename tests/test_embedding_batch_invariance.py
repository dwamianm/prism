"""Embedding values must not depend on neighboring texts or cache residency."""

import numpy as np
import pytest

from prme.storage.embedding import CachedEmbeddingProvider, FastEmbedProvider


class PaddingSensitiveModel:
    """Model the observed dependence of quantized ONNX output on padding."""

    def embed(self, texts, batch_size=256):
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            padding = max(map(len, batch))
            for text in batch:
                yield np.array([len(text), padding / 10000])


@pytest.fixture
def provider():
    value = FastEmbedProvider(dimension=2)
    value._model = PaddingSensitiveModel()
    return value


async def test_vectors_do_not_depend_on_neighbors_or_input_order(provider):
    texts = ["short", "a substantially longer source passage", "medium text"]
    single = [(await provider.embed([text]))[0] for text in texts]
    assert await provider.embed(texts) == single
    assert await provider.embed(list(reversed(texts))) == list(reversed(single))
    assert await provider.embed([]) == []


async def test_partial_cache_hits_do_not_change_the_same_request(provider):
    texts = ["short", "a substantially longer source passage", "medium text"]
    cold = CachedEmbeddingProvider(provider)
    warm = CachedEmbeddingProvider(provider)
    await warm.embed(texts[1:])
    assert await cold.embed(texts) == await warm.embed(texts)
