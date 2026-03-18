"""Tests for CrossEncoderReranker (neural cross-encoder reranking).

Tests mock sentence_transformers.CrossEncoder to avoid the heavy
dependency in CI. Covers: ImportError messaging, blending formula,
re-sorting, top_k boundary, and empty candidates.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.types import NodeType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_candidate(
    content: str = "test content",
    composite_score: float = 0.5,
    node_id=None,
) -> RetrievalCandidate:
    """Create a RetrievalCandidate with the given composite score."""
    node = MemoryNode(
        id=node_id or uuid4(),
        user_id="user-1",
        node_type=NodeType.FACT,
        content=content,
    )
    return RetrievalCandidate(
        node=node,
        paths=["VECTOR"],
        path_count=1,
        composite_score=composite_score,
    )


# ---------------------------------------------------------------------------
# ImportError Test
# ---------------------------------------------------------------------------


class TestCrossEncoderImportError:
    """CrossEncoderReranker raises ImportError when sentence-transformers missing."""

    def test_import_error_with_helpful_message(self):
        """_ensure_model raises ImportError with install instructions."""
        from prme.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()

        with patch.dict("sys.modules", {"sentence_transformers": None}):
            with pytest.raises(ImportError, match="sentence-transformers is required"):
                reranker._ensure_model()


# ---------------------------------------------------------------------------
# Reranking Logic Tests
# ---------------------------------------------------------------------------


class TestCrossEncoderReranking:
    """Tests for rerank() with mocked CrossEncoder scores."""

    @pytest.fixture()
    def mock_cross_encoder(self):
        """Fixture providing a mocked CrossEncoder class."""
        import numpy as np

        mock_model = MagicMock()
        # predict() returns raw logit scores (before sigmoid)
        mock_model.predict = MagicMock(
            side_effect=lambda pairs, batch_size=64: np.array(
                [2.0 - i * 0.5 for i in range(len(pairs))]
            )
        )
        return mock_model

    async def test_blending_formula(self, mock_cross_encoder):
        """Blended score = (1 - prior_weight) * sigmoid(ce) + prior_weight * original."""
        import numpy as np

        from prme.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        reranker._model = mock_cross_encoder

        candidates = [
            _make_candidate(content="doc A", composite_score=0.8),
            _make_candidate(content="doc B", composite_score=0.6),
        ]

        result = await reranker.rerank(
            query="test query",
            candidates=candidates,
            top_k=10,
            prior_weight=0.3,
        )

        # Verify blending for each candidate.
        # mock returns [2.0, 1.5] as raw scores.
        raw_scores = np.array([2.0, 1.5])
        ce_scores = 1.0 / (1.0 + np.exp(-raw_scores))

        expected_0 = 0.7 * ce_scores[0] + 0.3 * 0.8
        expected_1 = 0.7 * ce_scores[1] + 0.3 * 0.6

        assert abs(result[0].composite_score - expected_0) < 1e-6
        assert abs(result[1].composite_score - expected_1) < 1e-6

    async def test_reranking_resorts_candidates(self, mock_cross_encoder):
        """Reranking re-sorts candidates by blended score descending."""
        import numpy as np

        from prme.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        # Override predict to reverse the original ranking:
        # First candidate gets low CE score, second gets high.
        reranker._model = MagicMock()
        reranker._model.predict = MagicMock(
            side_effect=lambda pairs, batch_size=64: np.array(
                [-2.0, 3.0]  # sigmoid(-2) ~ 0.12, sigmoid(3) ~ 0.95
            )
        )

        candidates = [
            _make_candidate(content="originally first", composite_score=0.9),
            _make_candidate(content="originally second", composite_score=0.1),
        ]

        result = await reranker.rerank(
            query="test", candidates=candidates, top_k=10, prior_weight=0.3,
        )

        # Second candidate should now be first (high CE score).
        assert result[0].node.content == "originally second"
        assert result[1].node.content == "originally first"
        assert result[0].composite_score > result[1].composite_score

    async def test_candidates_beyond_top_k_retain_original_scores(
        self, mock_cross_encoder,
    ):
        """Candidates beyond top_k keep their original composite scores."""
        from prme.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        reranker._model = mock_cross_encoder

        candidates = [
            _make_candidate(content=f"doc {i}", composite_score=1.0 - i * 0.1)
            for i in range(5)
        ]
        # Store original scores for candidates beyond top_k=2.
        original_scores = [c.composite_score for c in candidates]

        result = await reranker.rerank(
            query="test", candidates=candidates, top_k=2, prior_weight=0.3,
        )

        # Candidates at index 2, 3, 4 should retain original scores.
        assert result[2].composite_score == original_scores[2]
        assert result[3].composite_score == original_scores[3]
        assert result[4].composite_score == original_scores[4]

        # Candidates at index 0, 1 should have been reranked (scores differ).
        # The mock returns different CE scores, so blended != original.
        assert len(result) == 5

    async def test_empty_candidates_returns_empty(self):
        """Reranking an empty list returns an empty list."""
        from prme.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        # No model needed for empty input.

        result = await reranker.rerank(
            query="test", candidates=[], top_k=10,
        )

        assert result == []

    async def test_deterministic_tiebreaking(self, mock_cross_encoder):
        """Candidates with equal blended scores are tie-broken by node ID."""
        import numpy as np
        from uuid import UUID

        from prme.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()
        # Both get the same CE score.
        reranker._model = MagicMock()
        reranker._model.predict = MagicMock(
            side_effect=lambda pairs, batch_size=64: np.array([1.0, 1.0])
        )

        id_a = UUID("00000000-0000-0000-0000-000000000001")
        id_b = UUID("00000000-0000-0000-0000-000000000002")

        candidates = [
            _make_candidate(content="doc B", composite_score=0.5, node_id=id_b),
            _make_candidate(content="doc A", composite_score=0.5, node_id=id_a),
        ]

        result = await reranker.rerank(
            query="test", candidates=candidates, top_k=10, prior_weight=0.3,
        )

        # Same blended score -> tie-broken by str(node.id) ascending.
        assert str(result[0].node.id) < str(result[1].node.id)
