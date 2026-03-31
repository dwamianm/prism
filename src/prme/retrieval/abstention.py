"""LLM-based abstention detection for the retrieval pipeline.

When the retrieval system returns topically related but specifically
irrelevant results (e.g., query asks about "table tennis" but context
only mentions "tennis"), composite scores remain high and score-based
abstention fails. This module uses an LLM to assess whether the
retrieved context actually answers the specific question.

Usage::

    from prme.retrieval.abstention import should_abstain

    response = await engine.retrieve(query, user_id=uid)
    context = format_for_llm(results=response.results, query=query)
    if await should_abstain(query, context, provider="openai", model="gpt-4o-mini"):
        # Context doesn't answer the question — abstain
        ...
"""

from __future__ import annotations

import logging

import instructor
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ABSTENTION_SYSTEM_PROMPT = """\
You are a relevance judge. Given a question and retrieved memory context, \
determine whether the context contains sufficient specific information to \
answer the question.

Return can_answer=true ONLY if the context contains the specific facts, \
names, dates, or numbers needed to directly answer the question.

Return can_answer=false if:
- The context is topically related but does not contain the specific \
information asked about (e.g., question asks about "table tennis" but \
context only mentions "tennis"; question asks about "Sacramento" but \
context only mentions "San Francisco")
- The context contains similar but different facts (e.g., "bus" vs "train", \
"vintage cameras" vs "vintage films", "tomato plants" vs "chili peppers", \
"managing engineers" vs "being an engineer")
- The question asks about a specific role, event, or activity that is not \
explicitly mentioned in the context, even if related roles/events exist
- The context lacks key details needed to compute the answer (e.g., missing \
dates, counts, or amounts)
- The question asks about a specific entity (university, company, person) \
and that exact entity is not mentioned in the context
- There is no relevant context at all

Common false positive traps — return can_answer=false for these:
- Question asks about "bus" but context only mentions "train" or "taxi"
- Question asks about "leading/managing a team" but context only mentions \
being a team member or individual contributor
- Question asks about a specific event at a specific place, but context \
only mentions the place or the event type separately

Be strict: topical similarity is NOT sufficient. The specific answer must \
be derivable from the context.\
"""


class _AbstentionCheck(BaseModel):
    """Structured output for abstention relevance check."""

    reasoning: str = Field(
        description="Brief reasoning about whether the context answers the question"
    )
    can_answer: bool = Field(
        description="True if the context contains specific information to answer the question"
    )


_client_cache: dict[str, instructor.AsyncInstructor] = {}


def _get_client(provider_string: str) -> instructor.AsyncInstructor:
    """Get or create a cached instructor async client."""
    if provider_string not in _client_cache:
        _client_cache[provider_string] = instructor.from_provider(
            provider_string, async_client=True
        )
    return _client_cache[provider_string]


async def should_abstain(
    query: str,
    context: str,
    *,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    max_retries: int = 2,
) -> bool:
    """Check if the system should abstain from answering a query.

    Uses an LLM to assess whether the retrieved context contains
    sufficient specific information to answer the question. This catches
    cases where retrieval returns topically related but specifically
    irrelevant results that score-based thresholds cannot detect.

    Args:
        query: The user's question.
        context: Retrieved context formatted for LLM consumption
            (e.g., output of ``format_for_llm``).
        provider: LLM provider name (default: "openai").
        model: LLM model name (default: "gpt-4o-mini").
        max_retries: Max retries on LLM failure.

    Returns:
        True if the system should abstain (context doesn't answer
        the question), False if the context can answer it.
    """
    provider_string = f"{provider}/{model}"
    try:
        client = _get_client(provider_string)
        result = await client.create(
            response_model=_AbstentionCheck,
            messages=[
                {"role": "system", "content": ABSTENTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {query}",
                },
            ],
            max_retries=max_retries,
        )
        return not result.can_answer
    except Exception:
        logger.warning(
            "Abstention check failed, defaulting to not abstain",
            exc_info=True,
        )
        return False
