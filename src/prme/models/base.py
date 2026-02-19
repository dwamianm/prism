"""Base memory object model.

All memory objects (events, nodes) inherit from MemoryObject,
which provides common fields: id, user_id, session_id, scope, timestamps.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from prme.types import Scope


class MemoryObject(BaseModel):
    """Base model for all PRME memory objects.

    Provides shared identity, scoping, and timestamp fields.
    """

    model_config = ConfigDict(frozen=False)

    id: UUID = Field(default_factory=uuid4, description="Unique object identifier")
    user_id: str = Field(description="Owner user identifier for scoping")
    session_id: str | None = Field(
        default=None, description="Optional session identifier for scoping"
    )
    scope: Scope = Field(
        default=Scope.PERSONAL, description="Memory scope level"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp (UTC)",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last update timestamp (UTC)",
    )
