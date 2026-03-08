"""Alias resolution logic for the organizer (issue #11).

Detects entity aliases (abbreviations, case variations, known synonyms)
and either merges them (high confidence) or links them with RELATES_TO
edges annotated with alias metadata. No LLM required -- uses string
matching and vector similarity only.

Alias types:
- ABBREVIATION: "JS" <-> "JavaScript", "ML" <-> "Machine Learning"
- CASE_VARIATION: "postgresql" <-> "PostgreSQL"
- SEMANTIC: Vector similarity >= 0.85 between entity content
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from prme.models.edges import MemoryEdge
from prme.types import EdgeType, LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.config import OrganizerConfig
    from prme.models.nodes import MemoryNode
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known abbreviation mappings (extensible)
# ---------------------------------------------------------------------------

# Maps canonical form -> set of known abbreviations/aliases
# All lookups are case-insensitive
KNOWN_ALIASES: dict[str, set[str]] = {
    "javascript": {"js", "ecmascript"},
    "typescript": {"ts"},
    "python": {"py"},
    "machine learning": {"ml"},
    "artificial intelligence": {"ai"},
    "natural language processing": {"nlp"},
    "application programming interface": {"api"},
    "continuous integration": {"ci"},
    "continuous deployment": {"cd"},
    "continuous integration/continuous deployment": {"ci/cd", "cicd"},
    "database": {"db"},
    "postgresql": {"postgres", "pg"},
    "mongodb": {"mongo"},
    "kubernetes": {"k8s"},
    "amazon web services": {"aws"},
    "google cloud platform": {"gcp"},
    "microsoft azure": {"azure"},
    "operating system": {"os"},
    "user interface": {"ui"},
    "user experience": {"ux"},
    "representational state transfer": {"rest"},
    "graphql": {"gql"},
    "hypertext markup language": {"html"},
    "cascading style sheets": {"css"},
    "structured query language": {"sql"},
}

# Build reverse lookup: abbreviation -> canonical form
_REVERSE_ALIASES: dict[str, str] = {}
for _canonical, _abbrevs in KNOWN_ALIASES.items():
    for _abbrev in _abbrevs:
        _REVERSE_ALIASES[_abbrev.lower()] = _canonical.lower()


# ---------------------------------------------------------------------------
# Alias candidate
# ---------------------------------------------------------------------------


class AliasCandidate:
    """A pair of entity nodes identified as potential aliases."""

    __slots__ = ("entity_a_id", "entity_b_id", "alias_type", "confidence")

    def __init__(
        self,
        entity_a_id: str,
        entity_b_id: str,
        alias_type: str,
        confidence: float,
    ) -> None:
        self.entity_a_id = entity_a_id
        self.entity_b_id = entity_b_id
        self.alias_type = alias_type  # "abbreviation", "case_variation", "semantic"
        self.confidence = confidence

    def __repr__(self) -> str:
        return (
            f"AliasCandidate({self.entity_a_id!r}, {self.entity_b_id!r}, "
            f"type={self.alias_type!r}, conf={self.confidence:.4f})"
        )


# ---------------------------------------------------------------------------
# Alias detection
# ---------------------------------------------------------------------------


def _is_abbreviation_match(name_a: str, name_b: str) -> bool:
    """Check if one name is a known abbreviation of the other.

    Returns True if (name_a, name_b) or (name_b, name_a) appears in the
    known alias table.
    """
    a_lower = name_a.strip().lower()
    b_lower = name_b.strip().lower()

    # Check direct: a is canonical, b is abbreviation
    if a_lower in KNOWN_ALIASES and b_lower in KNOWN_ALIASES[a_lower]:
        return True
    # Check reverse: b is canonical, a is abbreviation
    if b_lower in KNOWN_ALIASES and a_lower in KNOWN_ALIASES[b_lower]:
        return True

    # Check via reverse lookup
    a_canonical = _REVERSE_ALIASES.get(a_lower)
    b_canonical = _REVERSE_ALIASES.get(b_lower)

    if a_canonical is not None and a_canonical == b_lower:
        return True
    if b_canonical is not None and b_canonical == a_lower:
        return True

    return False


def _is_case_variation(name_a: str, name_b: str) -> bool:
    """Check if two names differ only in case.

    Must not be exactly equal (otherwise it's an exact duplicate, not
    a case variation).
    """
    a_stripped = name_a.strip()
    b_stripped = name_b.strip()
    return a_stripped != b_stripped and a_stripped.lower() == b_stripped.lower()


async def find_aliases(
    engine: MemoryEngine,
    config: OrganizerConfig,
    batch_size: int = 100,
    budget_ms: float = 5000.0,
) -> list[AliasCandidate]:
    """Find alias relationships between entity nodes.

    Checks for:
    1. Known abbreviation patterns
    2. Case-insensitive variations
    3. Semantic similarity via vector search (threshold >= alias_similarity_threshold)

    Only considers ENTITY nodes in active lifecycle states.

    Args:
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with alias_similarity_threshold.
        batch_size: Max entity nodes to scan per call.
        budget_ms: Time budget in milliseconds.

    Returns:
        List of AliasCandidate pairs.
    """
    start = time.monotonic()
    threshold = config.alias_similarity_threshold

    # Fetch active entity nodes
    entities = await engine.query_nodes(
        node_type=NodeType.ENTITY,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=batch_size,
    )

    seen_pairs: set[tuple[str, str]] = set()
    candidates: list[AliasCandidate] = []

    # Phase 1: String-based matching (abbreviation + case variation)
    for i in range(len(entities)):
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            return candidates

        for j in range(i + 1, len(entities)):
            a_id = str(entities[i].id)
            b_id = str(entities[j].id)
            pair_key = (min(a_id, b_id), max(a_id, b_id))

            if pair_key in seen_pairs:
                continue

            name_a = entities[i].content
            name_b = entities[j].content

            if _is_abbreviation_match(name_a, name_b):
                seen_pairs.add(pair_key)
                candidates.append(
                    AliasCandidate(a_id, b_id, "abbreviation", 0.95)
                )
            elif _is_case_variation(name_a, name_b):
                seen_pairs.add(pair_key)
                candidates.append(
                    AliasCandidate(a_id, b_id, "case_variation", 0.90)
                )

    # Phase 2: Semantic similarity via vector search
    for entity in entities:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        entity_id = str(entity.id)

        try:
            results = await engine._vector_index.search(
                entity.content,
                entity.user_id,
                k=10,
            )
        except Exception:
            logger.debug(
                "Vector search failed for entity %s", entity_id, exc_info=True
            )
            continue

        for result in results:
            other_id = result["node_id"]
            score = result["score"]

            if other_id == entity_id:
                continue

            if score < threshold:
                continue

            pair_key = (min(entity_id, other_id), max(entity_id, other_id))
            if pair_key in seen_pairs:
                continue

            # Verify the other node is also an ENTITY
            other_node = await engine.get_node(other_id)
            if other_node is None or other_node.node_type != NodeType.ENTITY:
                continue

            seen_pairs.add(pair_key)
            candidates.append(
                AliasCandidate(entity_id, other_id, "semantic", score)
            )

    return candidates


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------

# Confidence threshold above which aliases are merged (not just linked)
_MERGE_CONFIDENCE_THRESHOLD = 0.90


async def resolve_aliases(
    engine: MemoryEngine,
    aliases: list[AliasCandidate],
) -> int:
    """Resolve alias relationships between entity nodes.

    For high-confidence aliases (>= 0.90): merge the entities (archive
    the shorter/less-evidenced one, transfer edges, create SUPERSEDES).

    For lower-confidence aliases: create RELATES_TO edge with alias
    metadata linking the entities (non-destructive).

    Args:
        engine: The MemoryEngine for storage operations.
        aliases: List of AliasCandidate pairs from find_aliases().

    Returns:
        Count of aliases resolved (merged or linked).
    """
    resolved_count = 0
    merged_ids: set[str] = set()

    for alias in aliases:
        if alias.entity_a_id in merged_ids or alias.entity_b_id in merged_ids:
            continue

        node_a = await engine.get_node(alias.entity_a_id)
        node_b = await engine.get_node(alias.entity_b_id)

        if node_a is None or node_b is None:
            continue

        if node_a.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue
        if node_b.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue

        try:
            if alias.confidence >= _MERGE_CONFIDENCE_THRESHOLD:
                # High confidence: merge entities
                canonical, duplicate = _pick_canonical_entity(node_a, node_b)
                canonical_id = str(canonical.id)
                duplicate_id = str(duplicate.id)

                # Transfer evidence_refs
                new_refs = list(canonical.evidence_refs)
                for ref in duplicate.evidence_refs:
                    if ref not in new_refs:
                        new_refs.append(ref)
                if len(new_refs) > len(canonical.evidence_refs):
                    await engine._graph_store.update_node(
                        canonical_id, evidence_refs=new_refs
                    )

                # Transfer edges
                from prme.organizer.deduplication import _transfer_edges
                await _transfer_edges(engine, duplicate_id, canonical_id)

                # Create SUPERSEDES edge
                supersedes_edge = MemoryEdge(
                    source_id=UUID(canonical_id),
                    target_id=UUID(duplicate_id),
                    edge_type=EdgeType.SUPERSEDES,
                    user_id=canonical.user_id,
                    confidence=1.0,
                    metadata={
                        "reason": "alias_resolution",
                        "alias_type": alias.alias_type,
                        "confidence": alias.confidence,
                    },
                )
                await engine._graph_store.create_edge(supersedes_edge)

                # Supersede the duplicate
                await engine.supersede(duplicate_id, canonical_id)

                merged_ids.add(duplicate_id)
                resolved_count += 1

                logger.info(
                    "Merged alias: %s -> %s (type=%s, conf=%.4f)",
                    duplicate_id,
                    canonical_id,
                    alias.alias_type,
                    alias.confidence,
                )
            else:
                # Lower confidence: create RELATES_TO link
                link_edge = MemoryEdge(
                    source_id=UUID(alias.entity_a_id),
                    target_id=UUID(alias.entity_b_id),
                    edge_type=EdgeType.RELATES_TO,
                    user_id=node_a.user_id,
                    confidence=alias.confidence,
                    metadata={
                        "relation": "alias",
                        "alias_type": alias.alias_type,
                    },
                )
                await engine._graph_store.create_edge(link_edge)
                resolved_count += 1

                logger.info(
                    "Linked alias: %s <-> %s (type=%s, conf=%.4f)",
                    alias.entity_a_id,
                    alias.entity_b_id,
                    alias.alias_type,
                    alias.confidence,
                )

        except Exception:
            logger.warning(
                "Failed to resolve alias pair (%s, %s)",
                alias.entity_a_id,
                alias.entity_b_id,
                exc_info=True,
            )

    return resolved_count


def _pick_canonical_entity(
    node_a: MemoryNode,
    node_b: MemoryNode,
) -> tuple[MemoryNode, MemoryNode]:
    """Choose canonical entity: prefer longer name, then higher confidence.

    For aliases, the longer/more descriptive name is typically preferred
    as canonical ("JavaScript" over "JS").

    Returns:
        (canonical, duplicate) tuple.
    """
    # Prefer longer content (more descriptive name)
    if len(node_a.content.strip()) > len(node_b.content.strip()):
        return (node_a, node_b)
    if len(node_b.content.strip()) > len(node_a.content.strip()):
        return (node_b, node_a)

    # Same length: prefer higher confidence
    if node_a.confidence_base > node_b.confidence_base:
        return (node_a, node_b)
    if node_b.confidence_base > node_a.confidence_base:
        return (node_b, node_a)

    # Prefer more evidence
    if len(node_a.evidence_refs) > len(node_b.evidence_refs):
        return (node_a, node_b)
    if len(node_b.evidence_refs) > len(node_a.evidence_refs):
        return (node_b, node_a)

    # Older node wins
    if node_a.created_at <= node_b.created_at:
        return (node_a, node_b)
    return (node_b, node_a)
