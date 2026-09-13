"""Pydantic models for LLM extraction output.

Defines the structured schema that the LLM extraction system produces:
entities, facts (including decisions and preferences), relationships,
and an optional summary. All models include LLM-friendly Field descriptions
to guide structured extraction via instructor.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


ClaimPolarity = Literal["positive", "negative", "unknown"]


class ExtractedEntity(BaseModel):
    """An entity extracted from conversation text."""

    name: str = Field(description="Entity name as it appears in the text")
    entity_type: str = Field(
        description=(
            "One of: person, organization, location, product, concept, event"
        )
    )
    description: str | None = Field(
        default=None,
        description="Brief contextual description from the text",
    )
    scope: str | None = Field(
        default=None,
        description=(
            "Scope classification: 'personal' for individual context, "
            "'project' for project-specific context, 'organisation' for "
            "organization-wide context, 'agent' for agent working memory, "
            "'system' for system-generated content, 'sandbox' for "
            "temporary/testing context. Null if unclear."
        ),
    )


class ExtractedFact(BaseModel):
    """A fact (subject-predicate-object triple) extracted from text.

    The fact_type field classifies the triple as a plain fact,
    a decision, or a preference. This maps to NodeType.FACT,
    NodeType.DECISION, or NodeType.PREFERENCE during materialization.
    """

    subject: str = Field(description="Name of a listed entity this fact is about; copy entities[].name exactly")
    subject_entity_type: str | None = Field(default=None, description="Exact entities[].entity_type for the subject; required if its name has multiple types")
    object_entity_type: str | None = Field(default=None, description="Exact entities[].entity_type when the object names an entity; required for an ambiguous entity name, otherwise null for literal values")
    predicate: str = Field(
        description="Relationship or attribute type (e.g., works_at, lives_in, role)"
    )
    object: str = Field(description="Value or target entity")
    polarity: ClaimPolarity = Field(
        default="unknown",
        description=(
            "Semantic polarity of the proposition after accounting for negation. "
            "Use 'negative' for does not, never, no longer, rejected, or against; "
            "use 'positive' only when the proposition is affirmed. 'unknown' is "
            "reserved for custom or legacy providers that cannot classify it."
        ),
    )
    evidence_quote: str | None = Field(
        default=None,
        description=(
            "Exact verbatim source passage supporting the fact, including its subject, "
            "object, negation, conditions, exceptions, and temporal qualifiers. "
            "Use complete source sentences; never omit a trailing qualification."
        ),
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Extraction confidence",
    )
    temporal_ref: str | None = Field(
        default=None,
        description="Raw temporal reference if mentioned (e.g., 'yesterday', 'last month')",
    )
    fact_type: str = Field(
        default="fact",
        description="One of: fact, decision, preference",
    )
    scope: str | None = Field(
        default=None,
        description=(
            "Scope classification: 'personal', 'project', 'organisation', "
            "'agent', 'system', or 'sandbox'. Null if unclear."
        ),
    )
    epistemic_type: str = Field(
        default="asserted",
        description=(
            "Epistemic classification of this fact. Must be one of: "
            "observed (directly stated/witnessed), asserted (claimed as fact), "
            "inferred (derived from context), hypothetical (speculative), "
            "conditional (depends on conditions), unverified (from untrusted source). "
            "DEPRECATED is not allowed at creation time."
        ),
    )
    condition: str | None = Field(
        default=None,
        description=(
            "Exact verbatim source span describing what must be true for a "
            "conditional claim to apply. Required when epistemic_type is "
            "'conditional'; null otherwise."
        ),
    )
    temporal_intent: str | None = Field(
        default=None,
        description=(
            "Temporal intent classification for conflict detection. "
            "'update' if this fact replaces prior state (e.g., 'now works at', "
            "'moved to', 'changed to'). 'assertion' if this is a standalone "
            "claim with no temporal replacement signal. Null if unclear."
        ),
    )
    replaces_object: str | None = Field(
        default=None,
        description=(
            "Exact previous object value explicitly replaced by this fact in the source "
            "(e.g., 'Slack' in 'switched from Slack to Signal'). Null unless the source "
            "explicitly replaces that value. A new preference or another value does not "
            "by itself replace previous values. Never infer this from memory."
        ),
    )

    @field_validator("epistemic_type")
    @classmethod
    def validate_epistemic_type(cls, v: str) -> str:
        """Strictly validate epistemic_type against creation-time types.

        Rejects invalid types -- instructor will re-prompt the LLM when
        Pydantic validation fails (already configured with max_retries).
        DEPRECATED is not assignable at creation per CONTEXT.md decision.
        """
        allowed = {
            "observed", "asserted", "inferred",
            "hypothetical", "conditional", "unverified",
        }
        if v.lower() not in allowed:
            raise ValueError(
                f"Invalid epistemic_type '{v}'. Must be one of: "
                f"{', '.join(sorted(allowed))}. "
                "DEPRECATED is not assignable at creation."
            )
        return v.lower()


class ExtractedRelationship(BaseModel):
    """A relationship between two entities extracted from text."""

    source_entity: str = Field(description="Source entity name; copy entities[].name exactly")
    source_entity_type: str | None = Field(default=None, description="Exact source entity_type; required for an ambiguous name")
    target_entity: str = Field(description="Target entity name; copy entities[].name exactly")
    target_entity_type: str | None = Field(default=None, description="Exact target entity_type; required for an ambiguous name")
    relationship_type: str = Field(
        description="Source-supported relationship predicate, such as lives_in or works_at; do not force it into a graph edge category"
    )
    polarity: ClaimPolarity = Field(
        default="unknown", description=ExtractedFact.model_fields["polarity"].description
    )
    evidence_quote: str | None = Field(default=None, description=ExtractedFact.model_fields["evidence_quote"].description)
    epistemic_type: str = Field(default="unverified", description=ExtractedFact.model_fields["epistemic_type"].description)
    condition: str | None = Field(
        default=None, description=ExtractedFact.model_fields["condition"].description
    )

    @field_validator("epistemic_type")
    @classmethod
    def validate_epistemic_type(cls, value: str) -> str:
        return ExtractedFact.validate_epistemic_type(value)

    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Extraction confidence",
    )


class ExtractionResult(BaseModel):
    """Complete extraction result from a single message.

    Contains all structured knowledge extracted by the LLM:
    entities, facts (including decisions/preferences as fact_type variants),
    relationships between entities, and an optional summary.
    """

    entities: list[ExtractedEntity] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    summary: str | None = Field(
        default=None,
        description="Brief summary of the message content",
    )
