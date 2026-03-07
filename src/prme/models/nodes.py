"""Memory node model for the graph store.

MemoryNode represents all eight node types in the PRME graph:
Entity, Event, Fact, Decision, Preference, Task, Summary, Note.
"""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import Field

from prme.models.base import MemoryObject
from prme.types import DecayProfile, EpistemicType, LifecycleState, NodeType, SourceType


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
    epistemic_type: EpistemicType = Field(
        default=EpistemicType.ASSERTED,
        description="Epistemic classification of this memory object (RFC-0003 S3)",
    )
    source_type: SourceType = Field(
        default=SourceType.USER_STATED,
        description="Source provenance type for confidence matrix lookup (RFC-0003 S4)",
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
    decay_profile: DecayProfile = Field(
        default=DecayProfile.MEDIUM,
        description="Decay rate profile for salience/confidence decay (RFC-0015)",
    )
    last_reinforced_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of most recent reinforcement event (RFC-0015)",
    )
    reinforcement_boost: float = Field(
        default=0.0,
        ge=0.0,
        description="Cumulative reinforcement boost, capped per RFC-0008 S6",
    )
    salience_base: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Baseline salience before virtual decay (RFC-0015)",
    )
    confidence_base: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Baseline confidence before virtual decay (RFC-0015)",
    )
    pinned: bool = Field(
        default=False,
        description="If True, exempt from all automated decay (RFC-0015)",
    )
