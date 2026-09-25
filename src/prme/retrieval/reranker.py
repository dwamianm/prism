"""Neural cross-encoder reranker for retrieval candidates.

Uses sentence-transformers CrossEncoder to rescore query-document pairs
after the composite scoring stage. Model is loaded lazily on first use.
Inference runs in a thread pool to avoid blocking the async event loop.

A prior weight of 0 orders the reranked prefix by cross-encoder score alone.
With an envelope policy the prefix then takes its original scores in that
order, so rank fusion's scale is kept and only the ranks change (issue #88).
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from prme.retrieval.config import DEFAULT_RERANKER_PRIOR_WEIGHT, normalize_reranker_prior_weight
from prme.retrieval.models import ScoreAdjustment

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
        *,
        policy: Literal["legacy", "score_envelope", "anchored_score_envelope"] = "legacy",
        prior_weight: float = DEFAULT_RERANKER_PRIOR_WEIGHT,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if policy not in {"legacy", "score_envelope", "anchored_score_envelope"}:
            raise ValueError("Unknown reranker policy")
        self._policy = policy
        self._prior_weight = normalize_reranker_prior_weight(prior_weight)
        self._model_name = model_name
        self._batch_size = batch_size
        self._model = None  # Lazy init
        self._prediction_lock = threading.Lock()

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
        import numpy as np
        # Loading and inference share one lock: concurrent first requests must
        # not load multiple copies of a model or race its mutable inference state.
        with self._prediction_lock:
            self._ensure_model()
            import torch

            # Explicitly override the model's activation. Applying a second
            # sigmoid to already-normalized scores would put all results above
            # 0.5 and defeat a caller's relevance floor.
            scores = np.asarray(self._model.predict(
                pairs, batch_size=self._batch_size, activation_fn=torch.nn.Sigmoid(),
            ), dtype=float)
        if scores.shape != (len(pairs),) or not np.isfinite(scores).all():
            raise ValueError("Reranker must return one finite scalar per query-document pair")
        if ((scores < 0) | (scores > 1)).any():
            raise ValueError("Reranker scores must be normalized to [0, 1]")
        return scores.tolist()

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int = 100,
        prior_weight: float | None = None,
    ) -> list[RetrievalCandidate]:
        """Rerank candidates using cross-encoder scores.

        Args:
            query: The search query.
            candidates: Scored candidates from the pipeline.
            top_k: Only rerank the top-K candidates (rest keep original scores).
            prior_weight: Weight of original composite_score in blended score.
                         (1 - prior_weight) is the cross-encoder weight, and
                         0 orders the prefix by cross-encoder score alone.
                         None uses the weight this reranker was built with.

        Returns:
            Candidates re-sorted by blended score with an unchanged tail.
            Explicit envelope policies assign the original prefix score slots
            to this order; the anchored variant first prioritizes its original
            ordinary multi-path anchor when that anchor is in the prefix.
        """
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0:
            raise ValueError("top_k must be a nonnegative integer")
        prior_weight = self._prior_weight if prior_weight is None else normalize_reranker_prior_weight(prior_weight)
        # Do not change input candidates: callers may reuse the base ranking
        # for another policy or an ablation, and repeated calls must not blend
        # the model score into an already-blended prior.
        original_candidates = candidates
        candidates = [candidate.model_copy() for candidate in candidates]
        if not candidates or top_k == 0:
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
            candidate.reranker_score = ce_score
            if candidate.score_provenance is not None:
                provenance = candidate.score_provenance
                candidate.score_provenance = provenance.model_copy(update={
                    "adjustments": provenance.adjustments + (ScoreAdjustment(
                        kind="neural_blend", coefficient=prior_weight,
                        neural_score=ce_score, source_node_id=candidate.node.id,
                    ),),
                })

        # Re-sort reranked portion
        to_rerank.sort(key=lambda c: (-c.composite_score, str(c.node.id)))

        # Preserve the unreranked tail's original scores/order. They have no
        # neural relevance score and must not be described as model judgments.
        if self._policy == "anchored_score_envelope":
            anchor = original_anchor(original_candidates)
            to_rerank = [c for c in to_rerank if c.node.id == anchor] + [
                c for c in to_rerank if c.node.id != anchor
            ]
        if self._policy != "legacy":
            return assign_envelope(original_candidates, to_rerank + remainder, top_k)
        return to_rerank + remainder


def assign_envelope(
    original: list[RetrievalCandidate],
    reranked: list[RetrievalCandidate],
    top_k: int,
) -> list[RetrievalCandidate]:
    """Retain the prefix score multiset and record the ordinal assignment.

    This preserves scale for downstream packing and session expansion. It is
    neither probability calibration nor a source-retention guarantee.
    """
    count = min(len(original), top_k)
    if count == 0:
        return reranked
    prefix = {c.node.id for c in original[:count]}
    if ({c.node.id for c in reranked[:count]} != prefix
            or [c.node.id for c in reranked[count:]] != [c.node.id for c in original[count:]]):
        raise ValueError('Reranker prefix or tail identity changed')
    scores = sorted((c.composite_score for c in original[:count]), reverse=True)
    updated = []
    for candidate, assigned in zip(reranked[:count], scores):
        provenance = candidate.score_provenance
        if provenance is None or not provenance.adjustments or provenance.adjustments[-1].kind != 'neural_blend':
            raise ValueError('Neural score lineage is required before ordinal assignment')
        if provenance.replay_score() != candidate.composite_score:
            raise ValueError('Neural score lineage does not replay')
        copied = candidate.model_copy(update={'composite_score': assigned,
            'score_provenance': provenance.model_copy(update={'adjustments': (
                *provenance.adjustments, ScoreAdjustment(kind='neural_rank_assignment',
                    coefficient=assigned, source_node_id=candidate.node.id))})})
        if copied.score_provenance.replay_score() != assigned:
            raise ValueError('Assigned score lineage does not replay')
        updated.append(copied)
    updated.sort(key=lambda c: (-c.composite_score, str(c.node.id)))
    return updated + reranked[count:]


def original_anchor(candidates: list[RetrievalCandidate]) -> UUID | None:
    """Select the same ordinary anchor eligibility as balanced packing."""
    from prme.retrieval.packing import _is_pinned_or_active_task
    from prme.types import NodeType

    eligible = [c for c in candidates if c.path_count >= 2
                and c.node.node_type != NodeType.INSTRUCTION
                and not _is_pinned_or_active_task(c)]
    return min(eligible, key=lambda c: (-c.composite_score, str(c.node.id))).node.id if eligible else None
