"""PRME - Portable Relational Memory Engine.

A local-first, embeddable memory substrate for LLM-powered systems.
Combines event sourcing, graph-based relational modeling, hybrid retrieval,
and explicit or opportunistic memory reorganization.
"""

__version__ = "0.11.0"

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.workspace import (
        MemoryWorkspace,
        NamespaceInfo,
        NamespaceMemory,
        WorkspaceError,
    )
    from prme.client import MemoryClient, config_from_directory
    from prme.ingestion.pipeline import IngestionPipeline
    from prme.retrieval.models import RetrievalResponse
    from prme.retrieval.pipeline import RetrievalPipeline

from prme.config import PRMEConfig
from prme.config_audit import (
    HypothesisAudit as HypothesisAudit,
    HypothesisSetting as HypothesisSetting,
    audit_hypotheses as audit_hypotheses,
)
from prme.ingestion.errors import ExtractionError, MaterializationError
from prme.models.aggregation import (
    AssertionAggregation,
    AssertionGroup,
    AssertionQuery,
    QuantityAggregation,
    QuantityAggregationPlan,
    QuantityAggregationQuery,
    QuantityGroup,
    QuantitySample,
    PlannedQuantityAggregation,
)
from prme.models.temporal import (
    AssertionState,
    AssertionStateConflict,
    AssertionStateEntry,
    AssertionStateHistoricalCoverage,
    AssertionStateQuery,
    AssertionStateValue,
)
from prme.models.processing import (
    FastIngestItem,
    ProcessingResult,
    ProcessingStatus,
    StoreReceipt,
)
from prme.models.value_bindings import (
    MemoryValueBinding,
    RetrievedValueBinding,
    ToolArgumentBindingUse,
    ToolArgumentReplacement,
    ToolArgumentResolution,
)
from prme.storage.fast_ingest import FastIngestConflict
from prme.storage.alias_review import (
    AliasProposalDecision,
    AliasProposalInboxItem,
    AliasProposalReviewConflict,
    AliasProposalReviewRecord,
    AliasProposalReviewResult,
    AliasProposalStatus,
    StaleAliasProposal,
)
from prme.integrations.product_candidates import (
    PRODUCT_CANDIDATE_POLICY,
    ProductAlignmentCandidate,
    ProductCandidateEntity,
    rank_product_alignment_candidates,
)
from prme.models.credit import ContextAblation, ContextPresenceCredit
from prme.models.profile import (
    StaleProfileError,
    ProfileJobStatus,
    ProfileProcessingResult,
    ProfileCollectionResult,
)
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
from prme.storage.embedding import (
    CachedEmbeddingProvider,
    EmbeddingProvider,
    QueryEmbeddingProvider,
)
from prme.retrieval.credit import ablate_context, assess_context_presence
from prme.retrieval.answerability import (
    AnswerabilityAction,
    AnswerabilityAssessment,
    AnswerabilityConfig,
    AnswerabilityError,
    AnswerabilityEvaluator,
    AnswerabilityRequirement,
    AnswerabilityStatus,
    AnswerabilityVerdict,
    assess_answerability,
)
from prme.retrieval.claim_verification import (
    ClaimEvidence,
    ClaimVerification,
    ClaimVerificationConfig,
    ClaimVerificationDecisionBasis,
    ClaimVerificationError,
    ClaimVerificationLimitation,
    ClaimVerificationStatus,
    ClaimVerifier,
    EvidenceGroupScore,
    LocalizedEvidenceAssessment,
)
from prme.retrieval.full_learning import evaluate_full_retrieval
from prme.retrieval.temporal_relation_models import TemporalRelationMetadata
from prme.retrieval.temporal_relations import TemporalRelationConfig
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
    if name in {
        "MemoryWorkspace",
        "NamespaceInfo",
        "NamespaceMemory",
        "WorkspaceError",
    }:
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
    "AliasProposalDecision",
    "AliasProposalInboxItem",
    "AliasProposalReviewConflict",
    "AliasProposalReviewRecord",
    "AliasProposalReviewResult",
    "AliasProposalStatus",
    "AnswerabilityAction",
    "AnswerabilityAssessment",
    "AnswerabilityConfig",
    "AnswerabilityError",
    "AnswerabilityEvaluator",
    "AnswerabilityRequirement",
    "AnswerabilityStatus",
    "AnswerabilityVerdict",
    "AssertionAggregation",
    "AssertionGroup",
    "AssertionQuery",
    "AssertionState",
    "AssertionStateConflict",
    "AssertionStateEntry",
    "AssertionStateHistoricalCoverage",
    "AssertionStateQuery",
    "AssertionStateValue",
    "ClaimEvidence",
    "ClaimVerification",
    "ClaimVerificationConfig",
    "ClaimVerificationDecisionBasis",
    "ClaimVerificationError",
    "ClaimVerificationLimitation",
    "ClaimVerificationStatus",
    "ClaimVerifier",
    "LocalizedEvidenceAssessment",
    "QuantityAggregation",
    "QuantityAggregationPlan",
    "QuantityAggregationQuery",
    "QuantityGroup",
    "QuantitySample",
    "PlannedQuantityAggregation",
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
    "EvidenceGroupScore",
    "ExtractionError",
    "ExtractionRecord",
    "ExtractionStatus",
    "ExtractionProcessingResult",
    "FastIngestItem",
    "FastIngestConflict",
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
    "MemoryValueBinding",
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
    "PRODUCT_CANDIDATE_POLICY",
    "ProductAlignmentCandidate",
    "ProductCandidateEntity",
    "ProfileJobStatus",
    "ProfileProcessingResult",
    "ProfileCollectionResult",
    "RetrievalPipeline",
    "RetrievalResponse",
    "RetrievalReceipt",
    "RetrievedValueBinding",
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
    "StaleAliasProposal",
    "StaleRankingProfileError",
    "StoreReceipt",
    "TemporalRelationConfig",
    "TemporalRelationMetadata",
    "ToolArgumentBindingUse",
    "ToolArgumentReplacement",
    "ToolArgumentResolution",
    "ablate_context",
    "assess_context_presence",
    "assess_answerability",
    "evaluate_full_retrieval",
    "rank_product_alignment_candidates",
    "config_from_directory",
]
