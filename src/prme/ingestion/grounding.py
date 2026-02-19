"""Source text validation for hallucination filtering.

Validates extracted entities, facts, and relationships against the
original source text using substring matching. Items not grounded
in the source are discarded to prevent LLM hallucinations from
entering the knowledge graph.

Per user decision: "Validate extracted entities/facts against source
text -- discard ungrounded/hallucinated extractions."

Per research: Start with conservative substring matching. Better to
discard valid context-dependent references than accept hallucinations.
"""

from __future__ import annotations

import structlog

from prme.ingestion.schema import ExtractionResult

logger = structlog.get_logger(__name__)


def validate_grounding(
    result: ExtractionResult, source_text: str
) -> ExtractionResult:
    """Filter out extracted items not grounded in source text.

    Applies substring matching to verify that extracted entities,
    facts, and relationships reference items actually present in the
    source text.

    Filtering rules:
    - Entities: name must be a substring of source text (case-insensitive).
    - Facts: subject must be a substring of source text. The object
      is not checked because it may be a paraphrased attribute value
      (e.g., "senior engineer") that doesn't appear verbatim.
    - Relationships: both source_entity and target_entity must be
      substrings of source text.
    - Summary: always preserved (it's a paraphrase, not an extraction).

    Args:
        result: The ExtractionResult from LLM extraction.
        source_text: The original message text to validate against.

    Returns:
        A new ExtractionResult with ungrounded items removed.
    """
    source_lower = source_text.lower()

    # Filter entities: name must appear in source
    grounded_entities = []
    for entity in result.entities:
        if entity.name.lower() in source_lower:
            grounded_entities.append(entity)
        else:
            logger.warning(
                "grounding_entity_discarded",
                entity_name=entity.name,
                entity_type=entity.entity_type,
                reason="Entity name not found in source text",
            )

    # Filter facts: subject must appear in source
    grounded_facts = []
    for fact in result.facts:
        if fact.subject.lower() in source_lower:
            grounded_facts.append(fact)
        else:
            logger.warning(
                "grounding_fact_discarded",
                subject=fact.subject,
                predicate=fact.predicate,
                object=fact.object,
                reason="Fact subject not found in source text",
            )

    # Filter relationships: both endpoints must appear in source
    grounded_relationships = []
    for rel in result.relationships:
        source_grounded = rel.source_entity.lower() in source_lower
        target_grounded = rel.target_entity.lower() in source_lower
        if source_grounded and target_grounded:
            grounded_relationships.append(rel)
        else:
            ungrounded_side = (
                "source_entity"
                if not source_grounded
                else "target_entity"
            )
            logger.warning(
                "grounding_relationship_discarded",
                source_entity=rel.source_entity,
                target_entity=rel.target_entity,
                relationship_type=rel.relationship_type,
                reason=f"{ungrounded_side} not found in source text",
            )

    return ExtractionResult(
        entities=grounded_entities,
        facts=grounded_facts,
        relationships=grounded_relationships,
        summary=result.summary,
    )
