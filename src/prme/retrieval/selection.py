"""Explicit result selection, independent of ranking and token packing."""

import math

from prme.retrieval.models import (
    ExcludedCandidate,
    RetrievalCandidate,
    rank_fusion_relevance,
)


def validate_selection(
    min_score: float | None,
    limit: int | None,
    max_per_source: int | None = None,
    max_per_evidence: int | None = None,
) -> None:
    if min_score is not None and (not math.isfinite(min_score) or min_score < 0):
        raise ValueError("min_score must be a finite nonnegative number")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise ValueError("limit must be a nonnegative integer")
    if max_per_source is not None and (
        isinstance(max_per_source, bool)
        or not isinstance(max_per_source, int)
        or max_per_source < 1
    ):
        raise ValueError("max_per_source must be a positive integer")
    if max_per_evidence is not None and (
        isinstance(max_per_evidence, bool)
        or not isinstance(max_per_evidence, int)
        or max_per_evidence < 1
    ):
        raise ValueError("max_per_evidence must be a positive integer")


def _source_key(candidate: RetrievalCandidate) -> tuple[tuple[str, ...], str] | None:
    """Return an exact presentation-and-provenance group for one candidate.

    Derived claims can share a complete cited passage. Group only candidates
    with the same nonempty evidence set and byte-identical content: matching
    text from another event, or a different passage from the same event,
    remains independently selectable.
    """
    refs = tuple(sorted(str(ref) for ref in candidate.node.evidence_refs))
    if not refs:
        return None
    return refs, candidate.node.content


def _evidence_key(candidate: RetrievalCandidate) -> tuple[str, ...] | None:
    """Return one candidate's exact, order-independent nonempty evidence set."""
    refs = tuple(sorted(str(ref) for ref in candidate.node.evidence_refs))
    return refs or None


def with_rank_fusion_relevance(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    """Copy candidates with ``semantic_relevance`` set, leaving their scores unchanged.

    Under rank fusion, ``select_candidates`` then compares ``min_score`` with it.
    """
    return [
        candidate.model_copy(update={"semantic_relevance": rank_fusion_relevance(candidate)})
        for candidate in candidates
    ]


def select_candidates(
    candidates: list[RetrievalCandidate],
    *,
    min_score: float | None,
    limit: int | None,
    max_per_source: int | None = None,
    max_per_evidence: int | None = None,
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Keep ranked candidates meeting the inclusive score floor and count cap.

    Scores are model/configuration-dependent ranking signals, not calibrated
    probabilities. A candidate with a ``semantic_relevance`` (set under rank
    fusion) is compared with the floor by that value instead of its rank-based
    composite score. ``max_per_source`` optionally limits byte-identical passages
    carrying the same exact evidence set. ``max_per_evidence`` applies a broader
    cap to all nodes carrying the same exact evidence set, even when their text
    differs. Both fill the result cap from later groups. Nodes without evidence
    remain independently selectable.
    No implicit default floor or pin/task exemption is applied.
    """
    validate_selection(min_score, limit, max_per_source, max_per_evidence)
    selected, excluded = [], []
    source_counts: dict[tuple[tuple[str, ...], str], int] = {}
    evidence_counts: dict[tuple[str, ...], int] = {}
    for candidate in candidates:
        reason = None
        gated_score = (
            candidate.composite_score if candidate.semantic_relevance is None
            else candidate.semantic_relevance
        )
        if min_score is not None and gated_score < min_score:
            reason = "below_threshold"
        source_key = _source_key(candidate)
        evidence_key = _evidence_key(candidate)
        if (
            reason is None
            and max_per_source is not None
            and source_key is not None
            and source_counts.get(source_key, 0) >= max_per_source
        ):
            reason = "source_limit"
        if (
            reason is None
            and max_per_evidence is not None
            and evidence_key is not None
            and evidence_counts.get(evidence_key, 0) >= max_per_evidence
        ):
            reason = "evidence_limit"
        if reason is None and limit is not None and len(selected) >= limit:
            reason = "result_limit"
        if reason:
            excluded.append(ExcludedCandidate(
                node_id=candidate.node.id, reason=reason, composite_score=candidate.composite_score,
                semantic_relevance=candidate.semantic_relevance,
            ))
        else:
            selected.append(candidate)
            if source_key is not None:
                source_counts[source_key] = source_counts.get(source_key, 0) + 1
            if evidence_key is not None:
                evidence_counts[evidence_key] = evidence_counts.get(evidence_key, 0) + 1
    return selected, excluded
