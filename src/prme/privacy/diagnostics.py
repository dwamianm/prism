"""Closed work inspection: identities and counters, never exception text."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from prme.privacy.policy import PrivacyDenied
from prme.types import Scope


class ProtectedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID
    scope: Scope
    status: Literal["pending", "complete", "abandoned"]
    attempts: int = Field(ge=0, strict=True)
    last_error: Literal["privacy_work_failed"] | None = None


class ProtectedProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processed: int = Field(ge=0, strict=True)
    failed: int = Field(ge=0, strict=True)
    pending: int = Field(ge=0, strict=True)
    errors: dict[UUID, Literal["privacy_work_failed"]]


def project_jobs(values, *, identity):
    if identity != "plan_id":
        raise PrivacyDenied("privacy_invalid_result")
    try:
        return [
            ProtectedJob.model_validate(
                {
                    **value,
                    "last_error": "privacy_work_failed"
                    if value["last_error"]
                    else None,
                }
            ).model_dump(mode="json")
            for value in values
        ]
    except (KeyError, TypeError, ValueError):
        raise PrivacyDenied("privacy_invalid_result") from None


def project_processing(value):
    try:
        return ProtectedProcessingResult.model_validate(
            {
                **value,
                "errors": {key: "privacy_work_failed" for key in value["errors"]},
            }
        ).model_dump(mode="json")
    except (KeyError, TypeError, ValueError):
        raise PrivacyDenied("privacy_invalid_result") from None
