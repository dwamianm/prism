"""Vision providers: a model that describes one image in plain text (RFC-0018).

A provider is called once per keyframe, at admission, like extraction. Its
output becomes the observation event's content and is never recomputed by a
rebuild or a replay. This release ships an Ollama provider, which runs
against a local server by default; a hosted vision provider would be a
separate explicit option, as it is for extraction.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from prme.vision.errors import VisionError

if TYPE_CHECKING:
    from prme.config import VisionConfig

logger = logging.getLogger(__name__)


@runtime_checkable
class VisionProvider(Protocol):
    """Describes one image. ``model_name`` is recorded on every observation."""

    @property
    def model_name(self) -> str:
        """Return the full model identifier, for example ``ollama/qwen3.5:9b``."""
        ...

    async def describe(self, image: bytes, *, prompt: str) -> str:
        """Return the model's plain-text description of *image* (an encoded image file) under *prompt*."""
        ...


class OllamaVisionProvider:
    """A vision model served by Ollama.

    The client is created on first use, so a provider can be built without a
    reachable server. Sampling is pinned (temperature 0, seed 0) and thinking
    is off, so a description depends on the frame, the model and the prompt
    as far as the server allows.
    """

    def __init__(self, model: str, *, base_url: str | None = None, timeout: float = 180.0) -> None:
        if not model.strip():
            raise ValueError("A vision model name is required")
        self._model = model
        self._base_url = base_url
        self._timeout = timeout
        self._client = None

    @property
    def model_name(self) -> str:
        return f"ollama/{self._model}"

    def _ensure_client(self):
        if self._client is None:
            import ollama

            self._client = ollama.AsyncClient(host=self._base_url, timeout=self._timeout)
        return self._client

    async def describe(self, image: bytes, *, prompt: str) -> str:
        import httpx
        import ollama

        client = self._ensure_client()
        try:
            response = await client.chat(
                model=self._model,
                messages=[{"role": "user", "content": prompt, "images": [image]}],
                think=False,
                options={"temperature": 0, "seed": 0},
            )
        except ollama.ResponseError as exc:
            raise VisionError(
                f"Vision model {self._model} failed at {self._base_url or 'the default Ollama host'}: "
                f"{exc.error or exc}"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise VisionError(
                f"Cannot reach the Ollama server at {self._base_url or 'the default host'} "
                f"for vision model {self._model}: {type(exc).__name__}: {exc}"
            ) from exc
        text = response["message"]["content"].strip()
        if not text:
            raise VisionError(f"Vision model {self._model} returned an empty description")
        return text


def create_vision_provider(config: VisionConfig) -> VisionProvider:
    """Build the provider that *config* names."""
    provider = config.provider.strip().casefold()
    if provider == "ollama":
        return OllamaVisionProvider(config.model, base_url=config.base_url, timeout=config.timeout)
    raise ValueError(f"Unknown vision provider {config.provider!r}; this release supports 'ollama'")
