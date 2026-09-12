"""PRME - Portable Relational Memory Engine.

A local-first, embeddable memory substrate for LLM-powered systems.
Combines event sourcing, graph-based relational modeling, hybrid retrieval,
and scheduled memory reorganization.
"""

__version__ = "0.11.0"

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.client import MemoryClient
    from prme.ingestion.pipeline import IngestionPipeline
    from prme.retrieval.models import RetrievalResponse
    from prme.retrieval.pipeline import RetrievalPipeline

from prme.config import PRMEConfig
from prme.ingestion.errors import ExtractionError
from prme.models.processing import ProcessingResult, ProcessingStatus
from prme.models.extraction import ExtractionRecord
from prme.models.extraction_work import ExtractionStatus, ExtractionProcessingResult
from prme.storage.engine import MemoryEngine
from prme.types import (
    DECAY_LAMBDAS,
    DEFAULT_DECAY_PROFILE_MAPPING,
    DecayProfile,
    EdgeType,
    LifecycleState,
    NodeType,
    Scope,
)


def __getattr__(name: str):
    """Lazy imports for heavy modules to avoid circular import chains."""
    if name == "MemoryClient":
        from prme.client import MemoryClient

        return MemoryClient
    if name == "IngestionPipeline":
        from prme.ingestion.pipeline import IngestionPipeline

        return IngestionPipeline
    if name == "RetrievalResponse":
        from prme.retrieval.models import RetrievalResponse

        return RetrievalResponse
    if name == "RetrievalPipeline":
        from prme.retrieval.pipeline import RetrievalPipeline

        return RetrievalPipeline
    raise AttributeError(f"module 'prme' has no attribute {name!r}")


__all__ = [
    "DECAY_LAMBDAS",
    "DEFAULT_DECAY_PROFILE_MAPPING",
    "DecayProfile",
    "EdgeType",
    "ExtractionError",
    "ExtractionRecord",
    "ExtractionStatus",
    "ExtractionProcessingResult",
    "IngestionPipeline",
    "LifecycleState",
    "MemoryClient",
    "MemoryEngine",
    "NodeType",
    "PRMEConfig",
    "ProcessingResult",
    "ProcessingStatus",
    "RetrievalPipeline",
    "RetrievalResponse",
    "Scope",
]
