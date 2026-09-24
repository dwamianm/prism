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
5.5c. Episode Context Expansion (route sessions, then select local evidence)
5.5d. Evidence Projection (replace derived groups with bounded direct sources)
6. Context Packing (deterministic priority packing within token budget)

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
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Protocol

import duckdb

from prme.models.learning import RankingMultipliers
from prme.retrieval.execution import RetrievalExecution, feature_identity, reranker_identity
from prme.retrieval.candidates import (
    CandidateDiagnostics,
    generate_candidates,
    merge_normalized_bm25_hits,
)
from prme.retrieval.config import (
    DEFAULT_PACKING_CONFIG,
    DEFAULT_SCORING_WEIGHTS,
    PackingConfig,
    ScoringWeights,
)
from prme.retrieval.context_formatter import (
    build_context_guidance,
    is_temporal_reasoning_query,
)
from prme.retrieval.evidence_context import (
    augment_evidence_context,
    project_evidence_context,
)
from prme.retrieval.episode_context import expand_episode_context
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import (
    AggregationCoverage,
    AggregationLimitation,
    FilterMetadata,
    HistoricalCoverage,
    RetrievalCandidate,
    RetrievalMetadata,
    RetrievalResponse,
)
from prme.retrieval.packing import pack_context, requires_memory_text
from prme.retrieval.query_analysis import DEFAULT_TEMPORAL_LANGUAGES, analyze_query
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.scope import ScopeInput, normalize_scope
from prme.retrieval.selection import (
    select_candidates,
    validate_selection,
    with_rank_fusion_relevance,
)
from prme.retrieval.session_context import expand_session_context
from prme.retrieval.temporal_relations import (
    TemporalRelationConfig,
    TemporalRelationEnricher,
)
from prme.types import (
    EdgeType,
    LifecycleState,
    NodeType,
    RepresentationLevel,
    RetrievalMode,
    Scope,
    has_memory_text,
)

if TYPE_CHECKING:
    from pydantic import SecretStr

    from prme.models.relevance import RankingPolicy
    from prme.storage.graph_store import GraphStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)

_AGGREGATION_COVERAGE_NOTICE = (
    "Aggregation coverage: semantic candidates only; this is not an exhaustive "
    "stored-record enumeration. Do not claim a complete count or list from this context."
)
_HISTORICAL_COVERAGE_NOTICE = (
    "Historical coverage: knowledge_at is an ingestion-time cutoff over current "
    "lifecycle and derived indexes; mutations are not replayed. Do not treat this "
    "context as an exact historical snapshot."
)


class _OperationConnection(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...


class _OperationPool(Protocol):
    def acquire(self) -> AbstractAsyncContextManager[_OperationConnection]: ...


def _apply_bitemporal_filters(
    candidates: list[RetrievalCandidate],
    knowledge_at: datetime | None,
    event_time_from: datetime | None,
    event_time_to: datetime | None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> list[RetrievalCandidate]:
    """Drop candidates outside the bi-temporal window (issue #21).

    Operates on the MemoryNode attached to each candidate, so it works
    regardless of which stage produced the candidate. Nodes without an
    ``event_time`` fall back to ``created_at`` (ingestion time).
    """
    # Match graph/vector validity semantics for every source, including
    # lexical scans, pinned nodes, session expansion, and cross-scope hints.
    exempt = {NodeType.ENTITY, NodeType.PREFERENCE}
    if time_from is not None:
        candidates = [c for c in candidates if (
            c.node.node_type in exempt or c.node.valid_to is None or c.node.valid_to > time_from
        )]
    if time_to is not None:
        candidates = [c for c in candidates if (
            c.node.node_type in exempt or c.node.valid_from is None or c.node.valid_from <= time_to
        )]
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
        pool: _OperationPool | None = None,
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
        temporal_relation_config: TemporalRelationConfig | None = None,
        temporal_relation_enricher: TemporalRelationEnricher | None = None,
        reranker_policy: Literal["legacy", "score_envelope", "anchored_score_envelope"] = "legacy",
        query_reformulation_merge_policy: Literal["new_only", "max_signals"] = "new_only",
        query_reformulation_api_key: SecretStr | None = None,
        query_reformulation_base_url: str | None = None,
        query_reformulation_timeout: float | None = None,
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
        self._query_reformulation_api_key = query_reformulation_api_key
        self._query_reformulation_base_url = query_reformulation_base_url
        self._query_reformulation_timeout = query_reformulation_timeout
        # Reformulation clients live as long as this pipeline, on its event loop.
        self._query_reformulation_clients: dict = {}
        if reranker_policy not in {"legacy", "score_envelope", "anchored_score_envelope"}:
            raise ValueError("Unknown reranker policy")
        if query_reformulation_merge_policy not in {"new_only", "max_signals"}:
            raise ValueError("Unknown query reformulation merge policy")
        self._query_reformulation_merge_policy = query_reformulation_merge_policy
        self._temporal_languages = temporal_languages
        self._temporal_relation_config = (
            temporal_relation_config or TemporalRelationConfig()
        ).model_copy(deep=True)
        if (
            temporal_relation_enricher is not None
            and not self._temporal_relation_config.enabled
        ):
            raise ValueError(
                "temporal_relation_enricher requires temporal relation configuration to be enabled"
            )
        self._temporal_relation_enricher = temporal_relation_enricher
        if (
            self._temporal_relation_config.enabled
            and self._temporal_relation_enricher is None
        ):
            from prme.retrieval.temporal_relation_providers import (
                create_temporal_relation_enricher,
            )

            self._temporal_relation_enricher = create_temporal_relation_enricher(
                self._temporal_relation_config
            )

        # Lazy-init cross-encoder reranker when enabled.
        self._reranker = None
        if enable_reranker:
            from prme.retrieval.reranker import CrossEncoderReranker

            self._reranker = CrossEncoderReranker(model_name=reranker_model, policy=reranker_policy)

        self._feature_identity = feature_identity(vector_index, lexical_index, self._reranker)
        self._feature_identity["temporal_relation"] = {
            "enabled": self._temporal_relation_config.enabled,
            "protocol": "temporal_relation_v1",
            "resolver_provider": self._temporal_relation_config.resolver_provider,
            "resolver_model": self._temporal_relation_config.resolver_model,
            "gate_provider": self._temporal_relation_config.gate_provider,
            "gate_model": self._temporal_relation_config.gate_model,
            "gate_threshold": self._temporal_relation_config.gate_threshold,
            "confirmation_protocol_aligned": (
                self._temporal_relation_config.confirmation_protocol_aligned
            ),
            "configuration_sha256": self._temporal_relation_config.configuration_sha256,
        }

    def execution_features(self) -> dict:
        """Return the exact feature identity used for a new receipt."""
        features = {**self._feature_identity, "reranker": reranker_identity(self._reranker)}
        if self._enable_query_reformulation and self._query_reformulation_merge_policy != "new_only":
            features["query_reformulation_merge"] = {
                "policy": self._query_reformulation_merge_policy,
                "version": 1,
            }
        return features

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: ScopeInput = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        reference_time: datetime | None = None,
        knowledge_at: datetime | None = None,
        event_time_from: datetime | None = None,
        event_time_to: datetime | None = None,
        token_budget: int | None = None,
        min_score: float | None = None,
        limit: int | None = None,
        max_per_source: int | None = None,
        max_per_evidence: int | None = None,
        weights: ScoringWeights | None = None,
        ranking_multipliers: RankingMultipliers | None = None,
        ranking_profile: dict | None = None,
        min_fidelity: RepresentationLevel | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
        include_cross_scope: bool = True,
    ) -> RetrievalResponse:
        """Execute the full hybrid retrieval pipeline.

        This is the unified entry point for hybrid retrieval. Runs all
        stages in sequence and returns a RetrievalResponse with a packed
        MemoryBundle, scored results, metadata, and always-on score traces.

        Bi-temporal query support (issue #21):
        - ``knowledge_at``: Ingestion-time cutoff. Post-filters current
          candidates to include only nodes with created_at <= knowledge_at;
          it does not replay historical lifecycle or index state.
        - ``event_time_from``/``event_time_to``: Filters results by the
          event_time field (when events actually happened).

        Args:
            query: Raw query text from the user.
            user_id: User ID for scoping all backend queries.
            scope: Optional scope filter. Accepts a scope name or a nonempty sequence of
                Scopes, or None (no filter). Single Scope is normalized to
                a list for backward compatibility.
            time_from: Explicit start of temporal window. If provided,
                overrides any temporal signal from query analysis.
            time_to: Explicit end of temporal window. If provided,
                overrides any temporal signal from query analysis.
            reference_time: Timezone-aware clock for relative dates and
                scoring decay. Defaults to request time; not a knowledge cutoff.
            knowledge_at: Timezone-aware ingestion-time cutoff. Only includes
                current-index candidates ingested on or before this datetime;
                historical lifecycle and index state are not replayed.
            event_time_from: Filter by event_time >= this value (bi-temporal).
            event_time_to: Filter by event_time <= this value (bi-temporal).
            token_budget: Override default token budget for this request.
            min_score: Inclusive ranking score floor; not a probability.
                Under rank fusion it is compared with each result's
                semantic_relevance (semantic cosine) instead of the fused score.
            limit: Maximum primary results before context packing. Zero returns none.
            max_per_source: Optional maximum results with the same exact source
                passage and evidence set.
            max_per_evidence: Optional maximum results with the same exact
                nonempty evidence set, including differently worded siblings.
            weights: Override default scoring weights for this request.
            ranking_multipliers: Explicit bounded adjustment after query-specific
                weight redistribution, before reranking and session expansion.
            min_fidelity: Override minimum representation level. A
                text-bearing level (structured, prose or full) keeps records
                that fit only as a text-free key_value or reference fallback,
                or whose text is blank, out of the context.
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
        validate_selection(min_score, limit, max_per_source, max_per_evidence)
        if ranking_multipliers is not None:
            ranking_multipliers = RankingMultipliers.model_validate_json(ranking_multipliers.model_dump_json())
        execution_features = self.execution_features()
        start_time = time.monotonic()
        if reference_time is not None and reference_time.utcoffset() is None:
            raise ValueError("reference_time must include a timezone")
        if knowledge_at is not None and knowledge_at.utcoffset() is None:
            raise ValueError("knowledge_at must include a timezone")
        scoring_now = (reference_time or datetime.now(timezone.utc)).astimezone(timezone.utc)

        normalized_scope = normalize_scope(scope)

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
                # model_copy skips validation, so convert here.
                overrides["min_fidelity"] = RepresentationLevel(min_fidelity)
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
            reference_time=scoring_now,
        )

        # Query dates guide temporal affinity; they are not assertion-validity
        # cutoffs. An episode from 2024 may legitimately be imported in 2026.
        # Only caller-supplied bounds impose a hard validity filter.
        effective_time_from = time_from
        effective_time_to = time_to

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

        # --- Aggregation: supplementary keyword scan ---
        # For aggregation queries, supplement embedding search with a
        # broader bounded lexical scan using key terms from the query.
        # This catches events that embedding similarity misses.
        aggregation_extra: list[RetrievalCandidate] = []
        aggregation_term_limit_reached = False
        aggregation_scan_failed = False
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
            successful_term_results: list[list[dict]] = []
            for hits in term_results:
                if isinstance(hits, BaseException):
                    aggregation_scan_failed = True
                    continue
                if len(hits) >= 50:
                    aggregation_term_limit_reached = True
                successful_term_results.append(hits)
            agg_hits = merge_normalized_bm25_hits(successful_term_results)
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
                                lexical_score=hit.get("normalized_score", 0.0),
                            ))
                except Exception:
                    aggregation_scan_failed = True
                    logger.debug(
                        "Aggregation candidate resolution failed; continuing",
                        exc_info=True,
                    )

        # --- Stages 2-3: Candidate Generation + Merging ---
        candidate_diagnostics = CandidateDiagnostics()
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
            diagnostics=candidate_diagnostics,
        )

        aggregation_candidate_limit_paths: list[str] = []
        if analysis.is_aggregation:
            backend_limits = {
                "GRAPH": candidate_config.graph_max_candidates,
                "VECTOR": candidate_config.vector_k,
                "LEXICAL": candidate_config.lexical_k,
                "PINNED": 500,
            }
            aggregation_candidate_limit_paths.extend(
                backend
                for backend, backend_limit in backend_limits.items()
                if candidate_counts.get(backend, 0) >= backend_limit
            )
            if aggregation_term_limit_reached:
                aggregation_candidate_limit_paths.append("LEXICAL_AGG")
            if aggregation_scan_failed:
                candidate_diagnostics.backend_failures["LEXICAL_AGG"] = "backend_error"

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
            successful_entity_results: list[list[dict]] = []
            for name, hits in zip(entity_names, entity_results):
                if isinstance(hits, BaseException):
                    logger.debug(
                        "Entity-focused retrieval failed for '%s'; continuing",
                        name,
                        exc_info=hits,
                    )
                    continue
                successful_entity_results.append(hits)
            entity_hits = [
                hit
                for hit in merge_normalized_bm25_hits(successful_entity_results)
                if hit["node_id"] not in existing_ids
            ]
            existing_ids.update(hit["node_id"] for hit in entity_hits)
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
                                lexical_score=hit.get("normalized_score", 0.0),
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
                reference_time=scoring_now,
            )
            if reform_added:
                candidate_counts["REFORMULATION"] = reform_added

        embedding_mismatch = candidate_diagnostics.embedding_mismatch

        # --- Stage 3.5: Bi-temporal Post-Filtering (issue #21) ---
        # Applied after candidate generation and before epistemic filtering.
        candidates = _apply_bitemporal_filters(
            candidates, knowledge_at, event_time_from, event_time_to,
            effective_time_from, effective_time_to,
        )

        # --- Stage 4: Epistemic Filtering ---
        filtered, excluded = filter_epistemic(
            candidates, analysis.retrieval_mode,
            unverified_threshold=self._unverified_confidence_threshold,
        )

        # --- Stage 5: Scoring + Ranking ---
        # Capture a single timestamp so all candidates in this retrieval
        # use the same reference point for deterministic decay computation.
        scored, traces = score_and_rank(
            filtered, effective_weights,
            epistemic_weights=self._epistemic_weights,
            now=scoring_now,
            query_analysis=analysis,
            ranking_multipliers=ranking_multipliers,
        )

        # --- Stage 5a: Neural Reranking (optional) ---
        ranking_policy: RankingPolicy = "score_path_id"
        if self._reranker is not None:
            scored = await self._reranker.rerank(
                query=query,
                candidates=scored,
                top_k=self._reranker_top_k,
            )
            ranking_policy = "reranked_prefix"

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
                if expanded is not scored:
                    original_by_id = {candidate.node.id: candidate for candidate in scored}
                    if any(candidate.node.id not in original_by_id for candidate in expanded):
                        # Newly appended nodes did not pass the earlier temporal
                        # and epistemic filters (issue #60). Existing candidates
                        # already did, but filtering the combined list is stable.
                        expanded = _apply_bitemporal_filters(
                            expanded, knowledge_at, event_time_from, event_time_to,
                            effective_time_from, effective_time_to,
                        )
                        expanded, late_excluded = filter_epistemic(
                            expanded,
                            analysis.retrieval_mode,
                            unverified_threshold=self._unverified_confidence_threshold,
                        )
                        excluded.extend(late_excluded)
                    if (
                        [candidate.node.id for candidate in expanded]
                        != [candidate.node.id for candidate in scored]
                        or any(
                            candidate.composite_score
                            != original_by_id[candidate.node.id].composite_score
                            or candidate.reranker_score
                            != original_by_id[candidate.node.id].reranker_score
                            for candidate in expanded
                            if candidate.node.id in original_by_id
                        )
                    ):
                        ranking_policy = "score_id"
                scored = expanded
            except Exception:
                logger.warning(
                    "Session context expansion failed; continuing without expansion",
                    exc_info=True,
                )

        # --- Stage 5.5c: Two-stage Episode Context Expansion ---
        # Sessions are the existing episode boundary. Route complete candidate
        # episodes, then reserve a bounded local evidence set without an LLM.
        if effective_packing_config.episode_context_top_k > 0:
            try:
                expanded = expand_episode_context(
                    scored,
                    query,
                    effective_packing_config,
                )
                if (
                    [candidate.node.id for candidate in expanded]
                    != [candidate.node.id for candidate in scored]
                    or any(
                        candidate.composite_score != original.composite_score
                        for candidate, original in zip(expanded, scored)
                    )
                ):
                    ranking_policy = "score_id"
                scored = expanded
            except Exception:
                logger.warning(
                    "Episode context expansion failed; continuing without expansion",
                    exc_info=True,
                )

        # --- Stage 5.5d: Direct Evidence Projection ---
        # Derived claims route an evidence group; its direct source carries the
        # complete context. This opt-in stage replaces only groups with a visible,
        # owner/scope/time/epistemic-eligible source node.
        if effective_packing_config.evidence_projection_top_k > 0:
            try:
                projected = await project_evidence_context(
                    scored,
                    graph_store=self._graph_store,
                    user_id=user_id,
                    config=effective_packing_config,
                    scopes=normalized_scope,
                    retrieval_mode=analysis.retrieval_mode,
                    unverified_confidence_threshold=(
                        self._unverified_confidence_threshold
                    ),
                    knowledge_at=knowledge_at,
                    event_time_from=event_time_from,
                    event_time_to=event_time_to,
                    time_from=effective_time_from,
                    time_to=effective_time_to,
                )
                if projected is not scored:
                    ranking_policy = "score_id"
                scored = projected
            except Exception:
                logger.warning(
                    "Evidence projection failed; continuing without projection",
                    exc_info=True,
                )

        # Preserve concise semantic candidates while adding a bounded direct
        # source layer. PackingConfig rejects simultaneous replacement and
        # augmentation policies.
        if effective_packing_config.evidence_augmentation_top_k > 0:
            try:
                augmented = await augment_evidence_context(
                    scored,
                    graph_store=self._graph_store,
                    user_id=user_id,
                    config=effective_packing_config,
                    scopes=normalized_scope,
                    retrieval_mode=analysis.retrieval_mode,
                    unverified_confidence_threshold=(
                        self._unverified_confidence_threshold
                    ),
                    knowledge_at=knowledge_at,
                    event_time_from=event_time_from,
                    event_time_to=event_time_to,
                    time_from=effective_time_from,
                    time_to=effective_time_to,
                )
                if augmented is not scored:
                    ranking_policy = "score_id"
                scored = augmented
            except Exception:
                logger.warning(
                    "Evidence augmentation failed; continuing without augmentation",
                    exc_info=True,
                )

        # --- Cross-Scope Hint Generation ---
        # When scope is active and include_cross_scope=True, run a secondary
        # vector+lexical pass without scope filter (cheapest backends only,
        # per research Pattern 3) to surface highly relevant results from
        # other scopes. Hints are separate from primary results.
        cross_scope_hints: list[RetrievalCandidate] = []
        if (
            normalized_scope is not None
            and include_cross_scope
            and candidates
            and effective_packing_config.cross_scope_top_n > 0
        ):
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
                    hint_candidates, knowledge_at, event_time_from, event_time_to,
                    effective_time_from, effective_time_to,
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
                        ranking_multipliers=ranking_multipliers,
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

        aggregation_candidate_count = len(scored) if analysis.is_aggregation else 0

        if effective_weights.fusion == "rrf":
            # A fused score ranks within the pool, so an unrelated memory can
            # score near 1.0; min_score gates semantic cosine instead (issue
            # #110). The fused ranking and scores stay as they are.
            scored = with_rank_fusion_relevance(scored)
            cross_scope_hints = with_rank_fusion_relevance(cross_scope_hints)

        # Apply selection to results and the bundle together. Explicit count
        # and score bounds apply to pinned/tasks and adjacent context as well.
        scored, selection_excluded = select_candidates(
            scored,
            min_score=min_score,
            limit=limit,
            max_per_source=max_per_source,
            max_per_evidence=max_per_evidence,
        )
        excluded.extend(selection_excluded)
        cross_scope_hints, _ = select_candidates(cross_scope_hints, min_score=min_score, limit=None)
        traces = [c.score_trace for c in scored if c.score_trace is not None]

        # --- Stage 6: Context Packing ---
        coverage_notices = []
        if analysis.is_aggregation:
            coverage_notices.append(_AGGREGATION_COVERAGE_NOTICE)
        if knowledge_at is not None:
            coverage_notices.append(_HISTORICAL_COVERAGE_NOTICE)
        bundle = await asyncio.to_thread(
            pack_context,
            scored,
            config=effective_packing_config,
            coverage_notice="\n".join(coverage_notices) or None,
            context_guidance=build_context_guidance(
                query,
                query_analysis=analysis,
                reference_time=scoring_now,
                mode=effective_packing_config.context_guidance_mode,
                context_format=effective_packing_config.context_format,
            ),
        )

        temporal_relation_metadata = None
        if (
            self._temporal_relation_enricher is not None
            and is_temporal_reasoning_query(query, analysis)
        ):
            bundle, temporal_relation_metadata = (
                await self._temporal_relation_enricher.enrich(
                    query,
                    bundle,
                    question_time=scoring_now,
                    packing_config=effective_packing_config,
                )
            )

        aggregation_coverage: AggregationCoverage | None = None
        if analysis.is_aggregation:
            limitation_codes: list[AggregationLimitation] = ["semantic_matching"]
            if aggregation_candidate_limit_paths:
                limitation_codes.append("candidate_limit")
            if candidate_diagnostics.backend_failures:
                limitation_codes.append("backend_failure")
            selection_reasons = {item.reason for item in selection_excluded}
            if "below_threshold" in selection_reasons:
                limitation_codes.append("score_floor")
            if "result_limit" in selection_reasons:
                limitation_codes.append("result_limit")
            coverage_status: Literal[
                "semantic_candidates", "candidate_limited", "context_limited"
            ]
            # A record with no text to show is excluded for that reason, not
            # for lack of budget.
            blank_ids = (
                {c.node.id for c in scored if not has_memory_text(c.node.content)}
                if requires_memory_text(effective_packing_config)
                else set()
            )
            budget_limited = any(
                node_id not in blank_ids for node_id in bundle.excluded_ids
            )
            if budget_limited:
                limitation_codes.append("token_budget")

            if budget_limited:
                coverage_status = "context_limited"
            elif len(limitation_codes) > 1:
                coverage_status = "candidate_limited"
            else:
                coverage_status = "semantic_candidates"
            aggregation_coverage = AggregationCoverage(
                status=coverage_status,
                candidate_count=aggregation_candidate_count,
                selected_count=len(scored),
                context_count=bundle.included_count,
                limitations=tuple(limitation_codes),
                candidate_limit_paths=tuple(aggregation_candidate_limit_paths),
            )

        historical_coverage = (
            HistoricalCoverage(knowledge_at=knowledge_at)
            if knowledge_at is not None else None
        )

        # --- Retrieval Logging ---
        logging_started = time.monotonic()
        receipt_persisted = False
        try:
            from prme.models.relevance import make_receipt

            execution = RetrievalExecution(features=execution_features, parameters={
                "ranking_multipliers": ranking_multipliers.model_dump(mode="json") if ranking_multipliers else None,
                "ranking_profile": ranking_profile,
                "max_per_source": max_per_source,
                "max_per_evidence": max_per_evidence,
                "time_from": time_from.isoformat() if time_from else None,
                "time_to": time_to.isoformat() if time_to else None,
                "knowledge_at": knowledge_at.isoformat() if knowledge_at else None,
                "event_time_from": event_time_from.isoformat() if event_time_from else None,
                "event_time_to": event_time_to.isoformat() if event_time_to else None,
                "include_cross_scope": include_cross_scope,
                "epistemic_weights": {key: value for key, value in self._epistemic_weights.items()}
                    if self._epistemic_weights is not None else None,
                "unverified_confidence_threshold": self._unverified_confidence_threshold,
                "aggregation_coverage": aggregation_coverage.model_dump(mode="json")
                    if aggregation_coverage is not None else None,
                "historical_coverage": historical_coverage.model_dump(mode="json")
                    if historical_coverage is not None else None,
                "temporal_relation": temporal_relation_metadata.model_dump(mode="json")
                    if temporal_relation_metadata is not None else None,
                "temporal_languages": list(self._temporal_languages) if self._temporal_languages is not None else None,
                "reranker_top_k": self._reranker_top_k,
                "query_reformulation": {"enabled": self._enable_query_reformulation,
                    "count": self._query_reformulation_count, "provider": self._query_reformulation_provider,
                    "model": self._query_reformulation_model,
                    **({"merge_policy": self._query_reformulation_merge_policy}
                       if self._query_reformulation_merge_policy != "new_only" else {})},
            })
            receipt = make_receipt(request_id=analysis.request_id, user_id=user_id, query=query,
                                   reference_time=scoring_now, scopes=normalized_scope,
                                   scoring=effective_weights, packing=effective_packing_config,
                                   candidates=scored, bundle=bundle, min_score=min_score, result_limit=limit,
                                   retrieval_mode=retrieval_mode, time_from=effective_time_from,
                                   time_to=effective_time_to, ranking_policy=ranking_policy, execution=execution)
            op_id = str(uuid.uuid4())
            payload = json.dumps({
                "request_id": str(analysis.request_id),
                "receipt": receipt.model_dump_json(), "receipt_checksum": receipt.checksum,
                "query": query,
                "reference_time": scoring_now.isoformat(),
                "query_time_from": analysis.time_from.isoformat() if analysis.time_from else None,
                "query_time_to": analysis.time_to.isoformat() if analysis.time_to else None,
                "user_id": user_id,
                "candidates_generated": candidate_counts,
                "candidates_filtered": len(excluded),
                "candidates_included": bundle.included_count,
                "tokens_used": bundle.tokens_used,
                "token_budget": bundle.token_budget,
                "tokenizer": bundle.tokenizer,
                "scoring_config_version": effective_weights.version_id,
                "min_score": min_score, "result_limit": limit,
                "max_per_source": max_per_source,
                "max_per_evidence": max_per_evidence,
                "selection_excluded": [item.model_dump(mode="json") for item in selection_excluded],
                "backends_used": list(candidate_counts.keys()),
                "embedding_mismatch": embedding_mismatch,
                "backend_failures": candidate_diagnostics.backend_failures,
                "aggregation_coverage": aggregation_coverage.model_dump(mode="json")
                    if aggregation_coverage is not None else None,
                "historical_coverage": historical_coverage.model_dump(mode="json")
                    if historical_coverage is not None else None,
                "temporal_relation": temporal_relation_metadata.model_dump(mode="json")
                    if temporal_relation_metadata is not None else None,
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
                from prme.storage._threading import run_to_completion

                async with self._conn_lock:
                    await run_to_completion(
                        self._conn.execute,
                        "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, now())",
                        [op_id, "RETRIEVAL_REQUEST", str(analysis.request_id), payload, user_id],
                    )
            receipt_persisted = self._pool is not None or self._conn is not None
        except Exception:
            logger.warning(
                "Failed to log RETRIEVAL_REQUEST operation for request %s",
                analysis.request_id,
                exc_info=True,
            )

        # --- Assemble RetrievalResponse ---
        completed_at = time.monotonic()
        metadata = RetrievalMetadata(
            receipt_persisted=receipt_persisted,
            request_id=analysis.request_id,
            reference_time=scoring_now,
            min_score=min_score, result_limit=limit,
            max_per_source=max_per_source,
            max_per_evidence=max_per_evidence,
            candidates_generated=candidate_counts,
            candidates_filtered=len(excluded),
            candidates_included=bundle.included_count,
            scoring_config_version=effective_weights.version_id,
            ranking_multipliers=ranking_multipliers,
            ranking_profile_id=(
                uuid.UUID(ranking_profile["profile_id"])
                if ranking_profile and ranking_profile.get("profile_id") else None
            ),
            ranking_profile_status=(
                ranking_profile["status"] if ranking_profile else "none"
            ),
            ranking_profile_reason=(
                ranking_profile.get("reason") if ranking_profile else None
            ),
            timing_ms=round((completed_at - start_time) * 1000, 2),
            receipt_logging_ms=round((completed_at - logging_started) * 1000, 2),
            backends_used=list(candidate_counts.keys()),
            embedding_mismatch=embedding_mismatch,
            backend_failures=candidate_diagnostics.backend_failures,
            aggregation_coverage=aggregation_coverage,
            historical_coverage=historical_coverage,
            temporal_relation=temporal_relation_metadata,
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
            excluded=excluded,
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
        reference_time: datetime | None = None,
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

        The experimental ``max_signals`` policy instead merges distinct backend
        paths and component maxima for identical source snapshots. Every
        alternate pass must succeed before the candidate list changes. Backend
        errors, embedding mismatches and conflicting snapshots propagate;
        provider failure retains the existing empty-reformulation fallback.

        Returns:
            The number of new candidates appended across all alternate queries.
        """
        from prme.retrieval.reformulation import reformulate_query

        alt_queries = await reformulate_query(
            query,
            provider=self._query_reformulation_provider,
            model=self._query_reformulation_model,
            count=self._query_reformulation_count,
            api_key=self._query_reformulation_api_key,
            base_url=self._query_reformulation_base_url,
            timeout=self._query_reformulation_timeout,
            client_cache=self._query_reformulation_clients,
        )
        merge_signals_enabled = self._query_reformulation_merge_policy == "max_signals"
        if not alt_queries and not merge_signals_enabled:
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
                reference_time=reference_time,
            )
            diagnostics = CandidateDiagnostics() if merge_signals_enabled else None
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
                **({"diagnostics": diagnostics} if diagnostics is not None else {}),
            )
            if diagnostics is not None and (
                diagnostics.backend_failures or diagnostics.embedding_mismatch
            ):
                raise RuntimeError("Alternate-query backend failed")
            return alt_candidates

        results = await asyncio.gather(
            *[_gen(alt_q) for alt_q in alt_queries],
            return_exceptions=True,
        )
        if merge_signals_enabled:
            # Settle all passes before propagating failure, so none outlive
            # their owning retrieval/backend lease after an early exception.
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            merged, observation = merge_reformulation_signals(candidates, results)
            candidates[:] = merged
            return len(observation["added_ids"])

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


_REFORMULATION_SIGNALS = ('semantic_score', 'lexical_score', 'graph_proximity')


def merge_reformulation_signals(
    candidates: list[RetrievalCandidate],
    alternatives: list[list[RetrievalCandidate]],
) -> tuple[list[RetrievalCandidate], dict[str, list[str]]]:
    """Union backend paths and take maxima, without treating queries as backends."""
    import math

    merged = {c.node.id: c.model_copy(deep=True) for c in candidates}
    if len(merged) != len(candidates):
        raise ValueError('Duplicate original candidate identity')
    changed = set()
    added = set()
    for group in [candidates, *alternatives]:
        for candidate in group:
            if any(not math.isfinite(getattr(candidate, key)) for key in _REFORMULATION_SIGNALS):
                raise ValueError('Non-finite retrieval signal')
            nid = candidate.node.id
            if nid not in merged:
                merged[nid] = candidate.model_copy(deep=True)
                added.add(nid)
            target = merged[nid]
            if target.node.model_dump(mode='json') != candidate.node.model_dump(mode='json'):
                raise ValueError('Same identity has different source snapshots')
            before = (tuple(target.paths), *(getattr(target, key) for key in _REFORMULATION_SIGNALS))
            target.paths = sorted(set(target.paths) | set(candidate.paths))
            target.path_count = len(target.paths)
            for key in _REFORMULATION_SIGNALS:
                setattr(target, key, max(getattr(target, key), getattr(candidate, key)))
            after = (tuple(target.paths), *(getattr(target, key) for key in _REFORMULATION_SIGNALS))
            if before != after:
                changed.add(nid)
    return list(merged.values()), {'added_ids': sorted(map(str, added)),
                                  'changed_existing_ids': sorted(str(i) for i in changed - added)}
