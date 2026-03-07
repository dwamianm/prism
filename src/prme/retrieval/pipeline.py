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

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
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
    FilterMetadata,
    RetrievalCandidate,
    RetrievalMetadata,
    RetrievalResponse,
)
from prme.retrieval.packing import pack_context
from prme.retrieval.query_analysis import analyze_query
from prme.retrieval.scoring import score_and_rank
from prme.types import EdgeType, LifecycleState, RepresentationLevel, RetrievalMode, Scope

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
        conn: duckdb.DuckDBPyConnection | None = None,
        conn_lock: asyncio.Lock | None = None,
        pool: object | None = None,
        scoring_weights: ScoringWeights = DEFAULT_SCORING_WEIGHTS,
        packing_config: PackingConfig = DEFAULT_PACKING_CONFIG,
        epistemic_weights: dict[str, float] | None = None,
        unverified_confidence_threshold: float | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._conn = conn
        self._conn_lock = conn_lock if conn_lock is not None else asyncio.Lock()
        self._pool = pool  # asyncpg.Pool for PostgreSQL mode
        self._scoring_weights = scoring_weights
        self._packing_config = packing_config
        self._epistemic_weights = epistemic_weights
        self._unverified_confidence_threshold = unverified_confidence_threshold

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: Scope | list[Scope] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        token_budget: int | None = None,
        weights: ScoringWeights | None = None,
        min_fidelity: RepresentationLevel | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
        include_cross_scope: bool = True,
    ) -> RetrievalResponse:
        """Execute the full 6-stage retrieval pipeline.

        This is the unified entry point for hybrid retrieval. Runs all
        stages in sequence and returns a RetrievalResponse with a packed
        MemoryBundle, scored results, metadata, and always-on score traces.

        Args:
            query: Raw query text from the user.
            user_id: User ID for scoping all backend queries.
            scope: Optional scope filter. Accepts a single Scope, a list of
                Scopes, or None (no filter). Single Scope is normalized to
                a list for backward compatibility.
            time_from: Explicit start of temporal window. If provided,
                overrides any temporal signal from query analysis.
            time_to: Explicit end of temporal window. If provided,
                overrides any temporal signal from query analysis.
            token_budget: Override default token budget for this request.
            weights: Override default scoring weights for this request.
            min_fidelity: Override minimum representation level.
            retrieval_mode: Retrieval mode controlling epistemic filtering.
            include_cross_scope: Whether to include cross-scope hints when
                scope is active. When True and a scope filter is set, a
                secondary vector+lexical pass runs without scope restriction
                to surface highly relevant results from other scopes.
                Results appear as separate cross_scope_hints list, never
                merged into primary results. Defaults to True.

        Returns:
            RetrievalResponse with bundle, results, metadata, and score traces.
        """
        start_time = time.monotonic()

        # Normalize scope: single Scope -> list, list -> as-is, None -> None.
        normalized_scope: list[Scope] | None = None
        if isinstance(scope, Scope):
            normalized_scope = [scope]
        elif isinstance(scope, list):
            normalized_scope = scope
        # else: None means "all scopes, no filter"

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

        # Determine effective temporal window: explicit params take priority
        # over analysis-derived values from query text.
        effective_time_from = time_from if time_from is not None else analysis.time_from
        effective_time_to = time_to if time_to is not None else analysis.time_to

        # --- Stages 2-3: Candidate Generation + Merging ---
        candidates, candidate_counts = await generate_candidates(
            analysis,
            graph_store=self._graph_store,
            vector_index=self._vector_index,
            lexical_index=self._lexical_index,
            user_id=user_id,
            scope=normalized_scope,
            time_from=effective_time_from,
            time_to=effective_time_to,
            config=effective_packing_config,
        )

        # Track embedding mismatch from candidates module.
        # If VECTOR count is 0 but no explicit error, we check the flag
        # via the candidates module's logging. For now, infer from counts.
        embedding_mismatch = candidate_counts.get("VECTOR", 0) == 0

        # --- Stage 4: Epistemic Filtering ---
        filtered, excluded = filter_epistemic(
            candidates, analysis.retrieval_mode,
            unverified_threshold=self._unverified_confidence_threshold,
        )

        # --- Stage 5: Scoring + Ranking ---
        # Capture a single timestamp so all candidates in this retrieval
        # use the same reference point for deterministic decay computation.
        scoring_now = datetime.now(timezone.utc)
        scored, traces = score_and_rank(
            filtered, effective_weights,
            epistemic_weights=self._epistemic_weights,
            now=scoring_now,
        )

        # --- Stage 5.5: Conflict Metadata Annotation ---
        # Batch-annotate CONTESTED candidates with conflict_flag and
        # contradicts_id so consuming LLMs can surface conflicts.
        # Per locked decision: counterparts are NOT auto-injected into
        # results -- only included if independently relevant to the query.
        contested_ids = [
            str(c.node.id) for c in scored
            if c.node.lifecycle_state == LifecycleState.CONTESTED
        ]
        if contested_ids:
            for cid in contested_ids:
                # Look up CONTRADICTS edges in both directions
                edges_out = await self._graph_store.get_edges(
                    source_id=cid, edge_type=EdgeType.CONTRADICTS
                )
                edges_in = await self._graph_store.get_edges(
                    target_id=cid, edge_type=EdgeType.CONTRADICTS
                )
                all_edges = edges_out + edges_in
                if all_edges:
                    # Find the counterpart node ID
                    edge = all_edges[0]
                    counterpart_id = (
                        str(edge.target_id) if str(edge.source_id) == cid
                        else str(edge.source_id)
                    )
                    # Annotate the candidate
                    for c in scored:
                        if str(c.node.id) == cid:
                            c.conflict_flag = True
                            c.contradicts_id = uuid.UUID(counterpart_id)

        # --- Cross-Scope Hint Generation ---
        # When scope is active and include_cross_scope=True, run a secondary
        # vector+lexical pass without scope filter (cheapest backends only,
        # per research Pattern 3) to surface highly relevant results from
        # other scopes. Hints are separate from primary results.
        cross_scope_hints: list[RetrievalCandidate] = []
        if normalized_scope is not None and include_cross_scope and candidates:
            try:
                # Build hint config with reduced k for performance.
                hint_config = effective_packing_config.model_copy(
                    update={
                        "vector_k": effective_packing_config.cross_scope_top_n * 2,
                        "lexical_k": effective_packing_config.cross_scope_top_n * 2,
                    }
                )
                # Secondary generation: no scope filter, WITH temporal filter.
                hint_candidates, _ = await generate_candidates(
                    analysis,
                    graph_store=self._graph_store,
                    vector_index=self._vector_index,
                    lexical_index=self._lexical_index,
                    user_id=user_id,
                    scope=None,  # No scope filter for hints
                    time_from=effective_time_from,
                    time_to=effective_time_to,
                    config=hint_config,
                )
                # Filter to only results NOT in primary scopes.
                primary_scope_values = {s.value for s in normalized_scope}
                hint_candidates = [
                    c for c in hint_candidates
                    if c.node.scope.value not in primary_scope_values
                ]
                # Score the hints using the same weights and timestamp.
                if hint_candidates:
                    scored_hints, _ = score_and_rank(
                        hint_candidates, effective_weights, now=scoring_now,
                    )
                    # Only include top-N as cross-scope hints.
                    cross_scope_hints = scored_hints[
                        : effective_packing_config.cross_scope_top_n
                    ]
            except Exception:
                logger.warning(
                    "Cross-scope hint generation failed; continuing without hints",
                    exc_info=True,
                )

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
                "scope_filter": [s.value for s in normalized_scope] if normalized_scope else None,
                "time_from": effective_time_from.isoformat() if effective_time_from else None,
                "time_to": effective_time_to.isoformat() if effective_time_to else None,
                "cross_scope_hint_count": len(cross_scope_hints),
            })
            if self._pool is not None:
                # PostgreSQL backend
                async with self._pool.acquire() as pg_conn:
                    await pg_conn.execute(
                        "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                        "VALUES ($1, $2, $3, $4::jsonb, $5, now())",
                        op_id, "RETRIEVAL_REQUEST", str(analysis.request_id), payload, user_id,
                    )
            elif self._conn is not None:
                async with self._conn_lock:
                    await asyncio.to_thread(
                        self._conn.execute,
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

        # Build filter metadata for debugging/explainability.
        filter_meta = FilterMetadata(
            scope_filter=[s.value for s in normalized_scope] if normalized_scope else None,
            time_from=effective_time_from,
            time_to=effective_time_to,
            cross_scope_enabled=include_cross_scope and normalized_scope is not None,
        )

        return RetrievalResponse(
            bundle=bundle,
            results=scored,
            metadata=metadata,
            score_traces=traces,
            filter_metadata=filter_meta,
            cross_scope_hints=cross_scope_hints,
        )
