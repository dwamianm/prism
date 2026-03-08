"""Content-level contradiction detection for the store() path.

Detects explicit supersedence signals in new content (migration language
like "migrated from", "switched to", "no longer using") without requiring
LLM extraction. Used by store() when enable_store_supersedence=True.
"""

from __future__ import annotations

import re

# Patterns that signal the new content supersedes something older
CONTRADICTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bmigrat(?:ed?|e)\s+(?:from\s+)?(.+?)\s+to\s+", re.IGNORECASE),
    re.compile(r"\bswitch(?:ed)?\s+(?:from\s+)?(.+?)\s+to\s+", re.IGNORECASE),
    re.compile(r"\bmov(?:ed?|e)\s+(?:from\s+)?(.+?)\s+to\s+", re.IGNORECASE),
    re.compile(r"\breplac(?:ed?|e)\s+(.+?)\s+with\s+", re.IGNORECASE),
    re.compile(r"\bno\s+longer\s+(?:using|used|use)\b", re.IGNORECASE),
    re.compile(r"\bdeprecated\b", re.IGNORECASE),
    re.compile(r"\bwent\s+back\s+(?:to|from)\b", re.IGNORECASE),
    re.compile(r"\babandoned\b", re.IGNORECASE),
    re.compile(r"\bphased?\s+out\b", re.IGNORECASE),
    re.compile(r"\bis\s+(?:now\s+)?(?:obsolete|retired)\b", re.IGNORECASE),
]


class ContentContradictionDetector:
    """Detects explicit supersedence signals in content text.

    Keyword-based only -- no LLM required. Designed for the store() path
    which bypasses structured extraction.
    """

    def has_contradiction_signal(self, content: str) -> bool:
        """Check if content contains any contradiction/migration signal."""
        return any(p.search(content) for p in CONTRADICTION_PATTERNS)

    def extract_superseded_terms(self, content: str) -> list[str]:
        """Extract terms that are being superseded from the content.

        For patterns like "migrated from MySQL to PostgreSQL", extracts "MySQL".
        For patterns like "no longer using X", extracts terms near the pattern.

        Returns list of lowercase terms that appear to be superseded.
        """
        terms = []
        for pattern in CONTRADICTION_PATTERNS:
            for match in pattern.finditer(content):
                # Patterns with capture groups extract the "from" term
                if match.lastindex and match.lastindex >= 1:
                    captured = match.group(1).strip().lower()
                    # Add the full captured phrase
                    terms.append(captured)
                    # Also add individual words for multi-word terms
                    # (e.g. "VS Code" -> also add "vs", "code")
                    words = captured.split()
                    if len(words) > 1:
                        terms.extend(words)
        return terms

    def find_superseded_content(
        self,
        new_content: str,
        existing_contents: list[tuple[str, str]],  # [(node_id, content)]
    ) -> list[str]:
        """Find existing node IDs that the new content supersedes.

        Args:
            new_content: The new content being stored.
            existing_contents: List of (node_id, content) tuples for existing nodes.

        Returns:
            List of node_ids that should be marked as superseded.
        """
        if not self.has_contradiction_signal(new_content):
            return []

        superseded_terms = self.extract_superseded_terms(new_content)
        if not superseded_terms:
            # Has signal but no extractable terms -- check for "no longer"/"deprecated"
            # patterns that don't have a "from X" structure. In this case, look
            # for existing nodes with high content overlap.
            return []

        superseded_ids = []
        for node_id, existing_content in existing_contents:
            existing_lower = existing_content.lower()
            # Check if any superseded term appears in the existing content
            for term in superseded_terms:
                if term in existing_lower:
                    superseded_ids.append(node_id)
                    break

        return superseded_ids
