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
    config = ExtractionConfig(
        provider="bedrock", model=BEDROCK_MODEL, temperature=0.25
    )
    provider = create_extraction_provider(config)
    assert isinstance(provider, InstructorExtractionProvider)
    assert provider._resolve_model_id() == BEDROCK_MODEL
    assert provider._temperature == 0.25


async def test_openai_model_still_passed():
    # Passing model for non-bedrock providers is safe and expected.
    provider = InstructorExtractionProvider("openai/gpt-4o-mini", model="gpt-4o-mini")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("hello", role="user")
    assert client.create.await_args.kwargs["model"] == "gpt-4o-mini"
    assert client.create.await_args.kwargs["temperature"] == 0.0
    assert "reasoning_effort" not in client.create.await_args.kwargs


async def test_ollama_disables_reasoning_for_structured_output_by_default():
    provider = InstructorExtractionProvider("ollama/qwen3.5:9b")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("hello")
    assert client.create.await_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.parametrize("source_role", ["user", "assistant", "system"])
async def test_historical_source_is_always_submitted_as_extraction_input(source_role):
    provider = InstructorExtractionProvider("openai/gpt-4o-mini")
    client = _mock_client()
    source = "The historical source text."
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract(source, role=source_role)

    request = client.create.await_args.kwargs
    assert request["messages"][-1] == {"role": "user", "content": source}
    assert request["context"] == {"source_text": source, "source_role": source_role}
    system_prompt = request["messages"][0]["content"]
    if source_role == "assistant":
        assert "historical assistant message" in system_prompt
        assert "standalone recommendations" in system_prompt
    else:
        assert f"SOURCE MESSAGE ROLE: {source_role}" in system_prompt


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
])
async def test_provider_schema_drops_claims_missing_required_support(fact):
    provider = InstructorExtractionProvider("openai/gpt-4o-mini")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("Alice uses email")
    args = client.create.await_args.kwargs
    result = args["response_model"].model_validate(
        {"facts": [fact]}, context=args["context"]
    )
    assert result.facts == []


async def test_provider_schema_drops_a_fabricated_claim_without_failing_the_event():
    provider = InstructorExtractionProvider("openai/gpt-4o-mini")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("Alice uses email")
    args = client.create.await_args.kwargs
    result = args["response_model"].model_validate({"facts": [{
        "subject": "Alice", "predicate": "uses", "object": "Slack",
        "polarity": "positive", "evidence_quote": "Alice uses email",
    }]}, context=args["context"])
    assert result.facts == []


async def test_provider_schema_accepts_source_supported_fact():
    provider = InstructorExtractionProvider("openai/gpt-4o-mini")
    client = _mock_client()
    with patch.object(provider, "_ensure_client", return_value=client):
        await provider.extract("Alice uses email only for nonurgent requests.")
    args = client.create.await_args.kwargs
    result = args["response_model"].model_validate({"entities": [{"name": "Alice", "entity_type": "person"}], "facts": [{
            "subject": "Alice", "predicate": "uses", "object": "email",
            "polarity": "positive",
            "evidence_quote": "Alice uses email only for nonurgent requests.",
    }]}, context=args["context"])
    assert result.facts[0].evidence_quote.endswith("nonurgent requests.")
