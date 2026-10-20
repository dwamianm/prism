"""Scoped one-way placeholders. Key custody is supplied by PV-03."""

from __future__ import annotations

import base64
import hmac
import re
from typing import Any
from uuid import UUID

from prme.privacy.policy import PolicySnapshot, PrivacyDenied
from prme.privacy.recognizers import RESERVED_PREFIX

_TOKEN = re.compile(
    r"\[PRME_PSEUDONYM_V1:(email|phone|person|custom_identifier):"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):"
    r"([A-Za-z0-9_-]{43})\]"
)
_CATEGORIES = frozenset(("email", "phone", "person", "custom_identifier"))
_DOMAIN = b"prme-pseudonym-v1"


def framed_value(category: str, value: str) -> bytes:
    if category not in _CATEGORIES:
        raise PrivacyDenied("privacy_configuration_invalid")
    encoded = [category.encode("utf-8"), value.encode("utf-8")]
    return _DOMAIN + b"".join(len(part).to_bytes(8, "big") + part for part in encoded)


class Pseudonyms:
    """Consume #321's narrow SecretKeyHandle API; never provision key material."""

    def __init__(self, key_provider: Any):
        self._provider = key_provider

    async def handle(self, snapshot: PolicySnapshot, epoch: UUID):
        policy = snapshot.policy
        try:
            handle = await self._provider.key_for(
                policy.project_id, policy.owner_id, policy.scope.value, epoch)
            if (handle.project_id, handle.owner_id, handle.scope, handle.epoch, handle.purpose) != (
                    policy.project_id, policy.owner_id, policy.scope.value, epoch, "pseudonym"):
                handle.close()
                raise PrivacyDenied("privacy_boundary_mismatch")
            if not isinstance(handle.key_version, UUID):
                handle.close()
                raise PrivacyDenied("privacy_invalid_result")
            return handle
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    @staticmethod
    def issue(handle, category: str, value: str) -> str:
        try:
            message = framed_value(category, value)

            def digest(key: bytes):
                if not isinstance(key, bytes) or len(key) != 32:
                    raise PrivacyDenied("privacy_invalid_result")
                return hmac.digest(key, message, "sha256")

            mac = base64.urlsafe_b64encode(handle.use(digest)).decode("ascii").rstrip("=")
            return f"{RESERVED_PREFIX}{category}:{handle.epoch}:{mac}]"
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None


def issued_token_spans(text: str, issued: frozenset[str], epoch: UUID | None) -> tuple[tuple[int, int], ...]:
    """Only coordinator-supplied source tokens confer output continuity."""
    spans = []
    position = 0
    while True:
        position = text.find(RESERVED_PREFIX, position)
        if position < 0:
            return tuple(spans)
        token = _TOKEN.match(text, position)
        if token is None or token.group() not in issued or str(epoch) != token.group(2):
            raise PrivacyDenied("privacy_boundary_mismatch")
        spans.append((position, token.end()))
        position = token.end()


def validate_issued(issued: tuple[str, ...], epoch: UUID | None) -> frozenset[str]:
    if not isinstance(issued, tuple) or len(issued) > 1024 or len(set(issued)) != len(issued):
        raise PrivacyDenied("privacy_invalid_result")
    for token in issued:
        if not isinstance(token, str):
            raise PrivacyDenied("privacy_invalid_result")
        match = _TOKEN.fullmatch(token)
        if match is None or epoch is None or match.group(2) != str(epoch):
            raise PrivacyDenied("privacy_boundary_mismatch")
    return frozenset(issued)
