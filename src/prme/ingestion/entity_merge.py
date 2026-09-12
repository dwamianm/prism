"""Conservative entity merge at ingestion time.

EntityMerger deduplicates entity nodes by matching on both name AND
entity_type (case-insensitive name, exact type). Per user decision: "Entity merging at
ingestion should be conservative -- better to create a duplicate than
incorrectly merge two different entities."

Named entities match name.strip().lower(), exact entity_type, owner and scope.
Unresolved personal references additionally require the same source event.
This prevents merging "Jordan" (person) with "Jordan" (country).
"""

from __future__ import annotations

from uuid import UUID

import structlog

from prme.ingestion.graph_writer import GraphWriter
from prme.models.nodes import MemoryNode
from prme.models.entity_identity import unresolved_personal_reference
from prme.storage.graph_store import GraphStore
from prme.types import LifecycleState, NodeType, Scope

logger = structlog.get_logger(__name__)


class EntityMerger:
    """Best-effort entity deduplication at ingestion time.

    Queries existing ENTITY nodes in the graph store and returns an
    existing node ID when an exact name+type match is found. Creates
    a new node otherwise.
    """

    def __init__(self, graph_store: GraphStore, graph_writer: GraphWriter) -> None:
        self._graph_store = graph_store
        self._graph_writer = graph_writer

    async def find_or_create_entity(
        self,
        name: str,
        entity_type: str,
        user_id: str,
        *,
        description: str | None = None,
        session_id: str | None = None,
        evidence_event_id: str | None = None,
        scope: Scope = Scope.PERSONAL,
    ) -> tuple[str, bool]:
        """Find an existing entity or create a new one.

        Conservative match: both name (case-insensitive, stripped) and
        entity_type must match exactly. User isolation is enforced --
        entities from different users are never merged.

        Args:
            name: Entity name (e.g., "Sarah", "Google").
            entity_type: Entity type (e.g., "person", "organization").
            user_id: Owner user ID for scoping.
            description: Optional description for new entities.
            session_id: Optional session ID for new entities.
            evidence_event_id: Optional event ID providing evidence.

        Returns:
            Tuple of (node_id, is_new). is_new is True if a new node
            was created, False if an existing match was reused.
        """
        # A ranked/limited query can miss older entities and fragment their
        # fact history. Walk scoped, stable-ID pages until an exact match or
        # exhaustion; keep Python normalization identical on both backends.
        normalized_name = name.strip().lower()
        unresolved = unresolved_personal_reference(name, entity_type)
        after_id: str | None = None
        while True:
            existing_nodes = await self._graph_store.scan_nodes(
                node_type=NodeType.ENTITY, user_id=user_id, scope=scope,
                after_id=after_id, limit=256,
            )
            for node in existing_nodes:
                node_name = node.content.strip().lower()
                node_entity_type = (node.metadata or {}).get("entity_type")
                if node_name == normalized_name and node_entity_type == entity_type:
                    if unresolved and (evidence_event_id is None
                            or node.metadata.get("identity_status") != "unresolved_reference"
                            or node.metadata.get("reference_event_id") != evidence_event_id
                            or node.evidence_refs != [UUID(evidence_event_id)]):
                        # Literal "I" in two messages can refer to two quoted
                        # speakers. An owner/scope is not a resolved referent.
                        continue
                    logger.debug(
                        "Reusing existing entity",
                        entity_id=str(node.id),
                        name=name,
                        entity_type=entity_type,
                        user_id=user_id,
                    )
                    return (str(node.id), False)
            if len(existing_nodes) < 256:
                break
            after_id = str(existing_nodes[-1].id)

        # No match found -- create new entity
        evidence_refs: list[UUID] = []
        if evidence_event_id is not None:
            evidence_refs = [UUID(evidence_event_id)]

        metadata: dict = {"entity_type": entity_type}
        if unresolved:
            metadata.update(identity_status="unresolved_reference", reference_event_id=evidence_event_id)
        if description is not None:
            metadata["description"] = description

        from prme.types import DecayProfile

        node = MemoryNode(
            node_type=NodeType.ENTITY,
            content=name,
            user_id=user_id,
            session_id=session_id,
            scope=scope,
            lifecycle_state=LifecycleState.TENTATIVE,
            confidence=0.5,
            confidence_base=0.5,
            decay_profile=DecayProfile.SLOW,
            metadata=metadata,
            evidence_refs=evidence_refs,
        )

        node_id = await self._graph_writer.create_node(node)

        logger.info(
            "Created new entity",
            entity_id=node_id,
            name=name,
            entity_type=entity_type,
            user_id=user_id,
        )

        return (node_id, True)
