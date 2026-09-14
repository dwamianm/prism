"""ExtractionProvider Protocol and instructor-based implementations.

Defines the extraction interface (ExtractionProvider Protocol) and a
concrete implementation (InstructorExtractionProvider) that uses the
instructor library's from_provider() API to support OpenAI, Anthropic,
and Ollama backends through a single unified interface.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import structlog
from pydantic import Field, SecretStr, ValidationInfo, model_validator

from prme.ingestion.schema import ExtractedFact, ExtractedRelationship, ExtractionResult
from prme.ingestion.grounding import _mentioned, canonical_source_quote
from prme.ingestion.errors import ExtractionError, extraction_failure_code
from prme.ingestion.entity_references import reference_errors

if TYPE_CHECKING:
    import instructor

    from prme.config import ExtractionConfig

logger = structlog.get_logger(__name__)

_EXPLICIT_CONDITION_RE = re.compile(
    r"\b(?:if|unless|provided\s+that|as\s+long\s+as|only\s+if)\b",
    re.IGNORECASE,
)
_UNCERTAINTY_RE = re.compile(
    r"(?i:\b(?:might|could|possibly|perhaps|maybe)\b)|\bmay\b"
)
_EXPLICIT_DECISION_RE = re.compile(
    r"\b(?:decid\w*|chos(?:e|en)|select(?:ed|s)?|opt(?:ed|s)?|agree(?:d|s)?|"
    r"commit(?:ted|s)?|reject(?:ed|s)?)\b",
    re.IGNORECASE,
)


def _validate_condition(epistemic_type: str, condition: str | None, evidence_quote: str) -> None:
    """Require explicit conditional syntax to remain typed and auditable."""
    has_explicit_condition = _EXPLICIT_CONDITION_RE.search(evidence_quote) is not None
    if has_explicit_condition and epistemic_type != "conditional":
        raise ValueError("an explicit if/unless condition requires epistemic_type conditional")
    if epistemic_type == "conditional":
        if not condition or condition not in evidence_quote:
            raise ValueError("conditional claims require a verbatim condition from evidence_quote")
    elif condition is not None:
        raise ValueError("condition is only valid when epistemic_type is conditional")


def _validate_modality(
    fact_type: str, epistemic_type: str, evidence_quote: str
) -> None:
    """Reject common uncertainty and contingent-action category collapses."""
    uncertain = _UNCERTAINTY_RE.search(evidence_quote) is not None
    if uncertain and epistemic_type not in {"hypothetical", "conditional"}:
        raise ValueError("might/may/could claims require hypothetical or conditional epistemic_type")
    if (
        fact_type == "decision"
        and (uncertain or _EXPLICIT_CONDITION_RE.search(evidence_quote))
        and _EXPLICIT_DECISION_RE.search(evidence_quote) is None
    ):
        raise ValueError("a contingent future action is not a decision without an explicit choice or commitment")


class _CitedFact(ExtractedFact):
    """Built-in providers must return source support or retry validation."""

    fact_type: Literal["fact", "decision", "preference"] = "fact"
    polarity: Literal["positive", "negative"] = Field(
        description=ExtractedFact.model_fields["polarity"].description
    )

    evidence_quote: str = Field(
        min_length=1,
        description=ExtractedFact.model_fields["evidence_quote"].description,
    )

    @model_validator(mode="after")
    def source_support(self, info: ValidationInfo) -> _CitedFact:
        source = (info.context or {}).get("source_text")
        if source is not None:
            evidence_quote = canonical_source_quote(self.evidence_quote, source)
            if evidence_quote is None:
                raise ValueError("evidence_quote must be copied verbatim from the source")
            self.evidence_quote = evidence_quote
            if not _mentioned(self.subject, self.evidence_quote) or not _mentioned(self.object, self.evidence_quote):
                raise ValueError("subject and object must occur in evidence_quote; use source values without paraphrasing")
            _validate_condition(self.epistemic_type, self.condition, self.evidence_quote)
            _validate_modality(self.fact_type, self.epistemic_type, self.evidence_quote)
        return self


class _CitedRelationship(ExtractedRelationship):
    polarity: Literal["positive", "negative"] = Field(
        description=ExtractedFact.model_fields["polarity"].description
    )
    evidence_quote: str = Field(min_length=1, description=ExtractedFact.model_fields["evidence_quote"].description)
    epistemic_type: str = Field(description=ExtractedFact.model_fields["epistemic_type"].description)

    @model_validator(mode="after")
    def source_support(self, info: ValidationInfo):
        source = (info.context or {}).get("source_text")
        if source is not None:
            evidence_quote = canonical_source_quote(self.evidence_quote, source)
            if evidence_quote is None:
                raise ValueError("relationship evidence_quote must be copied verbatim from the source")
            self.evidence_quote = evidence_quote
            if not _mentioned(self.source_entity, self.evidence_quote) or not _mentioned(self.target_entity, self.evidence_quote):
                raise ValueError("relationship endpoints must occur in evidence_quote")
            _validate_condition(self.epistemic_type, self.condition, self.evidence_quote)
            _validate_modality("fact", self.epistemic_type, self.evidence_quote)
        return self


class _CitedExtractionResult(ExtractionResult):
    facts: list[_CitedFact] = Field(default_factory=list)
    relationships: list[_CitedRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def closed_entity_references(self):
        errors = reference_errors(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self

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
   - A polarity: "positive" when the proposition is affirmed or "negative" \
when it is denied, rejected, stopped, or stated with does not/never/no longer

3. **Relationships** between entities: How entities relate to each other. \
Use a source-supported predicate such as lives_in, works_at, or uses. Do not \
force residence into part_of, or infer causation from co-occurrence. Include \
an evidence_quote, epistemic_type, and polarity for every relationship. Prefer a fact \
triple for a statement; do not repeat it as a separate relationship.

4. **Summary**: A brief 1-2 sentence summary of the message content.

5. **Temporal references**: If a fact involves a time reference (e.g., \
"yesterday", "last week", "in March 2024", "3 days ago"), include the raw \
temporal text in the temporal_ref field.

6. **Scope Classification**: For each entity and fact, classify the scope:
   - "personal" — about a specific individual's preferences, habits, or personal context
   - "project" — about a specific project, its decisions, tools, or deliverables
   - "organisation" — about organization-wide policies, structures, or shared context
   - "agent" — about a specific AI agent's working memory or internal reasoning
   - "system" — system-generated content such as summaries or organizer output
   - "sandbox" — temporary or experimental context intended for isolated testing
   This is a descriptive suggestion. It never overrides the caller's write scope.
   If the scope is unclear, leave it as null (the system will use a safe default).

7. **Epistemic Type**: For each fact, classify its epistemic_type:
   - "observed" — directly stated or witnessed ("I work at Google")
   - "asserted" — claimed as fact without direct evidence
   - "inferred" — derived from context ("Based on their questions, they know Python")
   - "hypothetical" — speculative or possible without an explicit condition
   - "conditional" — applies only if an explicitly stated condition is true
   - "unverified" — from untrusted or unverified source
   Default to "asserted" if unclear. For "conditional", copy the exact source \
span describing the condition into condition. Otherwise set condition to null.

8. **Temporal Intent**: For each fact, classify its temporal_intent:
   - "update" — this fact replaces a prior state (signals: "now", "changed to", \
"moved to", "switched to", "no longer", "left", "started", "recently")
   - "assertion" — this is a standalone claim with no indication it replaces \
prior knowledge
   If unclear, leave temporal_intent as null (the system will use a safe default).

IMPORTANT RULES:
- Every named fact subject and relationship endpoint must use a name listed in entities.
  Copy that entity name exactly; do not alternate between shortened and full names.
  Literal unresolved personal references such as "I", "we", or "they" may be used
  without listing them as named entities. Copy the literal reference; do not invent a speaker name.
  Relationship endpoints are entity names, not phrases combining predicates and objects.
  If the same name identifies different entity types, include subject_entity_type,
  object_entity_type, source_entity_type, or target_entity_type to identify the intended listed entity.
- Relationship claims must preserve negations, uncertainty, and conditions just as facts do.
  Use conditional or hypothetical for possible relationships; never convert them to current reality.
- Polarity is independent of fact_type and epistemic_type. A dislike is a \
  negative preference; a rejected option is a negative decision. Keep the \
  predicate about the underlying relation and express denial in polarity.
- An action that will occur only if a future condition becomes true is a \
  conditional fact, not a decision, unless the source separately states that \
  the choice or commitment has already been made.
- Include an evidence_quote for every fact: copy the complete supporting source \
sentences verbatim, including negation, conditions, exceptions, and time references.
- Subject and object must occur in the supporting text. Keep object values as \
written rather than normalizing or paraphrasing them.
- Using something does not imply preferring it. One occurrence does not imply \
a habit. Multiple values can coexist (e.g., liking tea and coffee).
- Set replaces_object only for an explicit replacement of a named previous value \
("switched from Slack to Signal"). "Now also uses Signal" does not replace Slack. \
Copy the previous value exactly from the supporting passage.
- Preserve conditions and uncertainty. Use conditional or hypothetical epistemic \
types when appropriate; do not turn a possible future into a current fact.
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
            'anthropic/claude-3-5-sonnet-20241022', 'ollama/llama3.2',
            'bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0').
        model: Optional model id passed to client.create(). Required for the
            'bedrock' provider, whose instructor client is NOT pre-bound to a
            model by from_provider(). When None, the model is derived from the
            provider_string.
        max_retries: Number of instructor retries for schema validation failures.
        timeout: Timeout in seconds per extraction call.
        temperature: Sampling temperature passed to the provider.
    """

    def __init__(
        self,
        provider_string: str,
        *,
        model: str | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        temperature: float = 0.0,
        api_key: SecretStr | None = None,
        base_url: str | None = None,
    ) -> None:
        self._provider_string = provider_string
        self._model = model
        self._max_retries = max_retries
        self._timeout = timeout
        self._temperature = temperature
        self._api_key = api_key
        self._base_url = base_url
        self._client: instructor.AsyncInstructor | None = None

    def _ensure_client(self) -> instructor.AsyncInstructor:
        """Lazily create the instructor async client on first use.

        This avoids API key validation at construction time, allowing
        the provider to be created without environment variables set.
        """
        if self._client is None:
            import instructor
            from dotenv import dotenv_values

            # SDK defaults read process variables but do not load .env. Resolve
            # only the selected provider's settings, without mutating os.environ.
            kwargs: dict = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key.get_secret_value()
            if self._base_url:
                kwargs["base_url"] = self._base_url
            provider_prefix = {"openai": "OPENAI", "anthropic": "ANTHROPIC"}.get(self.provider_name)
            if provider_prefix:
                local = dotenv_values(".env")
                key_name, url_name = f"{provider_prefix}_API_KEY", f"{provider_prefix}_BASE_URL"
                key = self._api_key.get_secret_value() if self._api_key else os.environ.get(key_name, local.get(key_name))
                url = self._base_url or os.environ.get(url_name, local.get(url_name))
                if key:
                    kwargs["api_key"] = key
                if url:
                    kwargs["base_url"] = url

            self._client = instructor.from_provider(
                self._provider_string, async_client=True, **kwargs
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

    def _resolve_model_id(self) -> str | None:
        """Model id to pass to client.create().

        instructor.from_provider() pre-binds the model into the client for most
        providers (openai, anthropic, ...), but NOT for 'bedrock' -- there the
        model string is only used to pick a mode, so client.create() raises
        "Missing required parameter: modelId" unless we pass the model
        explicitly. Passing it for every provider is safe: create()'s model
        overrides the client default when both are set.
        """
        if self._model:
            return self._model
        if "/" in self._provider_string:
            return self._provider_string.split("/", 1)[1]
        return None

    async def extract(
        self, content: str, *, role: str = "user"
    ) -> ExtractionResult:
        """Extract structured information from message content.

        Args:
            content: The message text to extract from.
            role: The role of the message sender.

        Returns:
            ExtractionResult with extracted entities, facts, relationships,
            and summary. Raises ExtractionError on provider failure or timeout
            so the pipeline can distinguish an error from valid empty output.
        """
        try:
            client = self._ensure_client()
            create_kwargs: dict = {
                "response_model": _CitedExtractionResult,
                "context": {"source_text": content},
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": role, "content": content},
                ],
                "max_retries": self._max_retries,
                "temperature": self._temperature,
            }
            model_id = self._resolve_model_id()
            if model_id:
                create_kwargs["model"] = model_id
            result = await asyncio.wait_for(client.create(**create_kwargs), timeout=self._timeout)
            return result
        except Exception as exc:
            logger.error(
                "extraction_failed",
                provider=self._provider_string,
                content_length=len(content),
                error_type=type(exc).__name__,
            )
            reason = extraction_failure_code(exc)
            raise ExtractionError(f"Extraction failed ({reason})", reason_code=reason) from exc


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
        model=config.model,
        max_retries=config.max_retries,
        timeout=config.timeout,
        temperature=config.temperature,
        api_key=config.api_key,
        base_url=config.base_url,
    )
