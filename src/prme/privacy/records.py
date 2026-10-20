"""New versioned admission records; legacy event/journal bytes are unchanged."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from prme.privacy.capabilities import ProtectedPayload, RetryCommitment
from prme.privacy.policy import FrozenModel, PolicySnapshot

PRIVACY_KEY = "_prme_privacy_v1"


class PrivacyRecord(FrozenModel):
    schema_version: Literal[1] = 1
    event_id: UUID
    snapshot: PolicySnapshot
    source_id: UUID
    representation_id: UUID
    kind: Literal["direct", "extraction"]
    evidence: ProtectedPayload | None = None
    processing: ProtectedPayload | None = None
    processing_state: Literal["not_required", "pending", "complete", "cancelled", "expired"] = "not_required"
    retry: RetryCommitment | None = None
    request_index: int = 0

    def reference(self) -> dict:
        return {"schema_version": 1, "event_id": str(self.event_id), "source_id": str(self.source_id),
                "representation_id": str(self.representation_id), "policy_id": str(self.snapshot.policy.policy_id),
                "policy_revision": self.snapshot.policy.revision, "representation": "sanitized"}
