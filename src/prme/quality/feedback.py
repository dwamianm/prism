"""Feedback signal tracking for memory quality assessment.

Records whether surfaced memories were used, ignored, corrected, or
contradicted. These signals drive the auto-tuning of retrieval scoring
weights via the WeightTuner.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


class FeedbackSignalType(enum.Enum):
    """Type of feedback signal on a surfaced memory."""

    USED = "used"
    IGNORED = "ignored"
    CORRECTED = "corrected"
    CONTRADICTED = "contradicted"


@dataclass
class FeedbackSignal:
    """A single feedback signal on a retrieval result.

    Attributes:
        query: The original query that produced the retrieval.
        surfaced_node_ids: Node IDs that were surfaced for this query.
        signal_type: What happened to the surfaced memories.
        correction_content: If corrected, the user's correction text.
        timestamp: When the feedback was recorded.
    """

    query: str
    surfaced_node_ids: list[str]
    signal_type: FeedbackSignalType
    correction_content: str | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class FeedbackTracker:
    """In-memory tracker for feedback signals.

    Stores signals and provides quality scoring over configurable
    time windows. Thread-safe for single-writer patterns (the
    MemoryEngine serialises writes through the WriteQueue).
    """

    def __init__(self) -> None:
        self._signals: list[FeedbackSignal] = []

    def record(self, signal: FeedbackSignal) -> None:
        """Record a feedback signal.

        Args:
            signal: The feedback signal to store.
        """
        self._signals.append(signal)

    def get_signals(self, window_days: int = 30) -> list[FeedbackSignal]:
        """Return signals within the specified time window.

        Args:
            window_days: Number of days to look back. Defaults to 30.

        Returns:
            List of signals within the window, oldest first.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        return [s for s in self._signals if s.timestamp >= cutoff]

    def get_quality_score(self, window_days: int = 30) -> float:
        """Compute a quality score over the time window.

        Quality is the weighted ratio of positive signals (USED) to
        total signals. CORRECTED and CONTRADICTED are penalized more
        heavily than IGNORED.

        Score formula:
            quality = (used * 1.0 + ignored * 0.3) / total

        Where CORRECTED and CONTRADICTED count as 0.0 contribution.
        Returns 1.0 if no signals are present (optimistic default).

        Args:
            window_days: Number of days to look back. Defaults to 30.

        Returns:
            Quality score in [0.0, 1.0].
        """
        signals = self.get_signals(window_days)
        if not signals:
            return 1.0

        total = len(signals)
        score = 0.0
        for s in signals:
            if s.signal_type == FeedbackSignalType.USED:
                score += 1.0
            elif s.signal_type == FeedbackSignalType.IGNORED:
                score += 0.3
            # CORRECTED and CONTRADICTED contribute 0.0

        return score / total

    def clear(self) -> None:
        """Remove all stored signals."""
        self._signals.clear()

    def __len__(self) -> int:
        """Return total number of stored signals."""
        return len(self._signals)
