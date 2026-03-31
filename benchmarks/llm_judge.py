"""LLM generation and judging for benchmark evaluation.

Provides two core functions:
  - generate_answer: Given retrieval context + query, generate an answer via LLM.
  - judge_answer: LLM-as-judge scoring of generated vs expected answer.

Uses the same instructor + provider pattern as prme.ingestion.extraction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import instructor
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

GENERATION_SYSTEM_PROMPT = """\
Answer the question using the provided context. Be concise and direct.

TEMPORAL REASONING — STEP BY STEP:
1. When the context contains timestamps, first list all relevant dated events.
2. For relative time references ("yesterday", "last week", "a few weeks ago", \
"last Friday"), compute the actual date by applying the offset to the message \
timestamp. If the context includes a "COMPUTED:" line with the target date, \
use it directly.
3. For "how many days between X and Y" questions, identify exact dates for both \
events, then subtract. Show your work: "Event A: March 5, Event B: April 2, \
difference = 28 days."
4. For ordering questions ("which came first?"), list each event with its date \
before answering.
5. For counting questions ("how many events before X?"), list ALL matching events \
with dates, then count them.

INFERENCE: When the question asks about preferences, likely behaviors, opinions, \
political leanings, religious beliefs, or personality traits, make reasonable \
inferences from evidence in the context. Consider:
- Stated values, activities, and social circles
- Cultural indicators, community involvement, lifestyle choices
- Explicit statements and implicit patterns
For example, someone who regularly volunteers at progressive causes and advocates \
for social justice likely leans liberal. State your inference with the supporting \
evidence.

AGGREGATION: When asked "how many", "how much total", or "list all":
1. Scan the ENTIRE context for ALL instances, not just the first few.
2. List each instance explicitly.
3. Sum or count after listing.

If the context contains no relevant information at all, say "I don't know".
Do not fabricate specific facts, names, dates, or numbers that aren't \
supported by the context.\
"""

JUDGE_SYSTEM_PROMPT = """\
You are an impartial judge evaluating answer quality. Compare the generated \
answer against the expected answer and score on a scale of 0.0 to 1.0.

Scoring guidelines:
- 1.0: The generated answer fully matches the expected answer in meaning.
- 0.7-0.9: The answer is mostly correct with minor differences or extra detail.
- 0.4-0.6: The answer is partially correct, capturing some key information.
- 0.1-0.3: The answer is mostly wrong but has a small relevant element.
- 0.0: The answer is completely wrong, irrelevant, or says "I don't know" \
when an answer was expected.

Additional guidelines:
- If the expected answer requires inference and the generated answer makes a \
reasonable inference from context that aligns with the expected answer, \
score 0.7-0.9 even if the wording differs.
- For temporal questions, if the generated answer has the correct computed \
date (accounting for relative offsets), score 1.0 regardless of format.
- Minor formatting differences (e.g., "July 5" vs "5 July 2023") should \
not reduce the score below 0.9.

Focus on semantic correctness, not exact wording. A rephrased correct answer \
should score high.\
"""


REFORMULATION_SYSTEM_PROMPT = """\
You are a search query reformulator. Given a question about a conversation \
history, generate 2 alternative search queries that would help find the \
relevant information in the conversation logs.

Rules:
- Each reformulation should use different keywords and phrasing than the original
- Focus on the key entities (people, places, things) and actions mentioned
- One reformulation should be a simple keyword-style query
- One reformulation should rephrase the question from the perspective of the \
conversation participants
- Keep each reformulation under 30 words\
"""


class QueryReformulations(BaseModel):
    """Structured output for query reformulation."""

    queries: list[str] = Field(
        description="2 alternative search queries",
        min_length=2,
        max_length=2,
    )


class JudgeScore(BaseModel):
    """Structured output for LLM judge scoring."""

    reasoning: str = Field(description="Brief reasoning for the score")
    score: float = Field(ge=0.0, le=1.0, description="Score from 0.0 to 1.0")


class GeneratedAnswer(BaseModel):
    """Structured output for answer generation."""

    reasoning: str = Field(
        description="Brief chain-of-thought reasoning. For temporal questions, "
        "list relevant dates and show computation. For aggregation, list all "
        "matching items before counting."
    )
    answer: str = Field(
        default="",
        description="The concise final answer to the question",
    )

    @model_validator(mode="after")
    def _extract_answer_from_reasoning(self) -> "GeneratedAnswer":
        """Fallback: if answer is empty, extract from reasoning text."""
        if not self.answer.strip() and self.reasoning:
            # Some reasoning models embed the answer at the end of reasoning
            for marker in ("Answer:", "Answer :", "ANSWER:"):
                idx = self.reasoning.rfind(marker)
                if idx != -1:
                    self.answer = self.reasoning[idx + len(marker) :].strip()
                    break
            if not self.answer.strip():
                # Last resort: use the full reasoning as the answer
                self.answer = self.reasoning
        return self


@dataclass
class LLMJudgeConfig:
    """Configuration for the LLM generation and judging layer."""

    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_retries: int = 2
    enabled: bool = False

    @property
    def provider_string(self) -> str:
        return f"{self.provider}/{self.model}"


_client_cache: dict[str, instructor.AsyncInstructor] = {}


def _get_client(provider_string: str) -> instructor.AsyncInstructor:
    """Get or create a cached instructor async client."""
    if provider_string not in _client_cache:
        _client_cache[provider_string] = instructor.from_provider(
            provider_string, async_client=True
        )
    return _client_cache[provider_string]


async def check_abstention(
    query: str,
    context: str,
    config: LLMJudgeConfig,
) -> bool:
    """Check if the system should abstain from answering.

    Delegates to ``prme.retrieval.abstention.should_abstain``.

    Args:
        query: The user's question.
        context: Retrieved context (formatted for LLM).
        config: LLM configuration.

    Returns:
        True if the system should abstain (context doesn't answer the question).
    """
    from prme.retrieval.abstention import should_abstain

    return await should_abstain(
        query,
        context,
        provider=config.provider,
        model=config.model,
        max_retries=config.max_retries,
    )


async def reformulate_query(
    query: str,
    config: LLMJudgeConfig,
) -> list[str]:
    """Generate alternative search queries for improved recall.

    Args:
        query: The original question.
        config: LLM configuration.

    Returns:
        List of 2 alternative queries. Returns empty list on failure.
    """
    try:
        client = _get_client(config.provider_string)
        result = await client.create(
            response_model=QueryReformulations,
            messages=[
                {"role": "system", "content": REFORMULATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Original question: {query}"},
            ],
            max_retries=config.max_retries,
        )
        return result.queries
    except Exception:
        logger.warning("Query reformulation failed", exc_info=True)
        return []


async def generate_answer(
    query: str,
    context: str,
    config: LLMJudgeConfig,
) -> str:
    """Generate an answer from retrieval context using an LLM.

    Args:
        query: The user's question.
        context: Retrieved context to answer from.
        config: LLM configuration.

    Returns:
        Generated answer string. Returns empty string on failure.
    """
    try:
        client = _get_client(config.provider_string)
        result = await client.create(
            response_model=GeneratedAnswer,
            messages=[
                {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nQuestion: {query}",
                },
            ],
            max_retries=config.max_retries,
        )
        return result.answer
    except Exception:
        logger.error(
            "LLM generation failed",
            exc_info=True,
        )
        return ""


async def judge_answer(
    query: str,
    expected: str,
    generated: str,
    config: LLMJudgeConfig,
) -> float:
    """Score how well a generated answer matches the expected answer.

    Args:
        query: The original question.
        expected: The expected/ground-truth answer.
        generated: The LLM-generated answer.
        config: LLM configuration.

    Returns:
        Score in [0.0, 1.0]. Returns 0.0 on failure.
    """
    if not generated or generated.strip().lower() in ("i don't know", ""):
        return 0.0

    try:
        client = _get_client(config.provider_string)
        result = await client.create(
            response_model=JudgeScore,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Question: {query}\n\n"
                        f"Expected answer: {expected}\n\n"
                        f"Generated answer: {generated}"
                    ),
                },
            ],
            max_retries=config.max_retries,
        )
        return result.score
    except Exception:
        logger.error(
            "LLM judge failed",
            exc_info=True,
        )
        return 0.0
