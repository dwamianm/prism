"""Documented project configuration works without process-global mutation."""

import os
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from prme.config import ExtractionConfig, PRMEConfig
from prme.ingestion.extraction import create_extraction_provider


@pytest.fixture
def project_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("PRME_", "OPENAI_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    return tmp_path / ".env"


def test_documented_flat_and_nested_settings_load_from_project_file(project_env):
    project_env.write_text(
        "PRME_EXTRACTION_PROVIDER=ollama\nPRME_EXTRACTION_MODEL=example-model\n"
        "PRME_MATERIALIZATION_BUDGET_MS=17\nPRME_PACKING__TOKEN_BUDGET=777\n"
        "OPENAI_API_KEY=fixture-only-secret\nUNRELATED_APPLICATION_SETTING=ignored\n"
    )
    config = PRMEConfig()
    assert config.extraction.provider == "ollama"
    assert config.extraction.model == "example-model"
    assert config.packing.token_budget == 777
    assert config.materialization_budget_ms == 17
    assert "OPENAI_API_KEY" not in os.environ


def test_constructor_then_environment_then_file_precedence(project_env, monkeypatch):
    project_env.write_text("PRME_EXTRACTION_MODEL=from-file\n")
    assert ExtractionConfig().model == "from-file"
    monkeypatch.setenv("PRME_EXTRACTION_MODEL", "from-environment")
    assert ExtractionConfig().model == "from-environment"
    assert ExtractionConfig(model="from-constructor").model == "from-constructor"


def test_only_selected_provider_credentials_are_loaded(project_env):
    project_env.write_text("OPENAI_API_KEY=openai-fixture\nANTHROPIC_API_KEY=anthropic-fixture\n"
                          "ANTHROPIC_BASE_URL=https://example.invalid/anthropic\n")
    provider = create_extraction_provider(ExtractionConfig(provider="anthropic", model="example"))
    with patch("instructor.from_provider") as factory:
        provider._ensure_client()
    assert factory.call_args.kwargs == {
        "async_client": True, "api_key": "anthropic-fixture",
        "base_url": "https://example.invalid/anthropic",
    }
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_explicit_provider_credentials_override_environment_without_leaking_in_config(project_env, monkeypatch):
    project_env.write_text("OPENAI_API_KEY=file-fixture\nPRME_EXTRACTION_API_KEY=prme-file-fixture\n")
    monkeypatch.setenv("OPENAI_API_KEY", "env-fixture")
    config = ExtractionConfig(api_key=SecretStr("constructor-fixture"), base_url="https://example.invalid/openai")
    provider = create_extraction_provider(config)
    with patch("instructor.from_provider") as factory:
        provider._ensure_client()
    assert factory.call_args.kwargs["api_key"] == "constructor-fixture"
    assert factory.call_args.kwargs["base_url"] == "https://example.invalid/openai"
    assert "constructor-fixture" not in config.model_dump_json()
    assert os.environ["OPENAI_API_KEY"] == "env-fixture"


def test_sdk_environment_overrides_provider_file(project_env, monkeypatch):
    project_env.write_text("OPENAI_API_KEY=file-fixture\n")
    monkeypatch.setenv("OPENAI_API_KEY", "env-fixture")
    provider = create_extraction_provider(ExtractionConfig())
    with patch("instructor.from_provider") as factory:
        provider._ensure_client()
    assert factory.call_args.kwargs["api_key"] == "env-fixture"


def test_ollama_accepts_an_explicit_endpoint_without_cloud_credentials(project_env):
    project_env.write_text("OPENAI_API_KEY=unrelated-cloud-fixture\n")
    provider = create_extraction_provider(ExtractionConfig(
        provider="ollama", model="example", base_url="http://localhost:22434/v1",
    ))
    with patch("instructor.from_provider") as factory:
        provider._ensure_client()
    assert factory.call_args.kwargs == {"async_client": True, "base_url": "http://localhost:22434/v1"}
