"""Gradient-free weight tuner for retrieval scoring.

Adjusts ScoringWeights based on feedback signals using simple
multiplicative updates. This is NOT machine learning -- it is a
bounded, deterministic adjustment loop with small learning rates
to prevent oscillation.

Weight adjustment rules:
    USED:         +learning_rate to the dominant scoring component
    IGNORED:      -learning_rate*0.5 to the dominant scoring component
    CORRECTED:    -learning_rate to w_semantic, +learning_rate to w_confidence
    CONTRADICTED: -learning_rate to w_confidence, +learning_rate to w_epistemic

After adjustment, additive weights are renormalized to sum to 1.0 and
each individual weight is clamped to [0.01, 0.95].
"""

from __future__ import annotations

import logging

from prme.quality.feedback import FeedbackSignal, FeedbackSignalType
from prme.retrieval.config import ScoringWeights

logger = logging.getLogger(__name__)

# The six additive weight field names on ScoringWeights.
_ADDITIVE_FIELDS = [
    "w_semantic",
    "w_lexical",
    "w_graph",
    "w_recency",
    "w_salience",
    "w_confidence",
]

# Bounds for individual weights.
_MIN_WEIGHT = 0.01
_MAX_WEIGHT = 0.95


class WeightTuner:
    """Gradient-free tuner for ScoringWeights.

    Uses multiplicative updates driven by feedback signals.
    The learning rate is intentionally small (default 0.01) to
    prevent oscillation. Each call to ``update()`` produces a new
    frozen ScoringWeights instance.

    Attributes:
        current_weights: The current scoring weights being tuned.
        learning_rate: Step size for weight adjustments.
    """

    def __init__(
        self,
        current_weights: ScoringWeights,
        learning_rate: float = 0.01,
    ) -> None:
        self.current_weights = current_weights
        self.learning_rate = learning_rate

    def update(self, feedback_signals: list[FeedbackSignal]) -> ScoringWeights:
        """Apply feedback signals and return updated weights.

        Processes signals sequentially, accumulating adjustments into
        a mutable dict, then normalises and freezes the result.

        Args:
            feedback_signals: Signals to process.

        Returns:
            A new ScoringWeights with adjustments applied.
        """
        if not feedback_signals:
            return self.current_weights

        # Start from current weights as mutable dict.
        weights = {f: getattr(self.current_weights, f) for f in _ADDITIVE_FIELDS}
        # Also preserve non-additive weights.
        w_epistemic = self.current_weights.w_epistemic
        w_paths = self.current_weights.w_paths
        recency_lambda = self.current_weights.recency_lambda

        for signal in feedback_signals:
            weights, w_epistemic = self._apply_signal(
                weights, w_epistemic, signal,
            )

        # Clamp and normalize additive weights.
        weights = self._normalize_weights(weights)

        # Clamp epistemic weight (multiplicative, not part of additive sum).
        w_epistemic = max(_MIN_WEIGHT, min(_MAX_WEIGHT, w_epistemic))

        new_weights = ScoringWeights(
            w_semantic=weights["w_semantic"],
            w_lexical=weights["w_lexical"],
            w_graph=weights["w_graph"],
            w_recency=weights["w_recency"],
            w_salience=weights["w_salience"],
            w_confidence=weights["w_confidence"],
            w_epistemic=w_epistemic,
            w_paths=w_paths,
            recency_lambda=recency_lambda,
        )

        self.current_weights = new_weights
        return new_weights

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_signal(
        self,
        weights: dict[str, float],
        w_epistemic: float,
        signal: FeedbackSignal,
    ) -> tuple[dict[str, float], float]:
        """Apply a single feedback signal to mutable weight dict.

        Returns the (possibly modified) weights dict and epistemic weight.
        """
        lr = self.learning_rate

        if signal.signal_type == FeedbackSignalType.USED:
            # Increase the dominant additive component.
            dominant = self._dominant_component(weights)
            weights[dominant] += lr

        elif signal.signal_type == FeedbackSignalType.IGNORED:
            # Decrease the dominant additive component (half step).
            dominant = self._dominant_component(weights)
            weights[dominant] -= lr * 0.5

        elif signal.signal_type == FeedbackSignalType.CORRECTED:
            # Correction means semantic match was wrong --
            # decrease w_semantic, increase w_confidence.
            weights["w_semantic"] -= lr
            weights["w_confidence"] += lr

        elif signal.signal_type == FeedbackSignalType.CONTRADICTED:
            # Contradicted means confidence scoring failed --
            # decrease w_confidence, increase epistemic weight.
            weights["w_confidence"] -= lr
            w_epistemic += lr

        return weights, w_epistemic

    @staticmethod
    def _dominant_component(weights: dict[str, float]) -> str:
        """Return the field name of the largest additive weight."""
        return max(_ADDITIVE_FIELDS, key=lambda f: weights[f])

    @staticmethod
    def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
        """Clamp individual weights and renormalize to sum to 1.0.

        Steps:
        1. Clamp each weight to [_MIN_WEIGHT, _MAX_WEIGHT].
        2. Compute the sum of clamped weights.
        3. Scale proportionally so they sum to exactly 1.0.
        4. Re-clamp after scaling (edge case: scaling could push
           a near-boundary value outside bounds).

        Returns:
            New dict with normalized weights.
        """
        # Step 1: clamp.
        clamped = {
            f: max(_MIN_WEIGHT, min(_MAX_WEIGHT, v))
            for f, v in weights.items()
        }

        # Step 2-3: normalize to sum = 1.0.
        total = sum(clamped.values())
        if total == 0:
            # Degenerate: all weights clamped to min.
            even = 1.0 / len(clamped)
            return {f: even for f in clamped}

        normalized = {f: v / total for f, v in clamped.items()}

        # Step 4: final clamp after proportional scaling.
        normalized = {
            f: max(_MIN_WEIGHT, min(_MAX_WEIGHT, v))
            for f, v in normalized.items()
        }

        # Re-normalize after final clamp (minor adjustment).
        total2 = sum(normalized.values())
        if abs(total2 - 1.0) > 1e-9:
            normalized = {f: v / total2 for f, v in normalized.items()}

        # Round to 10 decimal places to avoid floating point noise.
        normalized = {f: round(v, 10) for f, v in normalized.items()}

        # Final fix-up: ensure exact sum is 1.0 by adjusting largest weight.
        remainder = 1.0 - sum(normalized.values())
        if abs(remainder) > 1e-12:
            largest = max(normalized, key=lambda f: normalized[f])
            normalized[largest] = round(normalized[largest] + remainder, 10)

        return normalized
