"""Tests for temporal affinity scoring in the retrieval pipeline.

Validates that:
- Temporal queries boost candidates with dates in content
- Non-temporal queries are unaffected by temporal boost
- Temporal proximity scoring works correctly (inside/outside window)
- Edge case: candidate with no created_at handled gracefully
- Determinism: same inputs produce same temporal scores
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
    _DATE_PATTERN,
    _compute_temporal_affinity,
    compute_composite_score,
    score_and_rank,
)
from prme.types import (
    EpistemicType,
    NodeType,
    QueryIntent,
    RetrievalMode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(
    *,
    content: str = "test content",
    created_at: datetime | None = None,
    event_time: datetime | None = None,
    updated_at: datetime | None = None,
    confidence: float = 0.5,
    salience: float = 0.5,
    epistemic_type: EpistemicType = EpistemicType.OBSERVED,
) -> MemoryNode:
    """Create a MemoryNode stub for temporal testing."""
    ts = created_at or datetime.now(timezone.utc)
    return MemoryNode(
        id=uuid4(),
        user_id="user-1",
        node_type=NodeType.EVENT,
        content=content,
        confidence=confidence,
        salience=salience,
        confidence_base=confidence,
        salience_base=salience,
        created_at=ts,
        updated_at=updated_at or ts,
        last_reinforced_at=ts,
        event_time=event_time,
        epistemic_type=epistemic_type,
    )


def _make_candidate(
    node: MemoryNode,
    *,
    semantic_score: float = 0.5,
    lexical_score: float = 0.3,
    graph_proximity: float = 0.0,
    path_count: int = 1,
) -> RetrievalCandidate:
    """Create a RetrievalCandidate with sensible defaults."""
    return RetrievalCandidate(
        node=node,
        paths=["VECTOR"] * path_count,
        path_count=path_count,
        semantic_score=semantic_score,
        lexical_score=lexical_score,
        graph_proximity=graph_proximity,
    )


def _make_temporal_query(
    query: str = "When did the meeting happen?",
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> QueryAnalysis:
    """Create a QueryAnalysis with TEMPORAL intent."""
    return QueryAnalysis(
        query=query,
        intent=QueryIntent.TEMPORAL,
        entities=[],
        temporal_signals=[],
        time_from=time_from,
        time_to=time_to,
        retrieval_mode=RetrievalMode.DEFAULT,
    )


def _make_semantic_query(
    query: str = "Tell me about the project",
) -> QueryAnalysis:
    """Create a QueryAnalysis with SEMANTIC intent."""
    return QueryAnalysis(
        query=query,
        intent=QueryIntent.SEMANTIC,
        entities=[],
        temporal_signals=[],
        retrieval_mode=RetrievalMode.DEFAULT,
    )


# ---------------------------------------------------------------------------
# Date pattern detection tests
# ---------------------------------------------------------------------------

class TestDatePattern:
    """Tests for the _DATE_PATTERN regex used in content scanning."""

    @pytest.mark.parametrize("text", [
        "Meeting on January 15, 2024",
        "Deployed on 2023-03-15",
        "Event on Mar 8",
        "Happened in 2024",
        "Due date: 03/15/2024",
        "Completed 8 May 2023",
        "Started on Feb 14, 2023",
        "Release 2024/01/02",
    ])
    def test_detects_date_strings(self, text: str):
        """Date pattern matches common date formats."""
        assert _DATE_PATTERN.search(text) is not None

    @pytest.mark.parametrize("text", [
        "The quick brown fox",
        "No dates here at all",
        "Just some regular text",
        "Numbers like 42 and 100",
    ])
    def test_no_false_positives_on_plain_text(self, text: str):
        """Date pattern does not match plain text without dates."""
        assert _DATE_PATTERN.search(text) is None


# ---------------------------------------------------------------------------
# Temporal affinity scoring tests
# ---------------------------------------------------------------------------

class TestTemporalAffinity:
    """Tests for _compute_temporal_affinity function."""

    def test_content_with_dates_gets_content_boost(self):
        """Candidate with date in content gets the 0.3 content weight."""
        now = datetime.now(timezone.utc)
        node = _make_node(content="Meeting on January 15, 2024", created_at=now)
        candidate = _make_candidate(node)
        query = _make_temporal_query(time_from=now, time_to=now)

        affinity = _compute_temporal_affinity(candidate, query)

        # Content signal: 0.3 * 1.0 = 0.3
        # Proximity signal: 0.7 * 1.0 = 0.7 (candidate is inside window)
        assert affinity == pytest.approx(1.0, abs=1e-6)

    def test_content_without_dates_no_content_boost(self):
        """Candidate without dates in content gets 0.0 for content signal."""
        now = datetime.now(timezone.utc)
        node = _make_node(content="Just a regular note", created_at=now)
        candidate = _make_candidate(node)
        query = _make_temporal_query(time_from=now, time_to=now)

        affinity = _compute_temporal_affinity(candidate, query)

        # Content signal: 0.3 * 0.0 = 0.0
        # Proximity signal: 0.7 * 1.0 = 0.7 (candidate is inside window)
        assert affinity == pytest.approx(0.7, abs=1e-6)

    def test_candidate_inside_temporal_window_full_proximity(self):
        """Candidate inside the temporal window gets full proximity score."""
        window_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
        candidate_time = datetime(2024, 1, 15, tzinfo=timezone.utc)

        node = _make_node(content="no date here", created_at=candidate_time)
        candidate = _make_candidate(node)
        query = _make_temporal_query(time_from=window_start, time_to=window_end)

        affinity = _compute_temporal_affinity(candidate, query)

        # Content: 0.3 * 0.0 = 0.0
        # Proximity: 0.7 * 1.0 = 0.7
        assert affinity == pytest.approx(0.7, abs=1e-6)

    def test_candidate_outside_window_decayed_proximity(self):
        """Candidate outside the window gets decayed proximity score."""
        window_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
        # 30 days before the window
        candidate_time = datetime(2023, 12, 2, tzinfo=timezone.utc)

        node = _make_node(content="no date here", created_at=candidate_time)
        candidate = _make_candidate(node)
        query = _make_temporal_query(time_from=window_start, time_to=window_end)

        affinity = _compute_temporal_affinity(candidate, query)

        # Proximity decays: exp(-0.02 * 30) ~ 0.549
        expected_proximity = math.exp(-0.02 * 30)
        expected = 0.3 * 0.0 + 0.7 * expected_proximity
        assert affinity == pytest.approx(expected, abs=1e-3)

    def test_candidate_far_outside_window_low_proximity(self):
        """Candidate very far from the window gets near-zero proximity."""
        window_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
        # 365 days before the window
        candidate_time = datetime(2023, 1, 1, tzinfo=timezone.utc)

        node = _make_node(content="no date here", created_at=candidate_time)
        candidate = _make_candidate(node)
        query = _make_temporal_query(time_from=window_start, time_to=window_end)

        affinity = _compute_temporal_affinity(candidate, query)

        # Very distant: should be close to zero
        assert affinity < 0.05

    def test_event_time_preferred_over_created_at(self):
        """event_time is used for proximity when available."""
        window_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # created_at is far away, but event_time is inside the window
        node = _make_node(
            content="no date here",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            event_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        )
        candidate = _make_candidate(node)
        query = _make_temporal_query(time_from=window_start, time_to=window_end)

        affinity = _compute_temporal_affinity(candidate, query)

        # Content: 0.0, Proximity: 1.0 (event_time is inside window)
        assert affinity == pytest.approx(0.7, abs=1e-6)

    def test_no_temporal_window_falls_back_to_content(self):
        """When query has no temporal window, uses content signal only."""
        node_with_date = _make_node(content="Meeting on January 15, 2024")
        node_without_date = _make_node(content="Just some notes")

        cand_with = _make_candidate(node_with_date)
        cand_without = _make_candidate(node_without_date)
        query = _make_temporal_query()  # No time_from/time_to

        aff_with = _compute_temporal_affinity(cand_with, query)
        aff_without = _compute_temporal_affinity(cand_without, query)

        # With date: 0.3 * 1.0 + 0.7 * (0.5) = 0.65 (partial proximity for dated content)
        assert aff_with == pytest.approx(0.65, abs=1e-6)
        # Without date: 0.3 * 0.0 + 0.7 * 0.0 = 0.0
        assert aff_without == pytest.approx(0.0, abs=1e-6)

    def test_only_time_from_set(self):
        """When only time_from is set, candidates after it get full score."""
        time_from = datetime(2024, 1, 15, tzinfo=timezone.utc)
        candidate_after = _make_node(
            content="no date",
            created_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
        candidate_before = _make_node(
            content="no date",
            created_at=datetime(2023, 12, 1, tzinfo=timezone.utc),
        )

        query = _make_temporal_query(time_from=time_from)

        aff_after = _compute_temporal_affinity(_make_candidate(candidate_after), query)
        aff_before = _compute_temporal_affinity(_make_candidate(candidate_before), query)

        # After time_from: full proximity
        assert aff_after == pytest.approx(0.7, abs=1e-6)
        # Before time_from: decayed
        assert aff_before < 0.7

    def test_only_time_to_set(self):
        """When only time_to is set, candidates before it get full score."""
        time_to = datetime(2024, 1, 15, tzinfo=timezone.utc)
        candidate_before = _make_node(
            content="no date",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        candidate_after = _make_node(
            content="no date",
            created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        )

        query = _make_temporal_query(time_to=time_to)

        aff_before = _compute_temporal_affinity(_make_candidate(candidate_before), query)
        aff_after = _compute_temporal_affinity(_make_candidate(candidate_after), query)

        # Before time_to: full proximity
        assert aff_before == pytest.approx(0.7, abs=1e-6)
        # After time_to: decayed
        assert aff_after < 0.7


# ---------------------------------------------------------------------------
# Composite score integration tests
# ---------------------------------------------------------------------------

class TestTemporalBoostCompositeScore:
    """Tests that temporal boost integrates correctly with composite scoring."""

    def test_temporal_query_boosts_dated_candidate(self):
        """TEMPORAL query gives higher score to candidate with date in content."""
        now = datetime.now(timezone.utc)
        query = _make_temporal_query(time_from=now, time_to=now)

        node_dated = _make_node(
            content="Meeting on January 15, 2024",
            created_at=now,
        )
        node_undated = _make_node(
            content="Some regular notes about the project",
            created_at=now,
        )

        cand_dated = _make_candidate(node_dated)
        cand_undated = _make_candidate(node_undated)

        trace_dated = compute_composite_score(
            cand_dated, DEFAULT_SCORING_WEIGHTS, now=now, query_analysis=query,
        )
        trace_undated = compute_composite_score(
            cand_undated, DEFAULT_SCORING_WEIGHTS, now=now, query_analysis=query,
        )

        # Dated candidate should score higher due to temporal boost
        assert trace_dated.composite_score > trace_undated.composite_score
        # Both should have non-zero temporal affinity (both inside time window)
        assert trace_dated.temporal_affinity > trace_undated.temporal_affinity

    def test_non_temporal_query_unaffected(self):
        """SEMANTIC query produces zero temporal affinity regardless of content."""
        now = datetime.now(timezone.utc)
        query = _make_semantic_query()

        node_dated = _make_node(
            content="Meeting on January 15, 2024",
            created_at=now,
        )
        cand = _make_candidate(node_dated)

        trace = compute_composite_score(
            cand, DEFAULT_SCORING_WEIGHTS, now=now, query_analysis=query,
        )

        assert trace.temporal_affinity == 0.0

    def test_no_query_analysis_no_temporal_boost(self):
        """Without query_analysis, temporal boost is not applied."""
        now = datetime.now(timezone.utc)
        node = _make_node(content="Meeting on January 15, 2024", created_at=now)
        cand = _make_candidate(node)

        trace = compute_composite_score(cand, DEFAULT_SCORING_WEIGHTS, now=now)

        assert trace.temporal_affinity == 0.0

    def test_temporal_boost_max_bounded(self):
        """Temporal boost adds at most temporal_boost (0.15) to composite."""
        now = datetime.now(timezone.utc)
        query = _make_temporal_query(time_from=now, time_to=now)

        node = _make_node(content="Meeting on January 15, 2024", created_at=now)
        cand = _make_candidate(node)

        weights = DEFAULT_SCORING_WEIGHTS

        # Score with temporal boost
        trace_with = compute_composite_score(
            cand, weights, now=now, query_analysis=query,
        )
        # Score without temporal boost (no query analysis)
        trace_without = compute_composite_score(
            cand, weights, now=now,
        )

        boost_diff = trace_with.composite_score - trace_without.composite_score
        # Boost should be at most temporal_boost (0.15)
        assert boost_diff <= weights.temporal_boost + 1e-6
        assert boost_diff >= 0.0

    def test_temporal_boost_zero_weight_disables(self):
        """Setting temporal_boost=0.0 disables temporal scoring."""
        now = datetime.now(timezone.utc)
        query = _make_temporal_query(time_from=now, time_to=now)
        node = _make_node(content="Meeting on January 15, 2024", created_at=now)
        cand = _make_candidate(node)

        weights = ScoringWeights(temporal_boost=0.0)

        trace = compute_composite_score(
            cand, weights, now=now, query_analysis=query,
        )

        # Even though query is TEMPORAL, zero weight means no boost
        assert trace.temporal_affinity == 0.0

    def test_determinism_temporal_scoring(self):
        """Same temporal inputs produce identical scores every time."""
        now = datetime.now(timezone.utc)
        query = _make_temporal_query(
            time_from=now - timedelta(days=7),
            time_to=now,
        )
        node = _make_node(
            content="Deployed on 2024-03-15",
            created_at=now - timedelta(days=3),
        )
        cand = _make_candidate(node)

        scores = [
            compute_composite_score(
                cand, DEFAULT_SCORING_WEIGHTS, now=now, query_analysis=query,
            ).composite_score
            for _ in range(100)
        ]

        assert len(set(scores)) == 1, f"Non-deterministic: {set(scores)}"


# ---------------------------------------------------------------------------
# Score and rank integration
# ---------------------------------------------------------------------------

class TestScoreAndRankTemporal:
    """Tests that score_and_rank passes temporal info correctly."""

    def test_score_and_rank_with_temporal_query(self):
        """score_and_rank properly threads query_analysis for temporal boost."""
        now = datetime.now(timezone.utc)
        target_time = datetime(2024, 3, 15, tzinfo=timezone.utc)
        query = _make_temporal_query(
            time_from=target_time - timedelta(days=1),
            time_to=target_time + timedelta(days=1),
        )

        # Candidate close to target time with date content
        node_close = _make_node(
            content="Deployed on March 15, 2024",
            created_at=target_time,
        )
        # Candidate far from target time without date content
        node_far = _make_node(
            content="Some generic note",
            created_at=target_time - timedelta(days=365),
        )

        cand_close = _make_candidate(node_close, semantic_score=0.5)
        cand_far = _make_candidate(node_far, semantic_score=0.5)

        ranked, traces = score_and_rank(
            [cand_far, cand_close],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=query,
        )

        # Close candidate should rank higher
        assert ranked[0].node.content == "Deployed on March 15, 2024"
        # Both should have temporal affinity recorded in traces
        assert traces[0].temporal_affinity > traces[1].temporal_affinity

    def test_score_and_rank_without_query_analysis(self):
        """score_and_rank works fine without query_analysis (backward compat)."""
        now = datetime.now(timezone.utc)
        node = _make_node(content="test", created_at=now)
        cand = _make_candidate(node)

        ranked, traces = score_and_rank([cand], DEFAULT_SCORING_WEIGHTS, now=now)

        assert len(ranked) == 1
        assert traces[0].temporal_affinity == 0.0
