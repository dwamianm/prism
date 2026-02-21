"""RetrievalPipeline orchestrator for the 6-stage hybrid retrieval pipeline.

Chains all six stages in sequence:
1. Query Analysis (intent, entities, temporal signals)
2. Candidate Generation (graph, vector, lexical, pinned -- parallel)
3. Candidate Merging (deduplicate by node_id, track paths)
4. Epistemic Filtering (exclude HYPOTHETICAL/DEPRECATED in DEFAULT mode)
5. Scoring + Ranking (8-input composite score, deterministic sort)
6. Context Packing (3-priority greedy bin-packing within token budget)

Each retrieval generates a RETRIEVAL_REQUEST operation record with a unique
request_id for replay capability and audit trail.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import duckdb

from prme.retrieval.candidates import generate_candidates
from prme.retrieval.config import (
    DEFAULT_PACKING_CONFIG,
    DEFAULT_SCORING_WEIGHTS,
    PackingConfig,
    ScoringWeights,
)
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import (
    RetrievalMetadata,
    RetrievalResponse,
)
from prme.retrieval.packing import pack_context
from prme.retrieval.query_analysis import analyze_query
from prme.retrieval.scoring import score_and_rank
from prme.types import RepresentationLevel, RetrievalMode, Scope

if TYPE_CHECKING:
    from prme.storage.graph_store import GraphStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)


class RetrievalPipeline:
    """6-stage retrieval pipeline orchestrator.

    Chains query analysis, candidate generation, epistemic filtering,
    scoring, context packing, and operation logging into a single
    ``retrieve()`` call that returns a RetrievalResponse.

    All backend references and configuration are injected at construction.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        vector_index: VectorIndex,
        lexical_index: LexicalIndex,
        conn: duckdb.DuckDBPyConnection,
        scoring_weights: ScoringWeights = DEFAULT_SCORING_WEIGHTS,
        packing_config: PackingConfig = DEFAULT_PACKING_CONFIG,
    ) -> None:
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._conn = conn
        self._scoring_weights = scoring_weights
        self._packing_config = packing_config

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: Scope | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        token_budget: int | None = None,
        weights: ScoringWeights | None = None,
        min_fidelity: RepresentationLevel | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
    ) -> RetrievalResponse:
        """Execute the full 6-stage retrieval pipeline.

        This is the unified entry point for hybrid retrieval. Runs all
        stages in sequence and returns a RetrievalResponse with a packed
        MemoryBundle, scored results, metadata, and always-on score traces.

        Args:
            query: Raw query text from the user.
            user_id: User ID for scoping all backend queries.
            scope: Optional scope filter.
            time_from: Explicit start of temporal window.
            time_to: Explicit end of temporal window.
            token_budget: Override default token budget for this request.
            weights: Override default scoring weights for this request.
            min_fidelity: Override minimum representation level.
            retrieval_mode: Retrieval mode controlling epistemic filtering.

        Returns:
            RetrievalResponse with bundle, results, metadata, and score traces.
        """
        start_time = time.monotonic()

        # Resolve effective configuration.
        effective_weights = weights if weights is not None else self._scoring_weights
        effective_packing_config = self._packing_config

        if token_budget is not None or min_fidelity is not None:
            # Create a modified packing config with overrides.
            overrides: dict = {}
            if token_budget is not None:
                overrides["token_budget"] = token_budget
            if min_fidelity is not None:
                overrides["min_fidelity"] = min_fidelity
            effective_packing_config = self._packing_config.model_copy(
                update=overrides
            )

        # --- Stage 1: Query Analysis ---
        analysis = await analyze_query(
            query,
            time_from=time_from,
            time_to=time_to,
            retrieval_mode=retrieval_mode,
        )

        # --- Stages 2-3: Candidate Generation + Merging ---
        candidates, candidate_counts = await generate_candidates(
            analysis,
            graph_store=self._graph_store,
            vector_index=self._vector_index,
            lexical_index=self._lexical_index,
            user_id=user_id,
            scope=scope,
            config=effective_packing_config,
        )

        # Track embedding mismatch from candidates module.
        # If VECTOR count is 0 but no explicit error, we check the flag
        # via the candidates module's logging. For now, infer from counts.
        embedding_mismatch = candidate_counts.get("VECTOR", 0) == 0

        # --- Stage 4: Epistemic Filtering ---
        filtered, excluded = filter_epistemic(candidates, analysis.retrieval_mode)

        # --- Stage 5: Scoring + Ranking ---
        scored, traces = score_and_rank(filtered, effective_weights)

        # --- Stage 6: Context Packing ---
        bundle = pack_context(scored, config=effective_packing_config)

        end_time = time.monotonic()
        timing_ms = (end_time - start_time) * 1000.0

        # --- Retrieval Logging ---
        try:
            op_id = str(uuid.uuid4())
            payload = json.dumps({
                "request_id": str(analysis.request_id),
                "query": query,
                "user_id": user_id,
                "candidates_generated": candidate_counts,
                "candidates_filtered": len(excluded),
                "candidates_included": bundle.included_count,
                "tokens_used": bundle.tokens_used,
                "scoring_config_version": effective_weights.version_id,
                "backends_used": list(candidate_counts.keys()),
                "embedding_mismatch": embedding_mismatch,
            })
            self._conn.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, now())",
                [op_id, "RETRIEVAL_REQUEST", str(analysis.request_id), payload, user_id],
            )
        except Exception:
            logger.warning(
                "Failed to log RETRIEVAL_REQUEST operation for request %s",
                analysis.request_id,
                exc_info=True,
            )

        # --- Assemble RetrievalResponse ---
        metadata = RetrievalMetadata(
            request_id=analysis.request_id,
            candidates_generated=candidate_counts,
            candidates_filtered=len(excluded),
            candidates_included=bundle.included_count,
            scoring_config_version=effective_weights.version_id,
            timing_ms=round(timing_ms, 2),
            backends_used=list(candidate_counts.keys()),
            embedding_mismatch=embedding_mismatch,
        )

        return RetrievalResponse(
            bundle=bundle,
            results=scored,
            metadata=metadata,
            score_traces=traces,
        )
