"""Supersedence and contradiction detection at ingestion time.

Detects contradicting assertions by matching subject + predicate and finding
differing objects. Branches on temporal_intent:
- "update" or None (default): creates SUPERSEDES edges (original behavior)
- "assertion": creates CONTRADICTS edges (true contradiction, both preserved)

Per user decision: "Detect supersedence at ingestion: when new facts
contradict existing ones, create supersedence chains immediately."

Example: "Sarah left Google" should immediately flag existing
"Sarah works_at Google" facts and supersede them (temporal_intent="update").
But "Sarah works at Google" vs "Sarah works at Meta" with temporal_intent=
"assertion" creates a CONTRADICTS edge preserving both claims.
"""

from __future__ import annotations

import structlog

from prme.ingestion.graph_writer import GraphWriter
from prme.models.nodes import MemoryNode
from prme.storage.graph_store import GraphStore
from prme.types import LifecycleState

logger = structlog.get_logger(__name__)

# Small set of known predicate equivalence classes.
# Per research: "Start with exact predicate match. If hit rate is too low,
# add a small set of predicate equivalence classes."
PREDICATE_EQUIVALENCES: dict[str, list[str]] = {
    "works_at": ["employed_by", "employed_at", "joined"],
    "lives_in": ["resides_in", "located_in"],
    "role": ["position", "title"],
}

# Build reverse lookup: predicate -> canonical form
_PREDICATE_TO_CANONICAL: dict[str, str] = {}
for canonical, equivalents in PREDICATE_EQUIVALENCES.items():
    _PREDICATE_TO_CANONICAL[canonical.lower()] = canonical.lower()
    for equiv in equivalents:
        _PREDICATE_TO_CANONICAL[equiv.lower()] = canonical.lower()


def _predicates_match(pred_a: str, pred_b: str) -> bool:
    """Check if two predicates match (exact or equivalence class).

    Two predicates match if they are equal (case-insensitive) or if
    they belong to the same equivalence class.

    Args:
        pred_a: First predicate.
        pred_b: Second predicate.

    Returns:
        True if predicates match, False otherwise.
    """
    a_lower = pred_a.strip().lower()
    b_lower = pred_b.strip().lower()

    # Exact match
    if a_lower == b_lower:
        return True

    # Equivalence class match
    canonical_a = _PREDICATE_TO_CANONICAL.get(a_lower)
    canonical_b = _PREDICATE_TO_CANONICAL.get(b_lower)

    if canonical_a is not None and canonical_b is not None:
        return canonical_a == canonical_b

    return False


class SupersedenceDetector:
    """Contradiction and supersedence detection at ingestion time.

    Identifies contradicting assertions by matching subject entity +
    predicate and finding differing object values. Branches on
    temporal_intent:

    - ``"update"`` or ``None`` (default): existing supersedence behavior.
      Old node is SUPERSEDED by the new one via GraphWriter.supersede().
    - ``"assertion"``: true contradiction. Both nodes are transitioned to
      CONTESTED via GraphWriter.contradict(), preserving both claims.

    Applies to all node types with predicate metadata (FACT, DECISION,
    PREFERENCE), not just FACT nodes.
    """

    def __init__(self, graph_store: GraphStore, graph_writer: GraphWriter) -> None:
        self._graph_store = graph_store
        self._graph_writer = graph_writer

    async def detect_and_supersede(
        self,
        new_fact_node_id: str,
        subject_entity_id: str,
        predicate: str,
        object_value: str,
        user_id: str,
        *,
        evidence_event_id: str | None = None,
        temporal_intent: str | None = None,
    ) -> list[str]:
        """Detect contradictions and create supersedence or contradiction chains.

        Examines all active nodes connected to the subject entity that have
        predicate metadata. If a node has a matching predicate but a different
        object value, it is a contradiction. The resolution depends on
        temporal_intent:

        - ``"update"`` or ``None`` (default): supersedence. The old node is
          transitioned to SUPERSEDED. This preserves backward compatibility
          for extraction results that lack temporal_intent.
        - ``"assertion"``: true contradiction. Both nodes are transitioned to
          CONTESTED via a CONTRADICTS edge. Neither is removed.

        Args:
            new_fact_node_id: ID of the new fact node.
            subject_entity_id: ID of the subject entity node.
            predicate: The predicate of the new fact (e.g., "works_at").
            object_value: The object value of the new fact (e.g., "Meta").
            user_id: Owner user ID for scoping.
            evidence_event_id: Optional event ID providing evidence.
            temporal_intent: Intent classification from extraction.
                "update" or None -> supersedence (default).
                "assertion" -> contradiction (preserve both).

        Returns:
            List of superseded or contradicted node IDs.
        """
        superseded: list[str] = []

        # Get all edges from subject entity
        edges = await self._graph_store.get_edges(source_id=subject_entity_id)

        for edge in edges:
            target_id = str(edge.target_id)

            # Skip the new fact itself
            if target_id == new_fact_node_id:
                continue

            # Get the target node
            target_node = await self._graph_store.get_node(target_id)
            if target_node is None:
                continue

            # Only check nodes that are active (tentative or stable)
            if target_node.lifecycle_state not in (
                LifecycleState.TENTATIVE,
                LifecycleState.STABLE,
            ):
                continue

            # Skip nodes without predicate metadata (non-fact-like nodes).
            # This naturally handles all node types -- only those with
            # predicate metadata are checked (FACT, DECISION, PREFERENCE
            # all use the subject-predicate-object triple structure).
            existing_metadata = target_node.metadata or {}
            existing_predicate = existing_metadata.get("predicate")
            existing_object = existing_metadata.get("object")

            if existing_predicate is None:
                continue

            logger.debug(
                "Checking predicate match",
                new_predicate=predicate,
                existing_predicate=existing_predicate,
                existing_fact_id=target_id,
            )

            # Check for contradiction: predicate matches but object differs
            if _predicates_match(predicate, existing_predicate):
                if existing_object != object_value:
                    if temporal_intent == "assertion":
                        # True contradiction: preserve both objects
                        logger.info(
                            "Contradiction detected: conflicting assertions",
                            old_fact_id=target_id,
                            new_fact_id=new_fact_node_id,
                            predicate=predicate,
                            old_object=existing_object,
                            new_object=object_value,
                            temporal_intent=temporal_intent,
                        )
                        await self._graph_writer.contradict(
                            node_a_id=target_id,
                            node_b_id=new_fact_node_id,
                            evidence_id=evidence_event_id,
                        )
                        superseded.append(target_id)
                    else:
                        # Default: supersedence (temporal_intent="update" or None)
                        logger.info(
                            "Supersedence detected: contradicting facts",
                            old_fact_id=target_id,
                            new_fact_id=new_fact_node_id,
                            predicate=predicate,
                            old_object=existing_object,
                            new_object=object_value,
                            subject_entity_id=subject_entity_id,
                        )
                        await self._graph_writer.supersede(
                            old_node_id=target_id,
                            new_node_id=new_fact_node_id,
                            evidence_id=evidence_event_id,
                        )
                        superseded.append(target_id)

        return superseded
