"""Local embedding initialization configures native runtime before import."""

import builtins
import os
from types import SimpleNamespace

import pytest

from prme.storage.embedding import FastEmbedProvider


@pytest.mark.parametrize("explicit", [None, "0", "1"])
def test_native_telemetry_default_is_set_before_dependency_load(monkeypatch, explicit):
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "test-placeholder")
    if explicit is None:
        monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("ORT_DISABLE_TELEMETRY", explicit)
    original_import = builtins.__import__
    models = []
    def load_model(**kwargs):
        model = object()
        models.append(model)
        return model
    def guarded_import(name, *args, **kwargs):
        if name == "fastembed":
            assert os.environ["ORT_DISABLE_TELEMETRY"] == (explicit or "1")
            return SimpleNamespace(TextEmbedding=load_model)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    provider = FastEmbedProvider()
    provider._ensure_model()
    provider._ensure_model()
    assert len(models) == 1 and provider._model is models[0]


def _constructor_arguments(monkeypatch) -> list[dict]:
    """Replace FastEmbed's model class and return the arguments of each construction."""
    original_import = builtins.__import__
    calls = []
    def load(name, *args, **kwargs):
        if name == "fastembed":
            return SimpleNamespace(TextEmbedding=lambda **arguments: calls.append(arguments) or object())
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", load)
    return calls


@pytest.mark.parametrize("threads", [None, 1, 4])
def test_a_thread_count_reaches_the_model_only_when_set(monkeypatch, threads):
    calls = _constructor_arguments(monkeypatch)
    FastEmbedProvider(threads=threads)._ensure_model()
    expected = {"model_name": "BAAI/bge-small-en-v1.5"}
    if threads is not None:
        expected["threads"] = threads
    assert calls == [expected]


@pytest.mark.parametrize("threads", [True, 0, -1, 1.5, "2"])
def test_an_invalid_thread_count_is_rejected(threads):
    with pytest.raises(ValueError, match="threads must be a positive integer"):
        FastEmbedProvider(threads=threads)


def test_the_fastembed_version_is_read_from_package_metadata_once(monkeypatch):
    import importlib.metadata

    from prme.storage import embedding

    reads = []
    def version(name):
        reads.append(name)
        return "9.9.9"
    embedding._fastembed_version.cache_clear()
    monkeypatch.setattr(importlib.metadata, "version", version)
    try:
        providers = [FastEmbedProvider(), FastEmbedProvider(threads=1)]
        assert {provider.model_version for provider in providers for _ in range(500)} == {"fastembed-9.9.9"}
        assert reads == ["fastembed"]
    finally:
        embedding._fastembed_version.cache_clear()


def test_a_missing_fastembed_distribution_is_reported_as_unknown(monkeypatch):
    import importlib.metadata

    from prme.storage import embedding

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)
    embedding._fastembed_version.cache_clear()
    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        assert FastEmbedProvider().model_version == "fastembed-unknown"
    finally:
        embedding._fastembed_version.cache_clear()
