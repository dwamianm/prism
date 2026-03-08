"""Novelty scoring for surprise-gated storage (issue #20).

Computes how novel incoming content is relative to existing memory
by measuring vector similarity against the current knowledge base.
Novel content receives a salience boost; redundant content receives
a salience penalty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NoveltyResult:
    """Result of novelty scoring for a piece of content.

    Attributes:
        novelty_score: 0.0-1.0, where 1.0 = completely novel.
        max_similarity: Highest cosine similarity found among neighbors.
        nearest_node_id: Node ID of the most similar existing node.
        nearest_neighbors: List of (node_id, similarity) tuples.
        salience_adjustment: Positive = boost, negative = penalty.
    """

    novelty_score: float
    max_similarity: float
    nearest_node_id: str | None
    nearest_neighbors: list[tuple[str, float]] = field(default_factory=list)
    salience_adjustment: float = 0.0


class NoveltyScorer:
    """Scores content novelty by comparing against existing vector index.

    Uses vector similarity search to find the most similar existing
    content. Novelty score = 1.0 - max_similarity. Content above the
    high threshold gets a salience boost; content below the low
    threshold gets a salience penalty.

    Args:
        high_novelty_threshold: Score above which content is "surprising".
        low_novelty_threshold: Score below which content is "redundant".
        salience_boost: Salience boost for surprising content.
        salience_penalty: Salience penalty for redundant content.
        search_k: Number of nearest neighbors to search.
    """

    def __init__(
        self,
        *,
        high_novelty_threshold: float = 0.7,
        low_novelty_threshold: float = 0.3,
        salience_boost: float = 0.15,
        salience_penalty: float = 0.10,
        search_k: int = 5,
    ) -> None:
        self._high_threshold = high_novelty_threshold
        self._low_threshold = low_novelty_threshold
        self._salience_boost = salience_boost
        self._salience_penalty = salience_penalty
        self._search_k = search_k

    async def score(
        self,
        content: str,
        user_id: str,
        vector_index: VectorIndex,
        *,
        exclude_node_id: str | None = None,
    ) -> NoveltyResult:
        """Score how novel the content is relative to existing memory.

        Args:
            content: Text content to evaluate.
            user_id: Owner user ID for scoping vector search.
            vector_index: Vector index to search against.
            exclude_node_id: Optional node ID to exclude from results.

        Returns:
            NoveltyResult with computed novelty score and salience adjustment.
        """
        # Search for similar existing content
        results = await vector_index.search(
            content, user_id, k=self._search_k
        )

        # Filter out excluded node
        if exclude_node_id is not None:
            results = [
                r for r in results if r["node_id"] != exclude_node_id
            ]

        # No neighbors found = completely novel content
        if not results:
            return NoveltyResult(
                novelty_score=1.0,
                max_similarity=0.0,
                nearest_node_id=None,
                nearest_neighbors=[],
                salience_adjustment=self._salience_boost,
            )

        # Extract neighbor info
        nearest_neighbors = [
            (r["node_id"], r.get("score", 0.0)) for r in results
        ]
        max_similarity = max(score for _, score in nearest_neighbors)
        nearest_node_id = nearest_neighbors[0][0] if nearest_neighbors else None

        # Novelty = inverse of max similarity
        novelty_score = max(0.0, min(1.0, 1.0 - max_similarity))

        # Compute salience adjustment
        if novelty_score >= self._high_threshold:
            salience_adjustment = self._salience_boost
        elif novelty_score <= self._low_threshold:
            salience_adjustment = -self._salience_penalty
        else:
            salience_adjustment = 0.0

        return NoveltyResult(
            novelty_score=novelty_score,
            max_similarity=max_similarity,
            nearest_node_id=nearest_node_id,
            nearest_neighbors=nearest_neighbors,
            salience_adjustment=salience_adjustment,
        )
