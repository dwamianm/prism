"""Surprise-gated storage: novelty scoring for incoming content.

Computes how "surprising" new content is relative to existing knowledge
by measuring semantic distance from the nearest existing memory nodes.
High-novelty content receives boosted initial salience; low-novelty
(redundant) content receives reduced salience.

Inspired by the Titans architecture (Google, ICLR 2025) which uses
KL-divergence thresholds for surprise-driven memory updates, and by
the hippocampal novelty signal in neuroscience.
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
    """Result of novelty scoring for incoming content.

    Attributes:
        novelty_score: Float 0.0-1.0. 1.0 = completely novel (no similar
            content exists). 0.0 = exact duplicate of existing content.
        max_similarity: The highest cosine similarity found among existing
            nodes. 0.0 if no existing nodes.
        nearest_node_id: Node ID of the most similar existing node, or
            None if no existing nodes.
        nearest_neighbors: List of (node_id, similarity) tuples for the
            top-k most similar existing nodes.
        salience_adjustment: Recommended adjustment to initial salience.
            Positive = boost (novel), negative = penalty (redundant),
            zero = neutral.
    """
    novelty_score: float
    max_similarity: float
    nearest_node_id: str | None
    nearest_neighbors: list[tuple[str, float]] = field(default_factory=list)
    salience_adjustment: float = 0.0

    @property
    def is_novel(self) -> bool:
        """True if content is considered novel (above threshold)."""
        return self.novelty_score > 0.5

    @property
    def is_redundant(self) -> bool:
        """True if content is highly similar to existing knowledge."""
        return self.novelty_score < 0.2


class NoveltyScorer:
    """Scores how novel incoming content is relative to existing memory.

    Uses vector similarity search to find the closest existing memory
    nodes. The novelty score is 1.0 - max_similarity, where max_similarity
    is the highest cosine similarity among existing nodes.

    Salience adjustment is computed as:
    - If novelty > high_novelty_threshold: +salience_boost
    - If novelty < low_novelty_threshold: -salience_penalty
    - Otherwise: 0.0 (neutral)

    Args:
        high_novelty_threshold: Novelty score above which content gets
            a salience boost. Default 0.7.
        low_novelty_threshold: Novelty score below which content gets
            a salience penalty. Default 0.3.
        salience_boost: Salience increase for highly novel content.
            Default 0.15.
        salience_penalty: Salience decrease for redundant content.
            Default 0.10.
        search_k: Number of nearest neighbors to search for.
            Default 5.
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
        """Score how novel the given content is.

        Args:
            content: Text content to evaluate for novelty.
            user_id: Owner user ID for scoping vector search.
            vector_index: Vector index to search against.
            exclude_node_id: Optional node ID to exclude from results
                (e.g., the node just created from this content).

        Returns:
            NoveltyResult with novelty score and salience adjustment.
        """
        try:
            results = await vector_index.search(
                content, user_id, k=self._search_k
            )
        except Exception:
            logger.warning(
                "Vector search failed during novelty scoring; "
                "defaulting to fully novel",
                exc_info=True,
            )
            return NoveltyResult(
                novelty_score=1.0,
                max_similarity=0.0,
                nearest_node_id=None,
                salience_adjustment=self._salience_boost,
            )

        # Filter out the excluded node
        if exclude_node_id is not None:
            results = [r for r in results if r["node_id"] != exclude_node_id]

        if not results:
            # Empty store or no similar content — everything is novel
            return NoveltyResult(
                novelty_score=1.0,
                max_similarity=0.0,
                nearest_node_id=None,
                salience_adjustment=self._salience_boost,
            )

        # Extract similarities
        neighbors = [
            (r["node_id"], r["score"]) for r in results
        ]
        max_similarity = max(score for _, score in neighbors)
        nearest_node_id = neighbors[0][0]  # Already sorted by score desc

        # Compute novelty
        novelty_score = max(0.0, min(1.0, 1.0 - max_similarity))

        # Compute salience adjustment
        if novelty_score >= self._high_threshold:
            salience_adjustment = self._salience_boost
        elif novelty_score <= self._low_threshold:
            salience_adjustment = -self._salience_penalty
        else:
            salience_adjustment = 0.0

        logger.debug(
            "Novelty score: %.3f (max_sim=%.3f, nearest=%s, adj=%.3f)",
            novelty_score,
            max_similarity,
            nearest_node_id,
            salience_adjustment,
        )

        return NoveltyResult(
            novelty_score=novelty_score,
            max_similarity=max_similarity,
            nearest_node_id=nearest_node_id,
            nearest_neighbors=neighbors,
            salience_adjustment=salience_adjustment,
        )
