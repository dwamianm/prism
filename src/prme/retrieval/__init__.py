"""PRME hybrid retrieval pipeline.

Public API exports for the retrieval module. All data models, scoring
configuration, filtering, scoring, packing, and pipeline orchestrator
are importable from this package.
"""

from prme.retrieval.abstention import should_abstain
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
from prme.retrieval.config import (
    DEFAULT_PACKING_CONFIG,
    DEFAULT_SCORING_WEIGHTS,
    PackingConfig,
    ScoringWeights,
)
from prme.retrieval.claim_verification import (
    ClaimEvidence,
    ClaimVerification,
    ClaimVerificationConfig,
    ClaimVerificationError,
    ClaimVerificationStatus,
    ClaimVerifier,
    EvidenceGroupScore,
)
from prme.retrieval.context_formatter import build_context_guidance, format_for_llm
from prme.retrieval.credit import ablate_context, assess_context_presence
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import (
    AggregationCoverage,
    ExcludedCandidate,
    MemoryBundle,
    QueryAnalysis,
    RetrievalCandidate,
    RetrievalMetadata,
    RetrievalResponse,
    ScoreTrace,
)
from prme.models.credit import ContextAblation, ContextPresenceCredit
from prme.retrieval.packing import pack_context
from prme.retrieval.pipeline import RetrievalPipeline
from prme.retrieval.reformulation import reformulate_query
from prme.retrieval.scoring import compute_composite_score, score_and_rank
from prme.retrieval.snapshots import (
    EntitySnapshot,
    generate_all_entity_snapshots,
    generate_entity_snapshot,
    render_snapshot_text,
)

__all__ = [
    "AggregationCoverage",
    "AnswerabilityAction",
    "AnswerabilityAssessment",
    "AnswerabilityConfig",
    "AnswerabilityError",
    "AnswerabilityEvaluator",
    "AnswerabilityRequirement",
    "AnswerabilityStatus",
    "AnswerabilityVerdict",
    "ContextAblation",
    "ContextPresenceCredit",
    "ClaimEvidence",
    "ClaimVerification",
    "ClaimVerificationConfig",
    "ClaimVerificationError",
    "ClaimVerificationStatus",
    "ClaimVerifier",
    "DEFAULT_PACKING_CONFIG",
    "DEFAULT_SCORING_WEIGHTS",
    "EntitySnapshot",
    "EvidenceGroupScore",
    "ExcludedCandidate",
    "format_for_llm",
    "MemoryBundle",
    "PackingConfig",
    "QueryAnalysis",
    "RetrievalCandidate",
    "RetrievalMetadata",
    "RetrievalPipeline",
    "RetrievalResponse",
    "ScoreTrace",
    "ScoringWeights",
    "compute_composite_score",
    "build_context_guidance",
    "ablate_context",
    "assess_answerability",
    "assess_context_presence",
    "filter_epistemic",
    "generate_all_entity_snapshots",
    "generate_entity_snapshot",
    "pack_context",
    "reformulate_query",
    "render_snapshot_text",
    "score_and_rank",
    "should_abstain",
]
