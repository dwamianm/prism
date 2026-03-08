"""Memory quality self-assessment and auto-tuning (issue #24).

Tracks feedback signals on surfaced memories and auto-tunes retrieval
scoring weights via gradient-free multiplicative updates.

Modules:
    feedback — FeedbackSignal, FeedbackSignalType, FeedbackTracker
    tuner    — WeightTuner for gradient-free weight optimization
    metrics  — QualityMetrics computation
"""

from prme.quality.feedback import FeedbackSignal, FeedbackSignalType, FeedbackTracker
from prme.quality.metrics import QualityMetrics, compute_quality_metrics
from prme.quality.tuner import WeightTuner

__all__ = [
    "FeedbackSignal",
    "FeedbackSignalType",
    "FeedbackTracker",
    "WeightTuner",
    "QualityMetrics",
    "compute_quality_metrics",
]
