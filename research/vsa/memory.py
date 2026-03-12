"""VSA-based memory store.

Replaces PRME's four-store architecture with a single VSA substrate.
Instead of separate event log, graph, vector index, and lexical index,
every memory is a single hypervector that encodes content, structure,
time, and relationships simultaneously.

Store and retrieve operations work entirely through VSA algebra:
- Store: bind content with roles, bundle into composite, add to memory
- Retrieve: construct a query vector, find most similar memories
- Supersede: the superseding memory naturally dominates via recency weighting
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from research.vsa.core import (
    HV, DEFAULT_DIM,
    bind, bundle, unbind, similarity, normalize,
    weighted_bundle, random_hv,
)
from research.vsa.codebook import Codebook
from research.vsa.temporal import TemporalEncoder


@dataclass
class MemoryRecord:
    """A stored memory with its metadata and VSA encoding."""

    id: str
    content: str
    node_type: str  # fact, event, preference, etc.
    composite_hv: HV  # the full VSA encoding
    content_hv: HV  # just the content encoding (for content-only queries)
    time_hv: HV  # temporal encoding
    created_at: datetime
    day: int  # simulation day
    confidence: float = 1.0
    lifecycle_state: str = "tentative"
    superseded_by: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """A single retrieval result with scoring breakdown."""

    record: MemoryRecord
    composite_score: float
    content_similarity: float
    temporal_similarity: float
    type_similarity: float
    lifecycle_weight: float


class VSAMemory:
    """Memory store built entirely on VSA operations.

    This is the core innovation: a single algebraic substrate
    that replaces graph traversal, vector search, lexical matching,
    and event log queries with unified hypervector operations.

    Architecture:
    - Each memory is encoded as: bundle(
        bind(CONTENT, content_hv),
        bind(TYPE, type_hv),
        bind(TIME, time_hv),
        bind(SCOPE, scope_hv),
        bind(AGENT, agent_hv),
      )
    - Retrieval constructs a partial query vector and finds
      the most similar stored memories
    - Supersedence is handled by lifecycle weights, not deletion
    """

    # Lifecycle state weights — how much each state contributes to retrieval
    LIFECYCLE_WEIGHTS = {
        "tentative": 0.8,
        "stable": 1.0,
        "contested": 0.5,
        "superseded": 0.1,  # heavily suppressed but not invisible
        "deprecated": 0.05,
        "archived": 0.0,
    }

    def __init__(
        self,
        dim: int = DEFAULT_DIM,
        codebook: Codebook | None = None,
        temporal: TemporalEncoder | None = None,
    ):
        """Initialize VSA memory store.

        Args:
            dim: Hypervector dimensionality.
            codebook: Optional pre-initialized codebook.
            temporal: Optional pre-initialized temporal encoder.
        """
        self.dim = dim
        self.codebook = codebook or Codebook(dim=dim)
        self.temporal = temporal or TemporalEncoder(dim=dim)
        self._memories: list[MemoryRecord] = []
        self._id_index: dict[str, int] = {}  # id -> list position

    @property
    def size(self) -> int:
        """Number of stored memories."""
        return len(self._memories)

    def store(
        self,
        content: str,
        node_type: str = "fact",
        day: int = 0,
        confidence: float = 1.0,
        scope: str = "personal",
        agent: str = "user",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a new memory.

        Encodes the content and metadata as a composite hypervector
        and adds it to the memory store.

        Args:
            content: Text content of the memory.
            node_type: Type (fact, event, preference, etc.).
            day: Simulation day number.
            confidence: Confidence score [0, 1].
            scope: Memory scope (personal, project, etc.).
            agent: Who created this memory.
            tags: Optional topic tags.
            metadata: Optional additional metadata.

        Returns:
            Unique ID for the stored memory.
        """
        memory_id = str(uuid.uuid4())[:8]

        # Encode content
        content_hv = self.codebook.get_or_encode(content)

        # Encode node type
        type_key = f"NT_{node_type.upper()}"
        type_hv = self.codebook.get(type_key)

        # Encode time
        time_hv = self.temporal.encode_day_offset(day)

        # Encode scope and agent
        scope_hv = self.codebook.get(f"SCOPE_{scope.upper()}")
        agent_hv = self.codebook.get(f"AGENT_{agent.upper()}")

        # Compose the full memory vector:
        # bind each component with its role, then bundle everything
        components = [
            bind(self.codebook.get("CONTENT"), content_hv),
            bind(self.codebook.get("TYPE"), type_hv),
            bind(self.codebook.get("TIME"), time_hv),
            bind(self.codebook.get("SCOPE"), scope_hv),
            bind(self.codebook.get("AGENT"), agent_hv),
        ]

        # Also bind in tag information if present
        if tags:
            for tag in tags:
                tag_hv = self.codebook.get(f"TAG_{tag.upper()}")
                components.append(bind(self.codebook.get("TOPIC"), tag_hv))

        composite_hv = bundle(*components) if len(components) >= 2 else components[0]

        record = MemoryRecord(
            id=memory_id,
            content=content,
            node_type=node_type,
            composite_hv=composite_hv,
            content_hv=content_hv,
            time_hv=time_hv,
            created_at=datetime.now(timezone.utc),
            day=day,
            confidence=confidence,
            tags=tags or [],
            metadata=metadata or {},
        )

        self._id_index[memory_id] = len(self._memories)
        self._memories.append(record)

        return memory_id

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        query_day: int | None = None,
        node_type: str | None = None,
        include_superseded: bool = False,
        content_weight: float = 0.15,
        tag_weight: float = 0.50,
        temporal_weight: float = 0.1,
        type_weight: float = 0.1,
        lifecycle_weight: float = 0.15,
    ) -> list[RetrievalResult]:
        """Retrieve memories most relevant to a query.

        Constructs a query hypervector and scores all memories
        using a weighted combination of:
        - Content similarity (bag-of-words VSA match)
        - Tag similarity (query words matching stored tags — semantic routing)
        - Temporal similarity (how close in time)
        - Type similarity (matching node type)
        - Lifecycle weight (suppressing superseded/deprecated)

        Tag matching is the key semantic signal: tags represent domain
        knowledge that bridges the gap between query terms and stored
        content. Without pre-trained embeddings, tags provide the
        "MySQL is a database" relationship that pure word vectors lack.

        Args:
            query: Text query.
            top_k: Maximum results to return.
            query_day: Day to query from (for temporal scoring).
            node_type: Optional type filter.
            include_superseded: Whether to include superseded memories.
            content_weight: Weight for content similarity.
            tag_weight: Weight for tag match signal.
            temporal_weight: Weight for temporal proximity.
            type_weight: Weight for type match.
            lifecycle_weight: Weight for lifecycle state.

        Returns:
            List of RetrievalResult, sorted by composite score descending.
        """
        if not self._memories:
            return []

        # Encode the query
        query_content_hv = self.codebook.get_or_encode(query)
        query_time_hv = (
            self.temporal.encode_day_offset(query_day)
            if query_day is not None
            else None
        )
        query_type_hv = (
            self.codebook.get(f"NT_{node_type.upper()}")
            if node_type is not None
            else None
        )

        # Extract query words for tag matching (stemmed for morphological matching)
        raw_query_words = set(query.lower().split())
        raw_query_words = {w.strip(".,;:!?\"'()[]{}") for w in raw_query_words}
        stopwords = self.codebook._STOPWORDS if hasattr(self.codebook, '_STOPWORDS') else set()
        raw_query_words -= stopwords
        query_words = {self.codebook._stem(w) for w in raw_query_words if w}

        # Identify the topic word — first content word in the query.
        # In "What database does the project use?", "database" is the topic.
        # This word gets extra weight in tag matching.
        topic_word = None
        for w in query.lower().split():
            w_clean = w.strip(".,;:!?\"'()[]{}")
            if w_clean and w_clean not in stopwords:
                topic_word = self.codebook._stem(w_clean)
                break

        # Also encode individual query content words as tag vectors
        # so we can match them against stored tag vectors via VSA
        query_tag_hvs = []
        for word in query_words:
            if word:
                query_tag_hvs.append(self.codebook.get(f"TAG_{word.upper()}"))

        # Pre-compute transition detection and clean-tag index.
        # Transition records (migrations, deprecations) should yield to
        # clean current-state facts with overlapping tags. But if no clean
        # alternative exists, the transition record retains full weight.
        _migration_signals = (
            "migrated", "switched", "moved", "replaced",
            "no longer", "deprecated", "went back",
            "changed from", "migration",
        )
        _transition_tag_names = {"migration", "deprecated", "legacy", "removed"}

        # Map: record index → is_transition
        transition_map: dict[int, bool] = {}
        # Tags that have at least one clean (non-transition) record
        clean_tags: set[str] = set()

        for idx, rec in enumerate(self._memories):
            if rec.lifecycle_state in ("archived", "superseded"):
                continue
            rec_lower = rec.content.lower()
            is_trans = any(sig in rec_lower for sig in _migration_signals)
            if not is_trans and rec.tags:
                is_trans = bool(_transition_tag_names & {t.lower() for t in rec.tags})
            transition_map[idx] = is_trans
            if not is_trans and rec.tags:
                for tag in rec.tags:
                    clean_tags.add(tag.lower())

        results = []

        for record in self._memories:
            # Skip archived
            if record.lifecycle_state == "archived":
                continue

            # Skip superseded unless requested
            if not include_superseded and record.lifecycle_state == "superseded":
                continue

            # Content similarity — bag-of-words VSA match
            content_sim = similarity(query_content_hv, record.content_hv)

            idx = self._id_index.get(record.id, -1)
            is_transition = transition_map.get(idx, False)
            # Only penalize if a clean alternative with overlapping tags exists
            has_clean_alt = is_transition and record.tags and any(
                t.lower() in clean_tags for t in record.tags
            )

            # Tag similarity — semantic routing signal
            # Two strategies:
            # 1. Direct word match: query words that appear in memory tags
            # 2. VSA tag vector match: encoded tag vectors compared via cosine
            tag_sim = 0.0
            if record.tags:
                record_tag_set = {self.codebook._stem(t.lower()) for t in record.tags}

                # Strategy 1: direct word overlap with topic-word boosting
                # The topic word (first content word in query) gets double weight,
                # so "What database does the project use?" prioritizes "database"
                # matches over "project" matches.
                direct_match = query_words & record_tag_set
                if query_words and direct_match:
                    topic_bonus = 1.0 if (topic_word and topic_word in direct_match) else 0.0
                    weighted_match = len(direct_match) + topic_bonus
                    weighted_total = len(query_words) + (1.0 if topic_word else 0.0)
                    tag_sim = weighted_match / max(weighted_total, 1.0)

                # Reduce tag signal for transition records when clean alternatives exist
                if tag_sim > 0 and has_clean_alt:
                    tag_sim *= 0.3

                # Strategy 2: VSA similarity between query word tags and memory tags
                # This catches partial matches and morphological variants
                if query_tag_hvs and tag_sim < 1.0:
                    tag_sims = []
                    for qt_hv in query_tag_hvs:
                        for tag in record.tags:
                            record_tag_hv = self.codebook.get(f"TAG_{tag.upper()}")
                            s = similarity(qt_hv, record_tag_hv)
                            if s > 0.5:  # only count strong matches
                                tag_sims.append(s)
                    if tag_sims:
                        vsa_tag_score = sum(tag_sims) / max(len(record.tags), 1)
                        tag_sim = max(tag_sim, vsa_tag_score)

            # Temporal similarity — recency bonus
            temporal_sim = 0.0
            if query_time_hv is not None:
                temporal_sim = similarity(query_time_hv, record.time_hv)
                # Shift to [0, 1] range for scoring
                temporal_sim = (temporal_sim + 1.0) / 2.0

            # Type similarity
            type_sim = 0.0
            if query_type_hv is not None:
                type_key = f"NT_{record.node_type.upper()}"
                record_type_hv = self.codebook.get(type_key)
                type_sim = max(0.0, similarity(query_type_hv, record_type_hv))
            else:
                type_sim = 0.5  # neutral when no type filter

            # Lifecycle weight
            lw = self.LIFECYCLE_WEIGHTS.get(record.lifecycle_state, 0.5)

            # Composite score
            composite = (
                content_weight * max(0.0, content_sim)
                + tag_weight * tag_sim
                + temporal_weight * temporal_sim
                + type_weight * type_sim
                + lifecycle_weight * lw
            ) * record.confidence

            # Transition records yield to clean current-state facts.
            if has_clean_alt:
                composite *= 0.50

            results.append(RetrievalResult(
                record=record,
                composite_score=composite,
                content_similarity=content_sim,
                temporal_similarity=temporal_sim,
                type_similarity=type_sim,
                lifecycle_weight=lw,
            ))

        # Sort by composite score descending
        results.sort(key=lambda r: r.composite_score, reverse=True)
        return results[:top_k]

    def supersede(self, old_id: str, new_id: str) -> None:
        """Mark a memory as superseded by another.

        The old memory's lifecycle state changes to "superseded"
        and it gets heavily suppressed in retrieval.

        Args:
            old_id: ID of the memory being superseded.
            new_id: ID of the superseding memory.
        """
        if old_id in self._id_index:
            idx = self._id_index[old_id]
            self._memories[idx].lifecycle_state = "superseded"
            self._memories[idx].superseded_by = new_id

    def promote(self, memory_id: str, state: str = "stable") -> None:
        """Promote a memory to a new lifecycle state.

        Args:
            memory_id: ID of the memory to promote.
            state: Target lifecycle state.
        """
        if memory_id in self._id_index:
            idx = self._id_index[memory_id]
            self._memories[idx].lifecycle_state = state

    def detect_supersedence(self, new_content: str, new_id: str, threshold: float = 0.15) -> list[str]:
        """Detect and mark memories that the new content supersedes.

        Uses VSA content similarity + keyword heuristics to find old
        memories that are contradicted or replaced by the new content.

        Two-pronged detection:
        1. VSA similarity above threshold + migration language in new content
        2. Shared domain keywords between old and new content

        Args:
            new_content: The new memory content.
            new_id: ID of the new memory.
            threshold: Minimum content similarity to consider.

        Returns:
            List of superseded memory IDs.
        """
        new_hv = self.codebook.get_or_encode(new_content)
        content_lower = new_content.lower()

        # Extract "from" and "to" phrases — what's being replaced and what replaces it
        from_phrases: list[str] = []  # phrases of what's being replaced
        to_phrases: list[str] = []    # phrases of what's replacing (protect these)
        import re

        # "migrated/switched/moved ... from X to Y" (allows words between verb and "from")
        from_to_matches = re.findall(
            r'(?:from|replaced)\s+([\w\s]+?)\s+(?:to|with)\s+([\w\s]+?)(?:\s+(?:for|using|on)\b|\.|,|$)',
            content_lower,
        )
        for from_part, to_part in from_to_matches:
            from_phrases.append(from_part.strip())
            to_phrases.append(to_part.strip())

        # "X is no longer" — capture the noun phrase
        no_longer = re.findall(r'((?:\w+\s+){0,2}\w+)\s+is\s+no\s+longer', content_lower)
        for match in no_longer:
            from_phrases.append(match.strip())

        # "X is deprecated" — capture the full noun phrase
        deprecated_matches = re.findall(r'((?:\w+\s+){0,2}\w+)\s+(?:is\s+)?deprecated', content_lower)
        for match in deprecated_matches:
            from_phrases.append(match.strip())

        # "went back to X from Y"
        back_to = re.findall(r'(?:went\s+back\s+to|returned\s+to)\s+([\w\s]+?)(?:\s+from\s+)([\w\s]+?)(?:\.|,|$)', content_lower)
        for to_part, from_part in back_to:
            to_phrases.append(to_part.strip())
            from_phrases.append(from_part.strip())

        # Filter noise words from each phrase, keeping the phrase structure
        noise_words = {
            "the", "a", "an", "is", "are", "was", "were",
            "our", "we", "i", "my", "and", "or", "for",
            "to", "in", "on", "with", "has", "have",
            "all", "it", "of", "as", "been", "be",
        }
        if hasattr(self.codebook, '_STOPWORDS'):
            noise_words |= self.codebook._STOPWORDS

        def clean_phrase(phrase: str) -> list[str]:
            """Return meaningful words from a phrase."""
            return [w for w in phrase.split() if w not in noise_words]

        from_word_sets = [clean_phrase(p) for p in from_phrases]
        to_word_sets = [clean_phrase(p) for p in to_phrases]

        # Also check for migration signals via keyword patterns
        # (broader than regex — catches "moved our infrastructure from")
        migration_signals = [
            "migrated", "switched", "moved", "replaced",
            "no longer", "deprecated", "went back",
            "changed", "migration",
        ]
        has_migration = (
            any(sig in content_lower for sig in migration_signals)
            or len(from_word_sets) > 0
        )

        if not has_migration:
            return []

        superseded_ids = []
        new_record = self.get_by_id(new_id)
        new_day = new_record.day if new_record else 0

        def phrase_matches(phrase_words: list[str], target_words: set[str]) -> bool:
            """Check if ALL words from a phrase appear in target words.

            Multi-word phrases like "vs code" require ALL words to match,
            preventing false positives where only "code" appears elsewhere.
            """
            return len(phrase_words) > 0 and all(w in target_words for w in phrase_words)

        for record in self._memories:
            if record.id == new_id:
                continue
            if record.lifecycle_state in ("superseded", "archived", "deprecated"):
                continue
            # Only supersede strictly older memories
            if record.day >= new_day:
                continue

            old_lower = record.content.lower()
            old_words = set(old_lower.split())
            old_words = {w.strip(".,;:!?\"'()[]{}") for w in old_words}
            old_tags = {t.lower() for t in record.tags}
            old_all = old_words | old_tags

            # Protect memories that mention the NEW subject
            protected = False
            for to_words in to_word_sets:
                if phrase_matches(to_words, old_all):
                    protected = True
                    break
            if protected:
                continue

            # Strategy 1: "from" phrase matches old content or tags
            # ALL words in the phrase must appear (prevents "code" false positive)
            matched = False
            for from_words in from_word_sets:
                if phrase_matches(from_words, old_all):
                    self.supersede(record.id, new_id)
                    superseded_ids.append(record.id)
                    matched = True
                    break
            if matched:
                continue

            # Strategy 2: VSA content similarity + shared tags
            content_sim = similarity(new_hv, record.content_hv)
            if content_sim > threshold:
                if new_record:
                    new_tags = {t.lower() for t in new_record.tags}
                    shared_tags = old_tags & new_tags
                    if shared_tags:
                        self.supersede(record.id, new_id)
                        superseded_ids.append(record.id)

        return superseded_ids

    def organize(self, current_day: int, promote_after_days: int = 7) -> dict[str, int]:
        """Run maintenance operations on the memory store.

        - Promote tentative → stable after sufficient time
        - Could add: deduplication, salience recalculation

        Args:
            current_day: Current simulation day.
            promote_after_days: Days before tentative → stable promotion.

        Returns:
            Dict of action counts: {"promoted": N, "archived": M, ...}
        """
        counts = {"promoted": 0}

        for record in self._memories:
            if record.lifecycle_state == "tentative":
                age = current_day - record.day
                if age >= promote_after_days:
                    record.lifecycle_state = "stable"
                    counts["promoted"] += 1

        return counts

    def get_by_id(self, memory_id: str) -> MemoryRecord | None:
        """Retrieve a memory by its ID."""
        if memory_id in self._id_index:
            return self._memories[self._id_index[memory_id]]
        return None

    def query_lifecycle_counts(self) -> dict[str, int]:
        """Count memories by lifecycle state."""
        counts: dict[str, int] = {}
        for record in self._memories:
            state = record.lifecycle_state
            counts[state] = counts.get(state, 0) + 1
        return counts
