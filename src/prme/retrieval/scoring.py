"""Composite scoring and deterministic ranking (Stage 5).

Implements the 8-input composite score formula from RFC-0005 Section 7
and deterministic ranking with tie-breaking by object_id.

Virtual decay (RFC-0015): salience and confidence are computed from
decay-model fields (decay_profile, last_reinforced_at, reinforcement_boost,
salience_base, confidence_base, pinned) rather than raw stored values.

Temporal affinity: when query intent is TEMPORAL, candidates with date
content and timestamps near the query's temporal window receive a bonus
score (up to temporal_boost weight).

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
    r"(?:"
    r"\b(?:current|currently|now|latest|today|at\s+the\s+moment|these\s+days|presently)\b"
    r"|\bso\s+far\b"
    r"|\busually\b"
    r"|\btypically\b"
    r"|\bnormally\b"
    r"|\bmost\s+recently\b"
    r"|\bright\s+now\b"
    r"|^(?:do|does|am|are|have|has)\s+I\b"  # questions about current state
    r"|\bdo\s+I\s+(?:go|have|use|own|keep|play|attend|work)\b"
    r")",
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


# Compiled regex for detecting recent-episodic query language.
_RECENT_EPISODIC_QUERY_RE = re.compile(
    r"(?:"
    r"\b(?:recently|last\s+time|the\s+other\s+day|earlier\s+today"
    r"|yesterday|this\s+(?:morning|afternoon|evening|week)"
    r"|just\s+(?:now|told|said|mentioned|asked)"
    r"|remember\s+when|did\s+(?:I|we)\s+(?:talk|discuss|mention|say))\b"
    r")",
    re.IGNORECASE,
)


def _is_recent_episodic_query(query_analysis: QueryAnalysis) -> bool:
    """Detect queries about recent interactions/episodes.

    Returns True if the query text contains recency-oriented episodic
    language such as "recently", "last time", "yesterday", "this week",
    "just told", "remember when", "did I talk about", etc.

    Args:
        query_analysis: The analyzed query.

    Returns:
        True if the query is about recent episodic interactions.
    """
    return bool(_RECENT_EPISODIC_QUERY_RE.search(query_analysis.query))


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
    recency_reference: datetime | None = None,
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
        recency_reference: Optional reference timestamp for relative recency
            computation. When provided, recency is computed as the time gap
            between this candidate and the recency_reference (typically the
            newest candidate in the batch), making recency meaningful even
            when all events are old relative to ``now``. When None, falls
            back to ``now``.

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
    # When recency_reference is provided, compute relative recency (gap
    # between this candidate and the newest candidate) so that recency is
    # meaningful even when all events are old relative to ``now``.
    reference_time = node.updated_at or node.created_at
    recency_anchor = recency_reference or now
    days_since_update = max(0.0, (recency_anchor - reference_time).total_seconds() / 86400.0)
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

    # Node-type boost: semantic memory types get a multiplicative boost
    # per PRIME dual-memory research (Zhang et al., EMNLP 2025).
    node_type_key = node.node_type.value
    node_type_multiplier = weights.node_type_boost.get(node_type_key, 1.0)
    composite *= node_type_multiplier

    # Relevance floor: when query-dependent signals are weak, cap the
    # composite score so that query-independent signals (recency, salience,
    # confidence) cannot inflate it beyond the actual relevance level.
    relevance = candidate.semantic_score + candidate.lexical_score
    if weights.relevance_floor > 0 and relevance < weights.relevance_floor:
        composite = min(composite, relevance)

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
        node_type_boost=node_type_multiplier,
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
    effective recency weight is increased to 0.25 (redistributed from
    semantic and lexical), recency_lambda is increased to 0.05 for
    steeper decay, and update-language candidates get 2.0x recency
    boost (capped at 1.0).

    Args:
        candidates: Candidates to score.
        weights: Scoring weight configuration (default: DEFAULT_SCORING_WEIGHTS).
        epistemic_weights: Optional dict of epistemic type string values to
            float multipliers. Passed through to compute_composite_score.
        now: Reference timestamp for decay computation.  Passed through to
            compute_composite_score for consistent scoring within a batch.
        query_analysis: Optional query analysis for temporal boost and
            supersedence-aware scoring. When provided:
            - TEMPORAL intent activates temporal affinity scoring
            - Current-state queries boost candidates with update language

    Returns:
        Tuple of (sorted candidates, corresponding score traces).
    """
    # Determine if supersedence-aware scoring applies.
    is_current_query = (
        query_analysis is not None and _is_current_state_query(query_analysis)
    )

    # If current-state query, compute adjusted weights: increase recency
    # from its configured value to 0.25 and use a steeper recency_lambda
    # (0.05 vs default 0.01) so that older sessions are more strongly
    # penalized. This makes newer facts rank above older ones even when
    # the older fact has higher semantic similarity.
    effective_weights = weights
    if is_current_query:
        target_recency = 0.25
        target_lambda = max(weights.recency_lambda, 0.05)
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
                    recency_lambda=target_lambda,
                    temporal_boost=weights.temporal_boost,
                    node_type_boost=weights.node_type_boost,
                )

    # Episodic recency boost: when query is about recent interactions,
    # boost recency weight per PRIME finding that simple recency often
    # beats semantic similarity for episodic recall.
    is_episodic = (
        not is_current_query
        and query_analysis is not None
        and _is_recent_episodic_query(query_analysis)
    )
    if is_episodic:
        target_recency = 0.20
        recency_increase = target_recency - effective_weights.w_recency
        if recency_increase > 0:
            sem_lex_total = effective_weights.w_semantic + effective_weights.w_lexical
            if sem_lex_total > 0:
                sem_reduction = recency_increase * (effective_weights.w_semantic / sem_lex_total)
                lex_reduction = recency_increase * (effective_weights.w_lexical / sem_lex_total)
                effective_weights = ScoringWeights(
                    w_semantic=effective_weights.w_semantic - sem_reduction,
                    w_lexical=effective_weights.w_lexical - lex_reduction,
                    w_graph=effective_weights.w_graph,
                    w_recency=target_recency,
                    w_salience=effective_weights.w_salience,
                    w_confidence=effective_weights.w_confidence,
                    w_epistemic=effective_weights.w_epistemic,
                    w_paths=effective_weights.w_paths,
                    recency_lambda=effective_weights.recency_lambda,
                    temporal_boost=effective_weights.temporal_boost,
                    node_type_boost=effective_weights.node_type_boost,
                )

    # Compute relative recency reference: use the newest event_time (or
    # updated_at/created_at) among all candidates. This makes the recency
    # signal meaningful even when all events are old relative to ``now``
    # (e.g., benchmark data from years ago evaluated today).
    # Only used for current-state queries where we need to differentiate
    # old vs new facts. For other queries (temporal, multi_session, etc.),
    # relative recency would hurt by biasing toward newer events.
    recency_ref: datetime | None = None
    if is_current_query and candidates:
        def _ref_time(c: RetrievalCandidate) -> datetime:
            return c.node.event_time or c.node.updated_at or c.node.created_at
        recency_ref = max(_ref_time(c) for c in candidates)

    traces: list[ScoreTrace] = []

    for candidate in candidates:
        trace = compute_composite_score(
            candidate, effective_weights, epistemic_weights, now=now,
            query_analysis=query_analysis,
            recency_reference=recency_ref,
        )

        # Supersedence boost: for current-state queries, candidates with
        # update language get their recency score boosted by 2.0x.
        if is_current_query and _has_update_language(candidate.node.content):
            boosted_recency = min(trace.recency_factor * 2.0, 1.0)
            # Recompute the additive score with the boosted recency.
            additive = (
                effective_weights.w_semantic * trace.semantic_similarity
                + effective_weights.w_lexical * trace.lexical_relevance
                + effective_weights.w_graph * trace.graph_proximity
                + effective_weights.w_recency * boosted_recency
                + effective_weights.w_salience * trace.salience
                + effective_weights.w_confidence * trace.confidence
            )
            composite = round(
                additive * trace.epistemic_weight * trace.node_type_boost, 10,
            )
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
                node_type_boost=trace.node_type_boost,
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
