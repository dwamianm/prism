"""Composite scoring and deterministic ranking (Stage 5).

Implements the 8-input composite score formula from RFC-0005 Section 7
and deterministic ranking with tie-breaking by object_id.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, ScoringWeights
from prme.retrieval.models import RetrievalCandidate, ScoreTrace
from prme.types import EPISTEMIC_WEIGHTS, EpistemicType


def compute_composite_score(
    candidate: RetrievalCandidate,
    weights: ScoringWeights,
) -> ScoreTrace:
    """Compute the 8-input composite score for a single candidate.

    Formula (RFC-0005 Section 7):
      additive = w_semantic*semantic + w_lexical*lexical + w_graph*graph
                 + w_recency*recency + w_salience*salience + w_confidence*confidence
      composite = additive * epistemic_weight
      path_score = min(path_count / 3.0, 1.0)   (tiebreaker only)

    Recency: exp(-lambda * days_since_update)
    Epistemic weight: lookup from EPISTEMIC_WEIGHTS table.

    Args:
        candidate: The retrieval candidate to score.
        weights: Scoring weight configuration.

    Returns:
        ScoreTrace with all 8 component values and the composite score.
    """
    node = candidate.node
    now = datetime.now(timezone.utc)

    # Recency factor: exponential decay based on days since last update.
    # Use updated_at if available, fall back to created_at.
    reference_time = node.updated_at or node.created_at
    days_since_update = (now - reference_time).total_seconds() / 86400.0
    recency = math.exp(-weights.recency_lambda * days_since_update)

    # Epistemic weight: lookup by epistemic_type (forward-compatible fallback).
    epistemic_type = getattr(node, "epistemic_type", EpistemicType.ASSERTED)
    epistemic_weight = EPISTEMIC_WEIGHTS.get(epistemic_type, 0.7)

    # Path score: multi-path corroboration (tiebreaker only).
    path_score = min(candidate.path_count / 3.0, 1.0)

    # Additive components (weights sum to 1.0).
    additive = (
        weights.w_semantic * candidate.semantic_score
        + weights.w_lexical * candidate.lexical_score
        + weights.w_graph * candidate.graph_proximity
        + weights.w_recency * recency
        + weights.w_salience * node.salience
        + weights.w_confidence * node.confidence
    )

    # Epistemic is multiplicative (not additive).
    composite = round(additive * epistemic_weight, 10)

    return ScoreTrace(
        semantic_similarity=candidate.semantic_score,
        lexical_relevance=candidate.lexical_score,
        graph_proximity=candidate.graph_proximity,
        recency_factor=recency,
        salience=node.salience,
        confidence=node.confidence,
        epistemic_weight=epistemic_weight,
        path_score=path_score,
        composite_score=composite,
    )


def score_and_rank(
    candidates: list[RetrievalCandidate],
    weights: ScoringWeights = DEFAULT_SCORING_WEIGHTS,
) -> tuple[list[RetrievalCandidate], list[ScoreTrace]]:
    """Score all candidates and return them in deterministic ranked order.

    For each candidate, computes the composite score, sets it on the
    candidate object, then sorts by (-composite_score, -path_score,
    str(node.id)) for fully deterministic ordering.

    Args:
        candidates: Candidates to score.
        weights: Scoring weight configuration (default: DEFAULT_SCORING_WEIGHTS).

    Returns:
        Tuple of (sorted candidates, corresponding score traces).
    """
    traces: list[ScoreTrace] = []

    for candidate in candidates:
        trace = compute_composite_score(candidate, weights)
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
