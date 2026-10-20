"""Closed portability contracts and fixed, content-free failures (RFC-0019)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, ConfigDict
from prme.models import MemoryNode, MemoryEdge
from prme.models.derivation import PreparedEmbedding
from prme.models.privacy_reads import ProtectedOperationView
from prme.privacy.erasure import LedgerHead
from prme.privacy.records import PrivacyRecord
from prme.types import Scope
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


class ExportEvent(FrozenModel):
    """Opaque source identity. There is deliberately no legacy source checksum."""
    id: UUID
    timestamp: AwareDatetime
    role: str = Field(min_length=1, max_length=64)
    user_id: UUID
    session_id: str | None = None
    scope: Scope
    created_at: AwareDatetime
    event_time: AwareDatetime | None = None


class ExportOperation(FrozenModel):
    id: UUID
    op_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    target_id: str | None
    payload: str
    actor_id: str | None
    namespace_id: str | None
    created_at: AwareDatetime


class ExportWork(FrozenModel):
    event_id: UUID
    kind: Literal["materialization", "extraction"]
    status: Literal["pending", "running", "complete", "failed"]
    attempts: int = Field(ge=0)
    generation: int = Field(default=0, ge=0)
    work_order: int | None = Field(default=None, gt=0)
    plan_id: UUID | None = None
    plan_revision: int = Field(default=1, ge=1)
    lease_expires_at: AwareDatetime | None = None
    next_attempt_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    # Provider/SQL exception strings never cross the export boundary.
    last_error: Literal["privacy_processing_unavailable"] | None = None


class ProtectedExportManifest(FrozenModel):
    schema_version: Literal[1] = 1
    kind: Literal["prme_protected_export"] = "prme_protected_export"
    mode: Literal["protected", "active_memory_only"]
    format: ProtectedStoreFormat
    created_at: AwareDatetime
    ledger_checkpoint: LedgerHead
    # This describes the trust boundary; a copied checkpoint cannot be current
    # authority. Key recovery artifacts are a distinct, deliberate protocol.
    current_ledger_required: Literal[True] = True
    contains_private_keys: Literal[False] = False
    admitted_memory_is_sensitive: Literal[True] = True
    original_source_checksum: Literal["unavailable_in_v2"] = "unavailable_in_v2"
    omitted: tuple[str, ...]


class ExportNode(MemoryNode):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ExportEdge(MemoryEdge):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProtectedExport(FrozenModel):
    manifest: ProtectedExportManifest
    admissions: tuple[PrivacyRecord, ...]
    events: tuple[ExportEvent, ...]
    nodes: tuple[ExportNode, ...]
    edges: tuple[ExportEdge, ...]
    embeddings: tuple[PreparedEmbedding, ...]
    operations: tuple[ExportOperation, ...]
    work: tuple[ExportWork, ...]
    unavailable_history: tuple[ProtectedOperationView, ...] = ()


class CopyDisposition(FrozenModel):
    copy_id: str = Field(pattern=r"^C[0-9]{2}$")
    disposition: Literal["classify", "rebuild", "retained_original", "external_unknown", "absent_by_layout"]
    count: int | None = Field(default=None, ge=0)


class PrivacyMigrationPlan(FrozenModel):
    schema_version: Literal[1] = 1
    source_format: Literal["legacy", "protected_v2"]
    source_layout: Literal["directory", "pack", "postgres"]
    copies: tuple[CopyDisposition, ...]
    source_events: int = Field(ge=0)
    admitted_nodes: int = Field(ge=0)
    required_key_purposes: tuple[Literal["store", "evidence", "processing", "pseudonym", "retry_commitment"], ...]
    policy_changes: tuple[str, ...]
    retained_copies: tuple[str, ...]
    unavailable_features: tuple[str, ...]
    legacy_plaintext_present: bool
    protected: Literal[False] = False
    external_backups: Literal["unknown"] = "unknown"
    physical_erasure: Literal[False] = False


class MigrationStatus(FrozenModel):
    schema_version: Literal[1] = 1
    job_id: UUID
    state: Literal["staging", "complete", "interrupted"]
    completed: int = Field(ge=0)
    total: int = Field(ge=0)
    source_retained: Literal[True] = True
    physical_erasure: Literal[False] = False
