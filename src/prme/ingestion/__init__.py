"""PRME ingestion pipeline package.

Provides LLM-powered structured extraction of entities, facts,
relationships, and summaries from conversation text, with grounding
validation to filter hallucinated extractions. The IngestionPipeline
orchestrates two-phase ingestion: immediate event persistence followed
by background (or awaitable) extraction and materialization.
"""

from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.extraction import (
    ExtractionProvider,
    InstructorExtractionProvider,
    create_extraction_provider,
)
from prme.ingestion.grounding import validate_grounding
from prme.ingestion.pipeline import IngestionPipeline
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
    "IngestionPipeline",
    "InstructorExtractionProvider",
    "SupersedenceDetector",
    "create_extraction_provider",
    "validate_grounding",
]
