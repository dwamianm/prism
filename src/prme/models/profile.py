"""Immutable inputs to the entity-profile publication boundary."""

import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypedDict

from prme.models.derivation import PreparedEmbedding, canonical_hash, node_checksum
from prme.models.nodes import MemoryNode
from prme.types import EpistemicType, LifecycleState, NodeType, Scope, SourceType


class ProfileJobStatus(TypedDict):
    """Owned prepared-profile job; plan_id is the profile's fixed node UUID."""

    plan_id: str
    scope: str
    status: Literal["pending", "complete", "abandoned"]
    attempts: int
    last_error: str | None


class ProfileProcessingResult(TypedDict):
    processed: int
    failed: int
    pending: int
    errors: dict[str, str]


def profile_request_hash(
    node: MemoryNode, sources, previous, generation: int, embedding_identity
) -> str:
    """Compare prepared requests without allocating fresh identities on retry."""
    values = node.model_dump(mode="json")
    for field in ("id", "created_at", "updated_at", "valid_from", "last_reinforced_at"):
        values.pop(field, None)
    return canonical_hash(
        {
            "node": values,
            "sources": [node_checksum(n) for n in sources],
            "previous": [node_checksum(n) for n in previous],
            "generation": generation,
            "embedding_identity": list(embedding_identity),
        }
    )


class StaleProfileError(ValueError):
    """Profile sources or its publication generation changed; rebuild explicitly."""


def profile_key(user_id: str, scope: Scope, name: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL, json.dumps(["prme-profile-v1", user_id, scope.value, name])
        )
    )


class ProfilePublication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    node: MemoryNode
    sources: tuple[MemoryNode, ...]
    previous: tuple[MemoryNode, ...]
    embedding: PreparedEmbedding
    generation: int = Field(ge=0)

    @property
    def entity_name(self) -> str:
        assert self.node.metadata is not None
        return str(self.node.metadata["entity_name"])

    @property
    def key(self) -> str:
        return profile_key(self.node.user_id, self.node.scope, self.entity_name)

    @property
    def operation_id(self) -> str:
        return str(uuid5(self.node.id, "prme:profile-published:v1"))

    @property
    def prepared_operation_id(self) -> str:
        return str(uuid5(self.node.id, "prme:profile-prepared:v1"))

    @property
    def request_hash(self) -> str:
        return profile_request_hash(
            self.node,
            self.sources,
            self.previous,
            self.generation,
            (self.embedding.model, self.embedding.version, self.embedding.dimension),
        )

    @property
    def checksum(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def validate_publication(self):
        node = self.node
        meta = node.metadata or {}
        active = (LifecycleState.TENTATIVE, LifecycleState.STABLE)
        if (
            node.node_type != NodeType.SUMMARY
            or meta.get("entity_profile") is not True
            or not isinstance(meta.get("entity_name"), str)
            or not meta["entity_name"].strip()
            or node.lifecycle_state not in active
            or node.epistemic_type != EpistemicType.INFERRED
            or node.source_type != SourceType.SYSTEM_INFERRED
        ):
            raise ValueError("Publication requires an active inferred entity profile")
        dependencies = self.sources + self.previous
        if not self.sources or len({n.id for n in dependencies}) != len(dependencies):
            raise ValueError(
                "Publication requires distinct source and previous-profile identities"
            )
        if node.id in {n.id for n in dependencies}:
            raise ValueError("Publication requires a new profile identity")
        for dep in dependencies:
            if (
                dep.user_id != node.user_id
                or dep.scope != node.scope
                or dep.lifecycle_state not in active
            ):
                raise ValueError(
                    "Publication dependencies must be active in the same owner and scope"
                )
        if any((n.metadata or {}).get("entity_profile") for n in self.sources):
            raise ValueError("Generated profiles cannot be profile sources")
        for old in self.previous:
            old_meta = old.metadata or {}
            if (
                old.node_type != NodeType.SUMMARY
                or old_meta.get("entity_profile") is not True
                or old_meta.get("entity_name") != meta["entity_name"]
            ):
                raise ValueError("Only prior profiles of this entity can be retired")
        if meta.get("source_node_ids") != [str(n.id) for n in self.sources]:
            raise ValueError("Profile source metadata must match its dependencies")
        if set(node.evidence_refs) != {
            ref for n in self.sources for ref in n.evidence_refs
        }:
            raise ValueError("Profile evidence must match its sources")
        if node.confidence > min(n.confidence for n in self.sources):
            raise ValueError("Profile confidence cannot exceed its sources")
        if (self.embedding.node_id, self.embedding.content) != (node.id, node.content):
            raise ValueError("Prepared embedding must describe this exact profile")
        return self
