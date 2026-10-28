"""Request-scoped retry equality using the separate native custody purpose.

These keyed commitments are private equality state, not anonymous metadata.
Verification never provisions a key or selects a newer version.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
from uuid import uuid5

from prme.privacy.capabilities import RetryCommitment, RetryContext
from prme.privacy.crypto import canonical, policy_binding
from prme.privacy.keys import KeyBoundary, RetryKeyProvider, aware
from prme.privacy.policy import PrivacyDenied


class NativeRetryCommitter:
    def __init__(self, provider: RetryKeyProvider, *, clock=None):
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _deadline(self, context):
        saved = context.snapshot
        bounds = [
            value
            for value in (
                saved.processing_deadline,
                saved.evidence_deadline,
                saved.memory_deadline,
            )
            if value is not None
        ]
        if not bounds:
            raise PrivacyDenied("privacy_retry_unavailable")
        deadline = min(bounds)
        if aware(self.clock()) >= aware(deadline):
            raise PrivacyDenied("privacy_retry_unavailable")
        return deadline

    def _value(self, key, request, context, retention_ref):
        if not isinstance(request, bytes):
            raise PrivacyDenied("privacy_retry_unavailable")
        ref = key.reference
        if (
            ref.purpose != "retry_commitment"
            or ref.boundary != KeyBoundary.from_snapshot(context.snapshot)
            or ref.request_id != context.request_id
            or ref.retention_ref != retention_ref
            or ref.policy_binding != policy_binding(context.snapshot)
        ):
            raise PrivacyDenied("privacy_retry_unavailable")
        binding = canonical(context.model_dump(mode="json"))
        message = (
            b"PRME\x00private-admission-retry\x00v1\x00"
            + len(binding).to_bytes(8, "big")
            + binding
            + len(request).to_bytes(8, "big")
            + request
        )
        return key.use(
            lambda secret: hmac.new(secret, message, hashlib.sha256).hexdigest()
        )

    async def commit(self, request: bytes, context: RetryContext) -> RetryCommitment:
        context = RetryContext.model_validate_json(context.model_dump_json())
        deadline = self._deadline(context)
        retention_ref = uuid5(context.request_id, "prme:retry-retention:v1")
        key = await self.provider.provision_retry_key(
            context, retention_ref=retention_ref, expires_at=deadline
        )
        try:
            value = self._value(key, request, context, retention_ref)
            return RetryCommitment(
                context=context,
                key_version=key.key_version,
                retention_ref=retention_ref,
                value=value,
            )
        finally:
            key.close()

    async def verify(self, request: bytes, commitment: RetryCommitment) -> bool:
        commitment = RetryCommitment.model_validate_json(commitment.model_dump_json())
        self._deadline(commitment.context)
        key = await self.provider.get_retry_key(
            commitment.context,
            key_version=commitment.key_version,
            retention_ref=commitment.retention_ref,
        )
        try:
            if key.key_version != commitment.key_version:
                raise PrivacyDenied("privacy_retry_unavailable")
            actual = self._value(
                key, request, commitment.context, commitment.retention_ref
            )
            return hmac.compare_digest(actual, commitment.value)
        finally:
            key.close()
