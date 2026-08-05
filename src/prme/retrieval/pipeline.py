"""RetrievalPipeline orchestrator for the hybrid retrieval pipeline.

Chains the stages in sequence:
1. Query Analysis (intent, entities, temporal signals)
2. Candidate Generation (graph, vector, lexical, pinned -- parallel)
3. Candidate Merging (deduplicate by node_id, track paths)
2.5. Entity-Focused Expansion (per-entity lexical fan-out)
2.6. Multi-Query Reformulation (opt-in LLM alt-query fan-out, issue #43)
4. Epistemic Filtering (exclude HYPOTHETICAL/DEPRECATED in DEFAULT mode)
5. Scoring + Ranking (8-input composite score, deterministic sort)
5.5b. Session Context Expansion (pull adjacent turns from same session)
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
from collections.abc import Sequence
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
from prme.retrieval.query_analysis import DEFAULT_TEMPORAL_LANGUAGES, analyze_query
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import EdgeType, LifecycleState, RepresentationLevel, RetrievalMode, Scope

if TYPE_CHECKING:
    from prme.storage.graph_store import GraphStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)


def _apply_bitemporal_filters(
    candidates: list[RetrievalCandidate],
    knowledge_at: datetime | None,
    event_time_from: datetime | None,
    event_time_to: datetime | None,
) -> list[RetrievalCandidate]:
    """Drop candidates outside the bi-temporal window (issue #21).

    Operates on the MemoryNode attached to each candidate, so it works
    regardless of which stage produced the candidate. Nodes without an
    ``event_time`` fall back to ``created_at`` (ingestion time).
    """
    if knowledge_at is not None:
        candidates = [c for c in candidates if c.node.created_at <= knowledge_at]
    if event_time_from is not None:
        candidates = [
            c for c in candidates
            if (c.node.event_time or c.node.created_at) >= event_time_from
        ]
    if event_time_to is not None:
        candidates = [
            c for c in candidates
            if (c.node.event_time or c.node.created_at) <= event_time_to
        ]
    return candidates


class RetrievalPipeline:
    """Hybrid retrieval pipeline orchestrator.

    Chains query analysis, candidate generation, optional multi-query
    reformulation, epistemic filtering, scoring, context packing, and
    operation logging into a single ``retrieve()`` call that returns a
    RetrievalResponse.

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
        enable_reranker: bool = False,
        reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        reranker_top_k: int = 100,
        enable_query_reformulation: bool = False,
        query_reformulation_count: int = 2,
        query_reformulation_provider: str = "openai",
        query_reformulation_model: str = "gpt-4o-mini",
        temporal_languages: Sequence[str] | None = DEFAULT_TEMPORAL_LANGUAGES,
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
        self._reranker_top_k = reranker_top_k
        self._enable_query_reformulation = enable_query_reformulation
        self._query_reformulation_count = query_reformulation_count
        self._query_reformulation_provider = query_reformulation_provider
        self._query_reformulation_model = query_reformulation_model
        self._temporal_languages = temporal_languages

        # Lazy-init cross-encoder reranker when enabled.
        self._reranker = None
        if enable_reranker:
            from prme.retrieval.reranker import CrossEncoderReranker

            self._reranker = CrossEncoderReranker(model_name=reranker_model)

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: Scope | list[Scope] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        knowledge_at: datetime | None = None,
        event_time_from: datetime | None = None,
        event_time_to: datetime | None = None,
        token_budget: int | None = None,
        weights: ScoringWeights | None = None,
        min_fidelity: RepresentationLevel | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
        include_cross_scope: bool = True,
    ) -> RetrievalResponse:
        """Execute the full hybrid retrieval pipeline.

        This is the unified entry point for hybrid retrieval. Runs all
        stages in sequence and returns a RetrievalResponse with a packed
        MemoryBundle, scored results, metadata, and always-on score traces.

        Bi-temporal query support (issue #21):
        - ``knowledge_at``: Point-in-time knowledge snapshot. Post-filters
          results to include only nodes with created_at <= knowledge_at.
        - ``event_time_from``/``event_time_to``: Filters results by the
          event_time field (when events actually happened).

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
            knowledge_at: Point-in-time knowledge snapshot (bi-temporal).
                Only includes nodes ingested on or before this datetime.
            event_time_from: Filter by event_time >= this value (bi-temporal).
            event_time_to: Filter by event_time <= this value (bi-temporal).
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

        # String form used by the lexical/vector index scope filters.
        scope_values = (
            [s.value for s in normalized_scope] if normalized_scope else None
        )

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
            languages=self._temporal_languages,
        )

        # Determine effective temporal window: explicit params take priority
        # over analysis-derived values from query text.
        effective_time_from = time_from if time_from is not None else analysis.time_from
        effective_time_to = time_to if time_to is not None else analysis.time_to

        # --- Aggregation boost: widen candidate pool for count/total queries ---
        candidate_config = effective_packing_config
        if analysis.is_aggregation:
            mult = effective_packing_config.aggregation_k_multiplier
            cap = effective_packing_config.aggregation_k_max
            candidate_config = effective_packing_config.model_copy(
                update={
                    "vector_k": min(int(effective_packing_config.vector_k * mult), cap),
                    "lexical_k": min(int(effective_packing_config.lexical_k * mult), cap),
                    "graph_max_candidates": min(
                        int(effective_packing_config.graph_max_candidates * mult), cap
                    ),
                }
            )

        # --- Aggregation: exhaustive keyword scan ---
        # For aggregation queries, supplement embedding search with a
        # comprehensive lexical scan using key terms from the query.
        # This catches events that embedding similarity misses.
        aggregation_extra: list[RetrievalCandidate] = []
        if analysis.is_aggregation:
            import re
            # Extract content words (nouns, verbs) from the query
            stop = {"how", "many", "much", "total", "did", "do", "have",
                    "has", "the", "and", "or", "in", "on", "at", "to",
                    "of", "for", "with", "from", "last", "past", "all",
                    "what", "list", "count", "number", "been", "was",
                    "were", "are", "is", "my", "me", "few", "spend",
                    "spent", "any", "some", "each", "every", "a", "an",
                    "that", "this", "those", "these", "it", "its"}
            words = re.findall(r'\b[a-z]{3,}\b', query.lower())
            keywords = [w for w in words if w not in stop]
            # Search for each keyword pair for better precision
            search_terms = keywords[:5]
            if len(search_terms) >= 2:
                # Also try bigrams
                for i in range(len(search_terms) - 1):
                    search_terms.append(f"{keywords[i]} {keywords[i+1]}")
            # Run all term searches concurrently, then resolve the unique
            # hits with a single batched node fetch (instead of sequential
            # searches with one get_node round trip per hit).
            # The scope filter is forwarded here as it is on the main lexical
            # path: without it this scan pulls other scopes' nodes straight
            # into the primary result list (issue #60).
            term_results = await asyncio.gather(
                *[
                    self._lexical_index.search(
                        term, user_id=user_id, limit=50, scope=scope_values,
                    )
                    for term in search_terms
                ],
                return_exceptions=True,
            )
            agg_seen_ids: set[str] = set()
            agg_hits: list[dict] = []
            for hits in term_results:
                if isinstance(hits, BaseException):
                    continue
                for hit in hits:
                    nid = hit["node_id"]
                    if nid not in agg_seen_ids:
                        agg_seen_ids.add(nid)
                        agg_hits.append(hit)
            if agg_hits:
                try:
                    agg_nodes = await self._graph_store.get_nodes(
                        [hit["node_id"] for hit in agg_hits]
                    )
                    agg_node_map = {str(n.id): n for n in agg_nodes}
                    for hit in agg_hits:
                        node = agg_node_map.get(hit["node_id"])
                        if node is not None:
                            aggregation_extra.append(RetrievalCandidate(
                                node=node,
                                paths=["LEXICAL"],
                                path_count=1,
                                lexical_score=hit.get("score", 0.0),
                            ))
                except Exception:
                    pass

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
            config=candidate_config,
        )

        # Merge aggregation extras into candidate pool
        if aggregation_extra:
            existing_ids = {str(c.node.id) for c in candidates}
            added = 0
            for c in aggregation_extra:
                if str(c.node.id) not in existing_ids:
                    existing_ids.add(str(c.node.id))
                    candidates.append(c)
                    added += 1
            if added:
                candidate_counts["LEXICAL_AGG"] = added

        # --- Stage 2.5: Entity-Focused Retrieval Expansion ---
        # When entities are extracted from the query, run additional lexical
        # searches for each entity name to catch facts that vector similarity
        # misses (e.g., "Sweden" mentioned once in a tangential context).
        if analysis.entities:
            existing_ids = {str(c.node.id) for c in candidates}
            # Run the per-entity searches concurrently, then resolve the
            # unique new hits with a single batched node fetch.
            entity_names = analysis.entities[:3]
            entity_results = await asyncio.gather(
                *[
                    self._lexical_index.search(
                        name, user_id=user_id, limit=20, scope=scope_values,
                    )
                    for name in entity_names
                ],
                return_exceptions=True,
            )
            entity_hits: list[dict] = []
            for name, hits in zip(entity_names, entity_results):
                if isinstance(hits, BaseException):
                    logger.debug(
                        "Entity-focused retrieval failed for '%s'; continuing",
                        name,
                        exc_info=hits,
                    )
                    continue
                for hit in hits:
                    nid = hit["node_id"]
                    if nid in existing_ids:
                        continue
                    existing_ids.add(nid)
                    entity_hits.append(hit)
            if entity_hits:
                try:
                    entity_nodes = await self._graph_store.get_nodes(
                        [hit["node_id"] for hit in entity_hits]
                    )
                    entity_node_map = {str(n.id): n for n in entity_nodes}
                    for hit in entity_hits:
                        node = entity_node_map.get(hit["node_id"])
                        if node is not None:
                            candidates.append(RetrievalCandidate(
                                node=node,
                                paths=["LEXICAL"],
                                path_count=1,
                                lexical_score=hit.get("score", 0.0),
                            ))
                            candidate_counts["LEXICAL"] = candidate_counts.get("LEXICAL", 0) + 1
                except Exception:
                    logger.debug(
                        "Entity-focused node resolution failed; continuing",
                        exc_info=True,
                    )

        # --- Stage 2.6: Multi-Query Reformulation (opt-in, issue #43) ---
        # When enabled, ask an LLM for alternative phrasings of the query, run
        # each as an additional candidate-generation pass, and merge the new
        # hits (deduplicated by node id) into the candidate pool. Off by
        # default: retrieve() makes no LLM calls unless this is configured.
        if self._enable_query_reformulation:
            reform_added = await self._expand_reformulated_queries(
                query,
                candidates=candidates,
                user_id=user_id,
                scope=normalized_scope,
                time_from=effective_time_from,
                time_to=effective_time_to,
                retrieval_mode=analysis.retrieval_mode,
                config=candidate_config,
            )
            if reform_added:
                candidate_counts["REFORMULATION"] = reform_added

        # Track embedding mismatch from candidates module.
        # If VECTOR count is 0 but no explicit error, we check the flag
        # via the candidates module's logging. For now, infer from counts.
        embedding_mismatch = candidate_counts.get("VECTOR", 0) == 0

        # --- Stage 3.5: Bi-temporal Post-Filtering (issue #21) ---
        # Applied after candidate generation and before epistemic filtering.
        candidates = _apply_bitemporal_filters(
            candidates, knowledge_at, event_time_from, event_time_to
        )

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
            query_analysis=analysis,
        )

        # --- Stage 5a: Neural Reranking (optional) ---
        if self._reranker is not None:
            scored = await self._reranker.rerank(
                query=query,
                candidates=scored,
                top_k=self._reranker_top_k,
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
            # Fetch CONTRADICTS edges touching ANY contested node in one
            # batched query (instead of 2 queries per contested node),
            # and index scored candidates by id once.
            contradicts_edges = await self._graph_store.get_edges(
                node_ids=contested_ids, edge_type=EdgeType.CONTRADICTS
            )
            scored_by_id = {str(c.node.id): c for c in scored}
            for cid in contested_ids:
                # Preserve direction priority: outgoing edges first, then
                # incoming, matching the previous per-node query order.
                edges_out = [
                    e for e in contradicts_edges if str(e.source_id) == cid
                ]
                edges_in = [
                    e for e in contradicts_edges if str(e.target_id) == cid
                ]
                all_edges = edges_out + edges_in
                if all_edges:
                    # Find the counterpart node ID
                    edge = all_edges[0]
                    counterpart_id = (
                        str(edge.target_id) if str(edge.source_id) == cid
                        else str(edge.source_id)
                    )
                    # Annotate the candidate
                    candidate = scored_by_id.get(cid)
                    if candidate is not None:
                        candidate.conflict_flag = True
                        candidate.contradicts_id = uuid.UUID(counterpart_id)

        # --- Stage 5.5b: Session Context Expansion ---
        # After scoring, expand top results with adjacent turns from the
        # same session_id. This addresses the "orphaned question" problem
        # where a question is retrieved but not its adjacent answer.
        if effective_packing_config.session_context_window > 0:
            try:
                expanded = await expand_session_context(
                    scored,
                    graph_store=self._graph_store,
                    user_id=user_id,
                    config=effective_packing_config,
                    scope=normalized_scope,
                )
                # Expansion appends adjacent turns that never went through
                # Stages 3.5 and 4, so the same predicates run once more over
                # the expanded set (issue #60). Nodes that already passed are
                # unaffected; only the newly appended ones can be dropped.
                if len(expanded) != len(scored):
                    expanded = _apply_bitemporal_filters(
                        expanded, knowledge_at, event_time_from, event_time_to
                    )
                    expanded, late_excluded = filter_epistemic(
                        expanded,
                        analysis.retrieval_mode,
                        unverified_threshold=self._unverified_confidence_threshold,
                    )
                    excluded.extend(late_excluded)
                scored = expanded
            except Exception:
                logger.warning(
                    "Session context expansion failed; continuing without expansion",
                    exc_info=True,
                )

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
                # Cheapest backends only (vector + lexical): the graph and
                # pinned backends are skipped so the hint pass doesn't
                # repeat the full candidate pipeline.
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
                    include_graph=False,
                    include_pinned=False,
                )
                # Filter to only results NOT in primary scopes.
                primary_scope_values = {s.value for s in normalized_scope}
                hint_candidates = [
                    c for c in hint_candidates
                    if c.node.scope.value not in primary_scope_values
                ]
                # Crossing the scope boundary is the point of this pass; the
                # bi-temporal window and the epistemic filter still apply
                # (issue #60).
                hint_candidates = _apply_bitemporal_filters(
                    hint_candidates, knowledge_at, event_time_from, event_time_to
                )
                hint_candidates, _ = filter_epistemic(
                    hint_candidates,
                    analysis.retrieval_mode,
                    unverified_threshold=self._unverified_confidence_threshold,
                )
                # Score the hints using the same weights and timestamp.
                if hint_candidates:
                    scored_hints, _ = score_and_rank(
                        hint_candidates, effective_weights, now=scoring_now,
                        query_analysis=analysis,
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

    async def _expand_reformulated_queries(
        self,
        query: str,
        *,
        candidates: list[RetrievalCandidate],
        user_id: str,
        scope: list[Scope] | None,
        time_from: datetime | None,
        time_to: datetime | None,
        retrieval_mode: RetrievalMode,
        config: PackingConfig,
    ) -> int:
        """Run LLM-reformulated alternate queries and merge new candidates.

        Asks the configured LLM for alternative phrasings of ``query``, runs
        each through the same rule-based analysis + candidate generation as the
        original, and appends candidates whose node id is not already present
        in ``candidates`` (mutated in place). Each alternate query reuses the
        original's effective scope, temporal window, and retrieval mode so
        results stay comparable.

        ``reformulate_query`` never raises (it returns an empty list on any
        failure), and the per-query candidate-generation passes run via
        ``asyncio.gather(..., return_exceptions=True)`` so a single failing
        alternate query cannot break the others or the original retrieval --
        on total failure no candidates are added and the original query's
        results stand.

        Returns:
            The number of new candidates appended across all alternate queries.
        """
        from prme.retrieval.reformulation import reformulate_query

        alt_queries = await reformulate_query(
            query,
            provider=self._query_reformulation_provider,
            model=self._query_reformulation_model,
            count=self._query_reformulation_count,
        )
        if not alt_queries:
            return 0

        # Fan out the alternate-query passes concurrently (matching the
        # aggregation-scan and entity-expansion stages above), then merge.
        async def _gen(alt_q: str) -> list[RetrievalCandidate]:
            alt_analysis = await analyze_query(
                alt_q,
                time_from=time_from,
                time_to=time_to,
                retrieval_mode=retrieval_mode,
                languages=self._temporal_languages,
            )
            alt_candidates, _ = await generate_candidates(
                alt_analysis,
                graph_store=self._graph_store,
                vector_index=self._vector_index,
                lexical_index=self._lexical_index,
                user_id=user_id,
                scope=scope,
                time_from=time_from,
                time_to=time_to,
                config=config,
            )
            return alt_candidates

        results = await asyncio.gather(
            *[_gen(alt_q) for alt_q in alt_queries],
            return_exceptions=True,
        )

        existing_ids = {str(c.node.id) for c in candidates}
        added = 0
        for alt_q, alt_candidates in zip(alt_queries, results):
            if isinstance(alt_candidates, BaseException):
                logger.debug(
                    "Reformulated query retrieval failed for %r; skipping",
                    alt_q,
                    exc_info=alt_candidates,
                )
                continue
            for c in alt_candidates:
                nid = str(c.node.id)
                if nid in existing_ids:
                    continue
                existing_ids.add(nid)
                candidates.append(c)
                added += 1
        return added
