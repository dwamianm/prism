"""Documented project configuration works without process-global mutation."""

import os
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from prme.config import APIConfig, ExtractionConfig, PRMEConfig
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


def test_extraction_temperature_defaults_to_deterministic_and_loads_from_env(
    project_env, monkeypatch
):
    assert ExtractionConfig().temperature == 0.0
    monkeypatch.setenv("PRME_EXTRACTION_TEMPERATURE", "0.25")
    assert ExtractionConfig().temperature == pytest.approx(0.25)


@pytest.mark.parametrize("temperature", [-0.01, 2.01, float("nan")])
def test_extraction_temperature_rejects_invalid_values(project_env, temperature):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExtractionConfig(temperature=temperature)


def test_constructor_typos_remain_errors_with_a_shared_dotenv_file(project_env):
    from pydantic import ValidationError

    project_env.write_text("UNRELATED_SETTING=ignored\nOPENAI_API_KEY=fixture-only\n")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExtractionConfig(modle="typo")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PRMEConfig(materializaton_budget_ms=17)


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


def test_server_credentials_are_redacted_in_config_and_repr(project_env):
    config = PRMEConfig(api=APIConfig(api_key="server-fixture-secret"))
    assert config.api.api_key.get_secret_value() == "server-fixture-secret"
    assert "server-fixture-secret" not in config.model_dump_json()
    assert "server-fixture-secret" not in repr(config)


@pytest.mark.parametrize("settings,expected", [
    ("PRME_EXTRACTION_PROVIDER=ollama\n", "Local Ollama extraction selected"),
    ("OPENAI_API_KEY=doctor-fixture-secret\n", "openai extraction credential configured"),
    ("PRME_EXTRACTION_PROVIDER=anthropic\nOPENAI_API_KEY=doctor-fixture-secret\n", "No credential found for anthropic"),
])
async def test_doctor_reports_selected_provider_without_printing_secrets(project_env, capsys, settings, expected):
    from argparse import Namespace
    from prme.cli import cmd_doctor

    project_env.write_text(settings)
    await cmd_doctor(Namespace(directory=str(project_env.parent)))
    output = capsys.readouterr().out
    assert expected in output
    assert "doctor-fixture-secret" not in output


@pytest.mark.parametrize("name,provider", [
    ("OPENAI_API_KEY", "openai"),
    ("OPENAI_BASE_URL", "openai"),
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("PRME_EXTRACTION_API_KEY", "openai"),
    ("PRME_EXTRACTION_BASE_URL", "ollama"),
    ("prme_extraction_api_key", "openai"),
])
async def test_doctor_identifies_shadowed_settings_without_values_or_requests(
    project_env, monkeypatch, capsys, name, provider,
):
    from argparse import Namespace
    from prme.cli import cmd_doctor

    project_env.write_text(f"PRME_EXTRACTION_PROVIDER={provider}\n{name}=file-secret-sentinel\n")
    monkeypatch.setenv(name, "process-secret-sentinel")
    original_environment = dict(os.environ)
    original_file = project_env.read_bytes()
    with patch("instructor.from_provider", side_effect=AssertionError("Doctor must be offline")):
        await cmd_doctor(Namespace(directory=str(project_env.parent)))
    output = capsys.readouterr().out
    assert f"{name.upper()} in the process environment overrides a different value in .env" in output
    assert "file-secret-sentinel" not in output
    assert "process-secret-sentinel" not in output
    assert dict(os.environ) == original_environment
    assert project_env.read_bytes() == original_file


@pytest.mark.parametrize("settings,process_value", [
    ("OPENAI_API_KEY=same-secret-sentinel\n", "same-secret-sentinel"),
    ("PRME_EXTRACTION_PROVIDER=anthropic\nOPENAI_API_KEY=file-secret-sentinel\n", "process-secret-sentinel"),
    ("PRME_EXTRACTION_API_KEY=override-secret-sentinel\nOPENAI_API_KEY=file-secret-sentinel\n", "process-secret-sentinel"),
])
async def test_doctor_does_not_warn_about_identical_or_unused_sdk_keys(
    project_env, monkeypatch, capsys, settings, process_value,
):
    from argparse import Namespace
    from prme.cli import cmd_doctor

    project_env.write_text(settings)
    monkeypatch.setenv("OPENAI_API_KEY", process_value)
    await cmd_doctor(Namespace(directory=str(project_env.parent)))
    output = capsys.readouterr().out
    assert "overrides a different value" not in output
    assert "secret-sentinel" not in output
