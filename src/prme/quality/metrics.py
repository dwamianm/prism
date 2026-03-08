"""Quality metrics computation for memory retrieval assessment.

Provides aggregate metrics over feedback signals to quantify
retrieval quality and weight adjustment history.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prme.quality.feedback import FeedbackSignal, FeedbackSignalType


@dataclass
class QualityMetrics:
    """Aggregate quality metrics over a set of feedback signals.

    Attributes:
        retrieval_quality: Overall quality score in [0.0, 1.0].
        used_rate: Fraction of signals where memory was used.
        correction_rate: Fraction of signals that were corrections.
        contradiction_rate: Fraction of signals that were contradictions.
        ignored_rate: Fraction of signals that were ignored.
        total_signals: Total number of signals analysed.
        weight_adjustments: Summary of weight changes (field -> delta).
    """

    retrieval_quality: float = 0.0
    used_rate: float = 0.0
    correction_rate: float = 0.0
    contradiction_rate: float = 0.0
    ignored_rate: float = 0.0
    total_signals: int = 0
    weight_adjustments: dict[str, float] = field(default_factory=dict)


def compute_quality_metrics(
    signals: list[FeedbackSignal],
    weight_adjustments: dict[str, float] | None = None,
) -> QualityMetrics:
    """Compute quality metrics from a list of feedback signals.

    Args:
        signals: Feedback signals to analyse.
        weight_adjustments: Optional dict of weight field name to
            cumulative delta. Included in the metrics for reporting.

    Returns:
        QualityMetrics with rates and overall quality score.
    """
    if not signals:
        return QualityMetrics(
            retrieval_quality=1.0,
            weight_adjustments=weight_adjustments or {},
        )

    total = len(signals)
    counts = {st: 0 for st in FeedbackSignalType}
    for s in signals:
        counts[s.signal_type] += 1

    used_rate = counts[FeedbackSignalType.USED] / total
    ignored_rate = counts[FeedbackSignalType.IGNORED] / total
    correction_rate = counts[FeedbackSignalType.CORRECTED] / total
    contradiction_rate = counts[FeedbackSignalType.CONTRADICTED] / total

    # Quality formula: USED contributes 1.0, IGNORED 0.3,
    # CORRECTED and CONTRADICTED contribute 0.0.
    quality = (
        counts[FeedbackSignalType.USED] * 1.0
        + counts[FeedbackSignalType.IGNORED] * 0.3
    ) / total

    return QualityMetrics(
        retrieval_quality=quality,
        used_rate=used_rate,
        correction_rate=correction_rate,
        contradiction_rate=contradiction_rate,
        ignored_rate=ignored_rate,
        total_signals=total,
        weight_adjustments=weight_adjustments or {},
    )
