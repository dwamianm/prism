"""The Ollama vision provider's request shape and error mapping (RFC-0018, phase 1)."""

from __future__ import annotations

import httpx
import ollama
import pytest

from prme.config import VisionConfig
from prme.vision.errors import VisionError
from prme.vision.provider import OllamaVisionProvider, VisionProvider, create_vision_provider


class FakeClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests: list[dict] = []

    async def chat(self, **request):
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {"message": {"content": self.outcome}}


def provider_with(outcome) -> tuple[OllamaVisionProvider, FakeClient]:
    provider = OllamaVisionProvider("qwen3.5:9b", base_url="http://127.0.0.1:11434", timeout=5)
    client = FakeClient(outcome)
    provider._client = client
    return provider, client


async def test_request_pins_sampling_and_sends_the_image_bytes():
    provider, client = provider_with("  A photo of a receipt. The total is \"$8.40\".  ")
    assert provider.model_name == "ollama/qwen3.5:9b"
    assert isinstance(provider, VisionProvider)
    text = await provider.describe(b"\xff\xd8\xff", prompt="Describe it.")
    assert text == 'A photo of a receipt. The total is "$8.40".'
    (request,) = client.requests
    assert request["model"] == "qwen3.5:9b" and request["think"] is False
    assert request["options"] == {"temperature": 0, "seed": 0}
    assert request["messages"] == [{"role": "user", "content": "Describe it.", "images": [b"\xff\xd8\xff"]}]


async def test_server_errors_become_vision_errors():
    provider, _ = provider_with(ollama.ResponseError("model 'qwen3.5:9b' not found", 404))
    with pytest.raises(VisionError, match="not found"):
        await provider.describe(b"x", prompt="p")
    provider, _ = provider_with(httpx.ConnectError("connection refused"))
    with pytest.raises(VisionError, match="Cannot reach the Ollama server"):
        await provider.describe(b"x", prompt="p")
    provider, _ = provider_with("   ")
    with pytest.raises(VisionError, match="empty description"):
        await provider.describe(b"x", prompt="p")


def test_create_vision_provider_knows_ollama_only():
    provider = create_vision_provider(VisionConfig(model="llava:7b", base_url="http://host:1"))
    assert isinstance(provider, OllamaVisionProvider) and provider.model_name == "ollama/llava:7b"
    with pytest.raises(ValueError, match="Unknown vision provider"):
        create_vision_provider(VisionConfig(provider="openai"))
    with pytest.raises(ValueError, match="model name"):
        OllamaVisionProvider("  ")
