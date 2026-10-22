"""Fill one embedding model's vectors for the memories a store already holds (``MemoryEngine.reembed()``, dwamianm/prism#368).

The job takes the active memories that have no vector under the target model, in id order, a batch at a time:
it embeds their text with the local model, stores each vector with the model's name, version and dimension, and
sleeps between batches when asked to. It writes only vectors, so the events, the graph and every other model's
vectors are never changed, and it is safe to run while the store is read and written.

What makes it resumable and safe against writes that arrive meanwhile:

- The progress record is the data itself. A memory with a vector under the model is done, so a stopped run
  continues at the first memory without one, and ``cursor`` only skips the ones that failed.
- A memory written while the job runs is picked up in the same sweep when its id is ahead of the cursor, and by
  a second sweep over the ids behind it before the call ends. With the model in ``embedding.additional_models``
  every write also stores the vector itself, so the job has nothing to catch up on.
- A vector for a memory that was retired, or that a concurrent write already gave one, is skipped, never
  duplicated or attached to a retired node.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import TYPE_CHECKING, Any

from prme.models.reembed import ReembedResult
from prme.storage.embedding import EmbeddingProvider, encode_texts
from prme.storage.multi_model import ModelIdentity, MultiModelVectorIndex, fastembed_provider_for

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)

# Failed batches in a row that end a call: a model that cannot run would otherwise fail every batch in turn.
_FAILED_BATCHES_IN_A_ROW = 3


class ReembedDisabledError(RuntimeError):
    """Multi-model embeddings are off for this engine (``enable_multi_model_embeddings``)."""


class ReembedBusyError(RuntimeError):
    """This engine is already filling this model's vectors."""


def encode_cursor(node_id: str) -> str:
    return base64.urlsafe_b64encode(node_id.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    try:
        from uuid import UUID

        return str(UUID(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()))
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid reembed cursor") from exc


class Reembedder:
    """Runs ``reembed()`` for one engine and holds the models it is running for."""

    def __init__(self, engine: MemoryEngine) -> None:
        self._engine = engine
        self._running: set[str] = set()

    def _index(self) -> MultiModelVectorIndex:
        if not self._engine._config.enable_multi_model_embeddings:
            raise ReembedDisabledError(
                "Embedding models are fixed for this engine; set enable_multi_model_embeddings "
                "(PRME_ENABLE_MULTI_MODEL_EMBEDDINGS) to store vectors by model and to re-embed"
            )
        index = self._engine._vector_index
        if not isinstance(index, MultiModelVectorIndex):
            raise ReembedDisabledError("This engine was opened without multi-model embedding storage")
        return index

    def provider_for(self, model: str | EmbeddingProvider | None, threads: int | None = None) -> EmbeddingProvider:
        """The provider of ``model``: a provider the caller passes, the active one, a configured additional one,
        or a local fastembed model of that name."""
        index = self._index()
        if model is not None and not isinstance(model, str):
            from prme.storage.embedding import validate_embedding_provider

            validate_embedding_provider(model)
            return model
        if model is None or model == index.identity.name:
            return index._provider
        for extra in index.indexes[1:]:
            if extra.identity.name == model:
                return extra._provider
        config = self._engine._config.embedding
        return fastembed_provider_for(model, threads=threads if threads is not None else config.threads)

    async def run(
        self, model: str | EmbeddingProvider | None = None, *, limit: int | None = None, cursor: str | None = None,
        dry_run: bool = False, user_id: str | None = None, batch_size: int = 64, pause_ms: int = 0,
        threads: int | None = None,
    ) -> ReembedResult:
        index = self._index()
        if limit is not None and limit < 0:
            raise ValueError("Reembed limit cannot be negative")
        if batch_size < 1:
            raise ValueError("Reembed batch size must be at least 1")
        if pause_ms < 0:
            raise ValueError("Reembed pause cannot be negative")
        if user_id is not None and not user_id.strip():
            raise ValueError("Reembed needs a nonempty owner, or none for the whole store")
        after = decode_cursor(cursor) if cursor else None
        provider = self.provider_for(model, threads)
        identity = ModelIdentity.of(provider)
        if identity.key in self._running:
            raise ReembedBusyError(f"{identity.name} is already being embedded by this engine")
        self._running.add(identity.key)
        try:
            return await self._run(index, provider, identity, limit, after, dry_run, user_id, batch_size, pause_ms)
        finally:
            self._running.discard(identity.key)

    async def _run(
        self, index: MultiModelVectorIndex, provider: EmbeddingProvider, identity: ModelIdentity,
        limit: int | None, after: str | None, dry_run: bool, user_id: str | None, batch_size: int, pause_ms: int,
    ) -> ReembedResult:
        started = time.monotonic()
        reader = index.active
        active = identity == index.identity

        def result(**values: Any) -> ReembedResult:
            return ReembedResult(
                embedding_model=identity.name, embedding_version=identity.version, dimension=identity.dimension,
                active=active, user_id=user_id, dry_run=dry_run, batches=values.pop("batches", 0),
                elapsed_seconds=round(time.monotonic() - started, 3), **values,
            )

        total, missing = await reader.coverage_counts(user_id, identity)
        if dry_run:
            selected = missing if limit is None else min(missing, limit)
            return result(total=total, done=total - missing, remaining=missing, selected=selected,
                          processed=0, failed=0)

        target = await index.attach(provider)
        selected = processed = failed = batches = 0
        failed_ids: set[str] = set()
        in_a_row = 0
        last = after

        async def sweep(start: str | None, upper: str | None) -> str | None:
            """Take memories after ``start`` (up to ``upper``) until none is left or the limit is reached."""
            nonlocal selected, processed, failed, batches, in_a_row
            position = start
            while in_a_row < _FAILED_BATCHES_IN_A_ROW:
                room = batch_size if limit is None else min(batch_size, limit - selected)
                if room <= 0:
                    break
                batch = await target.missing_nodes(after=position, upper=upper, limit=room, user_id=user_id)
                if not batch:
                    break
                position = batch[-1][0]
                batch = [item for item in batch if item[0] not in failed_ids]
                if not batch:
                    continue
                selected += len(batch)
                batches += 1
                written, bad = await self._store_batch(index, target, provider, batch)
                processed += written
                failed += len(bad)
                failed_ids.update(bad)
                in_a_row = in_a_row + 1 if bad and not written else 0
                if pause_ms:
                    await asyncio.sleep(pause_ms / 1000)
            return position

        last = await sweep(last, None)
        exhausted = limit is None or selected < limit
        if exhausted and in_a_row < _FAILED_BATCHES_IN_A_ROW and last != after:
            # Memories stored while the sweep ran got ids behind its position. One more pass over those ids.
            await sweep(after, last)
        if last is not None and not await target.missing_nodes(after=last, limit=1, user_id=user_id):
            last = None
        await target.save()
        total, missing = await reader.coverage_counts(user_id, identity)
        done = total - missing
        logger.info(
            "reembed.complete",
            extra={"model": identity.name, "selected": selected, "processed": processed, "failed": failed,
                   "remaining": missing},
        )
        return result(total=total, done=done, remaining=missing, selected=selected, processed=processed,
                      failed=failed, batches=batches, cursor=encode_cursor(last) if last else None)

    async def _store_batch(
        self, index: MultiModelVectorIndex, target: Any, provider: EmbeddingProvider,
        batch: list[tuple[str, str, str]],
    ) -> tuple[int, list[str]]:
        """Embed and store one batch. Returns the vectors written and the ids that failed."""
        vectors: list[list[float] | None]
        try:
            vectors = list(await encode_texts(provider, [content for _, content, _ in batch]))
        except Exception:
            # One text the model cannot embed must not fail the others.
            vectors = []
            for _, content, _ in batch:
                try:
                    vectors.append((await encode_texts(provider, [content]))[0])
                except Exception:
                    vectors.append(None)
        rows = [(node_id, owner, content, vector)
                for (node_id, content, owner), vector in zip(batch, vectors) if vector is not None]
        bad = [node_id for (node_id, _, _), vector in zip(batch, vectors) if vector is None]
        if not rows:
            return 0, bad
        try:
            written = await self._engine._write_queue.submit(
                lambda: target.put_vectors(rows), label=f"reembed.vectors:{rows[0][0]}",
            )
        except Exception:
            logger.warning("reembed.store_failed", exc_info=True)
            return 0, [node_id for node_id, *_ in rows] + bad
        return int(written), bad
