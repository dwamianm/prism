"""Confidence matrix for (epistemic_type, source_type) -> default confidence.

The matrix maps each valid (EpistemicType, SourceType) combination to an
initial confidence value. Invalid combinations (absent from the matrix)
return None on lookup. All values are [HYPOTHESIS] unless noted -- tunable
per deployment via config override.

Reference: RFC-0003 Section 4, RESEARCH.md confidence matrix table.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

from prme.types import EpistemicType, SourceType

logger = logging.getLogger(__name__)


class ConfidenceMatrix(BaseModel):
    """Default confidence values by (epistemic_type, source_type).

    Frozen Pydantic model with dict-based lookup. Invalid combinations
    (not in the matrix) return None. All values are [HYPOTHESIS] unless
    explicitly noted.
    """

    model_config = ConfigDict(frozen=True)

    matrix: dict[tuple[str, str], float] = {
        # OBSERVED -- directly witnessed/recorded
        ("observed", "user_stated"): 0.90,          # User decision: 0.85-0.90
        ("observed", "user_demonstrated"): 0.85,    # [HYPOTHESIS]
        ("observed", "external_document"): 0.75,    # [HYPOTHESIS]
        ("observed", "tool_output"): 0.80,          # [HYPOTHESIS]
        # ASSERTED -- stated as fact, not independently verified
        ("asserted", "user_stated"): 0.80,          # [HYPOTHESIS]
        ("asserted", "user_demonstrated"): 0.75,    # [HYPOTHESIS]
        ("asserted", "system_inferred"): 0.60,      # [HYPOTHESIS]
        ("asserted", "external_document"): 0.65,    # [HYPOTHESIS]
        ("asserted", "tool_output"): 0.70,          # [HYPOTHESIS]
        # INFERRED -- derived by system from patterns
        ("inferred", "user_demonstrated"): 0.60,    # [HYPOTHESIS]
        ("inferred", "system_inferred"): 0.55,      # [HYPOTHESIS]
        ("inferred", "external_document"): 0.50,    # [HYPOTHESIS]
        ("inferred", "tool_output"): 0.55,          # [HYPOTHESIS]
        # HYPOTHETICAL -- explicitly speculative
        ("hypothetical", "user_stated"): 0.35,      # [HYPOTHESIS]
        ("hypothetical", "system_inferred"): 0.25,  # [HYPOTHESIS]
        ("hypothetical", "external_document"): 0.30,  # [HYPOTHESIS]
        # UNVERIFIED -- external/untrusted, awaiting corroboration
        ("unverified", "system_inferred"): 0.20,    # [HYPOTHESIS]
        ("unverified", "external_document"): 0.25,  # [HYPOTHESIS]
        ("unverified", "tool_output"): 0.30,        # [HYPOTHESIS]
    }

    def lookup(
        self,
        epistemic_type: EpistemicType,
        source_type: SourceType,
    ) -> float | None:
        """Return the default confidence for a (type, source) pair.

        Args:
            epistemic_type: The epistemic classification.
            source_type: The source provenance type.

        Returns:
            The confidence value, or None for invalid/absent combinations.
        """
        return self.matrix.get((epistemic_type.value, source_type.value))

    def lookup_with_fallback(
        self,
        epistemic_type: EpistemicType,
        source_type: SourceType,
        fallback: float = 0.50,
    ) -> float:
        """Return the default confidence, falling back if combination is absent.

        Logs a warning when the fallback is used, since absent combinations
        may indicate a configuration gap or unexpected input.

        Args:
            epistemic_type: The epistemic classification.
            source_type: The source provenance type.
            fallback: Default value for missing combinations (default 0.50).

        Returns:
            The matrix confidence value, or the fallback.
        """
        value = self.lookup(epistemic_type, source_type)
        if value is None:
            logger.warning(
                "Confidence matrix missing combination (%s, %s), "
                "using fallback %.2f",
                epistemic_type.value,
                source_type.value,
                fallback,
            )
            return fallback
        return value


# Module-level singleton -- the default confidence matrix with all
# [HYPOTHESIS]-marked values from RESEARCH.md.
DEFAULT_CONFIDENCE_MATRIX = ConfidenceMatrix()
