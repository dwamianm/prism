"""Context packing for the retrieval pipeline (Stage 6).

Implements 3-priority greedy bin-packing per RFC-0006:
1. Pinned + active tasks (always include)
2. Multi-path objects by Signal-to-Token Ratio (STR) descending
3. Remaining by composite score

Token budget is NEVER exceeded. Mid-object truncation is not permitted --
either an item fits at some representation level, or it's excluded entirely.
"""

from __future__ import annotations

import math

from prme.retrieval.config import DEFAULT_PACKING_CONFIG, PackingConfig
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.types import LifecycleState, NodeType, RepresentationLevel


# Ordered from highest fidelity to lowest for representation selection.
_REPRESENTATION_ORDER: list[RepresentationLevel] = [
    RepresentationLevel.FULL,
    RepresentationLevel.PROSE,
    RepresentationLevel.STRUCTURED,
    RepresentationLevel.KEY_VALUE,
    RepresentationLevel.REFERENCE,
]


def estimate_token_cost(text: str, chars_per_token: float = 4.2) -> int:
    """Estimate token count from character length.

    Uses character-based estimation (RFC-0006 Method 2):
    ``math.ceil(len(text) / chars_per_token)``.

    This is the MVP approach -- tiktoken integration can be added later.

    Args:
        text: Input text to estimate.
        chars_per_token: Average characters per token (default 4.2).

    Returns:
        Estimated token count (always >= 1 for non-empty text).
    """
    if not text:
        return 0
    return math.ceil(len(text) / chars_per_token)


def compute_str(candidate: RetrievalCandidate) -> float:
    """Compute Signal-to-Token Ratio for a candidate (RFC-0006 S2).

    STR = composite_score / max(token_cost, 1).
    Higher STR = more information per token = better value.

    Args:
        candidate: A scored retrieval candidate.

    Returns:
        Signal-to-Token Ratio as a float.
    """
    token_cost = max(candidate.token_cost, 1)
    return candidate.composite_score / token_cost


def _render_representation(
    candidate: RetrievalCandidate,
    level: RepresentationLevel,
) -> str:
    """Render candidate content at the given representation level.

    Args:
        candidate: The retrieval candidate.
        level: Target representation level.

    Returns:
        Text content at the specified fidelity.
    """
    node = candidate.node
    content = node.content or ""

    if level == RepresentationLevel.FULL:
        return content

    if level == RepresentationLevel.PROSE:
        # Truncated to 80% of original length.
        cutoff = int(len(content) * 0.8)
        return content[:cutoff]

    if level == RepresentationLevel.STRUCTURED:
        # Key-value format with truncated content.
        preview = content[:200]
        if len(content) > 200:
            preview += "..."
        return f"type: {node.node_type.value}, content: {preview}"

    if level == RepresentationLevel.KEY_VALUE:
        return (
            f"id: {node.id}, type: {node.node_type.value}, "
            f"confidence: {node.confidence}"
        )

    # RepresentationLevel.REFERENCE
    return f"{node.node_type.value}:{node.id}"


def select_representation(
    candidate: RetrievalCandidate,
    available_tokens: int,
    min_fidelity: RepresentationLevel,
    chars_per_token: float = 4.2,
) -> tuple[RepresentationLevel, int]:
    """Determine the best representation level that fits the available budget.

    Iterates from highest fidelity (FULL) to lowest (REFERENCE), returning
    the first level that fits in available_tokens AND is >= min_fidelity.

    If even REFERENCE doesn't fit, returns (REFERENCE, cost) and lets the
    caller decide whether to include or exclude.

    Args:
        candidate: The retrieval candidate.
        available_tokens: Remaining token budget.
        min_fidelity: Minimum acceptable representation level.
        chars_per_token: Character-to-token ratio.

    Returns:
        Tuple of (selected RepresentationLevel, estimated token cost).
    """
    min_idx = _REPRESENTATION_ORDER.index(min_fidelity)

    # Only consider levels from FULL down to min_fidelity.
    eligible_levels = _REPRESENTATION_ORDER[: min_idx + 1]

    best_level = min_fidelity
    best_cost = 0

    for level in eligible_levels:
        text = _render_representation(candidate, level)
        cost = estimate_token_cost(text, chars_per_token)

        if cost <= available_tokens:
            return level, cost

        # Track the last level and cost for fallback.
        best_level = level
        best_cost = cost

    # Nothing fit -- return the lowest eligible level with its cost.
    # Caller decides whether to include or skip.
    ref_text = _render_representation(candidate, min_fidelity)
    ref_cost = estimate_token_cost(ref_text, chars_per_token)
    return min_fidelity, ref_cost


def classify_into_sections(candidate: RetrievalCandidate) -> str:
    """Map a candidate's node_type to a MemoryBundle section name.

    Args:
        candidate: The retrieval candidate.

    Returns:
        Section name string for the MemoryBundle.
    """
    node_type = candidate.node.node_type

    if node_type == NodeType.ENTITY:
        return "entity_snapshots"
    if node_type in (NodeType.FACT, NodeType.NOTE):
        return "stable_facts"
    if node_type == NodeType.DECISION:
        return "recent_decisions"
    if node_type == NodeType.TASK:
        return "active_tasks"
    return "provenance_refs"


def _is_pinned_or_active_task(candidate: RetrievalCandidate) -> bool:
    """Check if a candidate is pinned (salience==1.0) or an active task."""
    is_pinned = candidate.node.salience == 1.0
    is_active_task = (
        candidate.node.node_type == NodeType.TASK
        and candidate.node.lifecycle_state
        in (LifecycleState.TENTATIVE, LifecycleState.STABLE)
    )
    return is_pinned or is_active_task


def pack_context(
    scored_candidates: list[RetrievalCandidate],
    config: PackingConfig = DEFAULT_PACKING_CONFIG,
) -> MemoryBundle:
    """Pack scored candidates into a MemoryBundle within token budget.

    Implements 3-priority greedy bin-packing per RFC-0006 S5:

    1. **Priority 1:** Pinned (salience==1.0) + active tasks -- always include.
    2. **Priority 2:** Multi-path objects (path_count >= 2) by STR descending.
    3. **Priority 3:** Remaining by composite score descending.

    Token budget is NEVER exceeded. Mid-object truncation is not permitted.

    Args:
        scored_candidates: Candidates from scoring stage, sorted by score.
        config: Packing configuration (token budget, min fidelity, etc.).

    Returns:
        MemoryBundle with grouped sections, token usage, and excluded IDs.
    """
    budget = config.token_budget
    remaining = budget - config.overhead_tokens
    chars_per_token = config.chars_per_token
    min_fidelity = config.min_fidelity

    sections: dict[str, list[RetrievalCandidate]] = {}
    excluded_ids: list = []
    included_count = 0

    # Pre-compute token costs for all candidates.
    for candidate in scored_candidates:
        text = _render_representation(candidate, RepresentationLevel.FULL)
        candidate.token_cost = estimate_token_cost(text, chars_per_token)

    # Helper to attempt including a candidate.
    def _try_include(candidate: RetrievalCandidate) -> bool:
        nonlocal remaining, included_count

        level, cost = select_representation(
            candidate, remaining, min_fidelity, chars_per_token
        )

        if cost > remaining:
            # Doesn't fit even at minimum fidelity.
            excluded_ids.append(candidate.node.id)
            return False

        # Include at selected representation level.
        candidate.representation = level
        candidate.token_cost = cost

        section = classify_into_sections(candidate)
        if section not in sections:
            sections[section] = []
        sections[section].append(candidate)

        remaining -= cost
        included_count += 1
        return True

    # Track which candidates have been processed.
    processed_ids: set = set()

    # --- Priority 1: Pinned + active tasks (always include) ---
    priority_1 = [c for c in scored_candidates if _is_pinned_or_active_task(c)]
    for candidate in priority_1:
        _try_include(candidate)
        processed_ids.add(id(candidate))

    # --- Priority 2: Multi-path objects by STR descending ---
    priority_2 = [
        c
        for c in scored_candidates
        if id(c) not in processed_ids and c.path_count >= 2
    ]
    priority_2.sort(key=lambda c: -compute_str(c))
    for candidate in priority_2:
        _try_include(candidate)
        processed_ids.add(id(candidate))

    # --- Priority 3: Remaining by composite score ---
    priority_3 = [
        c for c in scored_candidates if id(c) not in processed_ids
    ]
    priority_3.sort(key=lambda c: (-c.composite_score, str(c.node.id)))
    for candidate in priority_3:
        _try_include(candidate)
        processed_ids.add(id(candidate))

    tokens_used = budget - config.overhead_tokens - remaining

    return MemoryBundle(
        sections=sections,
        included_count=included_count,
        excluded_ids=excluded_ids,
        tokens_used=tokens_used,
        token_budget=budget,
        budget_remaining=remaining,
        min_fidelity=min_fidelity,
    )
