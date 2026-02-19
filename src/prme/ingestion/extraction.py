"""ExtractionProvider Protocol and instructor-based implementations.

Defines the extraction interface (ExtractionProvider Protocol) and a
concrete implementation (InstructorExtractionProvider) that uses the
instructor library's from_provider() API to support OpenAI, Anthropic,
and Ollama backends through a single unified interface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

from prme.ingestion.schema import ExtractionResult

if TYPE_CHECKING:
    import instructor

    from prme.config import ExtractionConfig

logger = structlog.get_logger(__name__)

EXTRACTION_SYSTEM_PROMPT = """\
You are a knowledge extraction system. Your task is to extract structured \
information from conversation messages accurately and completely.

Extract the following from the provided text:

1. **Named Entities**: People, organizations, locations, products, concepts, \
and events mentioned in the text. Use the entity name exactly as it appears.

2. **Facts** (subject-predicate-object triples): Factual statements about \
entities. Each fact has:
   - A subject (an entity name from the text)
   - A predicate (the relationship or attribute, e.g., works_at, lives_in, \
role, likes, uses)
   - An object (the value or target entity)
   - A confidence score (0.0 to 1.0) reflecting how explicitly stated the \
fact is
   - A fact_type: use "fact" for general facts, "decision" for decisions \
made or communicated (e.g., "We decided to use PostgreSQL"), and \
"preference" for personal preferences expressed (e.g., "I prefer dark mode")

3. **Relationships** between entities: How entities relate to each other. \
Use relationship types: relates_to, part_of, caused_by, supports, mentions.

4. **Summary**: A brief 1-2 sentence summary of the message content.

5. **Temporal references**: If a fact involves a time reference (e.g., \
"yesterday", "last week", "in March 2024", "3 days ago"), include the raw \
temporal text in the temporal_ref field.

6. **Scope Classification**: For each entity and fact, classify the scope:
   - "personal" — about a specific individual's preferences, habits, or personal context
   - "project" — about a specific project, its decisions, tools, or deliverables
   - "org" — about organization-wide policies, structures, or shared context
   If the scope is unclear, leave it as null (the system will use a safe default).

IMPORTANT RULES:
- Only extract information that is EXPLICITLY STATED or STRONGLY IMPLIED by \
the text.
- Do NOT infer facts that are not grounded in the source text.
- Do NOT fabricate entities or relationships not present in the text.
- Use entity names exactly as they appear in the text.
- Assign higher confidence (0.7-1.0) to explicitly stated facts and lower \
confidence (0.3-0.6) to implied ones.
"""


@runtime_checkable
class ExtractionProvider(Protocol):
    """Protocol for LLM-powered structured extraction.

    Implementations accept message content and return a structured
    ExtractionResult containing entities, facts, relationships, and
    an optional summary.
    """

    @property
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'openai', 'anthropic')."""
        ...

    @property
    def model_name(self) -> str:
        """Return the full model identifier (e.g., 'openai/gpt-4o-mini')."""
        ...

    async def extract(
        self, content: str, *, role: str = "user"
    ) -> ExtractionResult:
        """Extract structured information from message content.

        Args:
            content: The message text to extract from.
            role: The role of the message sender (e.g., 'user', 'assistant').

        Returns:
            ExtractionResult with entities, facts, relationships, and summary.
        """
        ...


class InstructorExtractionProvider:
    """ExtractionProvider using instructor for any supported LLM.

    Supports OpenAI, Anthropic, and Ollama backends through instructor's
    unified from_provider() API. Uses lazy client initialization to avoid
    API key validation at construction time.

    Args:
        provider_string: Provider/model string (e.g., 'openai/gpt-4o-mini',
            'anthropic/claude-3-5-sonnet-20241022', 'ollama/llama3.2').
        model: Optional model override (unused, reserved for future use).
        max_retries: Number of instructor retries for schema validation failures.
        timeout: Timeout in seconds per extraction call.
    """

    def __init__(
        self,
        provider_string: str,
        *,
        model: str | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        self._provider_string = provider_string
        self._model = model
        self._max_retries = max_retries
        self._timeout = timeout
        self._client: instructor.AsyncInstructor | None = None

    def _ensure_client(self) -> instructor.AsyncInstructor:
        """Lazily create the instructor async client on first use.

        This avoids API key validation at construction time, allowing
        the provider to be created without environment variables set.
        """
        if self._client is None:
            import instructor

            self._client = instructor.from_provider(
                self._provider_string, async_client=True
            )
        return self._client

    @property
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'openai')."""
        return self._provider_string.split("/")[0]

    @property
    def model_name(self) -> str:
        """Return the full provider/model string."""
        return self._provider_string

    async def extract(
        self, content: str, *, role: str = "user"
    ) -> ExtractionResult:
        """Extract structured information from message content.

        Args:
            content: The message text to extract from.
            role: The role of the message sender.

        Returns:
            ExtractionResult with extracted entities, facts, relationships,
            and summary. Returns an empty ExtractionResult on failure
            (fail open -- pipeline handles retry).
        """
        try:
            client = self._ensure_client()
            result = await client.create(
                response_model=ExtractionResult,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": role, "content": content},
                ],
                max_retries=self._max_retries,
            )
            return result
        except Exception:
            logger.error(
                "extraction_failed",
                provider=self._provider_string,
                content_length=len(content),
                exc_info=True,
            )
            return ExtractionResult()


def create_extraction_provider(
    config: ExtractionConfig,
) -> ExtractionProvider:
    """Factory function to create an ExtractionProvider from config.

    Builds the provider string from config.provider and config.model,
    then creates an InstructorExtractionProvider instance.

    Args:
        config: ExtractionConfig with provider, model, max_retries, timeout.

    Returns:
        An ExtractionProvider instance (InstructorExtractionProvider).
    """
    provider_string = f"{config.provider}/{config.model}"
    return InstructorExtractionProvider(
        provider_string,
        max_retries=config.max_retries,
        timeout=config.timeout,
    )
