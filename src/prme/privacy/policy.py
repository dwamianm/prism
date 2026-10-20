"""Immutable, restrictive evidence policies (RFC-0019).

These types select behavior. They do not implement a sanitizer or a key store.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prme.types import Scope

Capture = Literal["none", "minimal", "full"]
Action = Literal["allow", "redact", "pseudonymize", "block"]
Category = Literal["credential", "email", "phone", "person", "location", "us_ssn",
                   "payment_card", "ip_address", "custom_identifier", "sensitive_topic"]


class PrivacyDenied(ValueError):
    """A fixed, content-free public failure. Never attach offending input."""

    def __init__(self, code: str = "privacy_denied"):
        # Only engine-owned codes are accepted; downstream exceptions are not codes.
        self.code = code if code in _CODES else "privacy_denied"
        super().__init__(self.code)


_CODES = frozenset({
    "privacy_erased", "privacy_ledger_unavailable", "privacy_ledger_stale",
    "privacy_migration_required", "privacy_erasure_conflict",
    "privacy_denied", "privacy_configuration_invalid", "privacy_capability_unavailable",
    "privacy_policy_conflict", "privacy_boundary_mismatch", "privacy_stale_policy",
    "privacy_expired", "privacy_unsupported_input", "privacy_evidence_ambiguous",
    "privacy_evidence_too_large", "privacy_resubmit_required", "privacy_async_forbidden",
    "privacy_provider_unavailable", "privacy_retry_unavailable", "privacy_retry_conflict",
    "privacy_processing_unavailable", "privacy_invalid_result", "privacy_reserved_metadata",
})


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CategoryAction(FrozenModel):
    category: Category
    action: Action

    @model_validator(mode="after")
    def supported(self):
        if self.category == "credential" and self.action != "block":
            raise ValueError("privacy_configuration_invalid")
        if self.category == "sensitive_topic" and self.action not in {"allow", "block"}:
            raise ValueError("privacy_configuration_invalid")
        if self.category in {"location", "us_ssn", "payment_card", "ip_address"} and self.action == "pseudonymize":
            raise ValueError("privacy_configuration_invalid")
        return self


class MemoryRetention(FrozenModel):
    mode: Literal["erase_after", "until_deleted"]
    seconds: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def consistent(self):
        if (self.mode == "erase_after") != (self.seconds is not None):
            raise ValueError("privacy_configuration_invalid")
        return self


class PolicyReference(FrozenModel):
    policy_id: UUID
    revision: int = Field(gt=0, strict=True)


class PrivacyPolicy(FrozenModel):
    """Explicit opt-in policy; identifiers are opaque, rules are private references.

    Memory erasure deadlines are independent of lifecycle TTL. Unlimited evidence
    requires both this explicit flag and the runtime deployment permission.
    """

    schema_version: Literal[1] = 1
    policy_id: UUID
    revision: int = Field(gt=0, strict=True)
    project_id: UUID
    owner_id: UUID
    scope: Scope = Scope.PERSONAL
    capture: Capture
    evidence_use: Literal["runtime", "diagnostics_only"]
    evidence_lifetime_seconds: int | None = Field(default=None, gt=0, strict=True)
    unlimited_evidence: bool = False
    memory_retention: MemoryRetention
    processing_ceiling_seconds: int = Field(default=86400, gt=0, le=86400, strict=True)
    durable_processing_input: bool = True
    minimal_max_bytes: int = Field(default=4096, gt=0, le=65536, strict=True)
    language: Literal["en"]
    sanitizer_version: Literal["prme-sanitizer-v1"] = "prme-sanitizer-v1"
    registry_version: Literal["prme-recognizers-v1"] = "prme-recognizers-v1"
    preset: Literal["balanced-v1", "strict-v1", "custom-v1"] = "balanced-v1"
    category_actions: tuple[CategoryAction, ...] = ()
    exclusion_refs: tuple[UUID, ...] = ()
    strict_schema_ref: UUID | None = None
    strict_schema_version: Literal["prme-strict-schema-v1"] | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.capture == "none":
            if self.evidence_use != "diagnostics_only" or self.unlimited_evidence or self.evidence_lifetime_seconds is not None:
                raise ValueError("privacy_configuration_invalid")
        elif (self.evidence_lifetime_seconds is None) != self.unlimited_evidence:
            raise ValueError("privacy_configuration_invalid")
        if len(self.exclusion_refs) > 64 or len(set(self.exclusion_refs)) != len(self.exclusion_refs):
            raise ValueError("privacy_configuration_invalid")
        categories = [rule.category for rule in self.category_actions]
        if len(categories) != len(set(categories)):
            raise ValueError("privacy_configuration_invalid")
        if (self.strict_schema_ref is None) != (self.strict_schema_version is None):
            raise ValueError("privacy_configuration_invalid")
        if self.preset == "strict-v1" and self.strict_schema_ref is None:
            raise ValueError("privacy_configuration_invalid")
        return self

    @property
    def profile(self) -> str:
        return "no_evidence" if self.capture == "none" else "standard" if self.evidence_use == "runtime" else "sealed"

    def actions(self) -> tuple[CategoryAction, ...]:
        base = {"credential": "block", "email": "redact", "phone": "redact", "person": "allow",
                "location": "allow", "us_ssn": "redact", "payment_card": "redact",
                "ip_address": "allow", "custom_identifier": "redact", "sensitive_topic": "block"}
        if self.preset == "strict-v1":
            base.update(person="pseudonymize", location="redact", us_ssn="block", payment_card="block", ip_address="redact")
        for item in self.category_actions:
            base[item.category] = (_restrict_action(base[item.category], item.action)
                                   if self.preset == "strict-v1" else item.action)
        return tuple(CategoryAction(category=k, action=v) for k, v in sorted(base.items()))


def _restrict_action(left: str, right: str) -> str:
    if left == right:
        return left
    if "block" in (left, right):
        return "block"
    if left == "allow":
        return right
    if right == "allow":
        return left
    raise PrivacyDenied("privacy_policy_conflict")


def _earliest(left: int | None, right: int | None) -> int | None:
    return right if left is None else left if right is None else min(left, right)


def compose(default: PrivacyPolicy | None, caller: PrivacyPolicy | None) -> tuple[PrivacyPolicy, tuple[PolicyReference, ...]]:
    """A caller can narrow a default; incompatible transformations never guess."""
    if default is None and caller is None:
        raise PrivacyDenied("privacy_configuration_invalid")
    try:
        policies = tuple(PrivacyPolicy.model_validate_json(p.model_dump_json())
                         for p in (default, caller) if p is not None)
    except (AttributeError, TypeError, ValueError):
        raise PrivacyDenied("privacy_configuration_invalid") from None
    refs = tuple(dict.fromkeys(PolicyReference(policy_id=p.policy_id, revision=p.revision) for p in policies))
    base = policies[0]
    if len(policies) == 1:
        # Also validates preset/override conflicts before any effect.
        base.actions()
        return base, refs
    other = policies[1]
    if (base.policy_id, base.revision) == (other.policy_id, other.revision) and base != other:
        raise PrivacyDenied("privacy_policy_conflict")
    if (base.project_id, base.owner_id, base.scope) != (other.project_id, other.owner_id, other.scope):
        raise PrivacyDenied("privacy_boundary_mismatch")
    for name in ("language", "sanitizer_version", "registry_version"):
        if getattr(base, name) != getattr(other, name):
            raise PrivacyDenied("privacy_policy_conflict")
    if base.strict_schema_ref and other.strict_schema_ref and base.strict_schema_ref != other.strict_schema_ref:
        raise PrivacyDenied("privacy_policy_conflict")
    left, right = dict((a.category, a.action) for a in base.actions()), dict((a.category, a.action) for a in other.actions())
    actions = tuple(CategoryAction(category=k, action=_restrict_action(left[k], right[k])) for k in sorted(left))
    capture = min((base.capture, other.capture), key=("none", "minimal", "full").index)
    lifetime = _earliest(base.evidence_lifetime_seconds, other.evidence_lifetime_seconds)
    seconds = _earliest(base.memory_retention.seconds, other.memory_retention.seconds)
    values = base.model_dump()
    values.update(
        capture=capture,
        evidence_use="diagnostics_only" if capture == "none" or "diagnostics_only" in (base.evidence_use, other.evidence_use) else "runtime",
        evidence_lifetime_seconds=None if capture == "none" else lifetime,
        unlimited_evidence=capture != "none" and lifetime is None,
        memory_retention=MemoryRetention(mode="until_deleted" if seconds is None else "erase_after", seconds=seconds),
        processing_ceiling_seconds=min(base.processing_ceiling_seconds, other.processing_ceiling_seconds),
        durable_processing_input=base.durable_processing_input and other.durable_processing_input,
        minimal_max_bytes=min(base.minimal_max_bytes, other.minimal_max_bytes),
        preset="custom-v1", category_actions=actions,
        exclusion_refs=tuple(sorted(set(base.exclusion_refs + other.exclusion_refs), key=str)),
        strict_schema_ref=base.strict_schema_ref or other.strict_schema_ref,
        strict_schema_version=base.strict_schema_version or other.strict_schema_version,
    )
    return PrivacyPolicy.model_validate(values), refs


class PolicySnapshot(FrozenModel):
    schema_version: Literal[1] = 1
    policy: PrivacyPolicy
    references: tuple[PolicyReference, ...]
    admitted_at: datetime
    evidence_deadline: datetime | None
    memory_deadline: datetime | None
    processing_deadline: datetime

    @model_validator(mode="after")
    def clocks(self):
        for value in (self.admitted_at, self.evidence_deadline, self.memory_deadline, self.processing_deadline):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("privacy_configuration_invalid")
        return self


def snapshot(default: PrivacyPolicy | None, caller: PrivacyPolicy | None, now: datetime) -> PolicySnapshot:
    policy, refs = compose(default, caller)
    if now.tzinfo is None or now.utcoffset() is None:
        raise PrivacyDenied("privacy_configuration_invalid")
    now = now.astimezone(timezone.utc)
    try:
        evidence = now + timedelta(seconds=policy.evidence_lifetime_seconds) if policy.evidence_lifetime_seconds else None
        memory = now + timedelta(seconds=policy.memory_retention.seconds) if policy.memory_retention.seconds else None
        work = now + timedelta(seconds=policy.processing_ceiling_seconds)
    except OverflowError:
        raise PrivacyDenied("privacy_configuration_invalid") from None
    return PolicySnapshot(policy=policy, references=refs, admitted_at=now, evidence_deadline=evidence,
                          memory_deadline=memory, processing_deadline=min(work, evidence) if evidence else work)
