"""LLM-based multi-query reformulation for the retrieval pipeline.

Stage 1 query analysis is rule-based and produces a single query per request
(no blocking LLM calls, per RFC-0005 S3). For recall-sensitive workloads a
single phrasing can miss facts that are only reachable under different keywords
or from the perspective of the answer rather than the question.

This module provides an *opt-in* reformulation step: given the original query
it asks an LLM for a small number of alternative phrasings. The pipeline runs
each alternative as an additional retrieval pass and merges the results,
deduplicated by node id. It is off by default (``enable_query_reformulation``)
because it adds one LLM call per ``retrieve()`` and requires a configured
provider.

The module caches instructor clients, takes provider/model parameters, and
falls back safely (an empty alternative list) when the provider is
unconfigured, the call fails or it runs past its timeout, so reformulation
never breaks an otherwise-working retrieval. Clients are built by
:func:`prme.model_runtime.create_instructor_client`, the same construction
extraction uses, so a configured endpoint and credential, Ollama's JSON mode
and Anthropic endpoints apply here too.

Usage::

    from prme.retrieval.reformulation import reformulate_query

    alternatives = await reformulate_query(
        query, provider="openai", model="gpt-4o-mini", count=2
    )
    # alternatives is a list[str]; empty on failure -- caller falls back to
    # the original query alone.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import instructor
from pydantic import BaseModel, Field, SecretStr

from prme.model_runtime import create_instructor_client, resolve_provider_connection

logger = logging.getLogger(__name__)

REFORMULATION_SYSTEM_PROMPT = """\
You are a search query reformulator. Given a question about a stored \
conversation or knowledge history, generate alternative search queries that \
would help find the relevant information.

Rules:
- Each reformulation should use different keywords and phrasing than the \
original.
- Focus on the key entities (people, places, things) and actions mentioned.
- Include at least one simple keyword-style query (2-4 words).
- Include at least one query phrased toward the likely ANSWER rather than the \
question (e.g., for "Where did X move from?" try "X home country").
- Keep each reformulation under 30 words.
- Do not repeat the original question verbatim.\
"""


class QueryReformulations(BaseModel):
    """Structured output for query reformulation."""

    queries: list[str] = Field(
        description="Alternative search queries",
        default_factory=list,
    )


# Clients are keyed by the connection their settings resolve to, including a
# credential or endpoint read from the environment or .env, so a client built
# for one endpoint or credential never serves another. The key is held as a
# SecretStr, which compares by value and masks its repr.
ConnectionKey = tuple[str, str | None, SecretStr | None]

# Process-wide cache for callers that do not pass their own. An async HTTP
# client belongs to the event loop that opened its connections, so callers that
# run several loops (the retrieval pipeline among them) pass a cache they own.
_client_cache: dict[ConnectionKey, instructor.AsyncInstructor] = {}


def _get_client(
    provider_string: str,
    *,
    api_key: SecretStr | None = None,
    base_url: str | None = None,
    cache: dict[ConnectionKey, instructor.AsyncInstructor] | None = None,
) -> instructor.AsyncInstructor:
    """Get or create the cached instructor async client for this connection."""
    key, url = resolve_provider_connection(provider_string, api_key=api_key, base_url=base_url)
    credential = SecretStr(key) if key is not None else None
    clients = _client_cache if cache is None else cache
    connection = (provider_string, url, credential)
    client = clients.get(connection)
    if client is None:
        client = create_instructor_client(provider_string, api_key=credential, base_url=url)
        clients[connection] = client
    return client


async def reformulate_query(
    query: str,
    *,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    count: int = 2,
    max_retries: int = 2,
    api_key: SecretStr | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    client_cache: dict[ConnectionKey, instructor.AsyncInstructor] | None = None,
) -> list[str]:
    """Generate alternative search queries for improved recall.

    Uses an LLM to produce ``count`` alternative phrasings of ``query``. The
    pipeline runs each alternative as an additional retrieval pass and merges
    the results (deduplicated by node id) with the original query's results.

    Returns an empty list on any failure (unconfigured provider, network
    error, validation error, timeout) so the caller can fall back to the
    original query alone -- reformulation never breaks retrieval.

    Args:
        query: The original query text.
        provider: LLM provider name (default: "openai").
        model: LLM model name (default: "gpt-4o-mini").
        count: Maximum number of alternative queries to return. Values are
            clamped to ``[1, 5]``; the LLM may return fewer.
        max_retries: Max retries on LLM schema-validation failure.
        api_key: Optional credential. When omitted or blank (as with a
            blank extraction key), OpenAI and Anthropic use their own
            ``<PROVIDER>_API_KEY`` from the environment or ``.env``.
        base_url: Optional endpoint. When omitted, OpenAI and Anthropic use
            ``<PROVIDER>_BASE_URL`` from the environment or ``.env``, and
            Ollama uses its local default.
        timeout: Optional limit in seconds for the whole call, including
            schema-validation retries. A call that runs longer counts as a
            failure and returns an empty list. ``None`` leaves the call to
            the provider SDK's own limits.
        client_cache: Optional mapping that keeps the clients this call
            builds. Pass one per event loop; the default is shared by the
            whole process.

    Returns:
        A list of alternative query strings (at most ``count``). Empty on
        failure. The original ``query`` is never included.
    """
    if not query or not query.strip():
        return []

    count = max(1, min(count, 5))
    provider_string = f"{provider}/{model}"
    create_kwargs: dict[str, Any] = {
        "response_model": QueryReformulations,
        "messages": [
            {"role": "system", "content": REFORMULATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Generate {count} alternative search queries.\n\n"
                    f"Original question: {query}"
                ),
            },
        ],
        "max_retries": max_retries,
        # Some clients (Bedrock) are not bound to a model when they are built.
        "model": model,
    }
    if provider.strip().casefold() == "ollama":
        # As in extraction: keep reasoning traces from using up the structured
        # response window.
        create_kwargs["reasoning_effort"] = "none"
    try:
        client = _get_client(
            provider_string,
            api_key=api_key or None,
            base_url=base_url,
            cache=client_cache,
        )
        result = await asyncio.wait_for(client.create(**create_kwargs), timeout=timeout)
    except TimeoutError:
        logger.warning(
            "Query reformulation timed out after %ss, falling back to original query only",
            timeout,
        )
        return []
    except Exception:
        logger.warning(
            "Query reformulation failed, falling back to original query only",
            exc_info=True,
        )
        return []

    # Drop blanks and any echo of the original query; cap at the request count.
    original_norm = query.strip().casefold()
    alternatives: list[str] = []
    seen: set[str] = set()
    for alt in result.queries:
        if not isinstance(alt, str):
            continue
        candidate = alt.strip()
        if not candidate:
            continue
        norm = candidate.casefold()
        if norm == original_norm or norm in seen:
            continue
        seen.add(norm)
        alternatives.append(candidate)
        if len(alternatives) >= count:
            break
    return alternatives
