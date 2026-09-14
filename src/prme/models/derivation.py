"""Complete immutable inputs for one graph derivation commit (RFC-0016).

These internal records are a commit protocol, not an assertion that inference
is correct. Mutable nested node models must be snapshotted before storage I/O.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, LifecycleState, Scope


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()


def derivation_operation_id(event_id: UUID | str, *, committed: bool = False, revision: int = 1) -> str:
    if revision < 1:
        raise ValueError("Derivation revision must be positive")
    kind = "committed" if committed else "prepared"
    suffix = f":revision:{revision}" if revision > 1 and not committed else ""
    return str(uuid5(UUID(str(event_id)), f"prme:{kind}-derivation:v1{suffix}"))


def node_checksum(node: MemoryNode) -> str:
    """Fingerprint semantic node values independently of database timezone."""
    values = node.model_dump(mode="json")
    for name, value in node.__dict__.items():
        if isinstance(value, datetime) and value.utcoffset() is not None:
            values[name] = value.astimezone(timezone.utc).isoformat()
    return canonical_hash(values)


class StaleDerivationPlanError(ValueError):
    """A referenced memory changed; recovery needs a new immutable plan."""


class PreparedEmbedding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    node_id: UUID
    content: str
    model: str = Field(min_length=1)
    version: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    values: tuple[float, ...]

    @model_validator(mode="after")
    def valid_dimension(self):
        if len(self.values) != self.dimension:
            raise ValueError("Prepared embedding dimension does not match its values")
        if not any(self.values) or any(abs(value) > 3.4028234663852886e38 for value in self.values):
            raise ValueError("Prepared cosine embeddings must be nonzero finite float32 vectors")
        if not any(abs(value) > 2 ** -150 for value in self.values):
            raise ValueError("Prepared embedding underflows to a zero float32 vector")
        return self


class PreparedLexicalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: UUID
    content: str


class DerivationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    plan_id: UUID
    user_id: str
    generation: int | None = Field(default=None, ge=1)
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_ids: tuple[UUID, ...]
    edge_ids: tuple[UUID, ...]
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DerivationPlan(BaseModel):
    """Saved identities, values, dependencies and index inputs for one source.

    ``references`` contains exact snapshots of existing nodes the planner read.
    Commit must reject changed dependencies. ``replacements`` are complete,
    fixed-ID SUPERSEDES edges, applied in order as part of the same transaction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    revision: int = Field(default=1, ge=1)
    materialization_policy: Literal[
        "source_passage_v1",
        "typed_references_v2",
        "relationship_claims_v3",
        "event_local_references_v4",
        "claim_qualifiers_v5",
        "grounded_quantities_v6",
    ] = "relationship_claims_v3"
    id: UUID = Field(default_factory=uuid4)
    event_id: UUID
    user_id: str = Field(min_length=1)
    scope: Scope
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    nodes: tuple[MemoryNode, ...] = ()
    edges: tuple[MemoryEdge, ...] = ()
    references: tuple[MemoryNode, ...] = ()
    replacements: tuple[MemoryEdge, ...] = ()
    embeddings: tuple[PreparedEmbedding, ...] = ()
    lexical_documents: tuple[PreparedLexicalDocument, ...] = ()

    @property
    def checksum(self) -> str:
        values = self.model_dump(mode="json")
        if self.revision == 1:
            values.pop("revision")  # Preserve checksums of existing v1 journals.
        return canonical_hash(values)

    @property
    def prepared_operation_id(self) -> str:
        return derivation_operation_id(self.event_id, revision=self.revision)

    @property
    def receipt_operation_id(self) -> str:
        return derivation_operation_id(self.event_id, committed=True)

    def verify_source(self, user_id: str, scope: str, content_hash: str) -> None:
        if (self.user_id, self.scope.value, self.content_hash) != (user_id, scope, content_hash):
            raise ValueError("Derivation plan does not match its immutable source")

    @model_validator(mode="after")
    def coherent_plan(self):
        new = {node.id: node for node in self.nodes}
        references = {node.id: node for node in self.references}
        if len(new) != len(self.nodes) or len(references) != len(self.references) or new.keys() & references.keys():
            raise ValueError("Derivation node identities must be distinct")
        known = new | references
        for node in known.values():
            if (node.user_id, node.scope) != (self.user_id, self.scope):
                raise ValueError("Derivation nodes must share the source owner and scope")
            if node.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE, LifecycleState.CONTESTED):
                raise ValueError("Derivation inputs must be active nodes")
            for value in node.__dict__.values():
                if isinstance(value, datetime) and value.utcoffset() is None:
                    raise ValueError("Derivation timestamps must have a timezone")
        if any(self.event_id not in node.evidence_refs for node in self.nodes):
            raise ValueError("New derivation nodes must cite the source event")
        all_edges = self.edges + self.replacements
        if len({edge.id for edge in all_edges}) != len(all_edges):
            raise ValueError("Derivation edge identities must be distinct")
        for edge in all_edges:
            if edge.user_id != self.user_id or edge.provenance_event_id != self.event_id:
                raise ValueError("Derivation edges must belong to and cite the source")
            if edge.source_id not in known or edge.target_id not in known:
                raise ValueError("Derivation edge endpoints must be planned or referenced")
            for value in edge.__dict__.values():
                if isinstance(value, datetime) and value.utcoffset() is None:
                    raise ValueError("Derivation timestamps must have a timezone")
        if any(edge.edge_type == EdgeType.SUPERSEDES for edge in self.edges):
            raise ValueError("Supersedence edges must be declared as replacements")
        retired = set()
        for edge in self.replacements:
            if edge.edge_type != EdgeType.SUPERSEDES or edge.source_id not in new:
                raise ValueError("A replacement must originate from a new derivation node")
            if edge.target_id == edge.source_id or edge.target_id in retired or edge.source_id in retired:
                raise ValueError("Replacement order must not repeat or reactivate retired nodes")
            retired.add(edge.target_id)
        if len({item.node_id for item in self.embeddings}) != len(self.embeddings):
            raise ValueError("A derivation node must have exactly one prepared embedding")
        if {item.node_id for item in self.embeddings} != new.keys():
            raise ValueError("Every new derivation node needs a prepared embedding")
        if len({item.node_id for item in self.lexical_documents}) != len(self.lexical_documents):
            raise ValueError("Prepared lexical documents must have distinct node identities")
        if any(item.node_id not in new for item in self.lexical_documents):
            raise ValueError("Prepared lexical documents can only index new nodes")
        canonical_hash(self.model_dump(mode="json"))  # Reject nonfinite nested model/metadata values.
        return self
