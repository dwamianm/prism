"""Tests for supersedence-aware scoring in the retrieval pipeline.

Validates that:
- Queries about current state boost candidates with update language.
- Regular (non-current-state) queries do not receive boosts.
- Recency weight rebalancing works correctly for current-state queries.
- Update language detection regex matches expected patterns.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, ScoringWeights
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate, ScoreTrace
from prme.retrieval.scoring import (
    _has_update_language,
    _is_current_state_query,
    compute_composite_score,
    score_and_rank,
)
from prme.types import (
    EpistemicType,
    NodeType,
    QueryIntent,
    RetrievalMode,
    Scope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(
    *,
    content: str = "test content",
    confidence: float = 0.5,
    salience: float = 0.5,
    updated_at: datetime | None = None,
    epistemic_type: EpistemicType = EpistemicType.OBSERVED,
) -> MemoryNode:
    """Create a MemoryNode for testing."""
    ts = updated_at or datetime.now(timezone.utc)
    return MemoryNode(
        id=uuid4(),
        user_id="user-1",
        node_type=NodeType.FACT,
        content=content,
        confidence=confidence,
        salience=salience,
        confidence_base=confidence,
        salience_base=salience,
        updated_at=ts,
        last_reinforced_at=ts,
        epistemic_type=epistemic_type,
    )


def _make_candidate(
    *,
    node: MemoryNode | None = None,
    content: str = "test content",
    semantic_score: float = 0.0,
    lexical_score: float = 0.0,
    graph_proximity: float = 0.0,
    path_count: int = 1,
    **node_kwargs,
) -> RetrievalCandidate:
    """Create a RetrievalCandidate with sensible defaults."""
    if node is None:
        node = _make_node(content=content, **node_kwargs)
    return RetrievalCandidate(
        node=node,
        paths=["VECTOR"] * path_count,
        path_count=path_count,
        semantic_score=semantic_score,
        lexical_score=lexical_score,
        graph_proximity=graph_proximity,
    )


def _make_query_analysis(
    query: str,
    intent: QueryIntent = QueryIntent.SEMANTIC,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> QueryAnalysis:
    """Create a QueryAnalysis for testing."""
    return QueryAnalysis(
        query=query,
        intent=intent,
        entities=[],
        temporal_signals=[],
        time_from=time_from,
        time_to=time_to,
        retrieval_mode=RetrievalMode.DEFAULT,
    )


# ---------------------------------------------------------------------------
# Update Language Detection Tests
# ---------------------------------------------------------------------------

class TestUpdateLanguageDetection:
    """Tests for _has_update_language regex matching."""

    @pytest.mark.parametrize(
        "content",
        [
            "The team changed to using React",
            "We switched to PostgreSQL last month",
            "The service migrated from DigitalOcean to AWS",
            "The office moved to downtown",
            "The old framework was replaced by a new one",
            "The config was updated to version 3.0",
            "The system now uses Redis for caching",
            "We no longer use Jenkins for CI",
            "The codebase was rewritten in Rust",
            "The database was upgraded to version 15",
            "The new provider is AWS",
            "This change is effective immediately",
            "MIGRATED FROM heroku to AWS",  # case insensitive
        ],
    )
    def test_detects_update_language(self, content: str):
        """Content with update language should be detected."""
        assert _has_update_language(content) is True

    @pytest.mark.parametrize(
        "content",
        [
            "The application is hosted on DigitalOcean",
            "We use PostgreSQL for our database",
            "The team prefers React for frontend",
            "The service runs on Kubernetes",
            "Our CI pipeline uses GitHub Actions",
            "The project started in January",
            "",  # empty string
        ],
    )
    def test_no_update_language(self, content: str):
        """Content without update language should not be detected."""
        assert _has_update_language(content) is False


# ---------------------------------------------------------------------------
# Current State Query Detection Tests
# ---------------------------------------------------------------------------

class TestCurrentStateQueryDetection:
    """Tests for _is_current_state_query."""

    @pytest.mark.parametrize(
        "query",
        [
            "What is the current cloud provider?",
            "What database are we currently using?",
            "What framework does the team use now?",
            "What is the latest deployment status?",
            "What CI tool are we using today?",
            "What is our stack at the moment?",
            "What language is the project written in these days?",
            "What is presently the main database?",
        ],
    )
    def test_detects_current_state_query(self, query: str):
        """Queries with current-state language should be detected."""
        analysis = _make_query_analysis(query)
        assert _is_current_state_query(analysis) is True

    def test_temporal_intent_no_time_reference_is_current(self):
        """TEMPORAL intent with no time_from/time_to implies current state."""
        analysis = _make_query_analysis(
            "When did we last update?",
            intent=QueryIntent.TEMPORAL,
            time_from=None,
            time_to=None,
        )
        assert _is_current_state_query(analysis) is True

    def test_temporal_intent_with_time_reference_is_not_current(self):
        """TEMPORAL intent with specific time references is historical, not current."""
        now = datetime.now(timezone.utc)
        analysis = _make_query_analysis(
            "What was the status last week?",
            intent=QueryIntent.TEMPORAL,
            time_from=now - timedelta(days=7),
            time_to=now,
        )
        assert _is_current_state_query(analysis) is False

    @pytest.mark.parametrize(
        "query",
        [
            "What was the original database?",
            "Tell me about PostgreSQL",
            "How does the authentication work?",
            "Who designed the architecture?",
        ],
    )
    def test_non_current_state_queries(self, query: str):
        """Queries not asking about current state should not be detected."""
        analysis = _make_query_analysis(query)
        assert _is_current_state_query(analysis) is False


# ---------------------------------------------------------------------------
# Supersedence-Aware Scoring Tests
# ---------------------------------------------------------------------------

class TestSupersedenceAwareScoring:
    """Tests for score_and_rank with supersedence-aware recency boosting."""

    def test_current_state_query_boosts_update_candidates(self):
        """Current-state query should rank update-language candidates higher."""
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)
        sixty_days_ago = now - timedelta(days=60)

        # Old fact: high semantic match, older
        old_fact = _make_candidate(
            content="The application is hosted on DigitalOcean",
            semantic_score=0.95,
            lexical_score=0.90,
            graph_proximity=0.0,
            updated_at=sixty_days_ago,
        )

        # Update fact: lower semantic match, newer, has update language
        update_fact = _make_candidate(
            content="The application migrated from DigitalOcean to AWS",
            semantic_score=0.80,
            lexical_score=0.70,
            graph_proximity=0.0,
            updated_at=thirty_days_ago,
        )

        analysis = _make_query_analysis("What is the current cloud provider?")

        # With supersedence-aware scoring
        ranked, traces = score_and_rank(
            [old_fact, update_fact],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=analysis,
        )

        # The update fact should rank higher despite lower semantic score
        assert ranked[0].node.content == update_fact.node.content

    def test_regular_query_does_not_boost(self):
        """Non-current-state queries should not apply supersedence boost."""
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)
        sixty_days_ago = now - timedelta(days=60)

        # Old fact: high semantic match
        old_fact = _make_candidate(
            content="The application is hosted on DigitalOcean",
            semantic_score=0.95,
            lexical_score=0.90,
            graph_proximity=0.0,
            updated_at=sixty_days_ago,
        )

        # Update fact: lower semantic match
        update_fact = _make_candidate(
            content="The application migrated from DigitalOcean to AWS",
            semantic_score=0.80,
            lexical_score=0.70,
            graph_proximity=0.0,
            updated_at=thirty_days_ago,
        )

        analysis = _make_query_analysis("Tell me about the infrastructure")

        # Without current-state detection, old fact should rank higher
        ranked, traces = score_and_rank(
            [old_fact, update_fact],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=analysis,
        )

        # The old fact should rank higher due to stronger semantic/lexical match
        assert ranked[0].node.content == old_fact.node.content

    def test_no_query_analysis_does_not_boost(self):
        """When query_analysis is None, no supersedence boost is applied."""
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        candidate = _make_candidate(
            content="The application migrated from DigitalOcean to AWS",
            semantic_score=0.80,
            lexical_score=0.70,
            updated_at=thirty_days_ago,
        )

        # Score without query_analysis
        ranked_without, traces_without = score_and_rank(
            [candidate],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=None,
        )

        # Score with non-current-state analysis
        analysis = _make_query_analysis("Tell me about the infrastructure")
        ranked_with, traces_with = score_and_rank(
            [candidate],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=analysis,
        )

        # Both should produce the same score (no boost)
        assert ranked_without[0].composite_score == ranked_with[0].composite_score

    def test_recency_weight_increase_for_current_queries(self):
        """Current-state queries should use increased recency weight (0.25)."""
        now = datetime.now(timezone.utc)
        # Use a longer time gap so the recency boost has a visible effect
        # (1 day ago recency is ~0.98, so 1.5x boost gets capped at 1.0)
        thirty_days_ago = now - timedelta(days=30)

        # Create separate candidates for each scoring call (score_and_rank
        # mutates candidates in-place).
        def make_update_candidate():
            return _make_candidate(
                content="The team switched to using Kubernetes",
                semantic_score=0.70,
                lexical_score=0.60,
                graph_proximity=0.0,
                updated_at=thirty_days_ago,
            )

        analysis = _make_query_analysis("What is the current deployment platform?")

        # Score with supersedence-aware analysis
        ranked_current, traces_current = score_and_rank(
            [make_update_candidate()],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=analysis,
        )

        # Score without (regular query)
        regular_analysis = _make_query_analysis("Tell me about infrastructure")
        ranked_regular, traces_regular = score_and_rank(
            [make_update_candidate()],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=regular_analysis,
        )

        # The current-state score should be higher because:
        # 1. Recency weight is increased (0.25 vs 0.10)
        # 2. Recency score is boosted 1.5x for update-language content
        # (semantic/lexical weights are correspondingly reduced)
        assert ranked_current[0].composite_score > ranked_regular[0].composite_score

    def test_recency_boost_capped_at_one(self):
        """Boosted recency factor should not exceed 1.0."""
        now = datetime.now(timezone.utc)
        # Very recent: recency will be close to 1.0, so 1.5x would exceed 1.0
        just_now = now - timedelta(minutes=1)

        candidate = _make_candidate(
            content="The config was updated to v2",
            semantic_score=0.50,
            lexical_score=0.50,
            updated_at=just_now,
        )

        analysis = _make_query_analysis("What is the current config version?")

        ranked, traces = score_and_rank(
            [candidate],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=analysis,
        )

        # The recency_factor in the trace should be capped at 1.0
        assert traces[0].recency_factor <= 1.0

    def test_non_update_candidates_not_recency_boosted(self):
        """Even in current-state queries, candidates without update
        language should not get the 2.0x recency boost."""
        now = datetime.now(timezone.utc)
        one_day_ago = now - timedelta(days=1)
        ten_days_ago = now - timedelta(days=10)

        # Recent candidate (recency reference point)
        recent = _make_candidate(
            content="Some recent fact",
            semantic_score=0.50,
            lexical_score=0.50,
            updated_at=now,
        )

        # Older non-update content — should NOT get recency boost
        older = _make_candidate(
            content="The database is PostgreSQL",
            semantic_score=0.80,
            lexical_score=0.70,
            updated_at=ten_days_ago,
        )

        analysis = _make_query_analysis("What is the current database?")

        ranked, traces = score_and_rank(
            [recent, older],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=analysis,
        )

        # Find the trace for the older candidate (by content)
        older_trace = next(
            t for c, t in zip(ranked, traces) if c.node.content == "The database is PostgreSQL"
        )

        # The recency factor should be raw relative decay (10 days gap) with
        # the current-state lambda (0.05), NOT the 2.0x update-language boost.
        current_state_lambda = 0.05
        expected_recency = math.exp(-current_state_lambda * 10.0)
        assert abs(older_trace.recency_factor - expected_recency) < 1e-6

    def test_weight_redistribution_sums_to_one(self):
        """Adjusted weights for current-state queries must still sum to 1.0."""
        now = datetime.now(timezone.utc)

        candidate = _make_candidate(
            content="switched to new framework",
            semantic_score=0.50,
            lexical_score=0.50,
            updated_at=now,
        )

        analysis = _make_query_analysis("What is the current framework?")

        # We verify indirectly: if weights don't sum to 1.0, ScoringWeights
        # validator raises ValueError. If score_and_rank succeeds, the
        # adjusted weights are valid.
        ranked, traces = score_and_rank(
            [candidate],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=analysis,
        )

        # Should succeed without error and produce valid score
        assert ranked[0].composite_score > 0.0

    def test_deterministic_with_supersedence_scoring(self):
        """Supersedence-aware scoring should be deterministic."""
        now = datetime.now(timezone.utc)
        ten_days_ago = now - timedelta(days=10)
        thirty_days_ago = now - timedelta(days=30)

        old_fact = _make_candidate(
            content="Hosted on DigitalOcean",
            semantic_score=0.95,
            lexical_score=0.90,
            updated_at=thirty_days_ago,
        )
        update_fact = _make_candidate(
            content="Migrated from DigitalOcean to AWS",
            semantic_score=0.80,
            lexical_score=0.70,
            updated_at=ten_days_ago,
        )

        analysis = _make_query_analysis("What is the current cloud provider?")

        scores = []
        for _ in range(50):
            ranked, _ = score_and_rank(
                [old_fact, update_fact],
                DEFAULT_SCORING_WEIGHTS,
                now=now,
                query_analysis=analysis,
            )
            scores.append(
                (ranked[0].composite_score, ranked[1].composite_score)
            )

        # All 50 runs should produce identical scores
        assert len(set(scores)) == 1, f"Non-deterministic: {set(scores)}"
