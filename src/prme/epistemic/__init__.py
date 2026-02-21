"""Epistemic type inference, confidence matrix, and migration utilities.

Provides the confidence matrix for (epistemic_type, source_type) -> float
lookup, inference functions for assigning epistemic and source types from
context, and startup migration for backfilling existing nodes.
"""

from prme.epistemic.inference import infer_epistemic_type, infer_source_type
from prme.epistemic.matrix import ConfidenceMatrix, DEFAULT_CONFIDENCE_MATRIX

__all__ = [
    "ConfidenceMatrix",
    "DEFAULT_CONFIDENCE_MATRIX",
    "infer_epistemic_type",
    "infer_source_type",
]
