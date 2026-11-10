"""Alias resolution logic for the organizer (issue #11).

Detects entity aliases (abbreviations, case variations, known synonyms)
and either merges compatible known name variants or links them with RELATES_TO
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

from prme.privacy.journals import compatible_admitted_pair
from prme.organizer.merge_policy import alias_pair_allowed
from prme.types import LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.config import OrganizerConfig
    from prme.models.nodes import MemoryNode
    from prme.storage.engine import MemoryEngine

from prme.privacy.diagnostics import ScopedLogger

logger = logging.getLogger(__name__)
logger = ScopedLogger(logger)


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


def _alias_name_forms(name: str) -> set[str]:
    """The names the string alias rules pair with *name*.

    Used by the incremental scan to ask the store for the unchanged twin of a
    changed entity by name: the name itself, its canonical form when it is an
    abbreviation, and the canonical form's known abbreviations.
    """
    key = name.strip().lower()
    canonical = _REVERSE_ALIASES.get(key, key)
    forms = {key, canonical}
    forms.update(KNOWN_ALIASES.get(canonical, ()))
    forms.update(KNOWN_ALIASES.get(key, ()))
    return forms


def _string_alias_candidate(
    node_a: MemoryNode, node_b: MemoryNode,
) -> AliasCandidate | None:
    """The string alias candidate for one pair, or None when names differ."""
    if _is_abbreviation_match(node_a.content, node_b.content):
        return AliasCandidate(str(node_a.id), str(node_b.id), "abbreviation", 0.95)
    if _is_case_variation(node_a.content, node_b.content):
        return AliasCandidate(str(node_a.id), str(node_b.id), "case_variation", 0.90)
    return None


async def _semantic_alias_candidates(
    engine: MemoryEngine,
    index,
    entity: MemoryNode,
    threshold: float,
    seen_pairs: set[tuple[str, str]],
) -> list[AliasCandidate]:
    """Semantic alias candidates for one entity, searched in the vector index.

    The durable twin is rechecked because an index may lag graph updates or be
    supplied by a custom backend. A search or read error is left to the caller
    so the full-scope run can skip the entity and the incremental run can stop
    its resumable prefix instead of marking the entity examined.
    """
    entity_id = str(entity.id)
    results = await index.search(
        entity.content,
        entity.user_id,
        k=10,
        scope=[entity.scope.value],
    )

    candidates: list[AliasCandidate] = []
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

        other_node = await engine.get_node(other_id, user_id=entity.user_id)
        if other_node is None or not compatible_admitted_pair(alias_pair_allowed, entity, other_node):
            continue

        seen_pairs.add(pair_key)
        candidates.append(AliasCandidate(entity_id, other_id, "semantic", score))
    return candidates


async def find_aliases(
    engine: MemoryEngine,
    config: OrganizerConfig,
    batch_size: int = 100,
    budget_ms: float = 5000.0,
    user_id: str | None = None,
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
        user_id: When given, only this user's entities are scanned.

    Returns:
        List of AliasCandidate pairs, never spanning two users.
    """
    from prme.privacy.journals import generation_search
    index = await generation_search(engine, user_id=user_id)
    start = time.monotonic()
    threshold = config.alias_similarity_threshold

    # Fetch active entity nodes
    entities = await engine.query_nodes(
        user_id=user_id,
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
            # Names alone do not authorize combining owners or scopes.
            if not compatible_admitted_pair(alias_pair_allowed, entities[i], entities[j]):
                continue

            a_id = str(entities[i].id)
            b_id = str(entities[j].id)
            pair_key = (min(a_id, b_id), max(a_id, b_id))

            if pair_key in seen_pairs:
                continue

            candidate = _string_alias_candidate(entities[i], entities[j])
            if candidate is None:
                continue
            seen_pairs.add(pair_key)
            candidates.append(candidate)

    # Phase 2: Semantic similarity via vector search
    for entity in entities:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        try:
            candidates.extend(
                await _semantic_alias_candidates(
                    engine, index, entity, threshold, seen_pairs,
                )
            )
        except Exception:
            logger.debug(
                "Vector search failed for entity %s", str(entity.id), exc_info=True
            )
            continue

    return candidates


# Names one incremental lookup may return for a changed entity. The full scan
# also bounds its work, so this is a page bound, not a completeness claim.
_ALIAS_NAME_LOOKUP_LIMIT = 500


async def find_aliases_in_page(
    engine: MemoryEngine,
    config: OrganizerConfig,
    entities: list[MemoryNode],
    *,
    index=None,
    budget_ms: float = 5000.0,
    user_id: str | None = None,
    seen_pairs: set[tuple[str, str]] | None = None,
) -> tuple[list[AliasCandidate], int, bool]:
    """Find aliases among one page of changed entities and their twins.

    Incremental counterpart of :func:`find_aliases` (dwamianm/prism#523). The
    caller supplies the ENTITY nodes created or changed since the last run.
    Each is looked up by its equivalent names, so an older name twin that is
    not on the page is still found, and searched in the vector index for
    semantic aliases.

    Returns the candidates, how many leading entities were fully examined
    (which is how far the caller may advance its keyset cursor), and whether a
    lookup or neighbor search failed. A failure stops the resumable prefix
    instead of counting the entity as examined, so the caller reports an error
    rather than a budget stop.
    """
    from prme.privacy.journals import generation_search

    if index is None:
        index = await generation_search(engine, user_id=user_id)
    if seen_pairs is None:
        seen_pairs = set()
    return await _find_aliases_in_page(
        engine,
        config,
        entities,
        index=index,
        budget_ms=budget_ms,
        seen_pairs=seen_pairs,
    )


async def _find_aliases_in_page(
    engine: MemoryEngine,
    config: OrganizerConfig,
    entities: list[MemoryNode],
    *,
    index,
    budget_ms: float,
    seen_pairs: set[tuple[str, str]],
) -> tuple[list[AliasCandidate], int, bool]:
    start = time.monotonic()
    threshold = config.alias_similarity_threshold
    candidates: list[AliasCandidate] = []

    # Phase 1: each changed entity asks the store for entities that share an
    # alias-equivalent name. The unchanged twin is found by name, not by
    # position in the page, so a new string alias pair is not missed just
    # because the twin is on another page. The semantic phase keeps the
    # index's top-k bound.
    name_checked = 0
    for entity in entities:
        # Make progress even when the caller's remaining budget is smaller
        # than one entity's work, so a stop leaves a resumable cursor instead
        # of repeating the same page forever.
        if name_checked > 0 and (time.monotonic() - start) * 1000.0 >= budget_ms:
            break

        entity_id = str(entity.id)
        try:
            partners = await engine.query_nodes(
                user_id=entity.user_id,
                scope=entity.scope,
                node_type=NodeType.ENTITY,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
                content_equals_any=sorted(_alias_name_forms(entity.content)),
                limit=_ALIAS_NAME_LOOKUP_LIMIT,
            )
        except Exception:
            logger.debug(
                "Name lookup failed for entity %s", entity_id, exc_info=True,
            )
            return candidates, 0, True

        for other in partners:
            other_id = str(other.id)
            if other_id == entity_id:
                continue
            # Names alone do not authorize combining owners or scopes.
            if not compatible_admitted_pair(alias_pair_allowed, entity, other):
                continue

            pair_key = (min(entity_id, other_id), max(entity_id, other_id))
            if pair_key in seen_pairs:
                continue

            candidate = _string_alias_candidate(entity, other)
            if candidate is None:
                continue
            seen_pairs.add(pair_key)
            candidates.append(candidate)
        name_checked += 1

    # Phase 2: semantic neighbors for the entities whose name lookup finished.
    completed = 0
    for entity in entities[:name_checked]:
        if completed > 0 and (time.monotonic() - start) * 1000.0 >= budget_ms:
            break
        try:
            candidates.extend(
                await _semantic_alias_candidates(
                    engine, index, entity, threshold, seen_pairs,
                )
            )
        except Exception:
            logger.debug(
                "Vector search failed for entity %s", str(entity.id), exc_info=True,
            )
            return candidates, completed, True
        completed += 1

    return candidates, completed, False


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------

# Confidence threshold above which aliases are merged (not just linked)
_MERGE_CONFIDENCE_THRESHOLD = 0.90


async def resolve_aliases(
    engine: MemoryEngine,
    aliases: list[AliasCandidate],
    *,
    user_id: str | None = None,
    failure_count: list[int] | None = None,
) -> int:
    """Resolve alias relationships between entity nodes.

    For compatible, known name variants with confidence >= 0.90: merge
    the entities (archive the shorter/less-evidenced one and transfer edges).

    Other compatible candidates, including arbitrarily high semantic scores,
    create RELATES_TO links marked identity_verified=False. Similarity does
    not establish identity. Incompatible provenance/type pairs are retained.

    Args:
        engine: The MemoryEngine for storage operations.
        aliases: List of AliasCandidate pairs from find_aliases().
        user_id: When given, only this user's entities may be read or changed.
        failure_count: When given, receives the number of pairs whose
            resolution raised. The incremental caller uses it to keep the page
            resumable instead of advancing past a pair that was not retried.

    Returns:
        Count of aliases resolved (merged or linked).
    """
    resolved_count = 0
    merged_ids: set[str] = set()

    for alias in aliases:
        if alias.entity_a_id in merged_ids or alias.entity_b_id in merged_ids:
            continue

        node_a = await engine.get_node(alias.entity_a_id, **({"user_id": user_id} if user_id else {}))
        node_b = await engine.get_node(alias.entity_b_id, **({"user_id": user_id} if user_id else {}))

        if node_a is None or node_b is None:
            continue

        # Revalidate before merging evidence/edges or creating an alias link.
        if (node_a.user_id, node_a.scope) != (node_b.user_id, node_b.scope):
            logger.warning(
                "Refusing to resolve cross-namespace alias pair (%s, %s)",
                alias.entity_a_id,
                alias.entity_b_id,
            )
            continue

        if not compatible_admitted_pair(alias_pair_allowed, node_a, node_b):
            logger.debug("Retaining incompatible alias candidates (%s, %s)", alias.entity_a_id, alias.entity_b_id)
            continue

        if node_a.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue
        if node_b.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue

        try:
            name_match = (_is_abbreviation_match(node_a.content, node_b.content)
                          or _is_case_variation(node_a.content, node_b.content)
                          or node_a.content.strip().casefold() == node_b.content.strip().casefold())
            if alias.confidence >= _MERGE_CONFIDENCE_THRESHOLD and name_match:
                result = await engine._graph_store.merge_nodes(
                    alias.entity_a_id, alias.entity_b_id, user_id=node_a.user_id,
                    kind="alias", score=alias.confidence,
                )
                if result is None or not result.applied:
                    continue
                canonical_id, duplicate_id = result.canonical_id, result.retired_id
                await engine._evict_from_indexes(duplicate_id)

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
                result = await engine._graph_store.propose_alias(
                    alias.entity_a_id,
                    alias.entity_b_id,
                    user_id=node_a.user_id,
                    alias_type=alias.alias_type,
                    score=alias.confidence,
                )
                if result is None or not result.applied:
                    continue
                resolved_count += 1

                logger.info(
                    "Linked alias: %s <-> %s (type=%s, conf=%.4f)",
                    alias.entity_a_id,
                    alias.entity_b_id,
                    alias.alias_type,
                    alias.confidence,
                )

        except Exception:
            if failure_count is not None:
                failure_count[0] += 1
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
