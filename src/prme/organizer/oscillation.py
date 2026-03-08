"""Oscillation detection for the store() path.

Detects flip-flop patterns in supersedence chains where content oscillates
between similar states (e.g. "use X" -> "use Y" -> "use X again"). When
oscillation is detected, confidence is reduced to reflect genuine uncertainty.

Used by store() when enable_store_supersedence=True, called after the
supersedence check completes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Words to ignore when computing Jaccard similarity (common English stop words)
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it",
    "they", "them", "their", "his", "her", "its",
    "and", "or", "but", "if", "then", "so", "for", "to", "of", "in",
    "on", "at", "by", "with", "from", "as", "into", "about", "that",
    "this", "these", "those", "not", "no", "all", "any", "some",
    "very", "just", "also", "too", "much", "more", "most",
})

# Minimum Jaccard similarity threshold for two nodes to be considered
# content-similar (indicating an oscillation loop).
_SIMILARITY_THRESHOLD = 0.3

# Maximum confidence penalty from oscillation detection.
_MAX_PENALTY = 0.3

# Per-cycle penalty increment.
_PENALTY_PER_CYCLE = 0.1

# Regex to extract words (alphanumeric sequences)
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class OscillationResult:
    """Result of detecting an oscillation pattern in a supersedence chain."""

    oscillating_node_ids: list[str]  # nodes forming the oscillation loop
    topic: str  # extracted topic of oscillation
    cycle_count: int  # number of times topic has flipped
    confidence_penalty: float  # suggested confidence reduction


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text, excluding stop words."""
    words = set(_WORD_RE.findall(text.lower()))
    return words - _STOP_WORDS


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _extract_topic(keywords_a: set[str], keywords_b: set[str]) -> str:
    """Extract the common topic from two keyword sets."""
    common = keywords_a & keywords_b
    if common:
        return " ".join(sorted(common)[:5])
    # Fallback: use the smaller set
    smaller = keywords_a if len(keywords_a) <= len(keywords_b) else keywords_b
    return " ".join(sorted(smaller)[:5])


class OscillationDetector:
    """Detects flip-flop patterns in supersedence chains.

    When content oscillates (e.g. "use X" -> "use Y" -> "use X again"),
    this reduces confidence on the oscillating nodes to reflect uncertainty.
    """

    def __init__(
        self,
        similarity_threshold: float = _SIMILARITY_THRESHOLD,
    ) -> None:
        self._similarity_threshold = similarity_threshold

    async def detect_oscillations(
        self,
        graph_store,
        node_id: str,
        *,
        max_chain_depth: int = 10,
    ) -> list[OscillationResult]:
        """Check if a node is part of an oscillation pattern.

        Traverses the supersedence chain backward from node_id and checks
        for content similarity loops (where a newer node's content is similar
        to an older superseded node's content).

        Returns list of detected oscillation patterns.
        """
        # Get the current node
        current_node = await graph_store.get_node(
            node_id, include_superseded=True
        )
        if current_node is None:
            return []

        # Get the supersedence chain backward (what this node replaced)
        chain = await graph_store.get_supersedence_chain(
            node_id, direction="backward"
        )

        if not chain:
            return []

        # Truncate chain to max_chain_depth
        chain = chain[:max_chain_depth]

        # Build the full chain: [current_node, ...backward_chain]
        # The backward chain is ordered: first element is what current replaced,
        # second is what that replaced, etc.
        full_chain = [current_node] + chain

        # Extract keywords for each node in the chain
        chain_keywords = [_extract_keywords(node.content) for node in full_chain]

        # Look for oscillation: compare current node (index 0) with nodes
        # at index 2, 4, 6, ... (nodes that are 2+ steps back)
        results: list[OscillationResult] = []
        cycle_count = 0
        oscillating_ids: list[str] = [str(full_chain[0].id)]

        for i in range(2, len(full_chain), 2):
            sim = _jaccard_similarity(chain_keywords[0], chain_keywords[i])
            if sim >= self._similarity_threshold:
                cycle_count += 1
                # Add intervening nodes and the matching node
                for j in range(1, i + 1):
                    nid = str(full_chain[j].id)
                    if nid not in oscillating_ids:
                        oscillating_ids.append(nid)

        if cycle_count > 0:
            topic = _extract_topic(chain_keywords[0], chain_keywords[2])
            penalty = min(_PENALTY_PER_CYCLE * cycle_count, _MAX_PENALTY)
            results.append(
                OscillationResult(
                    oscillating_node_ids=oscillating_ids,
                    topic=topic,
                    cycle_count=cycle_count,
                    confidence_penalty=penalty,
                )
            )

        return results
