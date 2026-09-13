"""Immutable inputs for crash-safe extractive consolidation publication."""

from __future__ import annotations

import json
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prme.models.derivation import PreparedEmbedding, canonical_hash, node_checksum
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import (
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
)


class StaleConsolidationError(ValueError):
    """The cluster sources or publication generation changed."""


def consolidation_key(user_id: str, scope: Scope, source_ids) -> str:
    """Return the stable lineage for an unchanged set of source identities."""
    identities = sorted(str(UUID(str(value))) for value in source_ids)
    if not user_id or not identities:
        raise ValueError("A consolidation key requires an owner and sources")
    return str(
        uuid5(
            NAMESPACE_URL,
            json.dumps(
                ["prme-extractive-consolidation-v2", user_id, scope.value, identities],
                separators=(",", ":"),
            ),
        )
    )


def consolidation_request_hash(
    sources: tuple[MemoryNode, ...] | list[MemoryNode],
    *,
    content: str,
    confidence: float,
    salience: float,
    selected_ids,
    embedding_identity,
) -> str:
    """Fingerprint all source-dependent inputs without fresh object identities."""
    ordered = sorted(sources, key=lambda node: str(node.id))
    if not ordered:
        raise ValueError("A consolidation request requires sources")
    return canonical_hash(
        {
            "policy": "source_labelled_extractive_v2",
            "owner": ordered[0].user_id,
            "scope": ordered[0].scope.value,
            "sources": [node_checksum(node) for node in ordered],
            "selected_ids": [str(UUID(str(value))) for value in selected_ids],
            "content": content,
            "confidence": confidence,
            "salience": salience,
            "embedding_identity": list(embedding_identity),
        }
    )


class ConsolidationPublication(BaseModel):
    """Complete prepared state for one atomic consolidation replacement."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    node: MemoryNode
    sources: tuple[MemoryNode, ...]
    previous: tuple[MemoryNode, ...] = ()
    edges: tuple[MemoryEdge, ...]
    embedding: PreparedEmbedding
    generation: int = Field(ge=0)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def key(self) -> str:
        return consolidation_key(
            self.node.user_id, self.node.scope, (node.id for node in self.sources)
        )

    @property
    def prepared_operation_id(self) -> str:
        return str(uuid5(self.node.id, "prme:consolidation-prepared:v1"))

    @property
    def operation_id(self) -> str:
        return str(uuid5(self.node.id, "prme:consolidation-published:v1"))

    @property
    def checksum(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def validate_publication(self):
        node = self.node
        meta = node.metadata or {}
        active = (LifecycleState.TENTATIVE, LifecycleState.STABLE)
        dependencies = self.sources + self.previous
        if (
            node.node_type != NodeType.SUMMARY
            or node.lifecycle_state not in active
            or node.epistemic_type != EpistemicType.INFERRED
            or node.source_type != SourceType.SYSTEM_INFERRED
            or meta.get("consolidation_summary") is not True
            or meta.get("consolidation_key") != self.key
            or meta.get("consolidation_request_hash") != self.request_hash
        ):
            raise ValueError("Publication requires an active inferred consolidation summary")
        if not self.sources or len({item.id for item in dependencies}) != len(dependencies):
            raise ValueError("Consolidation dependencies must have distinct identities")
        if node.id in {item.id for item in dependencies}:
            raise ValueError("Consolidation must allocate a new summary identity")
        for source in self.sources:
            if (
                source.user_id != node.user_id
                or source.scope != node.scope
                or source.lifecycle_state not in active
                or source.node_type not in (NodeType.FACT, NodeType.EVENT, NodeType.NOTE)
            ):
                raise ValueError("Consolidation sources must be active episodic memories in one namespace")
        for old in self.previous:
            old_meta = old.metadata or {}
            if (
                old.user_id != node.user_id
                or old.scope != node.scope
                or old.lifecycle_state not in active
                or old.node_type != NodeType.SUMMARY
                or old_meta.get("consolidation_summary") is not True
                or old_meta.get("consolidation_key") != self.key
            ):
                raise ValueError("Only active summaries from this consolidation lineage can be replaced")

        source_ids = [str(item.id) for item in sorted(self.sources, key=lambda item: str(item.id))]
        selected_ids = meta.get("selected_source_node_ids")
        if meta.get("source_node_ids") != source_ids or not isinstance(selected_ids, list):
            raise ValueError("Consolidation source metadata must match its dependencies")
        selected = {str(UUID(str(value))) for value in selected_ids}
        if not selected or not selected <= set(source_ids) or len(selected) != len(selected_ids):
            raise ValueError("Consolidation selected sources must be a distinct source subset")
        expected_evidence = {
            ref
            for source in self.sources
            if str(source.id) in selected
            for ref in (source.id, *source.evidence_refs)
        }
        if set(node.evidence_refs) != expected_evidence:
            raise ValueError("Consolidation evidence must match its selected sources")
        expected_targets = {UUID(value) for value in selected}
        if (
            len(self.edges) != len(expected_targets)
            or {edge.target_id for edge in self.edges} != expected_targets
            or any(
                edge.source_id != node.id
                or edge.edge_type != EdgeType.DERIVED_FROM
                or edge.user_id != node.user_id
                for edge in self.edges
            )
        ):
            raise ValueError("Consolidation edges must cover exactly the selected sources")
        if (self.embedding.node_id, self.embedding.content) != (node.id, node.content):
            raise ValueError("Prepared embedding must describe the exact summary")
        expected_hash = consolidation_request_hash(
            self.sources,
            content=node.content,
            confidence=node.confidence,
            salience=node.salience,
            selected_ids=selected_ids,
            embedding_identity=(
                self.embedding.model,
                self.embedding.version,
                self.embedding.dimension,
            ),
        )
        if expected_hash != self.request_hash:
            raise ValueError("Consolidation request hash does not match its prepared inputs")
        if node.id != uuid5(UUID(self.key), f"{self.request_hash}:{self.generation}"):
            raise ValueError("Consolidation identity must be deterministic for its request")
        return self
