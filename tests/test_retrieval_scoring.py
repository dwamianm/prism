"""TDD tests for epistemic filtering and composite scoring.

RED phase: All tests import from filtering.py and scoring.py which
don't exist yet -- tests must fail on import before GREEN phase.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, ScoringWeights
from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate, ScoreTrace
from prme.types import (
    DEFAULT_EXCLUDED_EPISTEMIC,
    EPISTEMIC_WEIGHTS,
    EpistemicType,
    LifecycleState,
    NodeType,
    RetrievalMode,
    Scope,
)

# --- Imports under test (these modules don't exist yet -> RED) ---
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.scoring import compute_composite_score, score_and_rank


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_node(
    *,
    node_id: UUID | None = None,
    user_id: str = "user-1",
    confidence: float = 0.5,
    salience: float = 0.5,
    updated_at: datetime | None = None,
    epistemic_type: EpistemicType | None = None,
) -> MemoryNode:
    """Create a MemoryNode stub for testing.

    epistemic_type is set directly via native MemoryNode field.
    salience_base and confidence_base are set to match salience/confidence
    so that virtual decay (RFC-0015) produces the expected raw values for
    freshly-created nodes.
    """
    ts = updated_at or datetime.now(timezone.utc)
    kwargs: dict = {
        "id": node_id or uuid4(),
        "user_id": user_id,
        "node_type": NodeType.FACT,
        "content": "test content",
        "confidence": confidence,
        "salience": salience,
        "confidence_base": confidence,
        "salience_base": salience,
        "updated_at": ts,
        "last_reinforced_at": ts,
    }
    if epistemic_type is not None:
        kwargs["epistemic_type"] = epistemic_type
    return MemoryNode(**kwargs)


def _make_candidate(
    *,
    node: MemoryNode | None = None,
    semantic_score: float = 0.0,
    lexical_score: float = 0.0,
    graph_proximity: float = 0.0,
    path_count: int = 1,
    **node_kwargs,
) -> RetrievalCandidate:
    """Create a RetrievalCandidate with sensible defaults."""
    if node is None:
        node = _make_node(**node_kwargs)
    return RetrievalCandidate(
        node=node,
        paths=["VECTOR"] * path_count,
        path_count=path_count,
        semantic_score=semantic_score,
        lexical_score=lexical_score,
        graph_proximity=graph_proximity,
    )


# ---------------------------------------------------------------------------
# Epistemic Filtering Tests
# ---------------------------------------------------------------------------

class TestEpistemicFiltering:
    """Tests for filter_epistemic (retrieval Stage 4)."""

    def test_filter_excludes_deprecated_in_default_mode(self):
        """DEFAULT mode removes DEPRECATED and HYPOTHETICAL candidates."""
        candidates = [
            _make_candidate(epistemic_type=EpistemicType.OBSERVED),
            _make_candidate(epistemic_type=EpistemicType.ASSERTED),
            _make_candidate(epistemic_type=EpistemicType.DEPRECATED),
            _make_candidate(epistemic_type=EpistemicType.HYPOTHETICAL),
            _make_candidate(epistemic_type=EpistemicType.INFERRED),
        ]

        kept, excluded = filter_epistemic(candidates, RetrievalMode.DEFAULT)

        assert len(kept) == 3
        assert len(excluded) == 2

        excluded_reasons = {e.reason for e in excluded}
        assert "epistemic_filtered:deprecated" in excluded_reasons
        assert "epistemic_filtered:hypothetical" in excluded_reasons

    def test_filter_keeps_all_in_explicit_mode(self):
        """EXPLICIT mode retains all candidates regardless of epistemic type."""
        candidates = [
            _make_candidate(epistemic_type=EpistemicType.OBSERVED),
            _make_candidate(epistemic_type=EpistemicType.DEPRECATED),
            _make_candidate(epistemic_type=EpistemicType.HYPOTHETICAL),
        ]

        kept, excluded = filter_epistemic(candidates, RetrievalMode.EXPLICIT)

        assert len(kept) == 3
        assert len(excluded) == 0

    def test_filter_handles_default_epistemic_type(self):
        """Candidates with default epistemic_type (ASSERTED) are kept."""
        # Node with default epistemic_type (ASSERTED)
        node_default = MemoryNode(
            id=uuid4(),
            user_id="user-1",
            node_type=NodeType.FACT,
            content="test",
        )
        assert node_default.epistemic_type == EpistemicType.ASSERTED
        candidates = [
            RetrievalCandidate(
                node=node_default,
                paths=["VECTOR"],
                path_count=1,
            ),
        ]

        kept, excluded = filter_epistemic(candidates, RetrievalMode.DEFAULT)

        assert len(kept) == 1
        assert len(excluded) == 0


# ---------------------------------------------------------------------------
# Composite Score Tests
# ---------------------------------------------------------------------------

class TestCompositeScoring:
    """Tests for compute_composite_score and score_and_rank (Stage 5)."""

    def test_composite_score_formula(self):
        """Known inputs produce expected hand-calculated output.

        candidate: semantic=0.95, lexical=0.0, graph=0.0,
                   salience_base=0.5, confidence_base=0.8, recency=30 days,
                   epistemic=OBSERVED, path_count=1

        With OBSERVED and MEDIUM profile at 30 days:
        - effective_salience = 0.5 * exp(-0.02*30) = 0.5 * 0.5488.. = 0.2744..
        - effective_confidence = 0.8 (OBSERVED exempt from confidence decay < 180d)

        additive = 0.25*0.95 + 0.20*0.0 + 0.20*0.0
                   + 0.10*exp(-0.01*30) + 0.10*eff_sal + 0.15*eff_conf
        * epistemic(OBSERVED=1.0)
        path_score = min(1/3, 1.0) = 0.3333..
        """
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        node = _make_node(
            confidence=0.8,
            salience=0.5,
            updated_at=thirty_days_ago,
            epistemic_type=EpistemicType.OBSERVED,
        )
        candidate = _make_candidate(
            node=node,
            semantic_score=0.95,
            lexical_score=0.0,
            graph_proximity=0.0,
            path_count=1,
        )

        weights = DEFAULT_SCORING_WEIGHTS
        trace = compute_composite_score(candidate, weights, now=now)

        # Virtual decay (RFC-0015): OBSERVED + MEDIUM at 30 days
        expected_salience = 0.5 * math.exp(-0.02 * 30)
        expected_confidence = 0.8  # OBSERVED: no confidence decay < 180 days

        # Verify individual components
        assert trace.semantic_similarity == 0.95
        assert trace.lexical_relevance == 0.0
        assert trace.graph_proximity == 0.0
        assert abs(trace.salience - expected_salience) < 1e-6
        assert abs(trace.confidence - expected_confidence) < 1e-6
        assert trace.epistemic_weight == 1.0  # OBSERVED
        assert abs(trace.path_score - 1.0 / 3.0) < 1e-6

        # Recency factor: exp(-0.01 * 30 days)
        expected_recency = math.exp(-weights.recency_lambda * 30)
        assert abs(trace.recency_factor - expected_recency) < 1e-6

        # Composite: additive * epistemic * node_type_boost
        expected_additive = (
            weights.w_semantic * 0.95
            + weights.w_lexical * 0.0
            + weights.w_graph * 0.0
            + weights.w_recency * expected_recency
            + weights.w_salience * expected_salience
            + weights.w_confidence * expected_confidence
        )
        node_type_boost = weights.node_type_boost.get("fact", 1.0)  # 1.15
        expected_composite = expected_additive * 1.0 * node_type_boost  # OBSERVED
        assert abs(trace.composite_score - round(expected_composite, 10)) < 1e-6
        assert trace.node_type_boost == node_type_boost

    def test_composite_score_determinism(self):
        """Same inputs 100 times produce identical output every time."""
        now = datetime.now(timezone.utc)
        node = _make_node(
            confidence=0.7,
            salience=0.6,
            updated_at=now - timedelta(days=10),
            epistemic_type=EpistemicType.ASSERTED,
        )
        candidate = _make_candidate(
            node=node,
            semantic_score=0.8,
            lexical_score=0.5,
            graph_proximity=0.4,
            path_count=2,
        )
        weights = DEFAULT_SCORING_WEIGHTS

        scores = [
            compute_composite_score(candidate, weights, now=now).composite_score
            for _ in range(100)
        ]

        # All 100 scores must be identical
        assert len(set(scores)) == 1, f"Non-deterministic: {set(scores)}"

    def test_deterministic_tiebreaking(self):
        """Two candidates with same score but different IDs -> consistent order."""
        now = datetime.now(timezone.utc)

        # Create two candidates with identical scores but different IDs
        id_a = UUID("00000000-0000-0000-0000-000000000001")
        id_b = UUID("00000000-0000-0000-0000-000000000002")

        node_a = _make_node(
            node_id=id_a,
            confidence=0.5,
            salience=0.5,
            updated_at=now,
            epistemic_type=EpistemicType.ASSERTED,
        )
        node_b = _make_node(
            node_id=id_b,
            confidence=0.5,
            salience=0.5,
            updated_at=now,
            epistemic_type=EpistemicType.ASSERTED,
        )

        candidate_a = _make_candidate(
            node=node_a,
            semantic_score=0.5,
            lexical_score=0.3,
            graph_proximity=0.0,
            path_count=1,
        )
        candidate_b = _make_candidate(
            node=node_b,
            semantic_score=0.5,
            lexical_score=0.3,
            graph_proximity=0.0,
            path_count=1,
        )

        weights = DEFAULT_SCORING_WEIGHTS

        # Run 50 times with different input orderings
        for _ in range(50):
            ranked, traces = score_and_rank(
                [candidate_b, candidate_a], weights, now=now
            )
            # Both should have the same composite score
            assert ranked[0].composite_score == ranked[1].composite_score
            # Tie-break by node ID (str sort): id_a < id_b
            assert str(ranked[0].node.id) < str(ranked[1].node.id)

    def test_custom_weights_accepted(self):
        """Non-default weights that sum to 1.0 produce valid scoring."""
        now = datetime.now(timezone.utc)
        custom_weights = ScoringWeights(
            w_semantic=0.50,
            w_lexical=0.10,
            w_graph=0.15,
            w_recency=0.10,
            w_salience=0.05,
            w_confidence=0.10,
        )

        five_days_ago = now - timedelta(days=5)
        node = _make_node(
            confidence=0.8,
            salience=0.6,
            updated_at=five_days_ago,
            epistemic_type=EpistemicType.OBSERVED,
        )
        candidate = _make_candidate(
            node=node,
            semantic_score=0.9,
            lexical_score=0.7,
            graph_proximity=0.5,
            path_count=2,
        )

        trace = compute_composite_score(candidate, custom_weights, now=now)

        # Virtual decay: OBSERVED + MEDIUM at 5 days
        lam = 0.020  # MEDIUM default
        eff_salience = 0.6 * math.exp(-lam * 5)
        eff_confidence = 0.8  # OBSERVED: no confidence decay < 180d

        # Score should use custom weights with effective values
        expected_recency = math.exp(-custom_weights.recency_lambda * 5)
        expected_additive = (
            0.50 * 0.9
            + 0.10 * 0.7
            + 0.15 * 0.5
            + 0.10 * expected_recency
            + 0.05 * eff_salience
            + 0.10 * eff_confidence
        )
        node_type_boost = custom_weights.node_type_boost.get("fact", 1.0)  # 1.15
        expected_composite = expected_additive * 1.0 * node_type_boost  # OBSERVED
        assert abs(trace.composite_score - round(expected_composite, 10)) < 1e-6

    def test_score_trace_captures_all_components(self):
        """ScoreTrace has all 8 non-zero-where-expected values."""
        now = datetime.now(timezone.utc)
        two_days_ago = now - timedelta(days=2)
        node = _make_node(
            confidence=0.7,
            salience=0.8,
            updated_at=two_days_ago,
            epistemic_type=EpistemicType.INFERRED,
        )
        candidate = _make_candidate(
            node=node,
            semantic_score=0.85,
            lexical_score=0.60,
            graph_proximity=0.70,
            path_count=3,
        )

        trace = compute_composite_score(candidate, DEFAULT_SCORING_WEIGHTS, now=now)

        # All components should be populated
        assert trace.semantic_similarity == 0.85
        assert trace.lexical_relevance == 0.60
        assert trace.graph_proximity == 0.70
        assert trace.recency_factor > 0.0  # 2 days ago -> close to 1.0
        # Effective values after virtual decay (MEDIUM, 2 days, INFERRED)
        assert trace.salience > 0.0
        assert trace.salience <= 0.8  # decayed from base
        assert trace.confidence > 0.0
        assert trace.confidence <= 0.7  # decayed from base
        assert trace.epistemic_weight == EPISTEMIC_WEIGHTS[EpistemicType.INFERRED]
        assert trace.path_score == 1.0  # min(3/3, 1.0)
        assert trace.composite_score > 0.0

    def test_epistemic_weight_is_multiplicative(self):
        """HYPOTHETICAL(0.3) vs OBSERVED(1.0) -> score differs by ~0.3 factor."""
        now = datetime.now(timezone.utc)

        node_observed = _make_node(
            confidence=0.5,
            salience=0.5,
            updated_at=now,
            epistemic_type=EpistemicType.OBSERVED,
        )
        node_hypothetical = _make_node(
            confidence=0.5,
            salience=0.5,
            updated_at=now,
            epistemic_type=EpistemicType.HYPOTHETICAL,
        )

        candidate_obs = _make_candidate(
            node=node_observed,
            semantic_score=0.8,
            lexical_score=0.5,
            graph_proximity=0.3,
            path_count=1,
        )
        candidate_hyp = _make_candidate(
            node=node_hypothetical,
            semantic_score=0.8,
            lexical_score=0.5,
            graph_proximity=0.3,
            path_count=1,
        )

        trace_obs = compute_composite_score(candidate_obs, DEFAULT_SCORING_WEIGHTS, now=now)
        trace_hyp = compute_composite_score(candidate_hyp, DEFAULT_SCORING_WEIGHTS, now=now)

        # OBSERVED weight = 1.0, HYPOTHETICAL weight = 0.3
        # So score_hyp / score_obs should be approximately 0.3
        ratio = trace_hyp.composite_score / trace_obs.composite_score
        expected_ratio = (
            EPISTEMIC_WEIGHTS[EpistemicType.HYPOTHETICAL]
            / EPISTEMIC_WEIGHTS[EpistemicType.OBSERVED]
        )
        assert abs(ratio - expected_ratio) < 1e-6


# ---------------------------------------------------------------------------
# Score and Rank Integration Tests
# ---------------------------------------------------------------------------

class TestScoreAndRank:
    """Tests for score_and_rank composing scoring + sorting."""

    def test_score_and_rank_returns_sorted_by_score_desc(self):
        """Candidates are sorted by composite_score descending."""
        now = datetime.now(timezone.utc)

        low = _make_candidate(
            semantic_score=0.1,
            confidence=0.2,
            salience=0.2,
            updated_at=now,
            epistemic_type=EpistemicType.ASSERTED,
        )
        high = _make_candidate(
            semantic_score=0.95,
            confidence=0.9,
            salience=0.9,
            updated_at=now,
            epistemic_type=EpistemicType.OBSERVED,
        )

        ranked, traces = score_and_rank([low, high], DEFAULT_SCORING_WEIGHTS)

        assert ranked[0].composite_score >= ranked[1].composite_score
        assert len(traces) == 2

    def test_score_and_rank_sets_candidate_fields(self):
        """score_and_rank sets composite_score and score_trace on candidates."""
        now = datetime.now(timezone.utc)
        candidate = _make_candidate(
            semantic_score=0.5,
            confidence=0.5,
            salience=0.5,
            updated_at=now,
            epistemic_type=EpistemicType.ASSERTED,
        )

        ranked, traces = score_and_rank([candidate], DEFAULT_SCORING_WEIGHTS)

        assert ranked[0].composite_score > 0.0
        assert ranked[0].score_trace is not None
        assert ranked[0].score_trace.composite_score == ranked[0].composite_score
