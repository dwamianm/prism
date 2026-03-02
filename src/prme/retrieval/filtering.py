"""Epistemic filtering for the retrieval pipeline (Stage 4).

Filters candidates based on epistemic type and retrieval mode per
RFC-0005 Section 6 and RFC-0003 Section 8. DEFAULT mode excludes
HYPOTHETICAL and DEPRECATED candidates, and UNVERIFIED candidates
below the confidence threshold; EXPLICIT mode retains all.
"""

from __future__ import annotations

from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate
from prme.types import DEFAULT_EXCLUDED_EPISTEMIC, EpistemicType, RetrievalMode

# [HYPOTHESIS] -- configurable threshold for UNVERIFIED nodes in DEFAULT mode.
# Per RFC-0003 S8: UNVERIFIED is "Excluded unless above threshold".
UNVERIFIED_CONFIDENCE_THRESHOLD: float = 0.30


def filter_epistemic(
    candidates: list[RetrievalCandidate],
    mode: RetrievalMode = RetrievalMode.DEFAULT,
    unverified_threshold: float | None = None,
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Filter candidates by epistemic type based on retrieval mode.

    In DEFAULT mode:
    - Excludes HYPOTHETICAL and DEPRECATED candidates.
    - Excludes UNVERIFIED candidates with confidence <= threshold (0.30).
    - Includes UNVERIFIED candidates above the threshold.

    In EXPLICIT mode:
    - All candidates are included regardless of epistemic type.

    Args:
        candidates: Candidates to filter.
        mode: Retrieval mode. DEFAULT excludes HYPOTHETICAL/DEPRECATED
              and low-confidence UNVERIFIED; EXPLICIT keeps all.
        unverified_threshold: Optional override for the UNVERIFIED confidence
            threshold. If None, uses module-level UNVERIFIED_CONFIDENCE_THRESHOLD.

    Returns:
        Tuple of (kept candidates, excluded candidate records).
    """
    if mode == RetrievalMode.EXPLICIT:
        return candidates, []

    threshold = (
        unverified_threshold
        if unverified_threshold is not None
        else UNVERIFIED_CONFIDENCE_THRESHOLD
    )

    kept: list[RetrievalCandidate] = []
    excluded: list[ExcludedCandidate] = []

    for candidate in candidates:
        # Direct field access -- epistemic_type is a native MemoryNode field.
        epistemic_type = candidate.node.epistemic_type

        if epistemic_type in DEFAULT_EXCLUDED_EPISTEMIC:
            excluded.append(
                ExcludedCandidate(
                    node_id=candidate.node.id,
                    reason=f"epistemic_filtered:{epistemic_type.value}",
                )
            )
        elif (
            epistemic_type == EpistemicType.UNVERIFIED
            and candidate.node.confidence <= threshold
        ):
            excluded.append(
                ExcludedCandidate(
                    node_id=candidate.node.id,
                    reason=(
                        f"unverified_below_threshold:"
                        f"{candidate.node.confidence:.2f}<="
                        f"{threshold:.2f}"
                    ),
                )
            )
        else:
            kept.append(candidate)

    return kept, excluded
