"""Explicit result selection, independent of ranking and token packing."""

import math

from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate


def validate_selection(min_score: float | None, limit: int | None) -> None:
    if min_score is not None and (not math.isfinite(min_score) or min_score < 0):
        raise ValueError("min_score must be a finite nonnegative number")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise ValueError("limit must be a nonnegative integer")


def select_candidates(
    candidates: list[RetrievalCandidate], *, min_score: float | None, limit: int | None,
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Keep ranked candidates meeting the inclusive score floor and count cap.

    Scores are model/configuration-dependent ranking signals, not calibrated
    probabilities. No implicit default floor or pin/task exemption is applied.
    """
    validate_selection(min_score, limit)
    selected, excluded = [], []
    for candidate in candidates:
        reason = None
        if min_score is not None and candidate.composite_score < min_score:
            reason = "below_threshold"
        elif limit is not None and len(selected) >= limit:
            reason = "result_limit"
        if reason:
            excluded.append(ExcludedCandidate(
                node_id=candidate.node.id, reason=reason, composite_score=candidate.composite_score,
            ))
        else:
            selected.append(candidate)
    return selected, excluded
