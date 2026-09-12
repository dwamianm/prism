"""Regression: InstructorExtractionProvider must pass model= to client.create().

instructor.from_provider() pre-binds the model for most providers, but NOT for
'bedrock' -- there client.create() raises "Missing required parameter: modelId"
unless the model is passed explicitly. These tests lock in that behavior so the
bedrock provider keeps working (and so create_extraction_provider forwards the
configured model).
"""

from unittest.mock import AsyncMock, patch
import asyncio
import pytest


from prme.config import ExtractionConfig
from prme.ingestion.extraction import (
    InstructorExtractionProvider,
    create_extraction_provider,
)
from prme.ingestion.schema import ExtractionResult
from prme.ingestion.errors import ExtractionError

BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"


def _mock_client():
    client = AsyncMock()
    client.create = AsyncMock(return_value=ExtractionResult())
    return client


async def test_extract_passes_model_to_create_for_bedrock():
    provider = InstructorExtractionProvider(
        f"bedrock/{BEDROCK_MODEL}", model=BEDROCK_MODEL
    )
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("Alex has a Blue Cross PPO plan.", role="user")

    client.create.assert_awaited_once()
    assert client.create.await_args.kwargs["model"] == BEDROCK_MODEL


async def test_model_derived_from_provider_string_when_not_given():
    # No explicit model= -> derive it from the provider string (split on first '/').
    provider = InstructorExtractionProvider(f"bedrock/{BEDROCK_MODEL}")
    assert provider._resolve_model_id() == BEDROCK_MODEL

    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("Alex has a Blue Cross PPO plan.", role="user")
    assert client.create.await_args.kwargs["model"] == BEDROCK_MODEL


async def test_factory_forwards_configured_model():
    config = ExtractionConfig(provider="bedrock", model=BEDROCK_MODEL)
    provider = create_extraction_provider(config)
    assert isinstance(provider, InstructorExtractionProvider)
    assert provider._resolve_model_id() == BEDROCK_MODEL


async def test_openai_model_still_passed():
    # Passing model for non-bedrock providers is safe and expected.
    provider = InstructorExtractionProvider("openai/gpt-4o-mini", model="gpt-4o-mini")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("hello", role="user")
    assert client.create.await_args.kwargs["model"] == "gpt-4o-mini"


async def test_provider_error_is_not_a_successful_empty_extraction():
    provider = InstructorExtractionProvider("openai/gpt-4o-mini")
    client = _mock_client()
    client.create.side_effect = RuntimeError("private provider response")
    with patch.object(provider, "_ensure_client", return_value=client):
        with pytest.raises(ExtractionError) as failure:
            await provider.extract("hello")
    assert "private provider response" not in str(failure.value)


async def test_configured_timeout_cancels_a_stalled_provider():
    provider = InstructorExtractionProvider("openai/gpt-4o-mini", timeout=.01)
    cancelled = asyncio.Event()

    async def stall(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = _mock_client()
    client.create.side_effect = stall
    with patch.object(provider, "_ensure_client", return_value=client):
        with pytest.raises(ExtractionError, match="TimeoutError"):
            await provider.extract("hello")
    assert cancelled.is_set()


@pytest.mark.parametrize("fact", [
    {"subject": "Alice", "predicate": "uses", "object": "email"},
    {"subject": "Alice", "predicate": "uses", "object": "email", "evidence_quote": "Alice always uses email"},
    {"subject": "Alice", "predicate": "uses", "object": "Slack", "evidence_quote": "Alice uses email"},
])
async def test_provider_schema_requests_retry_for_missing_or_fabricated_support(fact):
    from pydantic import ValidationError

    provider = InstructorExtractionProvider("openai/gpt-4o-mini")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("Alice uses email")
    args = client.create.await_args.kwargs
    with pytest.raises(ValidationError):
        args["response_model"].model_validate({"facts": [fact]}, context=args["context"])


async def test_provider_schema_accepts_source_supported_fact():
    provider = InstructorExtractionProvider("openai/gpt-4o-mini")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("Alice uses email only for nonurgent requests.")
    args = client.create.await_args.kwargs
    result = args["response_model"].model_validate({"facts": [{
        "subject": "Alice", "predicate": "uses", "object": "email",
        "evidence_quote": "Alice uses email only for nonurgent requests.",
    }]}, context=args["context"])
    assert result.facts[0].evidence_quote.endswith("nonurgent requests.")
