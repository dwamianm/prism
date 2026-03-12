"""Composite scoring and deterministic ranking (Stage 5).

Implements the 8-input composite score formula from RFC-0005 Section 7
and deterministic ranking with tie-breaking by object_id.

Virtual decay (RFC-0015): salience and confidence are computed from
decay-model fields (decay_profile, last_reinforced_at, reinforcement_boost,
salience_base, confidence_base, pinned) rather than raw stored values.

Temporal affinity: when query intent is TEMPORAL, candidates with date
content and timestamps near the query's temporal window receive a bonus
score (up to temporal_boost weight).
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from prme.models.nodes import MemoryNode
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, ScoringWeights
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate, ScoreTrace
from prme.types import DECAY_LAMBDAS, EPISTEMIC_WEIGHTS, DecayProfile, EpistemicType, LifecycleState, QueryIntent


# --- Temporal affinity patterns (compiled once, reused) ---

# Matches common date patterns in content text:
# - Month names (full and abbreviated): "January", "Jan", "Feb 14"
# - ISO dates: "2023-03-15", "2024/01/02"
# - Informal dates: "May 8", "March 15, 2023", "8 May 2023"
# - Year-only: "2023", "2024"
# - Day/month formats: "03/15", "15/03"
_DATE_PATTERN = re.compile(
    r"\b(?:"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"(?:\s+\d{1,2}(?:,?\s+\d{4})?)?"  # Optional day and year after month name
    r"|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"  # YYYY-MM-DD or YYYY/MM/DD
    r"|"
    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"  # MM/DD/YYYY or DD/MM/YYYY
    r"|"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September"
    r"|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"(?:\s+\d{4})?"  # "8 May 2023"
    r"|"
    r"(?:19|20)\d{2}"  # Year-only: 1900-2099
    r")\b",
    re.IGNORECASE,
)

# Decay rate for temporal proximity: score decays as candidate timestamp
# moves away from the query's temporal window. Lambda of 0.02 gives a
# half-life of ~35 days.
_TEMPORAL_PROXIMITY_LAMBDA = 0.02


def _compute_temporal_affinity(
    candidate: RetrievalCandidate,
    query_analysis: QueryAnalysis,
) -> float:
    """Compute temporal affinity score (0.0-1.0) for a candidate.

    Two sub-signals, weighted and combined:
    - Content date presence (0.3 weight): whether the candidate's content
      contains date-like strings. Binary: 1.0 if any date found, else 0.0.
    - Temporal proximity (0.7 weight): how close the candidate's timestamp
      is to the query's resolved temporal window. 1.0 if inside the window,
      exponential decay outside it.

    Args:
        candidate: The retrieval candidate to evaluate.
        query_analysis: The query analysis with temporal signals.

    Returns:
        Float in [0.0, 1.0] representing temporal affinity.
    """
    # Sub-signal 1: Content date presence (0.3 weight)
    content = candidate.node.content or ""
    has_date = 1.0 if _DATE_PATTERN.search(content) else 0.0

    # Sub-signal 2: Temporal proximity (0.7 weight)
    proximity = 0.0

    time_from = query_analysis.time_from
    time_to = query_analysis.time_to

    if time_from is not None or time_to is not None:
        # Use event_time if available, else created_at
        candidate_time = candidate.node.event_time or candidate.node.created_at

        if time_from is not None and time_to is not None:
            # Window defined: 1.0 if inside, decay outside
            if time_from <= candidate_time <= time_to:
                proximity = 1.0
            else:
                # Distance to nearest edge of the window
                if candidate_time < time_from:
                    days_away = (time_from - candidate_time).total_seconds() / 86400.0
                else:
                    days_away = (candidate_time - time_to).total_seconds() / 86400.0
                proximity = math.exp(-_TEMPORAL_PROXIMITY_LAMBDA * days_away)
        elif time_from is not None:
            # Only lower bound: 1.0 if after time_from, decay before it
            if candidate_time >= time_from:
                proximity = 1.0
            else:
                days_away = (time_from - candidate_time).total_seconds() / 86400.0
                proximity = math.exp(-_TEMPORAL_PROXIMITY_LAMBDA * days_away)
        else:
            # Only upper bound (time_to): 1.0 if before time_to, decay after it
            assert time_to is not None
            if candidate_time <= time_to:
                proximity = 1.0
            else:
                days_away = (candidate_time - time_to).total_seconds() / 86400.0
                proximity = math.exp(-_TEMPORAL_PROXIMITY_LAMBDA * days_away)
    else:
        # No temporal window resolved from query -- fall back to content signal only.
        # Give partial proximity credit if content has dates.
        proximity = has_date * 0.5

    return 0.3 * has_date + 0.7 * proximity


def _compute_effective_scores(node: MemoryNode, now: datetime) -> tuple[float, float]:
    """Compute virtual effective salience and confidence per RFC-0015.

    Exemptions (no decay applied):
    - Pinned nodes
    - PERMANENT decay profile
    - Terminal lifecycle states (ARCHIVED, DEPRECATED)

    Salience decays with the node's decay lambda; reinforcement boost
    decays at a fixed rho=0.10.  Confidence decays at mu = lambda * 0.5,
    with OBSERVED nodes exempt from confidence decay for the first 180 days.

    Returns:
        (effective_salience, effective_confidence) clamped to [0.0, 1.0].
    """
    # Exemptions: pinned, PERMANENT, or terminal lifecycle
    if (
        node.pinned
        or node.decay_profile == DecayProfile.PERMANENT
        or node.lifecycle_state in (LifecycleState.ARCHIVED, LifecycleState.DEPRECATED)
    ):
        return node.salience_base, node.confidence_base

    lam = DECAY_LAMBDAS.get(node.decay_profile, 0.020)  # default MEDIUM
    t = max(0.0, (now - node.last_reinforced_at).total_seconds() / 86400.0)

    # Salience: base decay + reinforcement boost decay (rho=0.10)
    effective_salience = (
        node.salience_base * math.exp(-lam * t)
        + node.reinforcement_boost * math.exp(-0.10 * t)
    )

    # Confidence: mu = lambda * 0.5
    mu = lam * 0.5
    # OBSERVED: no confidence decay for t < 180 days
    if node.epistemic_type == EpistemicType.OBSERVED and t < 180.0:
        effective_confidence = node.confidence_base
    else:
        effective_confidence = node.confidence_base * math.exp(-mu * t)

    return max(0.0, min(1.0, effective_salience)), max(0.0, min(1.0, effective_confidence))


def compute_composite_score(
    candidate: RetrievalCandidate,
    weights: ScoringWeights,
    epistemic_weights: dict[str, float] | None = None,
    now: datetime | None = None,
    query_analysis: QueryAnalysis | None = None,
) -> ScoreTrace:
    """Compute the 8-input composite score for a single candidate.

    Formula (RFC-0005 Section 7, updated by RFC-0015):
      additive = w_semantic*semantic + w_lexical*lexical + w_graph*graph
                 + w_recency*recency + w_salience*eff_salience
                 + w_confidence*eff_confidence
      composite = additive * epistemic_weight
      path_score = min(path_count / 3.0, 1.0)   (tiebreaker only)

    When query intent is TEMPORAL and query_analysis is provided, a
    temporal affinity bonus is added:
      composite += temporal_boost * temporal_affinity

    Salience and confidence are now computed via virtual decay from base
    values, decay profile, and reinforcement state (RFC-0015).

    Recency: exp(-lambda * days_since_update)
    Epistemic weight: lookup from EPISTEMIC_WEIGHTS table or config override.

    Args:
        candidate: The retrieval candidate to score.
        weights: Scoring weight configuration.
        epistemic_weights: Optional dict of epistemic type string values to
            float multipliers. If None, uses module-level EPISTEMIC_WEIGHTS.
        now: Reference timestamp for decay computation.  Defaults to
            ``datetime.now(timezone.utc)`` when not supplied.
        query_analysis: Optional query analysis for temporal boost computation.
            When provided and intent is TEMPORAL, temporal affinity scoring
            is activated.

    Returns:
        ScoreTrace with all 8 component values and the composite score.
    """
    node = candidate.node
    if now is None:
        now = datetime.now(timezone.utc)

    # Virtual decay: compute effective scores from decay model (RFC-0015).
    effective_salience, effective_confidence = _compute_effective_scores(node, now)

    # Recency factor: exponential decay based on days since last update.
    # Use updated_at if available, fall back to created_at.
    reference_time = node.updated_at or node.created_at
    days_since_update = (now - reference_time).total_seconds() / 86400.0
    recency = math.exp(-weights.recency_lambda * days_since_update)

    # Epistemic weight: config override dict (str keys) or module-level default (Enum keys).
    if epistemic_weights is not None:
        epistemic_weight = epistemic_weights.get(node.epistemic_type.value, 0.7)
    else:
        epistemic_weight = EPISTEMIC_WEIGHTS.get(node.epistemic_type, 0.7)

    # Path score: multi-path corroboration (tiebreaker only).
    path_score = min(candidate.path_count / 3.0, 1.0)

    # Additive components (weights sum to 1.0).
    additive = (
        weights.w_semantic * candidate.semantic_score
        + weights.w_lexical * candidate.lexical_score
        + weights.w_graph * candidate.graph_proximity
        + weights.w_recency * recency
        + weights.w_salience * effective_salience
        + weights.w_confidence * effective_confidence
    )

    # Epistemic is multiplicative (not additive).
    composite = additive * epistemic_weight

    # Temporal boost: added as a bonus when query intent is TEMPORAL.
    # This is NOT part of the additive sum-to-1.0 constraint -- it's an
    # extra signal that only activates for temporal queries.
    temporal_affinity = 0.0
    if (
        query_analysis is not None
        and query_analysis.intent == QueryIntent.TEMPORAL
        and weights.temporal_boost > 0.0
    ):
        temporal_affinity = _compute_temporal_affinity(candidate, query_analysis)
        composite += weights.temporal_boost * temporal_affinity

    composite = round(composite, 10)

    return ScoreTrace(
        semantic_similarity=candidate.semantic_score,
        lexical_relevance=candidate.lexical_score,
        graph_proximity=candidate.graph_proximity,
        recency_factor=recency,
        salience=effective_salience,
        confidence=effective_confidence,
        epistemic_weight=epistemic_weight,
        path_score=path_score,
        composite_score=composite,
        temporal_affinity=temporal_affinity,
    )


def score_and_rank(
    candidates: list[RetrievalCandidate],
    weights: ScoringWeights = DEFAULT_SCORING_WEIGHTS,
    epistemic_weights: dict[str, float] | None = None,
    now: datetime | None = None,
    query_analysis: QueryAnalysis | None = None,
) -> tuple[list[RetrievalCandidate], list[ScoreTrace]]:
    """Score all candidates and return them in deterministic ranked order.

    For each candidate, computes the composite score, sets it on the
    candidate object, then sorts by (-composite_score, -path_score,
    str(node.id)) for fully deterministic ordering.

    Args:
        candidates: Candidates to score.
        weights: Scoring weight configuration (default: DEFAULT_SCORING_WEIGHTS).
        epistemic_weights: Optional dict of epistemic type string values to
            float multipliers. Passed through to compute_composite_score.
        now: Reference timestamp for decay computation.  Passed through to
            compute_composite_score for consistent scoring within a batch.
        query_analysis: Optional query analysis for temporal boost. When
            provided and intent is TEMPORAL, temporal affinity scoring is
            activated. Passed through to compute_composite_score.

    Returns:
        Tuple of (sorted candidates, corresponding score traces).
    """
    traces: list[ScoreTrace] = []

    for candidate in candidates:
        trace = compute_composite_score(
            candidate, weights, epistemic_weights, now=now,
            query_analysis=query_analysis,
        )
        candidate.composite_score = trace.composite_score
        candidate.score_trace = trace
        traces.append(trace)

    # Deterministic sort: score descending, path_score descending, then ID ascending.
    scored_pairs = list(zip(candidates, traces))
    scored_pairs.sort(
        key=lambda pair: (
            -pair[0].composite_score,
            -pair[1].path_score,
            str(pair[0].node.id),
        )
    )

    sorted_candidates = [pair[0] for pair in scored_pairs]
    sorted_traces = [pair[1] for pair in scored_pairs]

    return sorted_candidates, sorted_traces
