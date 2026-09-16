"""Shared request-time and offline adjustments to applied additive weights."""
import math

from prme.models.learning import RankingMultipliers
from prme.retrieval.config import ScoringWeights

FEATURES = ("semantic", "lexical", "graph", "recency", "salience", "confidence")


def adjusted_weights(weights: ScoringWeights, multipliers: RankingMultipliers) -> ScoringWeights:
    """Apply bounded multipliers after query-specific weight redistribution."""
    if any(not math.isfinite(getattr(weights, "w_" + name)) or getattr(weights, "w_" + name) < 0
           for name in FEATURES):
        raise ValueError("Learning requires finite nonnegative additive weights")
    if all(getattr(multipliers, name) == 1 for name in FEATURES):
        return weights
    values = {"w_" + name: getattr(weights, "w_" + name) * getattr(multipliers, name) for name in FEATURES}
    total = sum(values.values())
    return ScoringWeights(**{**weights.model_dump(), **{key: value / total for key, value in values.items()}})
