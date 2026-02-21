"""PRME hybrid retrieval pipeline.

Public API exports for the retrieval module. All data models, scoring
configuration, filtering, scoring, packing, and pipeline orchestrator
are importable from this package.
"""

from prme.retrieval.config import (
    DEFAULT_PACKING_CONFIG,
    DEFAULT_SCORING_WEIGHTS,
    PackingConfig,
    ScoringWeights,
)
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import (
    ExcludedCandidate,
    MemoryBundle,
    QueryAnalysis,
    RetrievalCandidate,
    RetrievalMetadata,
    RetrievalResponse,
    ScoreTrace,
)
from prme.retrieval.packing import pack_context
from prme.retrieval.pipeline import RetrievalPipeline
from prme.retrieval.scoring import compute_composite_score, score_and_rank

__all__ = [
    "DEFAULT_PACKING_CONFIG",
    "DEFAULT_SCORING_WEIGHTS",
    "ExcludedCandidate",
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
    "filter_epistemic",
    "pack_context",
    "score_and_rank",
]
