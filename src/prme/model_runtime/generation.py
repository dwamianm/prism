"""Instructor client construction shared by PRME's generation callers.

Extraction, answerability and query reformulation all call a generation model
through Instructor. Building each client here keeps one rule for where a
request goes and which credential it carries:

* An explicit ``api_key`` or ``base_url`` wins. An explicit key is sent as
  given, even when it is empty; pass ``None`` to use the provider's own
  settings.
* For OpenAI and Anthropic, the provider's own ``<PROVIDER>_API_KEY`` and
  ``<PROVIDER>_BASE_URL`` come next, from the process environment and then the
  ``.env`` file in the current working directory. Only the selected
  provider's names are read, and nothing is written back to ``os.environ``.
* Ollama uses Instructor's JSON mode. Its constrained structured output returns
  JSON in the message content, and tool mode can return that same JSON without
  a tool envelope, which Instructor rejects.
* Anthropic with an endpoint gets a client built for that endpoint. Instructor
  1.14 creates Anthropic clients without the ``base_url`` it is given and
  passes it to every ``messages.create`` call instead, which fails.

Timeouts, retries and sampling stay with each caller, since they differ by
task.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import instructor
    from pydantic import SecretStr

_PROVIDER_ENVIRONMENT_PREFIX = {"openai": "OPENAI", "anthropic": "ANTHROPIC"}
# instructor.from_provider's default response budget for Anthropic.
_ANTHROPIC_MAX_TOKENS = 4096


def resolve_provider_connection(
    provider_string: str,
    *,
    api_key: SecretStr | None = None,
    base_url: str | None = None,
) -> tuple[str | None, str | None]:
    """Return the credential and endpoint a client for this provider will use.

    ``None`` means the provider SDK's own default applies (for Ollama, a local
    server and a placeholder key).
    """
    from dotenv import dotenv_values

    key = api_key.get_secret_value() if api_key is not None else None
    url = base_url or None
    prefix = _PROVIDER_ENVIRONMENT_PREFIX.get(_provider_name(provider_string))
    if prefix:
        # SDK defaults read process variables but do not load .env. Resolve
        # only the selected provider's settings, without mutating os.environ.
        local = dotenv_values(".env")
        key_name, url_name = f"{prefix}_API_KEY", f"{prefix}_BASE_URL"
        if api_key is None:
            key = os.environ.get(key_name) or local.get(key_name) or None
        url = url or os.environ.get(url_name) or local.get(url_name) or None
    return key, url


def create_instructor_client(
    provider_string: str,
    *,
    api_key: SecretStr | None = None,
    base_url: str | None = None,
) -> instructor.AsyncInstructor:
    """Create an async Instructor client for a ``provider/model`` string.

    Args:
        provider_string: Instructor provider and model, such as
            ``"openai/gpt-4o-mini"`` or ``"ollama/llama3.2"``.
        api_key: Optional credential that overrides the provider's
            environment and ``.env`` settings.
        base_url: Optional endpoint that overrides the provider's environment
            and ``.env`` settings.
    """
    import instructor

    provider = _provider_name(provider_string)
    key, url = resolve_provider_connection(
        provider_string, api_key=api_key, base_url=base_url
    )
    if provider == "anthropic" and url:
        import anthropic

        return instructor.from_anthropic(
            anthropic.AsyncAnthropic(api_key=key, base_url=url),
            model=provider_string.split("/", 1)[1],
            mode=instructor.Mode.ANTHROPIC_TOOLS,
            max_tokens=_ANTHROPIC_MAX_TOKENS,
        )

    kwargs: dict[str, Any] = {}
    if key is not None:
        kwargs["api_key"] = key
    if url:
        kwargs["base_url"] = url
    if provider == "ollama":
        kwargs["mode"] = instructor.Mode.JSON
    return instructor.from_provider(provider_string, async_client=True, **kwargs)


def _provider_name(provider_string: str) -> str:
    return provider_string.split("/", 1)[0].strip().casefold()
