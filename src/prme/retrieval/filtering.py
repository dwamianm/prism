"""Epistemic filtering for the retrieval pipeline (Stage 4).

Filters candidates based on epistemic type and retrieval mode per
RFC-0005 Section 6 and RFC-0003 Section 8. DEFAULT mode excludes
HYPOTHETICAL and DEPRECATED candidates; EXPLICIT mode retains all.
"""

from __future__ import annotations

from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate
from prme.types import DEFAULT_EXCLUDED_EPISTEMIC, EpistemicType, RetrievalMode


def filter_epistemic(
    candidates: list[RetrievalCandidate],
    mode: RetrievalMode = RetrievalMode.DEFAULT,
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Filter candidates by epistemic type based on retrieval mode.

    Args:
        candidates: Candidates to filter.
        mode: Retrieval mode. DEFAULT excludes HYPOTHETICAL/DEPRECATED;
              EXPLICIT keeps all.

    Returns:
        Tuple of (kept candidates, excluded candidate records).
    """
    if mode == RetrievalMode.EXPLICIT:
        return candidates, []

    kept: list[RetrievalCandidate] = []
    excluded: list[ExcludedCandidate] = []

    for candidate in candidates:
        # Forward-compatible: MemoryNode may not yet have epistemic_type.
        # Default to ASSERTED (included) if attribute is missing.
        epistemic_type = getattr(
            candidate.node, "epistemic_type", EpistemicType.ASSERTED
        )

        if epistemic_type in DEFAULT_EXCLUDED_EPISTEMIC:
            excluded.append(
                ExcludedCandidate(
                    node_id=candidate.node.id,
                    reason=f"epistemic_filtered:{epistemic_type.value}",
                )
            )
        else:
            kept.append(candidate)

    return kept, excluded
