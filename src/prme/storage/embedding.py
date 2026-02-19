"""Embedding provider abstraction and implementations.

Defines the EmbeddingProvider Protocol for swappable embedding backends
and provides FastEmbed (local ONNX) and OpenAI (API-based) implementations,
plus a factory function for config-driven provider selection.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prme.config import EmbeddingConfig


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding text into dense vectors.

    Implementations must provide model metadata (name, version, dimension)
    and a synchronous embed method that converts text to float vectors.
    """

    @property
    def model_name(self) -> str:
        """Identifier for the embedding model (e.g., 'BAAI/bge-small-en-v1.5')."""
        ...

    @property
    def model_version(self) -> str:
        """Version string for the embedding model or provider library."""
        ...

    @property
    def dimension(self) -> int:
        """Dimensionality of the output embedding vectors."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into dense float vectors.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors, one per input text.
        """
        ...


class FastEmbedProvider:
    """EmbeddingProvider using FastEmbed (ONNX-based local inference).

    Lazily initializes the underlying TextEmbedding model on first
    embed() call to avoid blocking construction with model downloads.

    Args:
        model_name: HuggingFace model identifier. Defaults to 'BAAI/bge-small-en-v1.5'.
        cache_dir: Optional directory for cached model files.
        dimension: Vector dimension for the chosen model. Defaults to 384.
    """

    # Known model dimensions for common models
    _KNOWN_DIMENSIONS: dict[str, int] = {
        "BAAI/bge-small-en-v1.5": 384,
        "BAAI/bge-base-en-v1.5": 768,
        "BAAI/bge-large-en-v1.5": 1024,
        "sentence-transformers/all-MiniLM-L6-v2": 384,
    }

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        *,
        cache_dir: str | None = None,
        dimension: int | None = None,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._dimension = dimension or self._KNOWN_DIMENSIONS.get(model_name, 384)
        self._model = None  # Lazy-initialized

    @property
    def model_name(self) -> str:
        """Return the configured embedding model identifier."""
        return self._model_name

    @property
    def model_version(self) -> str:
        """Return the fastembed library version as the model version."""
        try:
            return f"fastembed-{importlib.metadata.version('fastembed')}"
        except importlib.metadata.PackageNotFoundError:
            return "fastembed-unknown"

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension."""
        return self._dimension

    def _ensure_model(self) -> None:
        """Lazily initialize the TextEmbedding model on first use."""
        if self._model is None:
            from fastembed import TextEmbedding

            kwargs: dict = {"model_name": self._model_name}
            if self._cache_dir is not None:
                kwargs["cache_dir"] = self._cache_dir
            self._model = TextEmbedding(**kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using FastEmbed's ONNX inference.

        The model is downloaded and loaded on the first call.
        Subsequent calls reuse the loaded model.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors (384-dimensional for bge-small-en-v1.5).
        """
        self._ensure_model()
        assert self._model is not None
        # TextEmbedding.embed() returns a generator of numpy arrays
        return [embedding.tolist() for embedding in self._model.embed(texts)]


class OpenAIEmbeddingProvider:
    """EmbeddingProvider using OpenAI's embedding API.

    Lazily initializes the AsyncOpenAI client on first embed() call
    to avoid requiring API keys at construction time.

    The embed() method is synchronous (required by the EmbeddingProvider
    Protocol) and internally runs the async OpenAI call via asyncio.run()
    in a new event loop, since VectorIndex calls embed() from
    asyncio.to_thread().

    Args:
        model_name: OpenAI embedding model identifier.
            Defaults to 'text-embedding-3-small'.
        api_key: Optional OpenAI API key. If None, the client will
            use the OPENAI_API_KEY environment variable.
        dimension: Output vector dimension. Defaults to the model's
            known dimension (1536 for text-embedding-3-small).
    """

    _KNOWN_DIMENSIONS: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        *,
        api_key: str | None = None,
        dimension: int | None = None,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._dimension = dimension or self._KNOWN_DIMENSIONS.get(model_name, 1536)
        self._client = None  # Lazy-initialized AsyncOpenAI

    @property
    def model_name(self) -> str:
        """Return the configured OpenAI embedding model identifier."""
        return self._model_name

    @property
    def model_version(self) -> str:
        """Return a version string combining provider and model name."""
        return f"openai-{self._model_name}"

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension."""
        return self._dimension

    def _ensure_client(self):
        """Lazily initialize the AsyncOpenAI client on first use."""
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict = {}
            if self._api_key is not None:
                kwargs["api_key"] = self._api_key
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using the OpenAI embedding API.

        Synchronous wrapper required by the EmbeddingProvider Protocol.
        Internally runs the async API call via asyncio.run().

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors with the configured dimension.
        """
        return asyncio.run(self._embed_async(texts))

    async def _embed_async(self, texts: list[str]) -> list[list[float]]:
        """Async implementation of embedding via OpenAI API.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors from the API response.
        """
        client = self._ensure_client()
        response = await client.embeddings.create(
            model=self._model_name,
            input=texts,
            dimensions=self._dimension,
        )
        return [item.embedding for item in response.data]


def create_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Factory function to create the appropriate embedding provider.

    Dispatches based on config.provider to return a FastEmbedProvider
    or OpenAIEmbeddingProvider instance configured from the given config.

    Args:
        config: Embedding configuration specifying provider and model settings.

    Returns:
        An EmbeddingProvider instance matching the configured provider.

    Raises:
        ValueError: If the configured provider is not recognized.
    """
    if config.provider == "fastembed":
        return FastEmbedProvider(
            model_name=config.model_name,
            dimension=config.dimension,
        )
    elif config.provider == "openai":
        return OpenAIEmbeddingProvider(
            model_name=config.model_name,
            api_key=config.api_key,
            dimension=config.dimension,
        )
    else:
        raise ValueError(f"Unknown embedding provider: {config.provider}")
