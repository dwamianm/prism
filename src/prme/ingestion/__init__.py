"""PRME ingestion pipeline.

Modules for ingesting conversational data into the memory graph:
entity deduplication, supersedence detection, and extraction.
"""

from prme.ingestion.entity_merge import EntityMerger

__all__ = [
    "EntityMerger",
]
