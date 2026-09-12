"""Recovery diagnostics preserve meaningful failure categories without messages."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine, ExtractionError
from prme.ingestion.extraction import InstructorExtractionProvider
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_write_queue_logs_category_without_formatting_provider_exception():
    from structlog.testing import capture_logs
    from prme.storage.write_queue import WriteQueue

    class UnprintableProviderError(RuntimeError):
        def __str__(self):
            raise AssertionError("Provider response must not be formatted")

    error = UnprintableProviderError()
    queue = WriteQueue()
    await queue.start()
    try:
        with capture_logs() as logs:
            with pytest.raises(UnprintableProviderError) as failure:
                await asyncio.wait_for(queue.submit(AsyncMock(side_effect=error), label="authored-job"), 2)
            assert failure.value is error
            assert await queue.submit(AsyncMock(return_value="healthy")) == "healthy"
        assert logs == [{"event": "write_queue.job_failed", "label": "authored-job",
                         "error_type": "UnprintableProviderError", "log_level": "error"}]
    finally:
        await queue.stop()


async def test_provider_timeout_is_not_reported_as_caller_cancellation(config, user, monkeypatch):
    async def stall(**kwargs):
        await asyncio.Event().wait()
    provider = InstructorExtractionProvider("ollama/fake", timeout=.01)
    client = AsyncMock()
    client.create.side_effect = stall
    monkeypatch.setattr(provider, "_ensure_client", lambda: client)
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider = provider
        engine._pipeline._retry_delays = ()
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest("Alice uses Python", user_id=user, wait_for_extraction=True)
        eid = failure.value.event_id
        status = await engine.extraction_status(eid, user_id=user)
        assert status.status == "failed" and status.last_error == "TimeoutError"
        assert failure.value.reason_code == "TimeoutError"
        assert await engine.get_event(eid, user_id=user) is not None
    async with MemoryEngine.open(config) as engine:
        assert (await engine.extraction_status(eid, user_id=user)).last_error == "TimeoutError"


@pytest.mark.parametrize("status,kind", [(401, "AuthenticationError"), (429, "RateLimitError")])
async def test_provider_categories_survive_wrapping_without_exposing_details(config, user, monkeypatch, status, kind):
    import httpx
    import openai
    error = getattr(openai, kind)("private provider response", response=httpx.Response(
        status, request=httpx.Request("POST", "https://provider.invalid/")), body={"private": "credential"})
    error.__cause__ = RuntimeError("private underlying transport detail")
    provider = InstructorExtractionProvider("openai/fake")
    client = AsyncMock()
    client.create.side_effect = error
    monkeypatch.setattr(provider, "_ensure_client", lambda: client)
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._retry_delays = ()
        engine._pipeline._extraction_provider = provider
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest("Alice uses Python", user_id=user, wait_for_extraction=True)
        assert failure.value.reason_code == kind
        assert "private" not in str(failure.value)
        saved = await engine.extraction_status(failure.value.event_id, user_id=user)
        assert saved.last_error == kind and "private" not in saved.model_dump_json()


async def test_cyclic_provider_cause_still_records_terminal_failure(config, user):
    error = RuntimeError("private provider response")
    error.__cause__ = error
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._retry_delays = ()
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=error)
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest("Alice uses Python", user_id=user, wait_for_extraction=True)
        status = await engine.extraction_status(failure.value.event_id, user_id=user)
        assert status.status == "failed" and status.last_error == "RuntimeError"


def test_failure_codes_bound_unknown_names_and_find_instructor_validation_attempts():
    from types import SimpleNamespace
    from pydantic import ValidationError
    from prme.ingestion.errors import extraction_failure_code
    from prme.ingestion.schema import ExtractedFact

    try:
        ExtractedFact(subject="Alice", predicate="uses", object="Python", epistemic_type="invalid")
    except ValidationError as validation:
        retry = type("InstructorRetryException", (Exception,), {})()
        retry.failed_attempts = [SimpleNamespace(exception=validation)]
    assert extraction_failure_code(retry) == "ValidationError"
    error = type("Not a bounded reason " * 20, (Exception,), {})()
    assert extraction_failure_code(error) == "ExtractionError"
    deep = error
    for _ in range(100):
        wrapper = RuntimeError("private detail")
        wrapper.__cause__ = deep
        deep = wrapper
    assert extraction_failure_code(deep) == "RuntimeError"
