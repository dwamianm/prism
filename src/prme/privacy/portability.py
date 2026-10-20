"""Closed portability contracts and fixed, content-free failures (RFC-0019)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from prme.privacy.policy import FrozenModel


class ProtectedStoreFormat(FrozenModel):
    schema_version: Literal[2] = 2
    event_format: Literal["privacy-event-reference-v2"] = "privacy-event-reference-v2"
    journal_format: Literal["privacy-journal-v1"] = "privacy-journal-v1"
    envelope_format: Literal["evidence-envelope-v1"] = "evidence-envelope-v1"
    ledger_format: Literal[1] = 1
    project_id: UUID
    store_id: UUID
    state: Literal["complete", "staging"]
    migration_id: UUID | None = None


class PrivacyPortabilityError(ValueError):
    """No provider exception, source name, secret, or SQL enters public errors."""

    def __init__(self, code: str = "privacy_portability_failed"):
        codes = {
            "privacy_portability_failed", "privacy_source_unavailable",
            "privacy_source_changed", "privacy_source_not_quiescent",
            "privacy_source_key_unavailable", "privacy_source_capacity",
            "privacy_format_unsupported", "privacy_migration_incomplete",
            "privacy_destination_occupied", "privacy_export_invalid",
            "privacy_migration_required", "privacy_legacy_unclassified",
        }
        self.code = code if code in codes else "privacy_portability_failed"
        super().__init__(self.code)

