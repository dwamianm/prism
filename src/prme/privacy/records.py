"""New versioned admission records; legacy event/journal bytes are unchanged."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import model_validator

from prme.privacy.capabilities import ProtectedPayload, RetryCommitment
from prme.privacy.policy import FrozenModel, PolicySnapshot

PRIVACY_KEY = "_prme_privacy_v1"


class SanitizationReport(FrozenModel):
    """Fixed versions and counts only. No spans, removed values or value hashes."""
    sanitizer_version: Literal["prme-sanitizer-v1"] = "prme-sanitizer-v1"
    registry_version: Literal["prme-recognizers-v1"] = "prme-recognizers-v1"
    detector_versions: tuple[str, ...]
    action_counts: tuple[tuple[str, int], ...]

    @model_validator(mode="after")
    def bounded(self):
        from prme.privacy.local_analyzer import VERSIONS
        from prme.privacy.recognizers import CATEGORIES
        names = {f"{category}:{action}" for category in CATEGORIES
                 for action in ("allow", "redact", "pseudonymize", "block")}
        if (self.detector_versions != VERSIONS or len(self.action_counts) > len(names)
                or len({name for name, _ in self.action_counts}) != len(self.action_counts)
                or any(name not in names or type(count) is not int or not 0 <= count <= 65536
                       for name, count in self.action_counts)):
            raise ValueError("privacy_invalid_result")
        return self


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
    sanitization: SanitizationReport | None = None

    def reference(self) -> dict:
        return {"schema_version": 1, "event_id": str(self.event_id), "source_id": str(self.source_id),
                "representation_id": str(self.representation_id), "policy_id": str(self.snapshot.policy.policy_id),
                "policy_revision": self.snapshot.policy.revision, "representation": "sanitized"}
