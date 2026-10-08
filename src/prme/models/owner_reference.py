"""Explicit source-local declaration of first-person owner authorship."""

from collections.abc import Mapping
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from prme.epistemic.inference import OWNER_ROLES
from prme.models.speaker import SpeakerError

OWNER_REFERENCE_KEY = "prme_first_person_owner_v1"


def attach_owner_reference(
    metadata: Mapping[str, Any] | None, enabled: bool, *, role: str, speaker: str | None,
) -> Any:
    """Persist caller-declared authorship without changing the input mapping."""
    if type(enabled) is not bool:
        raise SpeakerError("first_person_owner must be a boolean")
    if isinstance(metadata, Mapping) and OWNER_REFERENCE_KEY in metadata:
        raise SpeakerError(f"metadata.{OWNER_REFERENCE_KEY} is reserved; pass first_person_owner instead")
    if not enabled:
        return metadata
    if not isinstance(role, str) or role.casefold() not in OWNER_ROLES or speaker is not None:
        raise SpeakerError("first_person_owner requires a user/human role and no named speaker")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise SpeakerError("metadata must be a mapping")
    return {**(metadata or {}), OWNER_REFERENCE_KEY: True}


def owner_reference_id(user_id: str, scope: str) -> UUID:
    # Length-prefix the account so neither separator nor scope can collide.
    return uuid5(NAMESPACE_URL, f"prme:owner-reference:v1:{len(user_id)}:{user_id}:{scope}")
