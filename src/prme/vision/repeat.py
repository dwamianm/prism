"""The repeat filter: drop a described frame that repeats the previous kept one (RFC-0018).

After description, a frame whose quoted text is the same set as the previous
kept frame's, and whose description embedding (the pack's own text
embedding) has cosine similarity at or above the configured threshold to
it, is dropped as a repeat. Any difference in quoted text keeps the frame,
so a whiteboard with one changed value is never dropped. Word overlap was
tried first and failed: the same whiteboard described twice overlapped only
0.61, while its embedding similarity was 0.985; the same lake in new words
scored 0.94 to 0.975; two slides with different text 0.87 to 0.96, which the
quote gate rejects on its own.

The numbers a description states outside quotes gate the same way
(dwamianm/prism#390). The prompt quotes visible text but not what the model
counts, and with the pack's default embedding "two fingers raised" and
"three fingers raised" in otherwise equal descriptions scored 0.95 to 0.99,
so a camera that saw one more finger dropped the new count as a repeat.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from prme.vision.describe import quoted_text, stated_counts


@dataclass(frozen=True)
class DescriptionKey:
    """What the repeat filter compares: the quoted values, the stated counts and the unit description vector."""

    quotes: frozenset[str]
    vector: np.ndarray
    counts: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(cls, quotes: frozenset[str], vector: list[float] | np.ndarray,
              counts: frozenset[str] = frozenset()) -> DescriptionKey:
        array = np.asarray(vector, dtype=np.float32)
        return cls(quotes=quotes, vector=array / (np.linalg.norm(array) + 1e-9), counts=counts)

    @classmethod
    def of(cls, description: str, vector: list[float] | np.ndarray) -> DescriptionKey:
        """The key of *description*, embedded as *vector*: its quoted values and its stated counts."""
        return cls.build(quoted_text(description), vector, counts=stated_counts(description))


def repeat_similarity(previous: DescriptionKey | None, current: DescriptionKey) -> float | None:
    """Cosine similarity of two descriptions when their quoted text and counts are the same sets, else None."""
    if previous is None or previous.quotes != current.quotes or previous.counts != current.counts:
        return None
    return float(np.dot(previous.vector, current.vector))


def is_repeat(previous: DescriptionKey | None, current: DescriptionKey, *, threshold: float) -> tuple[bool, float | None]:
    """Return whether *current* repeats *previous*, and the similarity that decided it."""
    similarity = repeat_similarity(previous, current)
    return similarity is not None and similarity >= threshold, similarity
