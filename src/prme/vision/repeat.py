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
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DescriptionKey:
    """What the repeat filter compares: the quoted values and the unit description vector."""

    quotes: frozenset[str]
    vector: np.ndarray

    @classmethod
    def build(cls, quotes: frozenset[str], vector: list[float] | np.ndarray) -> DescriptionKey:
        array = np.asarray(vector, dtype=np.float32)
        return cls(quotes=quotes, vector=array / (np.linalg.norm(array) + 1e-9))


def repeat_similarity(previous: DescriptionKey | None, current: DescriptionKey) -> float | None:
    """Cosine similarity of two descriptions when their quoted text is the same set, else None."""
    if previous is None or previous.quotes != current.quotes:
        return None
    return float(np.dot(previous.vector, current.vector))


def is_repeat(previous: DescriptionKey | None, current: DescriptionKey, *, threshold: float) -> tuple[bool, float | None]:
    """Return whether *current* repeats *previous*, and the similarity that decided it."""
    similarity = repeat_similarity(previous, current)
    return similarity is not None and similarity >= threshold, similarity
