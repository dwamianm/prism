"""Tests for NoveltyScorer and NoveltyResult.

Comprehensive unit tests covering novelty scoring for surprise-gated
storage (issue #20). Uses AsyncMock for VectorIndex to isolate the
scoring logic from actual vector search infrastructure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from prme.ingestion.novelty import NoveltyResult, NoveltyScorer


# ---------------------------------------------------------------------------
# NoveltyResult property tests
# ---------------------------------------------------------------------------


class TestNoveltyResult:
    """Tests for NoveltyResult dataclass properties."""

    def test_is_novel_above_threshold(self):
        """Novelty score > 0.5 should be considered novel."""
        result = NoveltyResult(
            novelty_score=0.8,
            max_similarity=0.2,
            nearest_node_id="node-1",
        )
        assert result.is_novel is True

    def test_is_novel_at_threshold(self):
        """Novelty score exactly 0.5 should NOT be considered novel."""
        result = NoveltyResult(
            novelty_score=0.5,
            max_similarity=0.5,
            nearest_node_id="node-1",
        )
        assert result.is_novel is False

    def test_is_novel_below_threshold(self):
        """Novelty score < 0.5 should NOT be considered novel."""
        result = NoveltyResult(
            novelty_score=0.3,
            max_similarity=0.7,
            nearest_node_id="node-1",
        )
        assert result.is_novel is False

    def test_is_redundant_below_threshold(self):
        """Novelty score < 0.2 should be considered redundant."""
        result = NoveltyResult(
            novelty_score=0.1,
            max_similarity=0.9,
            nearest_node_id="node-1",
        )
        assert result.is_redundant is True

    def test_is_redundant_at_threshold(self):
        """Novelty score exactly 0.2 should NOT be considered redundant."""
        result = NoveltyResult(
            novelty_score=0.2,
            max_similarity=0.8,
            nearest_node_id="node-1",
        )
        assert result.is_redundant is False

    def test_is_redundant_above_threshold(self):
        """Novelty score > 0.2 should NOT be considered redundant."""
        result = NoveltyResult(
            novelty_score=0.5,
            max_similarity=0.5,
            nearest_node_id="node-1",
        )
        assert result.is_redundant is False

    def test_fully_novel_result(self):
        """Score 1.0 is novel and not redundant."""
        result = NoveltyResult(
            novelty_score=1.0,
            max_similarity=0.0,
            nearest_node_id=None,
        )
        assert result.is_novel is True
        assert result.is_redundant is False

    def test_fully_redundant_result(self):
        """Score 0.0 is redundant and not novel."""
        result = NoveltyResult(
            novelty_score=0.0,
            max_similarity=1.0,
            nearest_node_id="node-1",
        )
        assert result.is_novel is False
        assert result.is_redundant is True

    def test_default_nearest_neighbors_empty(self):
        """nearest_neighbors defaults to empty list."""
        result = NoveltyResult(
            novelty_score=0.5,
            max_similarity=0.5,
            nearest_node_id="node-1",
        )
        assert result.nearest_neighbors == []

    def test_default_salience_adjustment_zero(self):
        """salience_adjustment defaults to 0.0."""
        result = NoveltyResult(
            novelty_score=0.5,
            max_similarity=0.5,
            nearest_node_id="node-1",
        )
        assert result.salience_adjustment == 0.0

    def test_frozen_dataclass(self):
        """NoveltyResult should be immutable (frozen=True)."""
        result = NoveltyResult(
            novelty_score=0.5,
            max_similarity=0.5,
            nearest_node_id="node-1",
        )
        with pytest.raises(AttributeError):
            result.novelty_score = 0.9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_vector_index(
    search_results: list[dict] | None = None,
    search_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock VectorIndex with configurable search results.

    Args:
        search_results: List of dicts with node_id, score, distance keys.
            Defaults to empty list (empty store).
        search_side_effect: If set, search() will raise this exception.

    Returns:
        AsyncMock mimicking VectorIndex.search().
    """
    mock = AsyncMock()
    if search_side_effect is not None:
        mock.search.side_effect = search_side_effect
    else:
        mock.search.return_value = search_results or []
    return mock


# ---------------------------------------------------------------------------
# NoveltyScorer.score() tests
# ---------------------------------------------------------------------------


class TestNoveltyScorer:
    """Tests for NoveltyScorer.score() method."""

    @pytest.mark.asyncio
    async def test_empty_vector_index(self):
        """Empty vector index should return novelty=1.0, no nearest neighbor."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[])

        result = await scorer.score("new content", "user-1", mock_vi)

        assert result.novelty_score == 1.0
        assert result.max_similarity == 0.0
        assert result.nearest_node_id is None
        assert result.nearest_neighbors == []
        assert result.salience_adjustment == 0.15  # default boost
        assert result.is_novel is True
        assert result.is_redundant is False

    @pytest.mark.asyncio
    async def test_highly_similar_content(self):
        """Content very similar to existing should score low novelty."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-a", "score": 0.95, "distance": 0.05},
            {"node_id": "node-b", "score": 0.80, "distance": 0.20},
        ])

        result = await scorer.score("almost duplicate", "user-1", mock_vi)

        assert result.novelty_score == pytest.approx(0.05)
        assert result.max_similarity == pytest.approx(0.95)
        assert result.nearest_node_id == "node-a"
        assert len(result.nearest_neighbors) == 2
        assert result.salience_adjustment == pytest.approx(-0.10)  # penalty
        assert result.is_novel is False
        assert result.is_redundant is True

    @pytest.mark.asyncio
    async def test_dissimilar_content(self):
        """Content very different from existing should score high novelty."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-x", "score": 0.15, "distance": 0.85},
            {"node_id": "node-y", "score": 0.10, "distance": 0.90},
        ])

        result = await scorer.score("totally new topic", "user-1", mock_vi)

        assert result.novelty_score == pytest.approx(0.85)
        assert result.max_similarity == pytest.approx(0.15)
        assert result.nearest_node_id == "node-x"
        assert result.salience_adjustment == pytest.approx(0.15)  # boost
        assert result.is_novel is True
        assert result.is_redundant is False

    @pytest.mark.asyncio
    async def test_middle_range_neutral_adjustment(self):
        """Content in middle range should get zero salience adjustment."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-m", "score": 0.50, "distance": 0.50},
        ])

        result = await scorer.score("somewhat related", "user-1", mock_vi)

        assert result.novelty_score == pytest.approx(0.50)
        assert result.max_similarity == pytest.approx(0.50)
        assert result.salience_adjustment == 0.0  # neutral
        assert result.is_novel is False  # 0.5 is not > 0.5
        assert result.is_redundant is False

    @pytest.mark.asyncio
    async def test_exclude_node_filtering(self):
        """exclude_node_id should filter out the specified node from results."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "self-node", "score": 0.99, "distance": 0.01},
            {"node_id": "other-node", "score": 0.30, "distance": 0.70},
        ])

        result = await scorer.score(
            "some content", "user-1", mock_vi,
            exclude_node_id="self-node",
        )

        # With self-node excluded, max_similarity should be from other-node
        assert result.novelty_score == pytest.approx(0.70)
        assert result.max_similarity == pytest.approx(0.30)
        assert result.nearest_node_id == "other-node"
        assert len(result.nearest_neighbors) == 1
        assert result.nearest_neighbors[0] == ("other-node", 0.30)

    @pytest.mark.asyncio
    async def test_exclude_node_all_filtered_out(self):
        """If exclude_node_id removes all results, treat as empty store."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "only-node", "score": 0.95, "distance": 0.05},
        ])

        result = await scorer.score(
            "content", "user-1", mock_vi,
            exclude_node_id="only-node",
        )

        assert result.novelty_score == 1.0
        assert result.max_similarity == 0.0
        assert result.nearest_node_id is None
        assert result.nearest_neighbors == []
        assert result.salience_adjustment == 0.15  # boost (novel)

    @pytest.mark.asyncio
    async def test_vector_search_failure_graceful_fallback(self):
        """Vector search failure should return novelty=1.0 (fail-open)."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(
            search_side_effect=RuntimeError("Connection lost"),
        )

        result = await scorer.score("any content", "user-1", mock_vi)

        assert result.novelty_score == 1.0
        assert result.max_similarity == 0.0
        assert result.nearest_node_id is None
        assert result.nearest_neighbors == []
        assert result.salience_adjustment == 0.15  # boost

    @pytest.mark.asyncio
    async def test_custom_thresholds(self):
        """Custom threshold parameters should work correctly."""
        scorer = NoveltyScorer(
            high_novelty_threshold=0.9,
            low_novelty_threshold=0.1,
            salience_boost=0.30,
            salience_penalty=0.20,
        )

        # Score 0.85 is above default 0.7 threshold but below custom 0.9
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-1", "score": 0.15, "distance": 0.85},
        ])
        result = await scorer.score("content", "user-1", mock_vi)
        assert result.novelty_score == pytest.approx(0.85)
        assert result.salience_adjustment == 0.0  # 0.85 < 0.9 threshold

        # Score 0.95 is above custom 0.9 threshold
        mock_vi2 = _make_mock_vector_index(search_results=[
            {"node_id": "node-2", "score": 0.05, "distance": 0.95},
        ])
        result2 = await scorer.score("content", "user-1", mock_vi2)
        assert result2.novelty_score == pytest.approx(0.95)
        assert result2.salience_adjustment == pytest.approx(0.30)  # custom boost

        # Score 0.05 is below custom 0.1 threshold
        mock_vi3 = _make_mock_vector_index(search_results=[
            {"node_id": "node-3", "score": 0.95, "distance": 0.05},
        ])
        result3 = await scorer.score("content", "user-1", mock_vi3)
        assert result3.novelty_score == pytest.approx(0.05)
        assert result3.salience_adjustment == pytest.approx(-0.20)  # custom penalty

    @pytest.mark.asyncio
    async def test_custom_search_k(self):
        """search_k parameter should be passed to vector_index.search()."""
        scorer = NoveltyScorer(search_k=3)
        mock_vi = _make_mock_vector_index(search_results=[])

        await scorer.score("content", "user-1", mock_vi)

        mock_vi.search.assert_called_once_with("content", "user-1", k=3)

    @pytest.mark.asyncio
    async def test_novelty_score_clamped_to_zero(self):
        """Similarity > 1.0 should clamp novelty score to 0.0."""
        scorer = NoveltyScorer()
        # Simulate a score > 1.0 (shouldn't happen in practice but tests clamping)
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-x", "score": 1.2, "distance": -0.2},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        assert result.novelty_score == 0.0  # clamped to 0.0
        assert result.max_similarity == pytest.approx(1.2)

    @pytest.mark.asyncio
    async def test_novelty_score_clamped_to_one(self):
        """Negative similarity should clamp novelty score to 1.0."""
        scorer = NoveltyScorer()
        # Simulate a negative score (edge case)
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-x", "score": -0.3, "distance": 1.3},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        assert result.novelty_score == 1.0  # clamped to 1.0
        assert result.max_similarity == pytest.approx(-0.3)

    @pytest.mark.asyncio
    async def test_multiple_neighbors_max_similarity_used(self):
        """Max similarity should be used even if not the first result."""
        scorer = NoveltyScorer()
        # First result by position has lower score; second is higher
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-a", "score": 0.60, "distance": 0.40},
            {"node_id": "node-b", "score": 0.80, "distance": 0.20},
            {"node_id": "node-c", "score": 0.40, "distance": 0.60},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        # max_similarity should pick 0.80 regardless of position
        assert result.max_similarity == pytest.approx(0.80)
        assert result.novelty_score == pytest.approx(0.20)
        # nearest_node_id is the first in the list (assumed sorted by VectorIndex)
        assert result.nearest_node_id == "node-a"
        assert len(result.nearest_neighbors) == 3

    @pytest.mark.asyncio
    async def test_boundary_at_high_threshold(self):
        """Novelty exactly at high_novelty_threshold should get boost."""
        scorer = NoveltyScorer(high_novelty_threshold=0.7)
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-1", "score": 0.30, "distance": 0.70},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        assert result.novelty_score == pytest.approx(0.70)
        assert result.salience_adjustment == pytest.approx(0.15)  # boost (>= threshold)

    @pytest.mark.asyncio
    async def test_boundary_at_low_threshold(self):
        """Novelty below low_novelty_threshold should get penalty."""
        scorer = NoveltyScorer(low_novelty_threshold=0.3)
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-1", "score": 0.75, "distance": 0.25},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        assert result.novelty_score == pytest.approx(0.25)
        assert result.salience_adjustment == pytest.approx(-0.10)  # penalty (<= threshold)

    @pytest.mark.asyncio
    async def test_boundary_just_above_low_threshold(self):
        """Novelty just above low_novelty_threshold should get neutral."""
        scorer = NoveltyScorer(low_novelty_threshold=0.3)
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-1", "score": 0.69, "distance": 0.31},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        assert result.novelty_score == pytest.approx(0.31)
        assert result.salience_adjustment == 0.0  # neutral

    @pytest.mark.asyncio
    async def test_boundary_just_below_high_threshold(self):
        """Novelty just below high_novelty_threshold should get neutral."""
        scorer = NoveltyScorer(high_novelty_threshold=0.7)
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-1", "score": 0.31, "distance": 0.69},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        assert result.novelty_score == pytest.approx(0.69)
        assert result.salience_adjustment == 0.0  # neutral

    @pytest.mark.asyncio
    async def test_exact_duplicate_score_zero(self):
        """Perfect similarity (score=1.0) should yield novelty=0.0."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "dup-node", "score": 1.0, "distance": 0.0},
        ])

        result = await scorer.score("exact duplicate", "user-1", mock_vi)

        assert result.novelty_score == 0.0
        assert result.max_similarity == 1.0
        assert result.salience_adjustment == pytest.approx(-0.10)  # penalty
        assert result.is_redundant is True

    @pytest.mark.asyncio
    async def test_search_called_with_correct_arguments(self):
        """search() should be called with content, user_id, and k."""
        scorer = NoveltyScorer(search_k=7)
        mock_vi = _make_mock_vector_index(search_results=[])

        await scorer.score("my content", "user-42", mock_vi)

        mock_vi.search.assert_called_once_with("my content", "user-42", k=7)

    @pytest.mark.asyncio
    async def test_exclude_node_does_not_affect_unrelated(self):
        """exclude_node_id should only filter the specified node."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-a", "score": 0.90, "distance": 0.10},
            {"node_id": "node-b", "score": 0.70, "distance": 0.30},
            {"node_id": "node-c", "score": 0.50, "distance": 0.50},
        ])

        result = await scorer.score(
            "content", "user-1", mock_vi,
            exclude_node_id="node-b",
        )

        # node-b filtered out; node-a and node-c remain
        assert len(result.nearest_neighbors) == 2
        neighbor_ids = [nid for nid, _ in result.nearest_neighbors]
        assert "node-b" not in neighbor_ids
        assert "node-a" in neighbor_ids
        assert "node-c" in neighbor_ids
        assert result.max_similarity == pytest.approx(0.90)

    @pytest.mark.asyncio
    async def test_no_exclude_node_returns_all(self):
        """Without exclude_node_id, all results should be returned."""
        scorer = NoveltyScorer()
        mock_vi = _make_mock_vector_index(search_results=[
            {"node_id": "node-a", "score": 0.80, "distance": 0.20},
            {"node_id": "node-b", "score": 0.60, "distance": 0.40},
        ])

        result = await scorer.score("content", "user-1", mock_vi)

        assert len(result.nearest_neighbors) == 2
