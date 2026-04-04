"""PRME - Portable Relational Memory Engine.

A local-first, embeddable memory substrate for LLM-powered systems.
Combines event sourcing, graph-based relational modeling, hybrid retrieval,
and scheduled memory reorganization.
"""

__version__ = "0.6.0"

from prme.config import PRMEConfig
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
    "IngestionPipeline",
    "LifecycleState",
    "MemoryClient",
    "MemoryEngine",
    "NodeType",
    "PRMEConfig",
    "RetrievalPipeline",
    "RetrievalResponse",
    "Scope",
]
