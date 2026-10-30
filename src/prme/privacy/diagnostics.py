"""Closed work inspection: identities and counters, never exception text."""

from contextlib import contextmanager
from contextvars import ContextVar
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


_protected_diagnostics: ContextVar[bool] = ContextVar(
    "prme_protected_diagnostics", default=False
)


def diagnostics_protected():
    return _protected_diagnostics.get()


@contextmanager
def protected_diagnostics(enabled=True):
    token = _protected_diagnostics.set(bool(enabled or _protected_diagnostics.get()))
    try:
        yield
    finally:
        _protected_diagnostics.reset(token)


def closing_diagnostics(function):
    """Classify cleanup diagnostics without requiring a decryption capability."""
    import functools

    @functools.wraps(function)
    async def guarded(engine, *args, **kwargs):
        protected = engine._privacy.selected()
        if not protected and not engine._closed:
            try:
                await engine._privacy.repository.require_legacy_range(None, None)
            except PrivacyDenied:
                protected = True
            except Exception:
                # The store cannot be read, for example because a hosted
                # project deletion dropped its namespace schema. Treat cleanup
                # diagnostics as protected, so an unread store never reports
                # exception text, and still close it.
                protected = True
        with protected_diagnostics(protected):
            try:
                return await function(engine, *args, **kwargs)
            except PrivacyDenied:
                raise
            except Exception:
                if protected:
                    raise PrivacyDenied("privacy_processing_unavailable") from None
                raise

    return guarded


class ScopedLogger:
    """Wrap only known PRME sinks; concurrent legacy calls are untouched.

    No raw message, argument, traceback, class name or attached context reaches
    the underlying logger in a protected scope. ContextVar follows asyncio tasks
    and the existing native run_to_completion thread boundary.
    """

    def __init__(self, logger):
        self._logger = logger

    def __getattr__(self, name):
        original = getattr(self._logger, name)
        if name in {"bind", "new"}:
            return lambda *args, **kwargs: (
                self
                if diagnostics_protected()
                else ScopedLogger(original(*args, **kwargs))
            )
        if name not in {
            "debug",
            "info",
            "warning",
            "warn",
            "error",
            "exception",
            "critical",
            "log",
        }:
            return original

        def emit(*args, **kwargs):
            if not diagnostics_protected():
                return original(*args, **kwargs)
            if name == "exception":
                return self._logger.error("privacy_operation_event")
            if name == "log":
                return original(args[0], "privacy_operation_event")
            return original("privacy_operation_event")

        return emit


def event_failure_code(event, error):
    from prme.privacy.records import PRIVACY_KEY
    from prme.ingestion.errors import extraction_failure_code

    return (
        "privacy_processing_failed"
        if PRIVACY_KEY in (event.metadata or {})
        else extraction_failure_code(error)
    )


def project_organize(value):
    from prme.organizer.models import OrganizeResult, JobResult
    from prme.organizer.jobs import ALL_JOBS

    allowed = {*ALL_JOBS, "materialization_drain"}
    if (
        not isinstance(value, OrganizeResult)
        or not set(value.jobs_run + value.jobs_skipped + list(value.per_job)) <= allowed
    ):
        raise PrivacyDenied("privacy_invalid_result")
    # Job details are heterogeneous extension dictionaries. Only fixed job IDs
    # and typed numerical outcomes belong in ordinary protected diagnostics.
    jobs = {
        name: JobResult(
            job=name,
            nodes_processed=job.nodes_processed,
            nodes_modified=job.nodes_modified,
            errors=job.errors,
            duration_ms=job.duration_ms,
        )
        for name, job in value.per_job.items()
    }
    return value.model_copy(deep=True, update={"per_job": jobs})


class ProtectedCollectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collected: int = Field(ge=0, strict=True)
    failed: int = Field(ge=0, strict=True)
    remaining: int = Field(ge=0, strict=True)
    errors: dict[UUID, Literal["privacy_work_failed"]]
    blocked_reason: Literal["privacy_work_unavailable"] | None


def project_collection(value):
    try:
        return ProtectedCollectionResult.model_validate(
            {
                **value,
                "errors": {key: "privacy_work_failed" for key in value["errors"]},
                "blocked_reason": "privacy_work_unavailable"
                if value["blocked_reason"]
                else None,
            }
        ).model_dump(mode="json")
    except (KeyError, TypeError, ValueError):
        raise PrivacyDenied("privacy_invalid_result") from None
