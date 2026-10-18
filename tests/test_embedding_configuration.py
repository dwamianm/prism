"""Built-in embedding configuration should fail early or resolve coherently."""

from __future__ import annotations

import pytest

from prme.config import EmbeddingConfig, PRMEConfig
from prme.storage.embedding import FastEmbedProvider, create_embedding_provider


def test_fastembed_dimension_is_inferred_from_model_name():
    config = EmbeddingConfig(model_name="mixedbread-ai/mxbai-embed-large-v1")

    assert config.dimension == 1024


def test_openai_provider_selects_a_coherent_default_model():
    config = EmbeddingConfig(provider="openai")

    assert config.model_name == "text-embedding-3-small"
    assert config.dimension == 1536


def test_openai_dimension_is_inferred_from_explicit_model():
    config = EmbeddingConfig(
        provider="openai",
        model_name="text-embedding-3-large",
    )

    assert config.dimension == 3072


def test_unknown_model_requires_an_explicit_dimension():
    with pytest.raises(ValueError, match="set dimension explicitly"):
        EmbeddingConfig(model_name="example/not-registered")

    config = EmbeddingConfig(
        model_name="example/not-registered",
        dimension=17,
    )
    assert config.dimension == 17


def test_nested_environment_model_infers_dimension(monkeypatch):
    monkeypatch.setenv(
        "PRME_EMBEDDING__MODEL_NAME",
        "mixedbread-ai/mxbai-embed-large-v1",
    )

    assert PRMEConfig(_env_file=None).embedding.dimension == 1024


def test_direct_fastembed_provider_infers_dimension_without_loading_model():
    provider = FastEmbedProvider(model_name="nomic-ai/nomic-embed-text-v1.5")

    assert provider.dimension == 768
    assert provider._model is None


@pytest.mark.parametrize("dimension", [True, 0, -1])
def test_direct_fastembed_provider_rejects_invalid_dimension(dimension):
    with pytest.raises(ValueError, match="positive integer"):
        FastEmbedProvider(dimension=dimension)


def test_fastembed_threads_default_to_the_runtime_choice():
    config = EmbeddingConfig()

    assert config.threads is None
    assert create_embedding_provider(config)._provider._threads is None


def test_fastembed_threads_reach_the_provider(monkeypatch):
    monkeypatch.setenv("PRME_EMBEDDING__THREADS", "2")
    config = PRMEConfig(_env_file=None).embedding

    assert config.threads == 2
    assert create_embedding_provider(config)._provider._threads == 2
    assert create_embedding_provider(EmbeddingConfig(threads=1))._provider._threads == 1


@pytest.mark.parametrize("threads", [True, 0, -3])
def test_fastembed_threads_must_be_a_positive_integer(threads):
    with pytest.raises(ValueError):
        EmbeddingConfig(threads=threads)


def test_fastembed_threads_read_the_standalone_environment_form(monkeypatch):
    monkeypatch.setenv("PRME_EMBEDDING_THREADS", "3")

    assert EmbeddingConfig(_env_file=None).threads == 3


def test_unset_fastembed_threads_leave_configuration_dumps_unchanged():
    from pathlib import Path

    from benchmarks.diagnostics.product_packing import gate_config
    from benchmarks.integrations.gpt54_baselines import _check_defaults_build
    from benchmarks.retrieval_eval import provenance

    assert "threads" not in EmbeddingConfig().model_dump()
    assert "threads" not in PRMEConfig(_env_file=None).model_dump(mode="json")["embedding"]
    assert EmbeddingConfig(threads=2).model_dump()["threads"] == 2
    # An ingest() build recorded before the setting existed still holds the defaults.
    defaults = provenance(gate_config(Path("{pack}")))["engine_config"]
    assert "threads" not in defaults["embedding"]
    _check_defaults_build(Path("recorded"), {"provenance": {"engine_config": defaults}})
    threaded = {**defaults, "embedding": {**defaults["embedding"], "threads": 1}}
    with pytest.raises(ValueError, match="embedding"):
        _check_defaults_build(Path("threaded"), {"provenance": {"engine_config": threaded}})
