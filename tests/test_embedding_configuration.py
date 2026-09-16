"""Built-in embedding configuration should fail early or resolve coherently."""

from __future__ import annotations

import pytest

from prme.config import EmbeddingConfig, PRMEConfig
from prme.storage.embedding import FastEmbedProvider


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
