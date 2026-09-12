"""Returned vectors and concurrent first use cannot corrupt provider state."""

from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from prme.storage.embedding import CachedEmbeddingProvider, FastEmbedProvider


class Provider:
    model_name = "authored"
    model_version = "1"
    dimension = 2

    def __init__(self):
        self.vector = [0.25, 0.75]
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [self.vector for _ in texts]


async def test_mutating_returned_vector_cannot_change_future_cache_results():
    provider = Provider()
    cache = CachedEmbeddingProvider(provider)
    cold = await cache.embed(["source"])
    cold[0][0] = 99
    warm = await cache.embed(["source", "source"])
    assert warm == [[.25, .75], [.25, .75]]
    warm[0][1] = 88
    assert warm[1] == [.25, .75]
    assert await cache.embed(["source"]) == [[.25, .75]]
    assert provider.calls == 1


async def test_provider_owned_buffers_cannot_change_the_cache_after_return():
    provider = Provider()
    cache = CachedEmbeddingProvider(provider)
    returned = await cache.embed(["source"])
    provider.vector[0] = -5
    assert returned == [[.25, .75]]
    assert await cache.embed(["source"]) == [[.25, .75]]


def test_concurrent_first_use_constructs_one_native_model(monkeypatch):
    import builtins
    original_import = builtins.__import__
    started = threading.Event()
    release = threading.Event()
    duplicated = threading.Event()
    models = []
    def construct(**kwargs):
        model = object()
        models.append(model)
        if len(models) > 1:
            duplicated.set()
        started.set()
        assert release.wait(5)
        return model
    def load(name, *args, **kwargs):
        if name == "fastembed":
            return SimpleNamespace(TextEmbedding=construct)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", load)
    provider = FastEmbedProvider()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(provider._ensure_model)
        assert started.wait(2)
        second = pool.submit(provider._ensure_model)
        try:
            # The old implementation enters both native constructors while the
            # first is held. The fixed second call waits for that first model.
            duplicated.wait(.25)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert len(models) == 1 and provider._model is models[0]


def test_failed_initialization_can_retry(monkeypatch):
    import builtins
    original_import = builtins.__import__
    attempts = 0
    model = object()
    def construct(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("Authored initialization failure")
        return model
    def load(name, *args, **kwargs):
        return SimpleNamespace(TextEmbedding=construct) if name == "fastembed" else original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", load)
    provider = FastEmbedProvider()
    with pytest.raises(OSError):
        provider._ensure_model()
    provider._ensure_model()
    assert provider._model is model and attempts == 2
