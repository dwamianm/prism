"""Shared Instructor client construction (issue #101).

Extraction, answerability and query reformulation build their clients through
``prme.model_runtime.create_instructor_client``. These tests pin where each
provider's requests go and which credential they carry. Nothing here sends a
request: the client factory is mocked or the client is only constructed.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import instructor
import pytest
from pydantic import SecretStr

from prme.config import ExtractionConfig
from prme.ingestion.extraction import create_extraction_provider
from prme.model_runtime import create_instructor_client, resolve_provider_connection
from prme.retrieval.answerability import AnswerabilityConfig, AnswerabilityEvaluator


@pytest.fixture
def project_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("PRME_", "OPENAI_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    return tmp_path / ".env"


def _kwargs(provider_string: str, **settings) -> dict:
    with patch("instructor.from_provider") as factory:
        create_instructor_client(provider_string, **settings)
    assert factory.call_args.args == (provider_string,)
    return factory.call_args.kwargs


def test_explicit_settings_override_environment_and_project_file(project_env, monkeypatch):
    project_env.write_text("OPENAI_API_KEY=file-key\nOPENAI_BASE_URL=https://file.invalid/v1\n")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://environment.invalid/v1")

    kwargs = _kwargs(
        "openai/example", api_key=SecretStr("explicit-key"), base_url="https://explicit.invalid/v1"
    )

    assert kwargs == {
        "async_client": True,
        "api_key": "explicit-key",
        "base_url": "https://explicit.invalid/v1",
    }


def test_environment_overrides_project_file_without_mutating_it(project_env, monkeypatch):
    project_env.write_text("OPENAI_API_KEY=file-key\nOPENAI_BASE_URL=https://file.invalid/v1\n")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    kwargs = _kwargs("openai/example")

    assert kwargs == {
        "async_client": True,
        "api_key": "environment-key",
        "base_url": "https://file.invalid/v1",
    }
    assert "OPENAI_BASE_URL" not in os.environ


def test_only_the_selected_providers_settings_are_read(project_env):
    project_env.write_text(
        "OPENAI_API_KEY=openai-key\nOPENAI_BASE_URL=https://openai.invalid/v1\n"
        "ANTHROPIC_API_KEY=anthropic-key\n"
    )

    assert _kwargs("anthropic/example") == {"async_client": True, "api_key": "anthropic-key"}


def test_ollama_uses_json_mode_and_never_reads_cloud_settings(project_env, monkeypatch):
    project_env.write_text("OPENAI_API_KEY=file-key\n")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://environment.invalid/v1")

    assert _kwargs("ollama/example") == {"async_client": True, "mode": instructor.Mode.JSON}
    assert _kwargs("ollama/example", base_url="http://gpu-host:11434/v1") == {
        "async_client": True,
        "base_url": "http://gpu-host:11434/v1",
        "mode": instructor.Mode.JSON,
    }


def test_other_providers_receive_only_explicit_settings(project_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    assert _kwargs("bedrock/example-model") == {"async_client": True}
    assert _kwargs("bedrock/example-model", base_url="https://bedrock.invalid") == {
        "async_client": True,
        "base_url": "https://bedrock.invalid",
    }


def test_an_explicit_empty_key_is_sent_as_given(project_env, monkeypatch):
    # Keyless OpenAI-compatible servers are reached with an explicit empty key;
    # the provider's own key must not be sent to them instead.
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    kwargs = _kwargs("openai/example", api_key=SecretStr(""), base_url="http://gateway.invalid/v1")

    assert kwargs == {"async_client": True, "api_key": "", "base_url": "http://gateway.invalid/v1"}


def test_resolved_connection_matches_the_client_settings(project_env, monkeypatch):
    project_env.write_text("ANTHROPIC_BASE_URL=https://file.invalid/anthropic\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-key")

    assert resolve_provider_connection("anthropic/example") == (
        "environment-key",
        "https://file.invalid/anthropic",
    )
    assert resolve_provider_connection("ollama/example") == (None, None)
    assert resolve_provider_connection(
        "ollama/example", api_key=SecretStr("proxy-key"), base_url="http://gpu-host:11434/v1"
    ) == ("proxy-key", "http://gpu-host:11434/v1")


def test_anthropic_client_is_built_for_its_endpoint(project_env):
    client = create_instructor_client(
        "anthropic/example",
        api_key=SecretStr("configured-key"),
        base_url="https://gateway.invalid/anthropic",
    )

    assert str(client.client.base_url) == "https://gateway.invalid/anthropic/"
    assert client.client.api_key == "configured-key"
    assert "base_url" not in client.kwargs
    assert client.kwargs["model"] == "example"
    assert client.mode == instructor.Mode.ANTHROPIC_TOOLS


def test_extraction_treats_a_blank_key_as_the_providers_own(project_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    provider = create_extraction_provider(ExtractionConfig(api_key=SecretStr("")))

    with patch("instructor.from_provider") as factory:
        provider._ensure_client()

    assert factory.call_args.kwargs == {"async_client": True, "api_key": "environment-key"}


def test_answerability_builds_its_client_through_the_shared_helper(project_env):
    project_env.write_text("OPENAI_API_KEY=file-key\n")
    evaluator = AnswerabilityEvaluator(
        AnswerabilityConfig(provider="openai", model="example", base_url="https://gateway.invalid/v1/")
    )

    with patch("instructor.from_provider") as factory:
        first = evaluator._ensure_client()
        second = evaluator._ensure_client()

    assert first is second
    factory.assert_called_once_with(
        "openai/example",
        async_client=True,
        api_key="file-key",
        base_url="https://gateway.invalid/v1",
    )


def test_answerability_keeps_an_explicit_empty_key(project_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    evaluator = AnswerabilityEvaluator(
        AnswerabilityConfig(api_key=SecretStr(""), base_url="http://gateway.invalid/v1")
    )

    with patch("instructor.from_provider") as factory:
        evaluator._ensure_client()

    assert factory.call_args.kwargs["api_key"] == ""
