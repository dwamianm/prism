"""Composite scoring and deterministic ranking (Stage 5).

Implements the 8-input composite score formula from RFC-0005 Section 7
and deterministic ranking with tie-breaking by object_id.

Virtual decay (RFC-0015): salience and confidence are computed from
decay-model fields (decay_profile, last_reinforced_at, reinforcement_boost,
salience_base, confidence_base, pinned) rather than raw stored values.

Supersedence-aware scoring: when queries ask about current state and
candidates contain temporal update language, recency scores are boosted
to prefer newer knowledge updates over older original facts.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from prme.models.nodes import MemoryNode
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, ScoringWeights
from prme.retrieval.models import RetrievalCandidate, ScoreTrace
from prme.types import DECAY_LAMBDAS, EPISTEMIC_WEIGHTS, DecayProfile, EpistemicType, LifecycleState

if TYPE_CHECKING:
    from prme.retrieval.models import QueryAnalysis


# --- Supersedence-aware scoring helpers ---

# Compiled regex for detecting update/change language in candidate content.
_UPDATE_LANGUAGE_RE = re.compile(
    r"(?:"
    r"changed\s+to"
    r"|switched\s+to"
    r"|migrated\s+(?:from|to)"
    r"|moved\s+to"
    r"|replaced"
    r"|updated\s+to"
    r"|now\s+uses"
    r"|no\s+longer"
    r"|rewritten"
    r"|upgraded\s+to"
    r"|new\s+\S+\s+is"
    r"|effective\s+immediately"
    r")",
    re.IGNORECASE,
)

# Compiled regex for detecting current-state query language.
_CURRENT_STATE_QUERY_RE = re.compile(
    r"\b(?:current|currently|now|latest|today|at\s+the\s+moment|these\s+days|presently)\b",
    re.IGNORECASE,
)


def _has_update_language(content: str) -> bool:
    """Check whether content contains temporal update signal words.

    Detects phrases like "changed to", "switched to", "migrated from/to",
    "now uses", "no longer", etc. that indicate a knowledge update.

    Args:
        content: The text content to check.

    Returns:
        True if update language is detected, False otherwise.
    """
    return bool(_UPDATE_LANGUAGE_RE.search(content))


def _is_current_state_query(query_analysis: QueryAnalysis) -> bool:
    """Determine if a query is asking about the current state of something.

    Returns True if the query text contains words like "current", "currently",
    "now", "latest", "today", "at the moment", "these days", "presently",
    OR if the intent is TEMPORAL with no specific past time reference
    (i.e., time_from and time_to are both None).

    Args:
        query_analysis: The analyzed query.

    Returns:
        True if the query is asking about current state.
    """
    from prme.types import QueryIntent

    # Check for current-state keywords in query text.
    if _CURRENT_STATE_QUERY_RE.search(query_analysis.query):
        return True

    # TEMPORAL intent with no specific past time reference implies
    # "what is the current state?" rather than "what happened at time X?"
    if (
        query_analysis.intent == QueryIntent.TEMPORAL
        and query_analysis.time_from is None
        and query_analysis.time_to is None
    ):
        return True

    return False


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
) -> ScoreTrace:
    """Compute the 8-input composite score for a single candidate.

    Formula (RFC-0005 Section 7, updated by RFC-0015):
      additive = w_semantic*semantic + w_lexical*lexical + w_graph*graph
                 + w_recency*recency + w_salience*eff_salience
                 + w_confidence*eff_confidence
      composite = additive * epistemic_weight
      path_score = min(path_count / 3.0, 1.0)   (tiebreaker only)

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
    composite = round(additive * epistemic_weight, 10)

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

    Supersedence-aware scoring: when ``query_analysis`` indicates a
    current-state query and candidates contain update language, the
    effective recency weight is increased (redistributed proportionally
    from semantic and lexical) and the recency score for update-language
    candidates is boosted by 1.5x (capped at 1.0).

    Args:
        candidates: Candidates to score.
        weights: Scoring weight configuration (default: DEFAULT_SCORING_WEIGHTS).
        epistemic_weights: Optional dict of epistemic type string values to
            float multipliers. Passed through to compute_composite_score.
        now: Reference timestamp for decay computation.  Passed through to
            compute_composite_score for consistent scoring within a batch.
        query_analysis: Optional QueryAnalysis for supersedence-aware scoring.
            When provided and the query asks about current state, candidates
            with update language get recency boosts.

    Returns:
        Tuple of (sorted candidates, corresponding score traces).
    """
    # Determine if supersedence-aware scoring applies.
    is_current_query = (
        query_analysis is not None and _is_current_state_query(query_analysis)
    )

    # If current-state query, compute adjusted weights: increase recency
    # from its configured value to 0.25, redistributing the difference
    # proportionally from semantic and lexical weights.
    effective_weights = weights
    if is_current_query:
        target_recency = 0.25
        recency_increase = target_recency - weights.w_recency
        if recency_increase > 0:
            # Redistribute from semantic and lexical proportionally.
            sem_lex_total = weights.w_semantic + weights.w_lexical
            if sem_lex_total > 0:
                sem_reduction = recency_increase * (weights.w_semantic / sem_lex_total)
                lex_reduction = recency_increase * (weights.w_lexical / sem_lex_total)
                effective_weights = ScoringWeights(
                    w_semantic=weights.w_semantic - sem_reduction,
                    w_lexical=weights.w_lexical - lex_reduction,
                    w_graph=weights.w_graph,
                    w_recency=target_recency,
                    w_salience=weights.w_salience,
                    w_confidence=weights.w_confidence,
                    w_epistemic=weights.w_epistemic,
                    w_paths=weights.w_paths,
                    recency_lambda=weights.recency_lambda,
                )

    traces: list[ScoreTrace] = []

    for candidate in candidates:
        trace = compute_composite_score(
            candidate, effective_weights, epistemic_weights, now=now,
        )

        # Supersedence boost: for current-state queries, candidates with
        # update language get their recency score boosted by 1.5x.
        if is_current_query and _has_update_language(candidate.node.content):
            boosted_recency = min(trace.recency_factor * 1.5, 1.0)
            # Recompute the additive score with the boosted recency.
            additive = (
                effective_weights.w_semantic * trace.semantic_similarity
                + effective_weights.w_lexical * trace.lexical_relevance
                + effective_weights.w_graph * trace.graph_proximity
                + effective_weights.w_recency * boosted_recency
                + effective_weights.w_salience * trace.salience
                + effective_weights.w_confidence * trace.confidence
            )
            composite = round(additive * trace.epistemic_weight, 10)
            # Create updated trace with boosted values.
            trace = ScoreTrace(
                semantic_similarity=trace.semantic_similarity,
                lexical_relevance=trace.lexical_relevance,
                graph_proximity=trace.graph_proximity,
                recency_factor=boosted_recency,
                salience=trace.salience,
                confidence=trace.confidence,
                epistemic_weight=trace.epistemic_weight,
                path_score=trace.path_score,
                composite_score=composite,
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
