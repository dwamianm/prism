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
from prme.models.aggregation import (
    AssertionAggregation,
    AssertionGroup,
    AssertionQuery,
    QuantityAggregation,
    QuantityAggregationQuery,
    QuantityGroup,
    QuantitySample,
)
from prme.models.temporal import (
    AssertionState,
    AssertionStateConflict,
    AssertionStateEntry,
    AssertionStateHistoricalCoverage,
    AssertionStateQuery,
    AssertionStateValue,
)
from prme.models.processing import ProcessingResult, ProcessingStatus, StoreReceipt
from prme.models.credit import ContextAblation, ContextPresenceCredit
from prme.models.profile import StaleProfileError, ProfileJobStatus, ProfileProcessingResult, ProfileCollectionResult
from prme.models.relevance import (
    AnswerCitationRecord,
    AnswerCitationSubmission,
    RelevanceRecord,
    RelevanceSubmission,
    RetrievalReceipt,
)
from prme.models.provenance import NodeProvenance, OperationAuditRecord
from prme.models.learning import (
    FullRetrievalEvaluation,
    FullRetrievalEvaluationConfig,
    FullRetrievalQueryResult,
    FullRetrievalTrial,
    LearningConfig,
    LearningEvaluation,
    RankingMultipliers,
    RankingProfile,
    RankingProfileApplication,
    RankingProfileState,
    RankingProfileStatus,
)
from prme.models.extraction import ExtractionRecord
from prme.models.extraction_work import ExtractionStatus, ExtractionProcessingResult
from prme.storage.engine import MemoryEngine
from prme.storage.namespace_identity import NamespaceIdentityError
from prme.storage.ranking_profiles import StaleRankingProfileError
from prme.storage.embedding import CachedEmbeddingProvider, EmbeddingProvider, QueryEmbeddingProvider
from prme.retrieval.credit import ablate_context, assess_context_presence
from prme.retrieval.full_learning import evaluate_full_retrieval
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
    RetrievalMode,
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
    "AnswerCitationRecord",
    "AnswerCitationSubmission",
    "AssertionAggregation",
    "AssertionGroup",
    "AssertionQuery",
    "AssertionState",
    "AssertionStateConflict",
    "AssertionStateEntry",
    "AssertionStateHistoricalCoverage",
    "AssertionStateQuery",
    "AssertionStateValue",
    "QuantityAggregation",
    "QuantityAggregationQuery",
    "QuantityGroup",
    "QuantitySample",
    "ConditionEvaluationMethod",
    "ConditionState",
    "ContextAblation",
    "ContextPresenceCredit",
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
    "FullRetrievalEvaluation",
    "FullRetrievalEvaluationConfig",
    "FullRetrievalQueryResult",
    "FullRetrievalTrial",
    "MaterializationError",
    "MemoryClient",
    "MemoryEngine",
    "MemoryWorkspace",
    "NamespaceInfo",
    "NamespaceMemory",
    "NamespaceIdentityError",
    "WorkspaceError",
    "NodeType",
    "NodeProvenance",
    "OperationAuditRecord",
    "PRMEConfig",
    "ProcessingResult",
    "ProcessingStatus",
    "ProfileJobStatus",
    "ProfileProcessingResult",
    "ProfileCollectionResult",
    "RetrievalPipeline",
    "RetrievalResponse",
    "RetrievalReceipt",
    "RetrievalMode",
    "RankingMultipliers",
    "RankingProfile",
    "RankingProfileApplication",
    "RankingProfileState",
    "RankingProfileStatus",
    "RelevanceSubmission",
    "RelevanceRecord",
    "Scope",
    "SourceType",
    "StaleProfileError",
    "StaleRankingProfileError",
    "StoreReceipt",
    "ablate_context",
    "assess_context_presence",
    "evaluate_full_retrieval",
    "config_from_directory",
]
