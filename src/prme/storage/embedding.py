"""Embedding provider abstraction and implementations.

Defines the EmbeddingProvider Protocol for swappable embedding backends
and provides a FastEmbed implementation using ONNX-based local models.
"""

from __future__ import annotations

import importlib.metadata
from typing import Protocol, runtime_checkable


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
