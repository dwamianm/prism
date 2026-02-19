"""PRME ingestion pipeline package.

Provides LLM-powered structured extraction of entities, facts,
relationships, and summaries from conversation text, with grounding
validation to filter hallucinated extractions.
"""

from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.extraction import (
    ExtractionProvider,
    InstructorExtractionProvider,
    create_extraction_provider,
)
from prme.ingestion.schema import (
    ExtractedEntity,
    ExtractedFact,
    ExtractedRelationship,
    ExtractionResult,
)
from prme.ingestion.supersedence import SupersedenceDetector

__all__ = [
    "EntityMerger",
    "ExtractedEntity",
    "ExtractedFact",
    "ExtractedRelationship",
    "ExtractionProvider",
    "ExtractionResult",
    "InstructorExtractionProvider",
    "SupersedenceDetector",
    "create_extraction_provider",
]
