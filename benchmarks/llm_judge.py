"""LLM-based answer generation and judging for PRME benchmarks.

Provides generation (RAG-style answer from retrieved context) and
judgment (scoring generated answers against ground truth) functions.
Uses instructor + the configured LLM provider (OpenAI, Anthropic,
or Ollama) for both steps.

The prompts are tuned for two known failure patterns in real benchmarks:

1. **Temporal reasoning** -- relative date references like "yesterday"
   must be resolved against the message timestamp, not echoed literally.
2. **Inference** -- the model should make reasonable inferences from
   context (e.g., someone who collects classic children's books likely
   has Dr. Seuss) rather than refusing with "I don't know".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

GENERATION_SYSTEM_PROMPT = """\
Answer the question using the provided context. Be concise and direct.

TEMPORAL REASONING: When the context contains timestamps and relative time \
references (e.g., "yesterday", "last week", "last Friday", "a few weeks ago"), \
compute the actual date by applying the offset to the message timestamp. \
For example, if a message dated "6 July 2023" says "yesterday I went to \
the museum", the museum visit was on 5 July 2023.

INFERENCE: When the question asks about preferences, likely behaviors, or \
opinions, you may make reasonable inferences from the context. For example, \
if someone collects classic children's books, it is reasonable to infer they \
would have well-known classics like Dr. Seuss. State your inference clearly.

If the context contains no relevant information at all, say "I don't know".
Do not fabricate specific facts, names, dates, or numbers that aren't \
supported by the context.\
"""

JUDGE_SYSTEM_PROMPT = """\
You are an impartial judge evaluating whether a generated answer correctly \
answers a question, given the expected (ground-truth) answer.

Score the generated answer on a scale from 0.0 to 1.0:

- 1.0 — The generated answer is fully correct and matches the expected answer.
- 0.7-0.9 — The answer is substantially correct but may differ in wording, \
  include extra detail, or use a slightly different formulation.
- 0.3-0.6 — The answer is partially correct (gets some elements right but \
  misses key parts).
- 0.0 — The answer is completely wrong, contradicts the expected answer, or \
  says "I don't know" when an answer is available.

Additional scoring guidelines:

- If the expected answer requires inference and the generated answer makes a \
  reasonable inference from the context that aligns with the expected answer, \
  score 0.7-0.9 even if the wording differs.
- For temporal questions, if the generated answer has the correct computed \
  date (accounting for relative offsets), score 1.0 regardless of format \
  differences.
- Minor formatting differences (e.g., "July 5" vs "5 July 2023") should \
  not reduce the score below 0.9.
- If the generated answer includes the expected answer plus additional \
  correct information, do not penalize.

Respond with ONLY a JSON object: {"score": <float>, "reason": "<brief explanation>"}\
"""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class LLMJudgeConfig:
    """Configuration for the LLM judge and generator.

    Attributes:
        provider: LLM provider string for instructor
            (e.g., "openai/gpt-4o-mini", "anthropic/claude-3-5-sonnet-20241022").
        max_retries: Number of instructor retries for schema validation.
        timeout: Timeout in seconds per LLM call.
        generation_temperature: Temperature for answer generation.
        judge_temperature: Temperature for judging (lower = more deterministic).
    """

    provider: str = "openai/gpt-4o-mini"
    max_retries: int = 2
    timeout: float = 30.0
    generation_temperature: float = 0.3
    judge_temperature: float = 0.0


# ---------------------------------------------------------------------------
# Pydantic response models (for instructor)
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel, Field as PydanticField

    class GeneratedAnswer(BaseModel):
        """Structured response from the generation LLM."""

        answer: str = PydanticField(
            description="The concise answer to the question"
        )

    class JudgeVerdict(BaseModel):
        """Structured response from the judge LLM."""

        score: float = PydanticField(
            description="Score between 0.0 and 1.0", ge=0.0, le=1.0
        )
        reason: str = PydanticField(
            description="Brief explanation for the score"
        )

except ImportError:
    # pydantic is a required dep, but guard for safety
    GeneratedAnswer = None  # type: ignore[assignment,misc]
    JudgeVerdict = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------

_client_cache: dict[str, object] = {}


def _get_client(provider: str):
    """Get or create a cached instructor async client."""
    if provider not in _client_cache:
        import instructor

        _client_cache[provider] = instructor.from_provider(
            provider, async_client=True
        )
    return _client_cache[provider]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def generate_answer(
    question: str,
    context: str,
    config: LLMJudgeConfig | None = None,
) -> str:
    """Generate an answer to *question* using the provided *context*.

    Uses the configured LLM provider via instructor to produce a concise
    answer grounded in the retrieved context. The prompt instructs the
    model to resolve relative temporal references and make reasonable
    inferences from the context.

    Args:
        question: The question to answer.
        context: Retrieved context passages concatenated into a single string.
        config: Optional LLMJudgeConfig; uses defaults if not provided.

    Returns:
        The generated answer string. Returns "I don't know" on failure.
    """
    if config is None:
        config = LLMJudgeConfig()

    try:
        client = _get_client(config.provider)
        result = await client.create(
            response_model=GeneratedAnswer,
            messages=[
                {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Context:\n{context}\n\n"
                        f"Question: {question}\n\n"
                        "Answer:"
                    ),
                },
            ],
            max_retries=config.max_retries,
        )
        return result.answer
    except Exception:
        logger.error(
            "generate_answer_failed",
            question=question[:100],
            provider=config.provider,
            exc_info=True,
        )
        return "I don't know"


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------


async def judge_answer(
    question: str,
    expected_answer: str,
    generated_answer: str,
    config: LLMJudgeConfig | None = None,
) -> tuple[float, str]:
    """Judge how well *generated_answer* matches *expected_answer*.

    Uses the configured LLM provider to score the generated answer
    against the ground truth on a 0.0-1.0 scale. The judge prompt is
    tuned to accept reasonable inferences and correctly computed
    temporal offsets.

    Args:
        question: The original question.
        expected_answer: Ground-truth answer from the benchmark dataset.
        generated_answer: The answer produced by generate_answer().
        config: Optional LLMJudgeConfig; uses defaults if not provided.

    Returns:
        Tuple of (score, reason) where score is in [0.0, 1.0] and
        reason is a brief explanation from the judge.
    """
    if config is None:
        config = LLMJudgeConfig()

    try:
        client = _get_client(config.provider)
        result = await client.create(
            response_model=JudgeVerdict,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n"
                        f"Expected answer: {expected_answer}\n"
                        f"Generated answer: {generated_answer}"
                    ),
                },
            ],
            max_retries=config.max_retries,
        )
        return result.score, result.reason
    except Exception:
        logger.error(
            "judge_answer_failed",
            question=question[:100],
            provider=config.provider,
            exc_info=True,
        )
        # On failure, fall back to simple string containment check
        expected_lower = expected_answer.lower()
        generated_lower = generated_answer.lower()
        if expected_lower in generated_lower:
            return 1.0, "fallback: exact containment match"
        # Check if any significant words overlap
        expected_words = {
            w for w in expected_lower.split() if len(w) > 2
        }
        generated_words = set(generated_lower.split())
        if expected_words and expected_words & generated_words:
            overlap = len(expected_words & generated_words) / len(
                expected_words
            )
            return round(overlap, 2), "fallback: word overlap"
        return 0.0, "fallback: judge call failed, no overlap detected"
