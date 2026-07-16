"""Regression: InstructorExtractionProvider must pass model= to client.create().

instructor.from_provider() pre-binds the model for most providers, but NOT for
'bedrock' -- there client.create() raises "Missing required parameter: modelId"
unless the model is passed explicitly. These tests lock in that behavior so the
bedrock provider keeps working (and so create_extraction_provider forwards the
configured model).
"""

from unittest.mock import AsyncMock, patch

import pytest

from prme.config import ExtractionConfig
from prme.ingestion.extraction import (
    InstructorExtractionProvider,
    create_extraction_provider,
)
from prme.ingestion.schema import ExtractionResult

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
