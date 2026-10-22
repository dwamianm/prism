"""The hosted extraction model, for the one benchmark baseline that needs it.

Every benchmark here extracts with a local Ollama model, so a build sends no text off the machine and spends nothing.
The fast cycle's hosted baseline is the single exception: it measures the model the hosted service extracts with, so
that work meant for the hosted service is judged against the model it will run on and not only against the DeepSeek
baseline. A run that is not a replay of the committed cache calls a paid model, so three things keep it from starting by
accident, and CI never sets any of them:

- ``PRME_BENCH_HOSTED=1`` must be set. Without it nothing in this module changes what a build does.
- ``PRME_BENCH_HOSTED_MODEL_ID`` names the model. The id is the one the hosted service is configured with; it is not
  kept in this repository.
- Every request is counted, and one that could pass ``PRME_BENCH_HOSTED_MAX_REQUESTS`` (default 400) or
  ``PRME_BENCH_HOSTED_MAX_TOKENS`` (default 2,500,000, input and output together) is refused before it is sent. A cold
  fast cycle on the three stages takes about 240 requests and 1.6 million tokens. ``PRME_BENCH_HOSTED_LEDGER`` names a
  JSON lines file that gets one line per request, with its tokens.

``PRME_BENCH_CACHE_ONLY=1`` refuses every live request, so replaying the committed cache spends nothing and needs no
credentials. The request goes through the AWS SDK with its default credential chain and region (``AWS_PROFILE``,
``AWS_DEFAULT_REGION``); the SDK is not a dependency of this project, so run with ``uv run --with boto3``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from prme.config import ExtractionConfig, PRMEConfig

FLAG = "PRME_BENCH_HOSTED"
MODEL_ENV = "PRME_BENCH_HOSTED_MODEL_ID"
REQUESTS_ENV = "PRME_BENCH_HOSTED_MAX_REQUESTS"
TOKENS_ENV = "PRME_BENCH_HOSTED_MAX_TOKENS"
LEDGER_ENV = "PRME_BENCH_HOSTED_LEDGER"
DEFAULT_MAX_REQUESTS = 400
DEFAULT_MAX_TOKENS = 2_500_000
# The label reports and the cache use for the model; the real id is never written to a report.
LABEL = "hosted"
# A response a request may make when it names no limit, for the check before it is sent.
_UNSET_MAX_OUTPUT = 8192


class HostedBudgetExceeded(BaseException):
    """A BaseException, so no retry loop or handler between the request and the build's top swallows it."""


def enabled() -> bool:
    return os.environ.get(FLAG) == "1"


def model_id() -> str:
    value = os.environ.get(MODEL_ENV, "").strip()
    if not value:
        raise ValueError(f"{FLAG}=1 needs {MODEL_ENV}: the model id the hosted service extracts with")
    return value


def identity(model: str = LABEL) -> dict:
    """What a build records about the model, in the shape the Ollama identity has."""
    model_id()
    return {"provider": "hosted", "model": model, "server_version": "hosted", "remote_host": None,
            "remote_model": None, "digest": "hosted"}


def extraction_config(timeout: float) -> ExtractionConfig:
    """The extraction settings for the hosted model, isolated from the environment as the Ollama ones are."""
    hosted = model_id()
    with patch.dict(os.environ, {}, clear=True):
        return ExtractionConfig(_env_file=None, provider="bedrock", model=hosted,  # type: ignore[call-arg]
                                timeout=timeout, batch_timeout=timeout)


def build_config(config: PRMEConfig, timeout: float) -> PRMEConfig:
    """``config`` extracting through the hosted model, with every request counted and limited."""
    extraction = extraction_config(timeout)
    install_meter()
    return config.model_copy(update={"extraction": extraction})


class Meter:
    """Requests and tokens spent in this process, against the limits the environment sets."""

    def __init__(self) -> None:
        self.requests = self.tokens = 0

    @staticmethod
    def _limit(name: str, default: int) -> int:
        return int(os.environ.get(name) or default)

    def check(self, kwargs: dict) -> None:
        """Refuse a request that could pass a limit, judging its worst case: three characters to a token in, the
        longest answer it allows out."""
        text = json.dumps({key: kwargs.get(key) for key in ("messages", "system", "toolConfig")}, default=str)
        worst = len(text) // 3 + 1 + ((kwargs.get("inferenceConfig") or {}).get("maxTokens") or _UNSET_MAX_OUTPUT)
        requests, tokens = self._limit(REQUESTS_ENV, DEFAULT_MAX_REQUESTS), self._limit(TOKENS_ENV, DEFAULT_MAX_TOKENS)
        if self.requests + 1 > requests or self.tokens + worst > tokens:
            raise HostedBudgetExceeded(
                f"{self.requests} requests and {self.tokens} tokens spent; this request could add {worst} tokens, "
                f"past {REQUESTS_ENV}={requests} or {TOKENS_ENV}={tokens}")

    def record(self, response: dict) -> None:
        usage = response.get("usage") or {}
        sent, received = int(usage.get("inputTokens") or 0), int(usage.get("outputTokens") or 0)
        self.requests += 1
        self.tokens += sent + received
        ledger = os.environ.get(LEDGER_ENV)
        if ledger:
            with Path(ledger).open("a") as out:
                out.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tokens_in": sent,
                                      "tokens_out": received, "requests": self.requests, "tokens": self.tokens}) + "\n")


METER = Meter()
_installed = False


def install_meter() -> None:
    """Count and limit every request the AWS SDK's runtime client sends. Idempotent."""
    global _installed
    if _installed:
        return
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("The hosted model needs the AWS SDK: run with `uv run --with boto3`") from exc
    original = boto3.client

    def client(service_name, *args, **kwargs):
        made = original(service_name, *args, **kwargs)
        if service_name == "bedrock-runtime":
            converse = made.converse

            def counted(**request):
                METER.check(request)
                response = converse(**request)
                METER.record(response)
                return response

            made.converse = counted
        return made

    boto3.client = client
    _installed = True
