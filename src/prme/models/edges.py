"""Memory edge model for the graph store.

MemoryEdge represents typed, temporally-valid relationships between nodes
with confidence scores and provenance tracking.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from prme.types import EdgeType


class MemoryEdge(BaseModel):
    """A typed edge in the memory graph.

    Edges connect source and target nodes with temporal validity,
    confidence scores, and provenance references. Edges have their
    own simpler ID scheme (not a MemoryObject).
    """

    id: UUID = Field(default_factory=uuid4, description="Unique edge identifier")
    source_id: UUID = Field(description="Source node ID")
    target_id: UUID = Field(description="Target node ID")
    edge_type: EdgeType = Field(description="Type of this relationship")
    user_id: str = Field(description="Owner user identifier for scoping")
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0 to 1.0)",
    )
    valid_from: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Start of temporal validity window (UTC)",
    )
    valid_to: datetime | None = Field(
        default=None,
        description="End of temporal validity window (UTC), None = still valid",
    )
    provenance_event_id: UUID | None = Field(
        default=None,
        description="ID of the event that caused this edge to be created",
    )
    metadata: dict | None = Field(
        default=None, description="Optional structured metadata"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp (UTC)",
    )
