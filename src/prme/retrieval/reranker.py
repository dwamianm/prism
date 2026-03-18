"""Neural cross-encoder reranker for retrieval candidates.

Uses sentence-transformers CrossEncoder to rescore query-document pairs
after the composite scoring stage. Model is loaded lazily on first use.
Inference runs in a thread pool to avoid blocking the async event loop.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.retrieval.models import RetrievalCandidate


class CrossEncoderReranker:
    """Neural cross-encoder reranker for retrieval candidates.

    Uses sentence-transformers CrossEncoder to rescore query-document pairs.
    Model is loaded lazily on first use. Inference runs in a thread pool
    to avoid blocking the async event loop.
    """

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        batch_size: int = 64,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._model = None  # Lazy init

    def _ensure_model(self) -> None:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for neural reranking. "
                    "Install with: pip install prme[reranker]"
                )
            self._model = CrossEncoder(self._model_name)

    def _predict_sync(self, pairs: list[tuple[str, str]]) -> list[float]:
        self._ensure_model()
        import numpy as np

        raw_scores = self._model.predict(pairs, batch_size=self._batch_size)
        # Sigmoid normalization to [0, 1]
        normalized = 1.0 / (1.0 + np.exp(-raw_scores))
        return normalized.tolist()

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int = 100,
        prior_weight: float = 0.3,
    ) -> list[RetrievalCandidate]:
        """Rerank candidates using cross-encoder scores.

        Args:
            query: The search query.
            candidates: Scored candidates from the pipeline.
            top_k: Only rerank the top-K candidates (rest keep original scores).
            prior_weight: Weight of original composite_score in blended score.
                         (1 - prior_weight) is the cross-encoder weight.

        Returns:
            Candidates re-sorted by blended score.
        """
        if not candidates:
            return candidates

        # Split: rerank top_k, keep rest unchanged
        to_rerank = candidates[:top_k]
        remainder = candidates[top_k:]

        # Build query-document pairs
        pairs = [(query, c.node.content or "") for c in to_rerank]

        # Run cross-encoder in thread (CPU-bound)
        ce_scores = await asyncio.to_thread(self._predict_sync, pairs)

        # Blend: (1 - prior_weight) * ce_score + prior_weight * original_composite
        for candidate, ce_score in zip(to_rerank, ce_scores):
            original = candidate.composite_score
            blended = (1.0 - prior_weight) * ce_score + prior_weight * original
            candidate.composite_score = blended

        # Re-sort reranked portion
        to_rerank.sort(key=lambda c: (-c.composite_score, str(c.node.id)))

        # Ensure remainder scores are below reranked minimum
        return to_rerank + remainder
