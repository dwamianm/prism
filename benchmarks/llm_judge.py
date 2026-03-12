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
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GENERATION_SYSTEM_PROMPT = """\
Answer the question using ONLY the provided context. Be concise and direct.
If the context doesn't contain enough information to answer, say "I don't know".
Do not make up information that is not supported by the context.\
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

Focus on semantic correctness, not exact wording. A rephrased correct answer \
should score high.\
"""


class JudgeScore(BaseModel):
    """Structured output for LLM judge scoring."""

    reasoning: str = Field(description="Brief reasoning for the score")
    score: float = Field(ge=0.0, le=1.0, description="Score from 0.0 to 1.0")


class GeneratedAnswer(BaseModel):
    """Structured output for answer generation."""

    answer: str = Field(description="The answer to the question")


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
