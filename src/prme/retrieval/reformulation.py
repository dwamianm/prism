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

This mirrors :mod:`prme.retrieval.abstention`: a self-contained module with a
cached instructor client, provider/model parameters, and a safe fallback (an
empty alternative list) when the provider is unconfigured or the call fails, so
reformulation never breaks an otherwise-working retrieval.

Usage::

    from prme.retrieval.reformulation import reformulate_query

    alternatives = await reformulate_query(
        query, provider="openai", model="gpt-4o-mini", count=2
    )
    # alternatives is a list[str]; empty on failure -- caller falls back to
    # the original query alone.
"""

from __future__ import annotations

import logging

import instructor
from pydantic import BaseModel, Field

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


_client_cache: dict[str, instructor.AsyncInstructor] = {}


def _get_client(provider_string: str) -> instructor.AsyncInstructor:
    """Get or create a cached instructor async client."""
    if provider_string not in _client_cache:
        _client_cache[provider_string] = instructor.from_provider(
            provider_string, async_client=True
        )
    return _client_cache[provider_string]


async def reformulate_query(
    query: str,
    *,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    count: int = 2,
    max_retries: int = 2,
) -> list[str]:
    """Generate alternative search queries for improved recall.

    Uses an LLM to produce ``count`` alternative phrasings of ``query``. The
    pipeline runs each alternative as an additional retrieval pass and merges
    the results (deduplicated by node id) with the original query's results.

    Returns an empty list on any failure (unconfigured provider, network
    error, validation error) so the caller can fall back to the original
    query alone -- reformulation never breaks retrieval.

    Args:
        query: The original query text.
        provider: LLM provider name (default: "openai").
        model: LLM model name (default: "gpt-4o-mini").
        count: Maximum number of alternative queries to return. Values are
            clamped to ``[1, 5]``; the LLM may return fewer.
        max_retries: Max retries on LLM schema-validation failure.

    Returns:
        A list of alternative query strings (at most ``count``). Empty on
        failure. The original ``query`` is never included.
    """
    if not query or not query.strip():
        return []

    count = max(1, min(count, 5))
    provider_string = f"{provider}/{model}"
    try:
        client = _get_client(provider_string)
        result = await client.create(
            response_model=QueryReformulations,
            messages=[
                {"role": "system", "content": REFORMULATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Generate {count} alternative search queries.\n\n"
                        f"Original question: {query}"
                    ),
                },
            ],
            max_retries=max_retries,
        )
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
