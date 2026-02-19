"""Memory node model for the graph store.

MemoryNode represents all eight node types in the PRME graph:
Entity, Event, Fact, Decision, Preference, Task, Summary, Note.
"""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import Field

from prme.models.base import MemoryObject
from prme.types import LifecycleState, NodeType


class MemoryNode(MemoryObject):
    """A typed node in the memory graph.

    Carries content, lifecycle state, confidence/salience scores,
    temporal validity window, supersedence pointer, and evidence references.
    """

    node_type: NodeType = Field(description="Type of this memory node")
    content: str = Field(description="Node content text")
    metadata: dict | None = Field(
        default=None, description="Optional structured metadata"
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0 to 1.0)",
    )
    salience: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Salience score (0.0 to 1.0)",
    )
    lifecycle_state: LifecycleState = Field(
        default=LifecycleState.TENTATIVE,
        description="Current lifecycle state",
    )
    valid_from: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Start of temporal validity window (UTC)",
    )
    valid_to: datetime | None = Field(
        default=None,
        description="End of temporal validity window (UTC), None = still valid",
    )
    superseded_by: UUID | None = Field(
        default=None,
        description="ID of the node that supersedes this one",
    )
    evidence_refs: list[UUID] = Field(
        default_factory=list,
        description="List of event IDs providing evidence for this node",
    )
