"""Model-agnostic vector storage (``enable_multi_model_embeddings``, dwamianm/prism#368).

A stored vector means something only under the model that made it. With the option on, a store keeps its
vectors by node, model name and model version, one index per model (``VectorIndex`` with one USearch file per
model on DuckDB, ``PgModelVectorIndex`` over the ``node_embeddings`` table on PostgreSQL), and
``MultiModelVectorIndex`` stands in front of them with the interface the engine already uses:

- Searches read only the active model's index (``embedding.model_name``).
- Every write goes to the active index and to the index of each of ``embedding.additional_models`` (dual
  write), so a new model can be built while the store keeps serving. A write to an additional model that fails
  is logged and left for ``reembed()``, which fills any node a model lacks.
- A store whose vectors were all made by another model than the configured one is not mixed: writes are
  refused with an error that names both models, and a search reports it as ``embedding_mismatch`` (the
  existing flag) while retrieval metadata also carries ``embedding_incomplete`` with the counts.

With the option off none of this is constructed, and the original index classes behave as they always did.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from prme.storage.embedding import (
    EmbeddingProvider,
    EmbeddingVersionMismatchError,
    FastEmbedProvider,
    fastembed_model_dimension,
)

logger = logging.getLogger(__name__)

# Lifecycle states whose nodes carry vectors, as in rebuild_indexes() and in vector search.
from prme.types import ACTIVE_LIFECYCLE_STATES  # noqa: E402

ACTIVE_STATES = tuple(state.value for state in ACTIVE_LIFECYCLE_STATES)


@dataclass(frozen=True, order=True)
class ModelIdentity:
    """What makes a stored vector comparable to another: the model's name and version, and the dimension."""

    name: str
    version: str
    dimension: int

    @classmethod
    def of(cls, provider: EmbeddingProvider) -> "ModelIdentity":
        return cls(provider.model_name, provider.model_version, provider.dimension)

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.version)

    def label(self) -> str:
        return f"{self.name} (version {self.version}, {self.dimension} dimensions)"

    def slug(self) -> str:
        """A short file-name-safe name that is the same for the same model and version."""
        readable = re.sub(r"[^A-Za-z0-9]+", "-", self.name).strip("-").lower()[:40] or "model"
        digest = hashlib.sha256(f"{self.name}\x00{self.version}".encode()).hexdigest()[:10]
        return f"{readable}.{digest}"


@dataclass(frozen=True)
class StoredModel:
    """One model's vectors in a store, with how many there are."""

    identity: ModelIdentity
    vectors: int

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.identity.name, "version": self.identity.version,
                "dimension": self.identity.dimension, "vectors": self.vectors}


class EmbeddingModelMismatchError(EmbeddingVersionMismatchError):
    """Every stored vector was made by another model than the configured one, so a write or a search would mix them.

    It is an ``EmbeddingVersionMismatchError``, so a search reports it as ``embedding_mismatch`` as before.
    """


@dataclass(frozen=True)
class EmbeddingCoverage:
    """How many of an owner's memories have a vector under the active model."""

    active: ModelIdentity
    nodes_total: int
    nodes_without_vector: int
    stored_models: tuple[StoredModel, ...] = ()

    @property
    def incomplete(self) -> bool:
        return self.nodes_without_vector > 0

    def as_metadata(self) -> dict[str, Any]:
        """The ``embedding_incomplete`` entry of retrieval metadata."""
        return {
            "model": self.active.name,
            "version": self.active.version,
            "dimension": self.active.dimension,
            "nodes_total": self.nodes_total,
            "nodes_without_vector": self.nodes_without_vector,
            "stored_models": [item.as_dict() for item in self.stored_models],
        }


def refusal_message(active: ModelIdentity, stored: list[StoredModel]) -> str:
    """The error text of a write or search refused because the store holds only other models' vectors."""
    held = "; ".join(f"{item.identity.label()}, {item.vectors} vectors" for item in stored)
    return (
        f"This store holds vectors made by {held}, and it has none under the configured embedding model "
        f"{active.label()}. Vectors from different models are not mixed, so writes are refused and vector "
        f"search returns nothing. Run reembed (prme reembed) to build the configured model's vectors, then "
        f"reopen the store; or set embedding.model_name back to the stored model."
    )


def fastembed_provider_for(model_name: str, *, threads: int | None = None) -> FastEmbedProvider:
    """A local provider for a registered fastembed model, embedded as it is, without task prefixes."""
    return FastEmbedProvider(model_name, dimension=fastembed_model_dimension(model_name), threads=threads)


async def write_additional_models(vector_index: Any, embeddings: Any, *, user_id: str) -> None:
    """After PostgreSQL published memories with the active model's vector, store the other models' vectors too.

    A no-op without multi-model storage. DuckDB stores them while it stages the active model's vector.
    """
    writer = getattr(vector_index, "write_additional", None)
    if writer is not None:
        await writer(list(embeddings), user_id=user_id)


def additional_providers_for(config: Any, supplied: list[EmbeddingProvider] | None) -> list[EmbeddingProvider]:
    """The providers of the models written beside the active one: the caller's, or the configured fastembed models."""
    if supplied is not None:
        return list(supplied)
    return [fastembed_provider_for(name, threads=config.embedding.threads) for name in config.embedding.additional_models]


class MultiModelVectorIndex:
    """The vector index of a store with ``enable_multi_model_embeddings``.

    ``active`` and every index in ``extras`` are per-model indexes of one backend. Each offers the usual index
    methods for its own model, and these model methods: ``identity``, ``has_vectors()``, ``stored_models()``,
    ``coverage_counts(user_id)``, ``missing_nodes(...)``, ``put_vectors(...)``, ``delete_other_models(...)``
    and ``drop_rows()``.
    """

    def __init__(
        self,
        active: Any,
        extras: list[Any],
        *,
        make_index: Callable[[EmbeddingProvider], Awaitable[Any]],
        drop_model_files: Callable[[ModelIdentity], Awaitable[None]] | None = None,
    ) -> None:
        self._active = active
        self._extras: list[Any] = list(extras)
        self._make_index = make_index
        self._drop_model_files = drop_model_files
        self._active_has_vectors = False

    # --- identity -----------------------------------------------------------------------------------

    @property
    def active(self) -> Any:
        return self._active

    @property
    def _provider(self) -> EmbeddingProvider:
        return self._active._provider

    @property
    def _exact_search(self) -> bool:
        return self._active._exact_search

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity.of(self._active._provider)

    @property
    def indexes(self) -> list[Any]:
        """The active index first, then the additional ones; every write goes to all of them."""
        return [self._active, *self._extras]

    def index_for(self, identity: ModelIdentity) -> Any | None:
        for index in self.indexes:
            if index.identity == identity:
                return index
        return None

    async def attach(self, provider: EmbeddingProvider) -> Any:
        """Open the index of a model that is not configured, so a re-embed can fill it and later writes reach it."""
        identity = ModelIdentity.of(provider)
        existing = self.index_for(identity)
        if existing is not None:
            return existing
        index = await self._make_index(provider)
        self._extras.append(index)
        return index

    # --- the guard ----------------------------------------------------------------------------------

    async def stored_models(self) -> list[StoredModel]:
        return await self._active.stored_models()

    async def require_usable(self) -> None:
        """Raise when the store's vectors were all made by other models than the active one.

        An empty store is usable. Once the active model has a vector the answer cannot change for this
        process, so it is remembered; until then every call asks, which is one cheap query.
        """
        if self._active_has_vectors:
            return
        if await self._active.has_vectors():
            self._active_has_vectors = True
            return
        stored = await self._active.stored_models()
        if stored:
            raise EmbeddingModelMismatchError(refusal_message(self.identity, stored))

    require_writable = require_usable

    # --- writes -------------------------------------------------------------------------------------

    async def index(self, node_id: str, content: str, user_id: str, *, replace: bool = False) -> int:
        await self.require_usable()
        key = await self._active.index(node_id, content, user_id, replace=replace)
        await self._write_extras("index", node_id, lambda extra: extra.index(node_id, content, user_id, replace=replace))
        return key

    async def stage(self, embedding: Any, *, user_id: str, fence: Any = None) -> int:
        """Stage the active model's prepared vector, then write the additional models' (DuckDB ingestion)."""
        await self.require_usable()
        key = await self._active.stage(embedding, user_id=user_id, fence=fence)
        await self._write_extras(
            "stage", str(embedding.node_id),
            lambda extra: extra.index(str(embedding.node_id), embedding.content, user_id, replace=True),
        )
        return key

    async def write_additional(self, embeddings: list[Any], *, user_id: str) -> None:
        """Dual-write memories a transaction already published with the active model's vector (PostgreSQL)."""
        for item in embeddings:
            await self._write_extras(
                "publish", str(item.node_id),
                lambda extra, item=item: extra.index(str(item.node_id), item.content, user_id, replace=True),
            )

    async def _write_extras(self, what: str, node_id: str, write: Callable[[Any], Awaitable[Any]]) -> None:
        for extra in self._extras:
            try:
                await write(extra)
            except Exception:
                # The memory is stored and searchable under the active model. The additional model is behind
                # by one node, which reembed() fills and its coverage count shows.
                logger.warning(
                    "multi_model.dual_write_failed",
                    extra={"operation": what, "node_id": node_id, "model": extra.identity.name},
                    exc_info=True,
                )

    async def delete_by_node_id(self, node_id: str) -> int:
        """Evict a retired node from every model's index, including the models that are not open."""
        removed = 0
        for index in self.indexes:
            removed = max(removed, await index.delete_by_node_id(node_id))
        keep = [index.identity for index in self.indexes]
        await self._active.delete_other_models(node_id, keep)
        return removed

    async def delete_profile_stage(self, plan: Any, *, fence: Any) -> int:
        removed = await self._active.delete_profile_stage(plan, fence=fence)
        for extra in self._extras:
            try:
                await extra.delete_by_node_id(str(plan.node.id))
            except Exception:
                logger.warning("multi_model.stage_cleanup_failed", exc_info=True)
        return removed

    async def clear(self) -> None:
        """Empty every open model's index; ``rebuild_indexes()`` then fills them again."""
        for index in self.indexes:
            await index.clear()
        self._active_has_vectors = False

    async def save(self) -> None:
        for index in self.indexes:
            await index.save()

    async def close(self) -> None:
        for index in self.indexes:
            await index.close()

    # --- reads --------------------------------------------------------------------------------------

    async def search(self, query: str, user_id: str, **options: Any) -> list[dict]:
        await self.require_usable()
        return await self._active.search(query, user_id, **options)

    async def search_by_vector(self, vector: list[float], user_id: str, **options: Any) -> list[dict]:
        await self.require_usable()
        return await self._active.search_by_vector(vector, user_id, **options)

    async def embedding_coverage(self, user_id: str | None = None) -> EmbeddingCoverage | None:
        """How many of an owner's memories lack a vector under the active model; ``None`` when none do."""
        expected, missing = await self._active.coverage_counts(user_id)
        if not missing:
            return None
        return EmbeddingCoverage(self.identity, expected, missing, tuple(await self._active.stored_models()))

    # --- models -------------------------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        """Each stored model with its vector count and whether it is complete, for ``prme embeddings status``."""
        stored = {item.identity: item.vectors for item in await self._active.stored_models()}
        live = {index.identity for index in self.indexes}
        report = []
        for identity in sorted(set(stored) | live):
            index = self.index_for(identity)
            complete: bool | None = None
            expected: int | None = None
            if index is not None:
                expected, missing = await index.coverage_counts(None)
                complete = missing == 0
            report.append({
                "model": identity.name, "version": identity.version, "dimension": identity.dimension,
                "vectors": stored.get(identity, 0),
                "role": ("active" if identity == self.identity else "additional" if identity in live else "stored"),
                "memories": expected, "complete": complete,
            })
        return {"active": self.identity.label(), "models": report}

    async def drop_model(self, identity: ModelIdentity) -> int:
        """Remove one model's vectors and its index. The active model cannot be dropped."""
        if identity == self.identity:
            raise ValueError("The active embedding model cannot be dropped; switch embedding.model_name first")
        index = self.index_for(identity)
        if index is not None:
            self._extras.remove(index)
            removed = await index.drop_rows()
            await index.close()
        else:
            removed = await self._active.drop_rows_of(identity)
        if self._drop_model_files is not None:
            await self._drop_model_files(identity)
        return removed
