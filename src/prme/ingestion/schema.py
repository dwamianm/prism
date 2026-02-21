"""Pydantic models for LLM extraction output.

Defines the structured schema that the LLM extraction system produces:
entities, facts (including decisions and preferences), relationships,
and an optional summary. All models include LLM-friendly Field descriptions
to guide structured extraction via instructor.
"""

from pydantic import BaseModel, Field, field_validator


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
            "'project' for project-specific context, 'org' for "
            "organization-wide context. Null if unclear."
        ),
    )


class ExtractedFact(BaseModel):
    """A fact (subject-predicate-object triple) extracted from text.

    The fact_type field classifies the triple as a plain fact,
    a decision, or a preference. This maps to NodeType.FACT,
    NodeType.DECISION, or NodeType.PREFERENCE during materialization.
    """

    subject: str = Field(description="Entity this fact is about")
    predicate: str = Field(
        description="Relationship or attribute type (e.g., works_at, lives_in, role)"
    )
    object: str = Field(description="Value or target entity")
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
            "Scope classification: 'personal', 'project', or 'org'. "
            "Null if unclear."
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

    source_entity: str = Field(description="Source entity name")
    target_entity: str = Field(description="Target entity name")
    relationship_type: str = Field(
        description="Edge type: relates_to, part_of, caused_by, supports, mentions"
    )
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
