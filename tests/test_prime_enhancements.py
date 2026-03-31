"""Tests for PRIME dual-memory research enhancements.

Covers:
1. Node-type scoring boost (scoring.py)
2. Episodic recency boost (scoring.py)
3. User profile preamble (context_formatter.py)
4. Conflict annotations (context_formatter.py)
5. Integration of profile + conflicts in format_for_llm
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, ScoringWeights
from prme.retrieval.context_formatter import (
    _build_conflict_annotations,
    _build_profile_preamble,
    format_for_llm,
)
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate, ScoreTrace
from prme.retrieval.scoring import (
    _is_recent_episodic_query,
    compute_composite_score,
    score_and_rank,
)
from prme.types import (
    EpistemicType,
    LifecycleState,
    NodeType,
    QueryIntent,
    Scope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    *,
    node_type: NodeType = NodeType.FACT,
    content: str = "test content",
    confidence: float = 0.8,
    salience: float = 0.5,
    lifecycle_state: LifecycleState = LifecycleState.STABLE,
    event_time: datetime | None = None,
    created_at: datetime | None = None,
    user_id: str = "user-1",
    **kwargs,
) -> MemoryNode:
    now = created_at or datetime.now(timezone.utc)
    return MemoryNode(
        id=kwargs.get("node_id") or uuid4(),
        user_id=user_id,
        node_type=node_type,
        content=content,
        confidence=confidence,
        salience=salience,
        confidence_base=kwargs.get("confidence_base", confidence),
        salience_base=kwargs.get("salience_base", salience),
        created_at=now,
        updated_at=kwargs.get("updated_at", now),
        last_reinforced_at=kwargs.get("last_reinforced_at", now),
        event_time=event_time,
        lifecycle_state=lifecycle_state,
        scope=kwargs.get("scope", Scope.PERSONAL),
    )


def _make_candidate(
    *,
    node: MemoryNode | None = None,
    semantic_score: float = 0.5,
    lexical_score: float = 0.3,
    graph_proximity: float = 0.2,
    path_count: int = 1,
    conflict_flag: bool = False,
    contradicts_id=None,
    **node_kwargs,
) -> RetrievalCandidate:
    if node is None:
        node = _make_node(**node_kwargs)
    return RetrievalCandidate(
        node=node,
        paths=["VECTOR"],
        path_count=path_count,
        semantic_score=semantic_score,
        lexical_score=lexical_score,
        graph_proximity=graph_proximity,
        conflict_flag=conflict_flag,
        contradicts_id=contradicts_id,
    )


def _make_query_analysis(query: str, intent: QueryIntent = QueryIntent.SEMANTIC) -> QueryAnalysis:
    return QueryAnalysis(query=query, intent=intent)


# ---------------------------------------------------------------------------
# 1. Node-Type Scoring Boost
# ---------------------------------------------------------------------------


class TestNodeTypeBoost:
    """Tests for the per-node-type multiplicative boost in scoring."""

    def test_fact_node_boosted(self):
        """FACT node gets 1.15x composite score vs EVENT node with same inputs."""
        now = datetime.now(timezone.utc)
        fact_node = _make_node(node_type=NodeType.FACT)
        event_node = _make_node(node_type=NodeType.EVENT)

        fact_cand = _make_candidate(node=fact_node)
        event_cand = _make_candidate(node=event_node)

        fact_trace = compute_composite_score(fact_cand, DEFAULT_SCORING_WEIGHTS, now=now)
        event_trace = compute_composite_score(event_cand, DEFAULT_SCORING_WEIGHTS, now=now)

        # FACT default boost is 1.15, EVENT is 1.0
        assert fact_trace.composite_score == pytest.approx(
            event_trace.composite_score * 1.15, rel=1e-6,
        )

    def test_preference_node_boosted(self):
        """PREFERENCE node gets 1.15x composite score vs EVENT node."""
        now = datetime.now(timezone.utc)
        pref_node = _make_node(node_type=NodeType.PREFERENCE)
        event_node = _make_node(node_type=NodeType.EVENT)

        pref_cand = _make_candidate(node=pref_node)
        event_cand = _make_candidate(node=event_node)

        pref_trace = compute_composite_score(pref_cand, DEFAULT_SCORING_WEIGHTS, now=now)
        event_trace = compute_composite_score(event_cand, DEFAULT_SCORING_WEIGHTS, now=now)

        assert pref_trace.composite_score == pytest.approx(
            event_trace.composite_score * 1.15, rel=1e-6,
        )

    def test_event_node_no_boost(self):
        """EVENT node gets 1.0x (no boost)."""
        now = datetime.now(timezone.utc)
        event_node = _make_node(node_type=NodeType.EVENT)
        event_cand = _make_candidate(node=event_node)

        trace = compute_composite_score(event_cand, DEFAULT_SCORING_WEIGHTS, now=now)

        # EVENT is not in default node_type_boost dict -> multiplier = 1.0
        assert trace.node_type_boost == 1.0

    def test_entity_node_no_boost(self):
        """ENTITY node gets 1.0x (no boost)."""
        now = datetime.now(timezone.utc)
        entity_node = _make_node(node_type=NodeType.ENTITY)
        entity_cand = _make_candidate(node=entity_node)

        trace = compute_composite_score(entity_cand, DEFAULT_SCORING_WEIGHTS, now=now)

        assert trace.node_type_boost == 1.0

    def test_custom_boost_config(self):
        """Custom node_type_boost={'event': 2.0} should double EVENT score."""
        now = datetime.now(timezone.utc)
        weights = ScoringWeights(node_type_boost={"event": 2.0})

        event_node = _make_node(node_type=NodeType.EVENT)
        fact_node = _make_node(node_type=NodeType.FACT)

        event_cand = _make_candidate(node=event_node)
        fact_cand = _make_candidate(node=fact_node)

        event_trace = compute_composite_score(event_cand, weights, now=now)
        fact_trace = compute_composite_score(fact_cand, weights, now=now)

        assert event_trace.node_type_boost == 2.0
        # FACT is no longer in the boost dict, so it defaults to 1.0
        assert fact_trace.node_type_boost == 1.0
        assert event_trace.composite_score == pytest.approx(
            fact_trace.composite_score * 2.0, rel=1e-6,
        )

    def test_empty_boost_config(self):
        """Empty node_type_boost={} means all types get 1.0x."""
        now = datetime.now(timezone.utc)
        weights = ScoringWeights(node_type_boost={})

        fact_node = _make_node(node_type=NodeType.FACT)
        event_node = _make_node(node_type=NodeType.EVENT)

        fact_cand = _make_candidate(node=fact_node)
        event_cand = _make_candidate(node=event_node)

        fact_trace = compute_composite_score(fact_cand, weights, now=now)
        event_trace = compute_composite_score(event_cand, weights, now=now)

        assert fact_trace.node_type_boost == 1.0
        assert event_trace.node_type_boost == 1.0
        assert fact_trace.composite_score == pytest.approx(
            event_trace.composite_score, rel=1e-6,
        )

    def test_boost_in_score_trace(self):
        """ScoreTrace.node_type_boost field is set correctly per node type."""
        now = datetime.now(timezone.utc)

        fact_cand = _make_candidate(node=_make_node(node_type=NodeType.FACT))
        decision_cand = _make_candidate(node=_make_node(node_type=NodeType.DECISION))
        event_cand = _make_candidate(node=_make_node(node_type=NodeType.EVENT))

        fact_trace = compute_composite_score(fact_cand, DEFAULT_SCORING_WEIGHTS, now=now)
        decision_trace = compute_composite_score(decision_cand, DEFAULT_SCORING_WEIGHTS, now=now)
        event_trace = compute_composite_score(event_cand, DEFAULT_SCORING_WEIGHTS, now=now)

        assert fact_trace.node_type_boost == 1.15
        assert decision_trace.node_type_boost == 1.10
        assert event_trace.node_type_boost == 1.0

    def test_version_id_changes_with_boost(self):
        """Changing node_type_boost changes version_id."""
        w1 = ScoringWeights(node_type_boost={"fact": 1.15})
        w2 = ScoringWeights(node_type_boost={"fact": 1.30})
        w3 = ScoringWeights(node_type_boost={})

        assert w1.version_id != w2.version_id
        assert w1.version_id != w3.version_id
        assert w2.version_id != w3.version_id


# ---------------------------------------------------------------------------
# 2. Episodic Recency Boost
# ---------------------------------------------------------------------------


class TestEpisodicRecencyBoost:
    """Tests for episodic recency detection and weight boosting."""

    def test_recently_query_detected(self):
        """'I recently bought a car' is detected as episodic."""
        qa = _make_query_analysis("I recently bought a car")
        assert _is_recent_episodic_query(qa) is True

    def test_yesterday_query_detected(self):
        """'What did I say yesterday?' is detected."""
        qa = _make_query_analysis("What did I say yesterday?")
        assert _is_recent_episodic_query(qa) is True

    def test_this_week_query_detected(self):
        """'What happened this week?' is detected."""
        qa = _make_query_analysis("What happened this week?")
        assert _is_recent_episodic_query(qa) is True

    def test_normal_query_not_detected(self):
        """'What is my favorite color?' is NOT detected."""
        qa = _make_query_analysis("What is my favorite color?")
        assert _is_recent_episodic_query(qa) is False

    def test_current_state_not_episodic(self):
        """'What do I currently use?' triggers current-state, not episodic.

        score_and_rank checks current-state first and skips episodic if
        current-state is true, so we verify _is_recent_episodic_query itself
        returns False (no episodic language) and the current-state path takes
        precedence in the ranking function.
        """
        qa = _make_query_analysis("What do I currently use?")
        # "currently" is current-state language, not episodic
        assert _is_recent_episodic_query(qa) is False

    def test_recency_weight_boosted(self):
        """When episodic query, recent candidate scores higher relative to old one.

        With an episodic query, recency weight is boosted to 0.20, so a
        recent candidate should gain a larger advantage over an old candidate
        compared to the same candidates scored without an episodic query.
        """
        now = datetime.now(timezone.utc)
        recent_time = now - timedelta(days=1)
        old_time = now - timedelta(days=60)

        recent_node = _make_node(
            content="I recently ate pizza",
            updated_at=recent_time,
            created_at=recent_time,
            last_reinforced_at=recent_time,
        )
        old_node = _make_node(
            content="I ate sushi a long time ago",
            updated_at=old_time,
            created_at=old_time,
            last_reinforced_at=old_time,
        )

        recent_cand = _make_candidate(node=recent_node)
        old_cand = _make_candidate(node=old_node)

        # Score with episodic query
        episodic_qa = _make_query_analysis("What did I recently eat?")
        ranked_episodic, traces_episodic = score_and_rank(
            [_make_candidate(node=recent_node), _make_candidate(node=old_node)],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=episodic_qa,
        )

        # Score without episodic query (normal)
        normal_qa = _make_query_analysis("What is my favorite food?")
        ranked_normal, traces_normal = score_and_rank(
            [_make_candidate(node=recent_node), _make_candidate(node=old_node)],
            DEFAULT_SCORING_WEIGHTS,
            now=now,
            query_analysis=normal_qa,
        )

        # In both cases, recent should rank higher, but the gap should be
        # larger with the episodic query due to boosted recency weight.
        episodic_gap = ranked_episodic[0].composite_score - ranked_episodic[1].composite_score
        normal_gap = ranked_normal[0].composite_score - ranked_normal[1].composite_score

        assert episodic_gap > normal_gap


# ---------------------------------------------------------------------------
# 3. User Profile Preamble
# ---------------------------------------------------------------------------


class TestProfilePreamble:
    """Tests for _build_profile_preamble in context_formatter."""

    def test_profile_with_facts_and_preferences(self):
        """Both facts and preferences appear in profile."""
        fact_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="User is from New York",
            lifecycle_state=LifecycleState.STABLE,
        )
        pref_cand = _make_candidate(
            node_type=NodeType.PREFERENCE,
            content="User prefers dark mode",
            lifecycle_state=LifecycleState.STABLE,
        )

        result = _build_profile_preamble([fact_cand, pref_cand])

        assert "## User Profile" in result
        assert "### Known Facts" in result
        assert "### Preferences" in result
        assert "User is from New York" in result
        assert "User prefers dark mode" in result

    def test_profile_ordering(self):
        """Preferences before Facts before Instructions."""
        fact_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="fact content",
            lifecycle_state=LifecycleState.STABLE,
        )
        pref_cand = _make_candidate(
            node_type=NodeType.PREFERENCE,
            content="preference content",
            lifecycle_state=LifecycleState.STABLE,
        )
        instr_cand = _make_candidate(
            node_type=NodeType.INSTRUCTION,
            content="instruction content",
            lifecycle_state=LifecycleState.STABLE,
        )

        result = _build_profile_preamble([fact_cand, pref_cand, instr_cand])

        pref_idx = result.index("### Preferences")
        fact_idx = result.index("### Known Facts")
        instr_idx = result.index("### Learned Rules")

        assert pref_idx < fact_idx < instr_idx

    def test_tentative_tag(self):
        """TENTATIVE nodes get '(tentative)' suffix."""
        tentative_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="User might be vegan",
            lifecycle_state=LifecycleState.TENTATIVE,
        )

        result = _build_profile_preamble([tentative_cand])

        assert "(tentative)" in result
        assert "User might be vegan (tentative)" in result

    def test_stable_only(self):
        """Only STABLE and TENTATIVE included (not SUPERSEDED, CONTESTED, etc.)."""
        stable_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="stable fact",
            lifecycle_state=LifecycleState.STABLE,
        )
        superseded_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="superseded fact",
            lifecycle_state=LifecycleState.SUPERSEDED,
        )
        contested_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="contested fact",
            lifecycle_state=LifecycleState.CONTESTED,
        )
        archived_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="archived fact",
            lifecycle_state=LifecycleState.ARCHIVED,
        )

        result = _build_profile_preamble(
            [stable_cand, superseded_cand, contested_cand, archived_cand]
        )

        assert "stable fact" in result
        assert "superseded fact" not in result
        assert "contested fact" not in result
        assert "archived fact" not in result

    def test_no_profile_for_events_only(self):
        """If only EVENT nodes, no profile section generated."""
        event_cand = _make_candidate(
            node_type=NodeType.EVENT,
            content="User went to a meeting",
            lifecycle_state=LifecycleState.STABLE,
        )

        result = _build_profile_preamble([event_cand])

        assert result == ""

    def test_confidence_sorting(self):
        """Higher confidence nodes appear first within group."""
        high_conf = _make_candidate(
            node_type=NodeType.FACT,
            content="high confidence fact",
            confidence=0.95,
            lifecycle_state=LifecycleState.STABLE,
        )
        low_conf = _make_candidate(
            node_type=NodeType.FACT,
            content="low confidence fact",
            confidence=0.4,
            lifecycle_state=LifecycleState.STABLE,
        )

        # Pass low before high to verify sorting reorders
        result = _build_profile_preamble([low_conf, high_conf])

        high_idx = result.index("high confidence fact")
        low_idx = result.index("low confidence fact")
        assert high_idx < low_idx

    def test_include_profile_false(self):
        """format_for_llm(include_profile=False) produces output without profile section."""
        fact_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="User is a developer",
            lifecycle_state=LifecycleState.STABLE,
        )

        with_profile = format_for_llm([fact_cand], "test query", include_profile=True)
        without_profile = format_for_llm([fact_cand], "test query", include_profile=False)

        assert "## User Profile" in with_profile
        assert "## User Profile" not in without_profile


# ---------------------------------------------------------------------------
# 4. Conflict Annotations
# ---------------------------------------------------------------------------


class TestConflictAnnotations:
    """Tests for _build_conflict_annotations in context_formatter."""

    def test_conflict_pair_shown(self):
        """Two CONTESTED nodes with contradicts_id show 'NEWER vs OLDER'."""
        newer_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        older_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        newer_node = _make_node(
            content="User now lives in Boston",
            lifecycle_state=LifecycleState.CONTESTED,
            event_time=newer_time,
            created_at=newer_time,
        )
        older_node = _make_node(
            content="User lives in New York",
            lifecycle_state=LifecycleState.CONTESTED,
            event_time=older_time,
            created_at=older_time,
        )

        newer_cand = _make_candidate(
            node=newer_node,
            conflict_flag=True,
            contradicts_id=older_node.id,
        )
        older_cand = _make_candidate(
            node=older_node,
            conflict_flag=True,
            contradicts_id=newer_node.id,
        )

        result = _build_conflict_annotations([newer_cand, older_cand])

        assert "## Conflicting Information" in result
        assert 'NEWER: "User now lives in Boston"' in result
        assert 'OLDER: "User lives in New York"' in result

    def test_no_conflicts_empty(self):
        """No conflicted nodes returns empty string."""
        normal_cand = _make_candidate(content="No conflict here")
        result = _build_conflict_annotations([normal_cand])
        assert result == ""

    def test_counterpart_not_in_results(self):
        """Conflicted node whose counterpart isn't in results shows fallback."""
        missing_id = uuid4()
        contested_node = _make_node(
            content="User lives in LA",
            lifecycle_state=LifecycleState.CONTESTED,
        )
        contested_cand = _make_candidate(
            node=contested_node,
            conflict_flag=True,
            contradicts_id=missing_id,
        )

        result = _build_conflict_annotations([contested_cand])

        assert "## Conflicting Information" in result
        assert "contradicting memory not in results" in result
        assert "User lives in LA" in result

    def test_deduplication(self):
        """A conflict pair only appears once even if both nodes have conflict_flag."""
        newer_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        older_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        node_a = _make_node(
            content="Fact A",
            event_time=newer_time,
            created_at=newer_time,
        )
        node_b = _make_node(
            content="Fact B",
            event_time=older_time,
            created_at=older_time,
        )

        cand_a = _make_candidate(
            node=node_a,
            conflict_flag=True,
            contradicts_id=node_b.id,
        )
        cand_b = _make_candidate(
            node=node_b,
            conflict_flag=True,
            contradicts_id=node_a.id,
        )

        result = _build_conflict_annotations([cand_a, cand_b])

        # Count the number of "NEWER:" entries -- should be exactly 1
        assert result.count("NEWER:") == 1
        assert result.count("OLDER:") == 1

    def test_newer_older_ordering(self):
        """The newer node (by event_time) is labeled 'NEWER'."""
        t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2024, 12, 1, tzinfo=timezone.utc)

        old_node = _make_node(
            content="old value",
            event_time=t1,
            created_at=t1,
        )
        new_node = _make_node(
            content="new value",
            event_time=t2,
            created_at=t2,
        )

        # Pass the OLD node first with conflict pointing to new node
        old_cand = _make_candidate(
            node=old_node,
            conflict_flag=True,
            contradicts_id=new_node.id,
        )
        new_cand = _make_candidate(
            node=new_node,
            conflict_flag=False,  # Only one side has conflict_flag
        )

        result = _build_conflict_annotations([old_cand, new_cand])

        assert 'NEWER: "new value"' in result
        assert 'OLDER: "old value"' in result


# ---------------------------------------------------------------------------
# 5. format_for_llm Integration
# ---------------------------------------------------------------------------


class TestFormatForLlmIntegration:
    """Integration tests for format_for_llm with PRIME enhancements."""

    def test_full_output_with_profile_and_conflicts(self):
        """format_for_llm with profile nodes and conflicts produces all sections."""
        now = datetime(2024, 6, 15, tzinfo=timezone.utc)

        # Profile node
        pref_node = _make_node(
            node_type=NodeType.PREFERENCE,
            content="User prefers tea over coffee",
            lifecycle_state=LifecycleState.STABLE,
            created_at=now,
            event_time=now,
        )
        pref_cand = _make_candidate(node=pref_node)

        # Conflict pair
        older_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        newer_time = datetime(2024, 5, 1, tzinfo=timezone.utc)

        old_node = _make_node(
            node_type=NodeType.FACT,
            content="User works at Acme",
            lifecycle_state=LifecycleState.CONTESTED,
            event_time=older_time,
            created_at=older_time,
        )
        new_node = _make_node(
            node_type=NodeType.FACT,
            content="User works at Globex",
            lifecycle_state=LifecycleState.CONTESTED,
            event_time=newer_time,
            created_at=newer_time,
        )
        old_cand = _make_candidate(
            node=old_node,
            conflict_flag=True,
            contradicts_id=new_node.id,
        )
        new_cand = _make_candidate(
            node=new_node,
            conflict_flag=True,
            contradicts_id=old_node.id,
        )

        # Event node (episodic, no profile)
        event_node = _make_node(
            node_type=NodeType.EVENT,
            content="User had a meeting",
            lifecycle_state=LifecycleState.STABLE,
            created_at=now,
            event_time=now,
        )
        event_cand = _make_candidate(node=event_node)

        result = format_for_llm(
            [pref_cand, old_cand, new_cand, event_cand],
            "Tell me about the user",
            include_profile=True,
        )

        # All three sections present
        assert "## User Profile" in result
        assert "## Conflicting Information" in result
        assert "## Retrieved Memory" in result
        assert "User prefers tea over coffee" in result
        assert "NEWER:" in result
        assert "User had a meeting" in result

    def test_full_output_events_only(self):
        """format_for_llm with only events produces no profile section (just body)."""
        now = datetime(2024, 6, 15, tzinfo=timezone.utc)

        event1 = _make_candidate(
            node_type=NodeType.EVENT,
            content="Event one",
            lifecycle_state=LifecycleState.STABLE,
            created_at=now,
            event_time=now,
        )
        event2 = _make_candidate(
            node_type=NodeType.EVENT,
            content="Event two",
            lifecycle_state=LifecycleState.STABLE,
            created_at=now,
            event_time=now,
        )

        result = format_for_llm(
            [event1, event2],
            "What happened?",
            include_profile=True,
        )

        # No profile, no conflicts, no "Retrieved Memory" header
        assert "## User Profile" not in result
        assert "## Conflicting Information" not in result
        assert "## Retrieved Memory" not in result
        # But the body content is present
        assert "Event one" in result
        assert "Event two" in result

    def test_include_profile_false_matches_old_behavior(self):
        """format_for_llm with include_profile=False matches raw format."""
        now = datetime(2024, 6, 15, tzinfo=timezone.utc)

        fact_cand = _make_candidate(
            node_type=NodeType.FACT,
            content="Important fact",
            lifecycle_state=LifecycleState.STABLE,
            created_at=now,
            event_time=now,
        )

        result_profile = format_for_llm(
            [fact_cand], "test query", include_profile=True,
        )
        result_raw = format_for_llm(
            [fact_cand], "test query", include_profile=False,
        )

        # With profile=True, the profile preamble wraps the body
        assert "## User Profile" in result_profile
        assert "## Retrieved Memory" in result_profile

        # With profile=False, no wrapping -- just the body
        assert "## User Profile" not in result_raw
        assert "## Retrieved Memory" not in result_raw
        assert "Important fact" in result_raw
