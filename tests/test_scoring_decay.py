"""Tests for virtual decay integration in the scoring pipeline (RFC-0015).

Verifies that compute_composite_score and score_and_rank use effective
(post-decay) salience and confidence values computed from the node's
decay profile, reinforcement state, and exemption flags.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, ScoringWeights
from prme.retrieval.models import RetrievalCandidate, ScoreTrace
from prme.retrieval.scoring import _compute_effective_scores, compute_composite_score, score_and_rank
from prme.types import (
    DECAY_LAMBDAS,
    DecayProfile,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(
    *,
    node_id: UUID | None = None,
    user_id: str = "user-1",
    confidence: float = 0.5,
    salience: float = 0.5,
    confidence_base: float | None = None,
    salience_base: float | None = None,
    updated_at: datetime | None = None,
    last_reinforced_at: datetime | None = None,
    epistemic_type: EpistemicType | None = None,
    decay_profile: DecayProfile = DecayProfile.MEDIUM,
    reinforcement_boost: float = 0.0,
    pinned: bool = False,
    lifecycle_state: LifecycleState = LifecycleState.TENTATIVE,
) -> MemoryNode:
    """Create a MemoryNode stub for decay scoring tests."""
    now = datetime.now(timezone.utc)
    kwargs: dict = {
        "id": node_id or uuid4(),
        "user_id": user_id,
        "node_type": NodeType.FACT,
        "content": "test content",
        "confidence": confidence,
        "salience": salience,
        "confidence_base": confidence_base if confidence_base is not None else confidence,
        "salience_base": salience_base if salience_base is not None else salience,
        "updated_at": updated_at or now,
        "last_reinforced_at": last_reinforced_at or updated_at or now,
        "decay_profile": decay_profile,
        "reinforcement_boost": reinforcement_boost,
        "pinned": pinned,
        "lifecycle_state": lifecycle_state,
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
# Virtual Decay in Scoring
# ---------------------------------------------------------------------------

class TestVirtualDecayScoring:
    """Tests for _compute_effective_scores and its integration into scoring."""

    def test_fresh_node_effective_scores_near_base(self):
        """A just-created node should have effective scores very close to base."""
        now = datetime.now(timezone.utc)
        node = _make_node(
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=now,
            decay_profile=DecayProfile.MEDIUM,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        assert eff_sal == pytest.approx(0.8, abs=1e-6)
        assert eff_conf == pytest.approx(0.7, abs=1e-6)

    def test_old_node_medium_profile_salience_decayed(self):
        """A 90-day-old MEDIUM node should have substantially decayed salience."""
        now = datetime.now(timezone.utc)
        ninety_days_ago = now - timedelta(days=90)
        node = _make_node(
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=ninety_days_ago,
            decay_profile=DecayProfile.MEDIUM,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]  # 0.020
        expected_salience = 0.8 * math.exp(-lam * 90)
        assert eff_sal == pytest.approx(expected_salience, abs=1e-6)
        # Salience should be significantly below base
        assert eff_sal < 0.8 * 0.5

    def test_pinned_node_no_decay(self):
        """Pinned nodes should return base scores regardless of age."""
        now = datetime.now(timezone.utc)
        long_ago = now - timedelta(days=365)
        node = _make_node(
            salience_base=0.9,
            confidence_base=0.8,
            last_reinforced_at=long_ago,
            decay_profile=DecayProfile.FAST,
            pinned=True,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        assert eff_sal == pytest.approx(0.9, abs=1e-6)
        assert eff_conf == pytest.approx(0.8, abs=1e-6)

    def test_permanent_profile_no_decay(self):
        """PERMANENT decay profile should not decay regardless of age."""
        now = datetime.now(timezone.utc)
        long_ago = now - timedelta(days=1000)
        node = _make_node(
            salience_base=0.7,
            confidence_base=0.6,
            last_reinforced_at=long_ago,
            decay_profile=DecayProfile.PERMANENT,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        assert eff_sal == pytest.approx(0.7, abs=1e-6)
        assert eff_conf == pytest.approx(0.6, abs=1e-6)

    def test_archived_node_no_decay(self):
        """ARCHIVED lifecycle state should exempt from decay."""
        now = datetime.now(timezone.utc)
        long_ago = now - timedelta(days=200)
        node = _make_node(
            salience_base=0.6,
            confidence_base=0.5,
            last_reinforced_at=long_ago,
            decay_profile=DecayProfile.RAPID,
            lifecycle_state=LifecycleState.ARCHIVED,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        assert eff_sal == pytest.approx(0.6, abs=1e-6)
        assert eff_conf == pytest.approx(0.5, abs=1e-6)

    def test_deprecated_lifecycle_no_decay(self):
        """DEPRECATED lifecycle state should exempt from decay."""
        now = datetime.now(timezone.utc)
        long_ago = now - timedelta(days=200)
        node = _make_node(
            salience_base=0.6,
            confidence_base=0.5,
            last_reinforced_at=long_ago,
            decay_profile=DecayProfile.FAST,
            lifecycle_state=LifecycleState.DEPRECATED,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        assert eff_sal == pytest.approx(0.6, abs=1e-6)
        assert eff_conf == pytest.approx(0.5, abs=1e-6)

    def test_observed_confidence_no_decay_under_180_days(self):
        """OBSERVED nodes should not have confidence decay for t < 180 days."""
        now = datetime.now(timezone.utc)
        days_ago = now - timedelta(days=90)
        node = _make_node(
            salience_base=0.8,
            confidence_base=0.9,
            last_reinforced_at=days_ago,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.OBSERVED,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        # Salience should decay
        assert eff_sal < 0.8
        # Confidence should NOT decay (OBSERVED, t < 180)
        assert eff_conf == pytest.approx(0.9, abs=1e-6)

    def test_observed_confidence_decays_after_180_days(self):
        """OBSERVED nodes should have confidence decay for t >= 180 days."""
        now = datetime.now(timezone.utc)
        days_ago = now - timedelta(days=200)
        node = _make_node(
            salience_base=0.8,
            confidence_base=0.9,
            last_reinforced_at=days_ago,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.OBSERVED,
        )

        eff_sal, eff_conf = _compute_effective_scores(node, now)

        lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]
        mu = lam * 0.5
        expected_confidence = 0.9 * math.exp(-mu * 200)
        assert eff_conf == pytest.approx(expected_confidence, abs=1e-6)
        assert eff_conf < 0.9

    def test_reinforcement_boost_decays_with_rho(self):
        """Reinforcement boost should decay at rho=0.10, independent of profile lambda."""
        now = datetime.now(timezone.utc)
        t_days = 10.0
        days_ago = now - timedelta(days=t_days)
        boost = 0.3
        node = _make_node(
            salience_base=0.5,
            confidence_base=0.5,
            last_reinforced_at=days_ago,
            decay_profile=DecayProfile.SLOW,
            reinforcement_boost=boost,
        )

        eff_sal, _ = _compute_effective_scores(node, now)

        lam = DECAY_LAMBDAS[DecayProfile.SLOW]
        expected = 0.5 * math.exp(-lam * t_days) + boost * math.exp(-0.10 * t_days)
        assert eff_sal == pytest.approx(expected, abs=1e-6)

    def test_effective_scores_clamped_to_unit(self):
        """Effective scores must be clamped to [0.0, 1.0]."""
        now = datetime.now(timezone.utc)
        # Large reinforcement boost could push salience > 1.0
        node = _make_node(
            salience_base=0.9,
            confidence_base=0.5,
            last_reinforced_at=now,
            decay_profile=DecayProfile.SLOW,
            reinforcement_boost=0.5,
        )

        eff_sal, _ = _compute_effective_scores(node, now)

        # 0.9 + 0.5 = 1.4, should be clamped to 1.0
        assert eff_sal == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Scoring Integration
# ---------------------------------------------------------------------------

class TestScoringDecayIntegration:
    """Tests for decay integration in compute_composite_score."""

    def test_composite_score_uses_effective_salience(self):
        """compute_composite_score should use decayed salience, not base."""
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        node = _make_node(
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=thirty_days_ago,
            updated_at=thirty_days_ago,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )
        candidate = _make_candidate(
            node=node,
            semantic_score=0.5,
            lexical_score=0.3,
            graph_proximity=0.2,
        )

        trace = compute_composite_score(candidate, DEFAULT_SCORING_WEIGHTS, now=now)

        # The trace should record effective (decayed) values, not base
        lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]
        expected_salience = 0.8 * math.exp(-lam * 30)
        assert trace.salience == pytest.approx(expected_salience, abs=1e-4)
        assert trace.salience < 0.8  # Must be less than base

    def test_score_trace_records_effective_values(self):
        """ScoreTrace should contain effective (post-decay) salience and confidence."""
        now = datetime.now(timezone.utc)
        sixty_days_ago = now - timedelta(days=60)

        node = _make_node(
            salience_base=0.9,
            confidence_base=0.8,
            last_reinforced_at=sixty_days_ago,
            updated_at=sixty_days_ago,
            decay_profile=DecayProfile.FAST,
            epistemic_type=EpistemicType.ASSERTED,
        )
        candidate = _make_candidate(node=node, semantic_score=0.5)

        trace = compute_composite_score(candidate, DEFAULT_SCORING_WEIGHTS, now=now)

        lam = DECAY_LAMBDAS[DecayProfile.FAST]
        mu = lam * 0.5
        expected_salience = 0.9 * math.exp(-lam * 60)
        expected_confidence = 0.8 * math.exp(-mu * 60)

        assert trace.salience == pytest.approx(expected_salience, abs=1e-4)
        assert trace.confidence == pytest.approx(expected_confidence, abs=1e-4)

    def test_old_node_ranks_lower_than_fresh_node(self):
        """An old node should rank lower than a fresh node, all else equal."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=60)

        fresh_node = _make_node(
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=now,
            updated_at=now,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )
        old_node = _make_node(
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=old_time,
            updated_at=old_time,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )

        fresh_candidate = _make_candidate(node=fresh_node, semantic_score=0.5)
        old_candidate = _make_candidate(node=old_node, semantic_score=0.5)

        ranked, traces = score_and_rank(
            [old_candidate, fresh_candidate], DEFAULT_SCORING_WEIGHTS, now=now,
        )

        # Fresh should rank first (higher score)
        assert ranked[0].composite_score > ranked[1].composite_score
        assert ranked[0].node.last_reinforced_at == now

    def test_pinned_old_node_ranks_same_as_fresh(self):
        """A pinned old node should score the same as a fresh node (no decay)."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=60)

        # Use same IDs to ensure tiebreak doesn't interfere
        fresh_id = UUID("00000000-0000-0000-0000-000000000001")
        pinned_id = UUID("00000000-0000-0000-0000-000000000002")

        fresh_node = _make_node(
            node_id=fresh_id,
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=now,
            updated_at=now,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )
        pinned_old_node = _make_node(
            node_id=pinned_id,
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=old_time,
            updated_at=now,  # same updated_at so recency factor is equal
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
            pinned=True,
        )

        fresh_candidate = _make_candidate(node=fresh_node, semantic_score=0.5)
        pinned_candidate = _make_candidate(node=pinned_old_node, semantic_score=0.5)

        fresh_trace = compute_composite_score(
            fresh_candidate, DEFAULT_SCORING_WEIGHTS, now=now,
        )
        pinned_trace = compute_composite_score(
            pinned_candidate, DEFAULT_SCORING_WEIGHTS, now=now,
        )

        # Salience and confidence should be identical (both use base values)
        assert fresh_trace.salience == pytest.approx(pinned_trace.salience, abs=1e-6)
        assert fresh_trace.confidence == pytest.approx(pinned_trace.confidence, abs=1e-6)

    def test_score_and_rank_passes_now_consistently(self):
        """score_and_rank should pass the same `now` to all candidates."""
        now = datetime.now(timezone.utc)
        t1 = now - timedelta(days=10)
        t2 = now - timedelta(days=20)

        node1 = _make_node(
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=t1,
            updated_at=t1,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )
        node2 = _make_node(
            salience_base=0.8,
            confidence_base=0.7,
            last_reinforced_at=t2,
            updated_at=t2,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )

        c1 = _make_candidate(node=node1, semantic_score=0.5)
        c2 = _make_candidate(node=node2, semantic_score=0.5)

        ranked, traces = score_and_rank([c1, c2], DEFAULT_SCORING_WEIGHTS, now=now)

        # Both should have been scored with the same `now` value.
        # Verify by recomputing with the same `now` and checking match.
        trace1_direct = compute_composite_score(c1, DEFAULT_SCORING_WEIGHTS, now=now)
        trace2_direct = compute_composite_score(c2, DEFAULT_SCORING_WEIGHTS, now=now)

        # Find matched traces by node id
        for r, t in zip(ranked, traces):
            if r.node.id == node1.id:
                assert t.composite_score == pytest.approx(
                    trace1_direct.composite_score, abs=1e-10,
                )
            elif r.node.id == node2.id:
                assert t.composite_score == pytest.approx(
                    trace2_direct.composite_score, abs=1e-10,
                )

    def test_determinism_same_now_produces_same_scores(self):
        """Same `now` timestamp should always produce identical scores."""
        now = datetime.now(timezone.utc)
        t = now - timedelta(days=15)

        node = _make_node(
            salience_base=0.7,
            confidence_base=0.6,
            last_reinforced_at=t,
            updated_at=t,
            decay_profile=DecayProfile.FAST,
            reinforcement_boost=0.1,
            epistemic_type=EpistemicType.INFERRED,
        )
        candidate = _make_candidate(
            node=node, semantic_score=0.8, lexical_score=0.4,
        )

        scores = [
            compute_composite_score(
                candidate, DEFAULT_SCORING_WEIGHTS, now=now,
            ).composite_score
            for _ in range(100)
        ]

        assert len(set(scores)) == 1, f"Non-deterministic: {set(scores)}"

    def test_backward_compatible_without_now(self):
        """compute_composite_score without now= should still work (defaults to utcnow)."""
        node = _make_node(
            salience_base=0.5,
            confidence_base=0.5,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )
        candidate = _make_candidate(node=node, semantic_score=0.5)

        # Should not raise
        trace = compute_composite_score(candidate, DEFAULT_SCORING_WEIGHTS)
        assert trace.composite_score > 0.0

    def test_score_and_rank_backward_compatible_without_now(self):
        """score_and_rank without now= should still work."""
        node = _make_node(
            salience_base=0.5,
            confidence_base=0.5,
            decay_profile=DecayProfile.MEDIUM,
            epistemic_type=EpistemicType.ASSERTED,
        )
        candidate = _make_candidate(node=node, semantic_score=0.5)

        ranked, traces = score_and_rank([candidate], DEFAULT_SCORING_WEIGHTS)
        assert len(ranked) == 1
        assert traces[0].composite_score > 0.0


# ---------------------------------------------------------------------------
# Half-Life Verification
# ---------------------------------------------------------------------------

class TestHalfLifeVerification:
    """Verify that each decay profile reaches 50% salience at the expected half-life."""

    @pytest.mark.parametrize(
        "profile,lam",
        [
            (DecayProfile.SLOW, DECAY_LAMBDAS[DecayProfile.SLOW]),
            (DecayProfile.MEDIUM, DECAY_LAMBDAS[DecayProfile.MEDIUM]),
            (DecayProfile.FAST, DECAY_LAMBDAS[DecayProfile.FAST]),
            (DecayProfile.RAPID, DECAY_LAMBDAS[DecayProfile.RAPID]),
        ],
    )
    def test_salience_half_life(self, profile: DecayProfile, lam: float):
        """At t = ln(2)/lambda, salience should be ~50% of base."""
        half_life_days = math.log(2) / lam
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=half_life_days)

        base_salience = 0.8
        node = _make_node(
            salience_base=base_salience,
            confidence_base=0.5,
            last_reinforced_at=past,
            decay_profile=profile,
            epistemic_type=EpistemicType.ASSERTED,
        )

        eff_sal, _ = _compute_effective_scores(node, now)

        assert eff_sal == pytest.approx(base_salience * 0.5, abs=1e-4)

    def test_slow_half_life_value(self):
        """SLOW half-life should be ~138.6 days."""
        lam = DECAY_LAMBDAS[DecayProfile.SLOW]
        half_life = math.log(2) / lam
        assert half_life == pytest.approx(138.6, abs=0.1)

    def test_medium_half_life_value(self):
        """MEDIUM half-life should be ~34.7 days."""
        lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]
        half_life = math.log(2) / lam
        assert half_life == pytest.approx(34.7, abs=0.1)

    def test_fast_half_life_value(self):
        """FAST half-life should be ~9.9 days."""
        lam = DECAY_LAMBDAS[DecayProfile.FAST]
        half_life = math.log(2) / lam
        assert half_life == pytest.approx(9.9, abs=0.1)

    def test_rapid_half_life_value(self):
        """RAPID half-life should be ~3.5 days."""
        lam = DECAY_LAMBDAS[DecayProfile.RAPID]
        half_life = math.log(2) / lam
        assert half_life == pytest.approx(3.5, abs=0.1)

    def test_confidence_half_life_is_double_salience(self):
        """Confidence half-life should be 2x salience half-life (mu = lambda * 0.5)."""
        now = datetime.now(timezone.utc)
        lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]
        mu = lam * 0.5
        conf_half_life_days = math.log(2) / mu
        past = now - timedelta(days=conf_half_life_days)

        base_confidence = 0.8
        node = _make_node(
            salience_base=0.5,
            confidence_base=base_confidence,
            last_reinforced_at=past,
            decay_profile=DecayProfile.MEDIUM,
            # Use ASSERTED so confidence decay applies (not OBSERVED exemption)
            epistemic_type=EpistemicType.ASSERTED,
        )

        _, eff_conf = _compute_effective_scores(node, now)

        assert eff_conf == pytest.approx(base_confidence * 0.5, abs=1e-4)

    def test_permanent_profile_zero_lambda(self):
        """PERMANENT profile should have lambda = 0.0."""
        assert DECAY_LAMBDAS[DecayProfile.PERMANENT] == 0.0
