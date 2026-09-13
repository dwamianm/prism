"""PRME - Portable Relational Memory Engine.

A local-first, embeddable memory substrate for LLM-powered systems.
Combines event sourcing, graph-based relational modeling, hybrid retrieval,
and explicit or opportunistic memory reorganization.
"""

__version__ = "0.11.0"

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.workspace import MemoryWorkspace, NamespaceInfo, NamespaceMemory, WorkspaceError
    from prme.client import MemoryClient, config_from_directory
    from prme.ingestion.pipeline import IngestionPipeline
    from prme.retrieval.models import RetrievalResponse
    from prme.retrieval.pipeline import RetrievalPipeline

from prme.config import PRMEConfig
from prme.ingestion.errors import ExtractionError, MaterializationError
from prme.models.processing import ProcessingResult, ProcessingStatus
from prme.models.profile import StaleProfileError, ProfileJobStatus, ProfileProcessingResult, ProfileCollectionResult
from prme.models.relevance import RelevanceRecord, RelevanceSubmission, RetrievalReceipt
from prme.models.learning import LearningConfig, LearningEvaluation, RankingMultipliers
from prme.models.extraction import ExtractionRecord
from prme.models.extraction_work import ExtractionStatus, ExtractionProcessingResult
from prme.storage.engine import MemoryEngine
from prme.storage.namespace_identity import NamespaceIdentityError
from prme.storage.embedding import CachedEmbeddingProvider, EmbeddingProvider, QueryEmbeddingProvider
from prme.types import (
    ConditionEvaluationMethod,
    ConditionState,
    DECAY_LAMBDAS,
    DEFAULT_DECAY_PROFILE_MAPPING,
    DecayProfile,
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
)


def __getattr__(name: str):
    """Lazy imports for heavy modules to avoid circular import chains."""
    if name in {"MemoryWorkspace", "NamespaceInfo", "NamespaceMemory", "WorkspaceError"}:
        from prme import workspace
        return getattr(workspace, name)
    if name == "MemoryClient":
        from prme.client import MemoryClient

        return MemoryClient
    if name == "config_from_directory":
        from prme.client import config_from_directory

        return config_from_directory
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
    "ConditionEvaluationMethod",
    "ConditionState",
    "DECAY_LAMBDAS",
    "DEFAULT_DECAY_PROFILE_MAPPING",
    "DecayProfile",
    "CachedEmbeddingProvider",
    "EmbeddingProvider",
    "QueryEmbeddingProvider",
    "EdgeType",
    "EpistemicType",
    "ExtractionError",
    "ExtractionRecord",
    "ExtractionStatus",
    "ExtractionProcessingResult",
    "IngestionPipeline",
    "LifecycleState",
    "LearningConfig",
    "LearningEvaluation",
    "MaterializationError",
    "MemoryClient",
    "MemoryEngine",
    "MemoryWorkspace",
    "NamespaceInfo",
    "NamespaceMemory",
    "NamespaceIdentityError",
    "WorkspaceError",
    "NodeType",
    "PRMEConfig",
    "ProcessingResult",
    "ProcessingStatus",
    "ProfileJobStatus",
    "ProfileProcessingResult",
    "ProfileCollectionResult",
    "RetrievalPipeline",
    "RetrievalResponse",
    "RetrievalReceipt",
    "RankingMultipliers",
    "RelevanceSubmission",
    "RelevanceRecord",
    "Scope",
    "SourceType",
    "StaleProfileError",
    "config_from_directory",
]
