"""Embedding provider abstraction and implementations.

Defines the EmbeddingProvider Protocol for swappable embedding backends
and provides FastEmbed (local ONNX) and OpenAI (API-based) implementations,
a CachedEmbeddingProvider wrapper for LRU embedding caching,
plus a factory function for config-driven provider selection.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import math
import os
import threading
from collections import OrderedDict
from functools import lru_cache
from numbers import Real
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prme.config import EmbeddingConfig
    from fastembed import TextEmbedding


class EmbeddingVersionMismatchError(ValueError):
    """Stored vectors cannot be compared with the configured embedding model."""


_KNOWN_FASTEMBED_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "mixedbread-ai/mxbai-embed-large-v1": 1024,
    "nomic-ai/nomic-embed-text-v1.5": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
}


@lru_cache(maxsize=None)
def fastembed_model_dimension(model_name: str) -> int:
    """Resolve a registered FastEmbed model dimension without loading weights."""
    known = _KNOWN_FASTEMBED_DIMENSIONS.get(model_name)
    if known is not None:
        return known
    from fastembed import TextEmbedding

    for description in TextEmbedding.list_supported_models():
        if description.get("model") != model_name:
            continue
        dimension = description.get("dim")
        if type(dimension) is int and dimension > 0:
            return dimension
        break
    raise ValueError(
        f"FastEmbed model {model_name!r} has no registered dimension; "
        "set dimension explicitly"
    )


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding text into dense vectors.

    Implementations must provide model metadata (name, version, dimension)
    and an async embed method that converts text to float vectors.
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

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into dense float vectors.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors, one per input text.
        """
        ...


@runtime_checkable
class QueryEmbeddingProvider(EmbeddingProvider, Protocol):
    """Optional asymmetric query encoder paired with embed()'s document space.

    model_version must identify both document and query encoding recipes.
    Existing providers implementing only embed() remain supported unchanged.
    """

    async def embed_query(self, text: str) -> list[float]:
        """Encode one search query in the compatible document vector space."""
        ...


def validate_embedding_provider(provider: EmbeddingProvider) -> tuple[str, str, int]:
    """Validate non-secret metadata without initializing a model or calling it."""
    name = getattr(provider, "model_name", None)
    version = getattr(provider, "model_version", None)
    dimension = getattr(provider, "dimension", None)
    if not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip():
        raise ValueError("Embedding provider needs nonempty model_name and model_version")
    if type(dimension) is not int or dimension < 1:
        raise ValueError("Embedding provider dimension must be a positive integer")
    if not callable(getattr(provider, "embed", None)):
        raise ValueError("Embedding provider needs an async embed(texts) method")
    query = getattr(provider, "embed_query", None)
    if query is not None and not callable(query):
        raise ValueError("Optional embed_query must be an async callable")
    return name, version, dimension


def has_query_encoder(provider: EmbeddingProvider | None) -> bool:
    if isinstance(provider, CachedEmbeddingProvider):
        return has_query_encoder(provider._provider)
    return callable(getattr(provider, "embed_query", None))


def _validated_vectors(vectors, *, count: int, dimension: int) -> list[list[float]]:
    """Own and validate the complete response before any cache/index admission."""
    try:
        values = list(vectors)
        if len(values) != count:
            raise ValueError("Embedding provider must return exactly one vector per input")
        result = []
        for vector in values:
            items = list(vector)
            if len(items) != dimension:
                raise ValueError("Embedding provider returned an unexpected vector dimension")
            if any(isinstance(v, bool) or not isinstance(v, Real) for v in items):
                raise ValueError("Embedding provider vectors must contain finite float32-compatible numbers")
            converted = [float(v) for v in items]
            if any(not math.isfinite(v) or abs(v) > 3.4028234663852886e38 for v in converted):
                raise ValueError("Embedding provider vectors must contain finite float32-compatible numbers")
            result.append(converted)
        return result
    except (TypeError, OverflowError) as exc:
        raise ValueError("Embedding provider returned a malformed vector response") from exc


async def encode_texts(provider: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
    """Validate cardinality, dimensions and values without exposing input text."""
    identity = validate_embedding_provider(provider)
    count = len(texts)
    if not count:
        return []
    vectors = await provider.embed(list(texts))
    if validate_embedding_provider(provider) != identity:
        raise ValueError("Embedding provider identity changed during encoding")
    return _validated_vectors(vectors, count=count, dimension=identity[2])


async def encode_query(provider: EmbeddingProvider, text: str) -> list[float]:
    """Use an optional query encoder and validate the returned vector."""
    encoder = getattr(provider, "embed_query", None)
    if callable(encoder):
        identity = validate_embedding_provider(provider)
        vector = await encoder(text)
        if validate_embedding_provider(provider) != identity:
            raise ValueError("Embedding provider identity changed during encoding")
        return _validated_vectors([vector], count=1, dimension=identity[2])[0]
    return (await encode_texts(provider, [text]))[0]


class FastEmbedProvider:
    """EmbeddingProvider using FastEmbed (ONNX-based local inference).

    Lazily initializes the underlying TextEmbedding model on first
    embed() call to avoid blocking construction with model downloads.

    The synchronous ONNX inference is wrapped in asyncio.to_thread()
    to avoid blocking the event loop.

    Args:
        model_name: HuggingFace model identifier. Defaults to 'BAAI/bge-small-en-v1.5'.
        cache_dir: Optional directory for cached model files.
        dimension: Vector dimension for the chosen model. Defaults to 384.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        *,
        cache_dir: str | None = None,
        dimension: int | None = None,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        if dimension is not None and (
            isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1
        ):
            raise ValueError("FastEmbed dimension must be a positive integer")
        self._dimension = (
            dimension
            if dimension is not None
            else fastembed_model_dimension(model_name)
        )
        self._model: TextEmbedding | None = None  # Lazy-initialized
        self._initialization_lock = threading.Lock()

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
        if self._model is not None:
            return
        # embed() runs in native worker threads. Hold this guard through model
        # construction, including after an awaiting caller is cancelled. A
        # failed construction leaves None and permits a later explicit retry.
        with self._initialization_lock:
            if self._model is not None:
                return
            # Set before FastEmbed imports ONNX Runtime: its optional native
            # telemetry uploader can outlive its shutdown mutexes on macOS.
            # Respect a host application's explicit telemetry setting.
            os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
            from fastembed import TextEmbedding

            kwargs: dict = {"model_name": self._model_name}
            if self._cache_dir is not None:
                kwargs["cache_dir"] = self._cache_dir
            self._model = TextEmbedding(**kwargs)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Synchronous embedding using FastEmbed's ONNX inference.

        The model is downloaded and loaded on the first call.
        Subsequent calls reuse the loaded model.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors (384-dimensional for bge-small-en-v1.5).
        """
        self._ensure_model()
        assert self._model is not None
        # Quantized ONNX output can vary with batch padding. In particular,
        # caching changes which neighbors remain in a miss batch. Embed each
        # text with the same inference shape so cache residency and ingestion
        # grouping cannot change its vector. This trades short-text throughput
        # for reproducibility; the model weights and vector space are unchanged.
        return [
            embedding.tolist()
            for embedding in self._model.embed(texts, batch_size=1)
        ]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts asynchronously using FastEmbed's ONNX inference.

        Wraps the synchronous ONNX inference in asyncio.to_thread()
        to avoid blocking the event loop.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors (384-dimensional for bge-small-en-v1.5).
        """
        return await asyncio.to_thread(self._embed_sync, texts)


class OpenAIEmbeddingProvider:
    """EmbeddingProvider using OpenAI's embedding API.

    Lazily initializes the AsyncOpenAI client on first embed() call
    to avoid requiring API keys at construction time.

    The embed() method is natively async, using the AsyncOpenAI client
    directly. No asyncio.run() wrapper is needed.

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

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using the OpenAI embedding API.

        Natively async using the AsyncOpenAI client. No asyncio.run()
        wrapper needed -- safe to call from any async context.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors with the configured dimension.
        """
        client = self._ensure_client()
        response = await client.embeddings.create(
            model=self._model_name,
            input=texts,
            dimensions=self._dimension,
        )
        return [item.embedding for item in response.data]


class CachedEmbeddingProvider:
    """EmbeddingProvider wrapper that caches embeddings with LRU eviction.

    Wraps any EmbeddingProvider and caches results keyed by SHA-256
    hash of the input text. Cache hits skip the provider entirely;
    only uncached texts are batched to the underlying provider.

    Args:
        provider: The underlying EmbeddingProvider to wrap.
        maxsize: Maximum number of cached embeddings before LRU eviction.
            Defaults to 512.
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        maxsize: int = 512,
    ) -> None:
        self._provider = provider
        self._maxsize = maxsize
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()

    @property
    def model_name(self) -> str:
        """Delegate to wrapped provider."""
        return self._provider.model_name

    @property
    def model_version(self) -> str:
        """Delegate to wrapped provider."""
        return self._provider.model_version

    @property
    def dimension(self) -> int:
        """Delegate to wrapped provider."""
        return self._provider.dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with LRU caching.

        For each input text, computes a SHA-256 hash as cache key.
        Cached embeddings are returned immediately; uncached texts
        are batched to the wrapped provider. Results preserve input order.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors, one per input text.
        """
        # Compute cache keys for all texts
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]

        # Separate cached hits from uncached misses
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, key in enumerate(keys):
            if key in self._cache:
                # Move to end for LRU ordering
                self._cache.move_to_end(key)
                results[i] = list(self._cache[key])
            else:
                uncached_indices.append(i)
                uncached_texts.append(texts[i])

        # Batch embed uncached texts
        if uncached_texts:
            new_embeddings = await encode_texts(self._provider, uncached_texts)
            for j, idx in enumerate(uncached_indices):
                # Providers and callers may reuse or modify list buffers. The
                # cache owns an immutable snapshot; each result owns its list.
                embedding = list(new_embeddings[j])
                cache_key = keys[idx]
                results[idx] = embedding
                # Store in cache
                self._cache[cache_key] = tuple(embedding)
                self._cache.move_to_end(cache_key)
                # Evict oldest if over maxsize
                while len(self._cache) > self._maxsize:
                    self._cache.popitem(last=False)

        # All slots should be filled
        return results  # type: ignore[return-value]

    async def embed_query(self, text: str) -> list[float]:
        """Cache query vectors separately only when the provider distinguishes them."""
        if not has_query_encoder(self._provider):
            return (await self.embed([text]))[0]
        key = "query:" + hashlib.sha256(text.encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return list(self._cache[key])
        vector = tuple(await encode_query(self._provider, text))
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
        return list(vector)


def create_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Factory function to create the appropriate embedding provider.

    Dispatches based on config.provider to return a FastEmbedProvider
    or OpenAIEmbeddingProvider instance configured from the given config,
    wrapped in a CachedEmbeddingProvider for LRU caching.

    Args:
        config: Embedding configuration specifying provider and model settings.

    Returns:
        An EmbeddingProvider instance matching the configured provider,
        wrapped in CachedEmbeddingProvider.

    Raises:
        ValueError: If the configured provider is not recognized.
    """
    provider: EmbeddingProvider
    if config.provider == "fastembed":
        provider = FastEmbedProvider(
            model_name=config.model_name,
            dimension=config.dimension,
        )
    elif config.provider == "openai":
        provider = OpenAIEmbeddingProvider(
            model_name=config.model_name,
            api_key=(
                config.api_key.get_secret_value()
                if config.api_key is not None
                else None
            ),
            dimension=config.dimension,
        )
    else:
        raise ValueError(f"Unknown embedding provider: {config.provider}")
    return CachedEmbeddingProvider(provider)
