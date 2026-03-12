"""Codebook: stable mapping from symbols to hypervectors.

The codebook maintains a deterministic, reproducible mapping from
string symbols (roles, entity names, type labels) to atomic
hypervectors. Once a symbol is assigned a vector, it never changes.

This is the "vocabulary" of the VSA — every concept that can be
bound or queried needs a codebook entry.
"""

from __future__ import annotations

from typing import Iterator

import numpy as np

from research.vsa.core import HV, DEFAULT_DIM, random_hv, bind, similarity


class Codebook:
    """Manages the symbol → hypervector mapping.

    Uses seeded RNG so the same symbol always gets the same vector
    across runs (given the same seed).

    Built-in role vectors (auto-populated):
    - AGENT, ACTION, OBJECT, TIME, LOCATION, TYPE, SCOPE
    - NODE_TYPE_* for each PRME node type
    - EDGE_TYPE_* for relationship types
    """

    # Structural roles — the "slots" in a frame-based memory
    BUILTIN_ROLES = [
        "AGENT", "ACTION", "OBJECT", "TOPIC", "LOCATION",
        "TIME", "TYPE", "SCOPE", "SOURCE", "CONFIDENCE",
        "CONTENT", "RELATION", "SUBJECT", "PREDICATE",
    ]

    # PRME node types as VSA symbols
    NODE_TYPES = [
        "ENTITY", "EVENT", "FACT", "DECISION",
        "PREFERENCE", "TASK", "SUMMARY", "NOTE", "INSTRUCTION",
    ]

    # Relationship types
    EDGE_TYPES = [
        "RELATES_TO", "SUPERSEDES", "DERIVED_FROM", "MENTIONS",
        "PART_OF", "CAUSED_BY", "SUPPORTS", "CONTRADICTS", "HAS_FACT",
    ]

    # Lifecycle states
    LIFECYCLE_STATES = [
        "TENTATIVE", "STABLE", "CONTESTED",
        "SUPERSEDED", "DEPRECATED", "ARCHIVED",
    ]

    def __init__(self, dim: int = DEFAULT_DIM, seed: int = 42):
        """Initialize the codebook.

        Args:
            dim: Hypervector dimensionality.
            seed: RNG seed for reproducible vector generation.
        """
        self.dim = dim
        self._seed = seed
        self._rng = np.random.default_rng(seed)
        self._symbols: dict[str, HV] = {}

        # Pre-populate built-in symbols
        for role in self.BUILTIN_ROLES:
            self._symbols[role] = random_hv(dim, bipolar=True, rng=self._rng)

        for nt in self.NODE_TYPES:
            key = f"NT_{nt}"
            self._symbols[key] = random_hv(dim, bipolar=True, rng=self._rng)

        for et in self.EDGE_TYPES:
            key = f"ET_{et}"
            self._symbols[key] = random_hv(dim, bipolar=True, rng=self._rng)

        for ls in self.LIFECYCLE_STATES:
            key = f"LS_{ls}"
            self._symbols[key] = random_hv(dim, bipolar=True, rng=self._rng)

    def get(self, symbol: str) -> HV:
        """Get or create the hypervector for a symbol.

        If the symbol hasn't been seen before, a new random vector
        is generated deterministically (based on insertion order
        relative to the seeded RNG).

        Args:
            symbol: The symbol string (case-sensitive).

        Returns:
            The hypervector for this symbol.
        """
        if symbol not in self._symbols:
            self._symbols[symbol] = random_hv(self.dim, bipolar=True, rng=self._rng)
        return self._symbols[symbol]

    # Stopwords to filter out during encoding — these carry no semantic weight
    _STOPWORDS = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "and", "or", "but", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more", "most",
        "other", "some", "such", "no", "only", "own", "same", "than",
        "too", "very", "just", "because", "as", "until", "while",
        "of", "at", "by", "for", "with", "about", "against", "between",
        "through", "during", "before", "after", "above", "below", "to",
        "from", "up", "down", "in", "out", "on", "off", "over", "under",
        "again", "further", "then", "once", "here", "there", "when",
        "where", "why", "how", "what", "which", "who", "whom", "this",
        "that", "these", "those", "i", "me", "my", "myself", "we", "our",
        "ours", "ourselves", "you", "your", "yours", "he", "him", "his",
        "she", "her", "hers", "it", "its", "they", "them", "their",
        "use", "used", "uses", "using", "currently", "now",
    })

    @staticmethod
    def _stem(word: str) -> str:
        """Lightweight suffix stripping for English words.

        Not a full stemmer — just handles the most common inflections
        so "deploy" and "deployment" map to the same vector. This is
        critical for VSA where words are atomic random vectors with
        no inherent semantic relationship.
        """
        if len(word) <= 3:
            return word

        # Order matters: try longest suffixes first
        suffixes = [
            "mentation", "ization", "isation", "fulness",
            "ousness", "iveness", "lessly",
            "ments", "ation", "ition", "iness", "ously",
            "ment", "ness", "able", "ible", "tion", "sion",
            "ally", "edly", "ling", "ings",
            "ing", "ies", "ous", "ful", "ive", "ity",
            "ers", "est", "ent", "ant",
            "ly", "ed", "er", "es",
            "s",
        ]
        for suffix in suffixes:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[:-len(suffix)]
        return word

    def get_or_encode(self, text: str) -> HV:
        """Get vector for a text string using bag-of-words bundling with stemming.

        For single tokens, returns the atomic vector (stemmed).
        For multi-word text, filters stopwords, stems content words,
        and bundles them. This means querying "deploy" will match any
        memory containing "deployment" — word forms don't matter.

        N-gram pairs are also included to capture local phrases
        like "VS Code" or "MySQL 8.0".

        Args:
            text: Text string to encode.

        Returns:
            Hypervector representing the text.
        """
        from research.vsa.core import bundle, bind, normalize

        # Tokenize and clean
        raw_words = text.lower().split()
        # Strip punctuation
        raw_words = [w.strip(".,;:!?\"'()[]{}") for w in raw_words]
        raw_words = [w for w in raw_words if w]

        if len(raw_words) == 0:
            return np.zeros(self.dim)

        # Content words (no stopwords), stemmed
        content_words = [self._stem(w) for w in raw_words if w not in self._STOPWORDS]

        if len(content_words) == 0:
            # All stopwords — fall back to stemmed raw words
            content_words = [self._stem(w) for w in raw_words]

        if len(content_words) == 1:
            return self.get(content_words[0])

        # Bag-of-words: bundle all content word vectors
        components = [self.get(w) for w in content_words]

        # Also add bigram bindings for local phrase capture
        # bind(word_i, word_i+1) captures "VS Code", "MySQL 8.0", etc.
        for i in range(len(content_words) - 1):
            bigram = bind(self.get(content_words[i]), self.get(content_words[i + 1]))
            components.append(bigram)

        return bundle(*components)

    def lookup(self, query: HV, top_k: int = 5, threshold: float = 0.1) -> list[tuple[str, float]]:
        """Find the closest symbols to a query vector.

        Args:
            query: Query hypervector.
            top_k: Maximum number of results.
            threshold: Minimum similarity to include.

        Returns:
            List of (symbol, similarity) tuples, sorted by similarity descending.
        """
        results = []
        for symbol, vec in self._symbols.items():
            sim = similarity(query, vec)
            if sim >= threshold:
                results.append((symbol, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._symbols

    def __len__(self) -> int:
        return len(self._symbols)

    def symbols(self) -> Iterator[str]:
        """Iterate over all registered symbols."""
        return iter(self._symbols)
