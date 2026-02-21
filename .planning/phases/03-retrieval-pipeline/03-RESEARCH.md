# Phase 3: Retrieval Pipeline - Research

**Researched:** 2026-02-20
**Domain:** Hybrid retrieval pipeline — query analysis, multi-backend candidate generation, deterministic scoring, context packing
**Confidence:** MEDIUM-HIGH

## Summary

Phase 3 builds the retrieval pipeline that transforms a query into a ranked, context-packed Memory Bundle. The pipeline has 6 stages (RFC-0005): query analysis, parallel candidate generation (graph + vector + lexical + pinned), candidate merging, epistemic filtering, composite scoring, and context packing (RFC-0006). The codebase already has all four storage backends implemented and working (EventStore, DuckPGQGraphStore, VectorIndex with USearch, LexicalIndex with tantivy-py). The existing `MemoryEngine.search()` method does basic parallel vector+lexical search, which Phase 3 replaces with the full hybrid pipeline.

The primary technical challenges are: (1) BM25 score normalization (tantivy returns unbounded scores that must be normalized to [0,1] for the composite formula), (2) embedding version mismatch detection (already tracked in vector_metadata table), (3) deterministic scoring with floating-point tie-breaking, and (4) token cost estimation for context packing. No new external dependencies are required — `tiktoken` is recommended for exact token counting but can be deferred to a supporting dependency; the character-based approximation in RFC-0006 is sufficient for MVP. `dateparser` is already in `pyproject.toml` for temporal expression extraction in query analysis.

**Primary recommendation:** Build as a layered pipeline module (`src/prme/retrieval/`) with each RFC stage as a separate, testable component. The unified `retrieve()` method on MemoryEngine delegates to the pipeline. No new storage backends are needed — this phase composes existing backends.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Single unified `retrieve()` entry point — no separate methods for vector, lexical, or graph search
- System auto-detects intent from the query string and selects which backends to invoke — no caller hints or mode parameters
- First-class parameters: `retrieve(query, namespace=..., scope=..., time_from=..., time_to=..., token_budget=...)`  — explicit, discoverable, not nested in a filter object
- Returns a structured `RetrievalResponse` with results list + metadata (candidate counts per backend, scoring config used, timing, token usage)
- Accept RFC-0005 proposed defaults: semantic=0.30, lexical=0.15, graph=0.20, recency=0.10, salience=0.10, confidence=0.10, epistemic=0.05 (multiplicative), paths=0.00 (tiebreaker)
- Per-request weight overrides: default weights in config, but caller can pass weight overrides on any retrieve() call
- Weight configurations are versioned — each config gets a version ID stored alongside retrieval results for reproducibility
- Caller sets minimum fidelity: caller specifies a minimum representation level (REFERENCE, KEY_VALUE, STRUCTURED, PROSE, FULL) — system won't downgrade below it, just includes fewer results
- Truncation is explicitly signaled: response includes how many items were dropped, their IDs, and the budget remaining when cutoff happened
- Returns structured MemoryBundle with grouped sections: entity snapshots, stable facts, recent decisions, active tasks, provenance refs
- Full candidate audit: log every candidate ID, its scores, and the reason it was excluded — complete debugging picture for filtered/low-scored candidates
- Retrieval replay: each retrieval gets a request_id, and you can re-run it by ID to verify identical results — supports the deterministic constraint

### Claude's Discretion
- Score traces (per-result breakdown): Claude decides whether always-on or opt-in based on RFC-0005 §9 requirement
- RETRIEVAL_REQUEST event storage: Claude decides whether same DuckDB event store or separate ops log based on event sourcing architecture
- Embedding version mismatch handling: Claude picks the safest approach per RFC guidance
- Bin-packing priority order configurability: Claude balances simplicity and flexibility for the 3-tier priority system

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| RETR-01 | User can search memory by semantic similarity via vector search | VectorIndex.search() already returns cosine similarity scores. Pipeline wraps this as one candidate generation path. |
| RETR-02 | User can search memory by exact terms via lexical full-text search | LexicalIndex.search() already returns BM25 scores. Pipeline wraps this as one candidate generation path. Score normalization needed (BM25 is unbounded). |
| RETR-03 | System performs hybrid retrieval combining graph neighborhood, vector similarity, lexical match, and recency/salience signals | All four backends exist. Pipeline runs them in parallel via asyncio.gather(), merges by object_id, tracks path_count. GraphStore.get_neighborhood() provides graph traversal with recursive CTEs. |
| RETR-04 | System applies deterministic re-ranking with configurable, versioned scoring weights | 8-input composite score formula from RFC-0005 §7. Weights stored in versioned config. Tie-breaking by object_id for stable sort. |
| RETR-05 | System returns explainable retrieval traces showing score components per result | ScoreTrace dataclass per result with all 8 component values. RETRIEVAL_REQUEST operation logged per RFC-0005 §9. |
| RETR-06 | System constructs context-packed memory bundles within a configurable token budget | 3-priority greedy bin-packing from RFC-0006 §5. STR metric. 5 representation levels. Character-based token estimation with tiktoken optional. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| duckdb | >=1.2.0 | Event store, graph store (nodes/edges), vector metadata | Already in project; single-file embedded SQL |
| usearch | >=2.16.0 | HNSW approximate nearest neighbor index | Already in project; USearch provides cosine similarity |
| tantivy | >=0.22.0 | BM25 full-text search via tantivy-py bindings | Already in project; BM25 ranking built-in |
| pydantic | >=2.12 | Data models for QueryAnalysis, ScoreTrace, RetrievalResponse, MemoryBundle | Already in project; validation + serialization |
| dateparser | >=1.2 | Temporal expression extraction ("last week", "3 days ago") | Already in project pyproject.toml |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tiktoken | >=0.9 | Exact token counting per target LLM tokenizer | RFC-0006 Method 1 — required when budget within 15% of full; can defer to later plan if character-based approximation is acceptable for MVP |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| tiktoken | Character-based approximation (ceil(chars/4.2)) | No external dep; less accurate; RFC-0006 requires Method 1 near budget limit |
| dateparser | Custom regex for temporal expressions | dateparser already in deps; handles "last week", "3 months ago", multi-locale |
| Separate retrieval service | In-process pipeline | In-process is correct for local-first architecture; no network overhead |

**Installation:**
```bash
# tiktoken is the only potential new dependency
uv add tiktoken>=0.9
```

## Architecture Patterns

### Recommended Project Structure
```
src/prme/
├── retrieval/              # NEW — Phase 3
│   ├── __init__.py         # Public API exports
│   ├── pipeline.py         # RetrievalPipeline orchestrator (6 stages)
│   ├── query_analysis.py   # Stage 1: intent classification, entity/temporal extraction
│   ├── candidates.py       # Stage 2+3: parallel candidate generation + merging
│   ├── filtering.py        # Stage 4: epistemic filtering per RFC-0003 §8
│   ├── scoring.py          # Stage 5: composite scoring + deterministic ranking
│   ├── packing.py          # Stage 6: context packing + STR + MemoryBundle assembly
│   ├── models.py           # QueryAnalysis, ScoredCandidate, ScoreTrace, RetrievalResponse, MemoryBundle
│   └── config.py           # ScoringWeights (versioned), PackingConfig, retrieval defaults
├── storage/                # EXISTING — used by retrieval
│   ├── engine.py           # MemoryEngine gains retrieve() method
│   ├── graph_store.py      # GraphStore Protocol (neighborhood traversal)
│   ├── vector_index.py     # VectorIndex (semantic search)
│   ├── lexical_index.py    # LexicalIndex (BM25 search)
│   └── event_store.py      # EventStore (retrieval logging)
└── types.py                # Add EpistemicType, QueryIntent, RetrievalMode, RepresentationLevel enums
```

### Pattern 1: Pipeline as Composed Stages
**What:** Each retrieval stage is a pure function (or near-pure async function) that takes structured input and returns structured output. The pipeline orchestrator calls them in sequence.
**When to use:** Always — this is the mandatory architecture per RFC-0005.
**Example:**
```python
# Each stage is independently testable
class RetrievalPipeline:
    async def retrieve(self, query: str, ...) -> RetrievalResponse:
        analysis = await self._analyze_query(query)
        candidates = await self._generate_candidates(analysis)
        merged = self._merge_candidates(candidates)
        filtered = self._filter_epistemic(merged, analysis.retrieval_mode)
        scored = self._score_and_rank(filtered, analysis)
        bundle = self._pack_context(scored, token_budget)
        self._log_retrieval(request_id, analysis, scored, bundle)
        return RetrievalResponse(bundle=bundle, metadata=...)
```

### Pattern 2: Candidate as Enriched Object
**What:** Each candidate carries its source paths, raw scores from each backend, and the merged composite score. This avoids re-querying backends for score traces.
**When to use:** For the merged candidate set (Stage 3 onward).
**Example:**
```python
@dataclass
class RetrievalCandidate:
    node: MemoryNode
    paths: list[str]          # ["GRAPH", "VECTOR", "LEXICAL", "PINNED"]
    path_count: int
    semantic_score: float     # 0.0 if not from vector path
    lexical_score: float      # 0.0 if not from lexical path
    graph_proximity: float    # 1.0/0.7/0.4 based on hop count, 0.0 if not from graph
    composite_score: float    # Computed in Stage 5
    score_trace: ScoreTrace   # All 8 component values
    representation: str       # Chosen in Stage 6
    token_cost: int           # Estimated in Stage 6
```

### Pattern 3: Versioned Scoring Config
**What:** Scoring weights are a frozen, hashable configuration object with a deterministic version ID derived from the weight values.
**When to use:** Every retrieval — the version ID is stored in the retrieval log for replay.
**Example:**
```python
class ScoringWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    w_semantic: float = 0.30
    w_lexical: float = 0.15
    w_graph: float = 0.20
    w_recency: float = 0.10
    w_salience: float = 0.10
    w_confidence: float = 0.10
    w_epistemic: float = 0.05   # multiplicative
    w_paths: float = 0.00       # tiebreaker only

    @property
    def version_id(self) -> str:
        """Deterministic hash of weight values for reproducibility."""
        import hashlib
        payload = f"{self.w_semantic}:{self.w_lexical}:{self.w_graph}:..."
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def validate_sum(self) -> None:
        """Weights (excluding epistemic+paths) must sum to 1.0."""
        total = self.w_semantic + self.w_lexical + self.w_graph + \
                self.w_recency + self.w_salience + self.w_confidence
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, must be 1.0"
```

### Pattern 4: Score Normalization Strategies
**What:** Each backend returns scores in different ranges. All must be normalized to [0, 1] before the composite formula.
**When to use:** Stage 5, before applying weights.
**Details:**
- **Vector (cosine similarity):** Already [0, 1] from USearch (1 - distance). No normalization needed.
- **Lexical (BM25):** Unbounded positive. Normalize via `min-max` within the current result set: `(score - min_score) / (max_score - min_score)`. If only one result, score = 1.0. This is per-query normalization, not global.
- **Graph proximity:** Fixed mapping: 1-hop = 1.0, 2-hop = 0.7, 3-hop = 0.4 (from RFC-0005 §7). Already [0, 1].
- **Recency:** `exp(-lambda * days)` where lambda = 0.02. Already (0, 1].
- **Salience/confidence:** Already [0, 1] on MemoryNode model.
- **Epistemic weight:** Lookup table from RFC-0003 §8. Already [0.1, 1.0].
- **Path count:** `min(path_count / 3.0, 1.0)`. Already [0, 1].

### Anti-Patterns to Avoid
- **Eager full-node fetch:** Don't load full MemoryNode objects during candidate generation. Fetch IDs + scores first, then batch-load nodes only for candidates that survive filtering. The graph neighborhood query already returns full nodes — accept this for graph path, but vector/lexical should return IDs only, then batch-resolve.
- **Non-deterministic sort:** Python's sort is stable, but floating-point comparison can produce different orderings on repeated runs. Always include `object_id` as a secondary sort key for determinism.
- **Score normalization across queries:** BM25 min-max normalization must be per-query, not global. A global normalization would make scores incomparable across queries and break determinism for the same query.
- **Blocking LLM calls in query analysis:** RFC-0005 §3 explicitly says query analysis should NOT be a blocking LLM call by default. Use `dateparser` for temporal extraction and simple keyword/pattern matching for intent classification. An LLM call is permitted only if the retrieval budget allows it.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Temporal expression parsing | Custom regex for "last week", "3 months ago" | `dateparser.parse()` | Already in deps; handles locales, relative dates, edge cases |
| Token counting | Character counting only | `tiktoken` (or char approx as fallback) | RFC-0006 requires Method 1 near budget limit; tiktoken gives exact counts |
| BM25 ranking | Custom TF-IDF | `tantivy` (already integrated) | BM25 is implemented natively in tantivy; just normalize the output |
| HNSW similarity search | Custom vector comparison | `usearch` (already integrated) | Optimized C++ with Python bindings; handles ef_search parameter |
| Recursive graph traversal | Python-side BFS | DuckDB recursive CTEs (already in DuckPGQGraphStore) | Database-side traversal is faster for large graphs; already implemented |

**Key insight:** Phase 3 is a composition/orchestration phase, not a new storage phase. Every backend is already built. The work is in wiring them together with correct scoring, filtering, and packing logic.

## Common Pitfalls

### Pitfall 1: BM25 Score Normalization Distortion
**What goes wrong:** BM25 scores are unbounded and vary wildly across queries. Naive normalization (divide by max) compresses scores when there's one outlier result.
**Why it happens:** BM25 score magnitude depends on query term frequency, document length, and corpus statistics. A query matching a rare term produces very different score ranges than a common term.
**How to avoid:** Use min-max normalization per query result set. If max == min (all scores equal), set all to 1.0. Document this normalization strategy as a `[HYPOTHESIS]` — alternative approaches (sigmoid, percentile) may work better but require empirical testing.
**Warning signs:** Lexical scores dominating or being ignored in composite scores despite lexical weight being 0.15.

### Pitfall 2: Graph Neighborhood Explosion
**What goes wrong:** A 3-hop traversal from a well-connected entity returns hundreds of candidates, overwhelming the scorer.
**Why it happens:** Graph traversal is exponential in hop count. An entity with 20 edges at 3 hops could return 8000 candidates.
**How to avoid:** RFC-0005 specifies `max_candidates: 50` for graph traversal. Enforce this at the query level with ORDER BY edge confidence + LIMIT. The existing `get_neighborhood()` does not have a `max_candidates` parameter — this must be added or post-filtered.
**Warning signs:** Retrieval latency spikes when querying entities with many edges.

### Pitfall 3: Floating-Point Determinism
**What goes wrong:** Two identical queries on the same data produce different result orderings because floating-point addition is not associative.
**Why it happens:** Python floating-point arithmetic depends on operation order. Score = (0.3 * 0.95) + (0.15 * 0.87) can differ by epsilon depending on evaluation order.
**How to avoid:** RFC-0005 §10 requires tie-breaking by `object_id`. After scoring, sort by `(-composite_score, str(object_id))` to guarantee stable ordering. Use `round(score, 10)` to reduce floating-point noise if needed.
**Warning signs:** Retrieval replay test (same query, same data) produces different result order.

### Pitfall 4: Embedding Version Mismatch Silent Degradation
**What goes wrong:** After switching embedding models, old vectors are compared with new query vectors, producing meaningless similarity scores.
**Why it happens:** The vector_metadata table tracks model name/version per vector, but search() doesn't check for mismatches.
**How to avoid:** Before vector search, check `embedding_model` and `embedding_version` in vector_metadata against the current provider. If mismatched, either error or fall back to lexical-only per RFC-0005 §4.2. The current VectorIndex stores model metadata but doesn't validate at search time.
**Warning signs:** Vector search returns seemingly random results after a model change.

### Pitfall 5: Token Budget Overflow
**What goes wrong:** The packing algorithm includes an object that pushes the bundle over the token budget.
**Why it happens:** Token cost estimation is approximate (especially character-based Method 2). The actual serialized representation may use more tokens than estimated.
**How to avoid:** RFC-0006 §3 requires Method 1 (exact tokenization) when within 15% of budget. Build the packing algorithm with a safety margin. Never exceed budget — truncation mid-object is not permitted. Use REFERENCE representation as the minimum-cost fallback.
**Warning signs:** Bundle tokens_used exceeds context_budget in production.

### Pitfall 6: Epistemic Filter Ordering
**What goes wrong:** Epistemic filtering is applied after scoring, allowing DEPRECATED objects to influence score normalization.
**Why it happens:** Temptation to score everything and then filter for simplicity.
**How to avoid:** RFC-0005 §6 is explicit: epistemic filtering (Stage 4) MUST occur before scoring (Stage 5). Filter first, then score the survivors. This is a conformance requirement.
**Warning signs:** DEPRECATED objects appearing in score traces; score distributions shifting when deprecated objects are added to the graph.

## Code Examples

### Query Analysis with dateparser
```python
# Using dateparser (already in pyproject.toml) for temporal extraction
import dateparser
from datetime import datetime, timezone

def extract_temporal_signals(query: str) -> list[dict]:
    """Extract temporal expressions from query text.

    dateparser handles: "last week", "3 days ago", "before January",
    "since Monday", "in 2024", etc.
    """
    # dateparser.search.search_dates returns [(text, datetime), ...]
    from dateparser.search import search_dates

    results = search_dates(query, settings={
        'RETURN_AS_TIMEZONE_AWARE': True,
        'TIMEZONE': 'UTC',
    })

    if not results:
        return []

    signals = []
    for text_match, parsed_date in results:
        signals.append({
            "type": "ABSOLUTE" if any(c.isdigit() for c in text_match) else "RELATIVE",
            "value": text_match,
            "resolved": parsed_date,
        })
    return signals
```

### BM25 Score Normalization
```python
def normalize_bm25_scores(results: list[dict]) -> list[dict]:
    """Normalize BM25 scores to [0, 1] via min-max within result set."""
    if not results:
        return results

    scores = [r["score"] for r in results]
    min_s = min(scores)
    max_s = max(scores)

    if max_s == min_s:
        # All scores equal — normalize to 1.0
        for r in results:
            r["normalized_score"] = 1.0
    else:
        for r in results:
            r["normalized_score"] = (r["score"] - min_s) / (max_s - min_s)

    return results
```

### Composite Score Calculation
```python
import math
from dataclasses import dataclass

@dataclass
class ScoreTrace:
    """Explainable score breakdown for a single retrieval candidate."""
    semantic_similarity: float
    lexical_relevance: float
    graph_proximity: float
    recency_factor: float
    salience: float
    confidence: float
    epistemic_weight: float
    path_score: float
    composite_score: float

def compute_composite_score(
    candidate: "RetrievalCandidate",
    weights: "ScoringWeights",
    recency_lambda: float = 0.02,
) -> ScoreTrace:
    """Compute the 8-input composite score per RFC-0005 §7."""
    days_since = (datetime.now(timezone.utc) - candidate.node.updated_at).days
    recency = math.exp(-recency_lambda * days_since)

    epistemic_w = EPISTEMIC_WEIGHTS.get(
        candidate.node.epistemic_type, 0.7  # default for unclassified
    )
    path_score = min(candidate.path_count / 3.0, 1.0)

    # Additive components (weights sum to 1.0)
    additive = (
        weights.w_semantic * candidate.semantic_score +
        weights.w_lexical * candidate.lexical_score +
        weights.w_graph * candidate.graph_proximity +
        weights.w_recency * recency +
        weights.w_salience * candidate.node.salience +
        weights.w_confidence * candidate.node.confidence
    )

    # Epistemic is multiplicative
    score = additive * epistemic_w

    # Path count is tiebreaker only (w_paths = 0.00 by default)
    # Applied as secondary sort key, not additive

    return ScoreTrace(
        semantic_similarity=candidate.semantic_score,
        lexical_relevance=candidate.lexical_score,
        graph_proximity=candidate.graph_proximity,
        recency_factor=recency,
        salience=candidate.node.salience,
        confidence=candidate.node.confidence,
        epistemic_weight=epistemic_w,
        path_score=path_score,
        composite_score=round(score, 10),  # reduce float noise
    )
```

### Token Cost Estimation
```python
import math

# RFC-0006 §3 defaults [HYPOTHESIS]
CHARS_PER_TOKEN = {
    "english": 4.2,
    "json": 3.5,
    "code": 3.0,
}

def estimate_token_cost(text: str, content_type: str = "english") -> int:
    """Character-based token estimation (RFC-0006 Method 2)."""
    cpt = CHARS_PER_TOKEN.get(content_type, 4.2)
    return math.ceil(len(text) / cpt)

def exact_token_cost(text: str, model: str = "gpt-4o") -> int:
    """Exact token counting via tiktoken (RFC-0006 Method 1).

    Use when budget is within 15% of full.
    """
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except (ImportError, KeyError):
        # Fallback to character-based if tiktoken unavailable
        return estimate_token_cost(text)
```

### Greedy Bin-Packing
```python
def pack_context(
    scored_candidates: list["ScoredCandidate"],
    token_budget: int,
    min_fidelity: str = "REFERENCE",
    overhead_tokens: int = 100,
) -> "MemoryBundle":
    """3-priority greedy bin-packing per RFC-0006 §5."""
    available = token_budget - overhead_tokens
    included = []
    excluded = []

    # Priority 1: Pinned + active tasks (always include)
    pinned = [c for c in scored_candidates if c.node.salience == 1.0
              or c.node.node_type == NodeType.TASK]
    remaining = [c for c in scored_candidates if c not in pinned]

    for c in pinned:
        rep, cost = select_representation(c, available, min_fidelity)
        if cost <= available:
            included.append((c, rep, cost))
            available -= cost
        else:
            # Downgrade to minimum fidelity
            ref_cost = token_cost(c, "REFERENCE")
            if ref_cost <= available:
                included.append((c, "REFERENCE", ref_cost))
                available -= ref_cost

    # Priority 2: Multi-path objects by STR descending
    multi_path = sorted(
        [c for c in remaining if c.path_count >= 2],
        key=lambda c: c.composite_score / max(estimate_token_cost(c.node.content), 1),
        reverse=True,
    )
    # ... (same pattern as Priority 1)

    # Priority 3: Remaining by composite score
    rest = sorted(
        [c for c in remaining if c not in multi_path],
        key=lambda c: (-c.composite_score, str(c.node.id)),
    )
    # ... (same pattern)

    return MemoryBundle(
        items=included,
        excluded=excluded,
        tokens_used=token_budget - overhead_tokens - available,
        token_budget=token_budget,
    )
```

### Embedding Version Mismatch Detection
```python
async def check_embedding_version(
    conn: duckdb.DuckDBPyConnection,
    current_provider: EmbeddingProvider,
) -> tuple[bool, str | None]:
    """Check if stored embeddings match the current provider.

    Returns (is_compatible, mismatch_info).
    """
    row = conn.execute("""
        SELECT DISTINCT embedding_model, embedding_version
        FROM vector_metadata
        LIMIT 1
    """).fetchone()

    if row is None:
        return True, None  # No stored embeddings

    stored_model, stored_version = row[0], row[1]
    if stored_model != current_provider.model_name:
        return False, f"Model mismatch: stored={stored_model}, current={current_provider.model_name}"
    if stored_version != current_provider.model_version:
        return False, f"Version mismatch: stored={stored_version}, current={current_provider.model_version}"

    return True, None
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Vector-only retrieval | Hybrid (vector + lexical + graph) | 2024-2025 | Significant recall improvement for factual/entity queries |
| Fixed context window | Token-budgeted context packing | 2024 | STR metric optimizes utility per token |
| Opaque ranking | Explainable score traces | 2024-2025 | Debugging and trust in retrieval decisions |
| Static scoring weights | Per-request configurable weights | Current best practice | Adapts to different query types |

**Deprecated/outdated:**
- Pure TF-IDF: BM25 is strictly better; tantivy uses BM25 natively
- Global embedding similarity thresholds: Per-query normalization is more robust
- Monolithic retrieval functions: Pipeline stage pattern is standard for hybrid systems

## Discretionary Decisions

Research recommendations for areas marked "Claude's Discretion" in CONTEXT.md:

### Score Traces: Always-On (Recommended)
RFC-0005 §9 requires RETRIEVAL_REQUEST logging with scores. Score traces per result add minimal overhead (they're computed during scoring anyway) and are essential for debugging. **Recommendation:** Always compute and include ScoreTrace in RetrievalResponse. Make the traces available on the response object but don't log full traces by default — log summary stats (candidate counts, top-5 scores) always, and full traces at DEBUG level. The overhead of computing traces is near-zero since all components are already calculated.

### RETRIEVAL_REQUEST Event Storage: Separate Operations Table (Recommended)
The append-only event log (EventStore) stores user/assistant conversation events. Retrieval requests are operational metadata, not conversation content. **Recommendation:** Store RETRIEVAL_REQUEST records in a new `operations` table in DuckDB. This aligns with RFC-0002 §4 which defines the operation log as a separate concern from the event log. The operations table schema should match the RFC-0002 §6 operation log format. This keeps the event log clean for deterministic rebuild from conversation events only.

### Embedding Version Mismatch: Fallback to Lexical-Only (Recommended)
RFC-0005 §4.2 permits either rejecting or falling back. **Recommendation:** Fall back to lexical-only search with a warning flag on the response (`embedding_mismatch: true`). Rejection is too harsh for a memory system — better to return partial results than nothing. Log the mismatch at WARNING level. The response metadata should include which backends were actually used.

### Bin-Packing Priority Configurability: Fixed 3-Tier (Recommended)
RFC-0006 §5 specifies 3 fixed tiers. **Recommendation:** Keep the 3-tier order fixed (pinned/tasks first, multi-path second, remaining third). Making tier order configurable adds complexity without clear benefit. The STR tiebreaker within tiers already provides flexibility. If a caller wants different behavior, they can use weight overrides to change which objects score highest.

## Open Questions

1. **Epistemic Type on MemoryNode**
   - What we know: RFC-0003 requires `epistemic_type` on every memory object. The current MemoryNode model does NOT have an `epistemic_type` field. It only has `confidence`, `salience`, and `lifecycle_state`.
   - What's unclear: Phase 3 needs epistemic filtering (Stage 4) and epistemic weights (Stage 5). The Phase 2.3 reconciliation identified this as a gap but did not add the field.
   - Recommendation: Add `epistemic_type` field to MemoryNode as part of Phase 3 (or as a prerequisite task). Default to `ASSERTED` for existing objects. Add the column to the nodes table schema. This is required by REQUIREMENTS.md (EPIS-01 is listed as Phase 3 pre-req).

2. **Namespace vs Scope for Retrieval Filtering**
   - What we know: The current codebase uses `Scope` (3 values: PERSONAL, PROJECT, ORG) and `user_id` for isolation. RFC-0004 defines a full namespace model with 6 types. NSPC-05 requires namespace filters before returning vector search candidates.
   - What's unclear: Should Phase 3 build against the full namespace model or the existing scope model?
   - Recommendation: Build Phase 3 against the existing `scope` + `user_id` filtering. Use `scope` as the namespace proxy for now. The full namespace model (NSPC-01/02) is mapped to Phase 3 pre-req in REQUIREMENTS.md but can be satisfied with the current 3-scope enum plus user_id. Pre-filter vector results by user_id (already done) and scope (needs addition to vector_metadata or post-filter).

3. **Graph Neighborhood max_candidates**
   - What we know: RFC-0005 §4.1 specifies `max_candidates: 50` for graph traversal. The current `get_neighborhood()` has no max_candidates parameter.
   - What's unclear: Should max_candidates be enforced at the SQL level (LIMIT) or at the Python level (truncate after query)?
   - Recommendation: Add LIMIT to the neighborhood CTE query. Sort by edge confidence DESC, then by node created_at DESC to get the most relevant neighbors first. This is a small change to DuckPGQGraphStore._get_neighborhood_sync().

4. **Operation Log Table Schema**
   - What we know: RFC-0002 §4 defines an operation log. No operations table exists yet. Phase 3 needs it for RETRIEVAL_REQUEST logging (RFC-0005 §9).
   - What's unclear: Full operation log schema (all op types from RFC-0002 §6) vs. minimal schema for retrieval logging only.
   - Recommendation: Create a minimal `operations` table with the fields needed for RETRIEVAL_REQUEST now. Schema: `id UUID, op_type VARCHAR, target_id UUID, payload JSON, actor_id VARCHAR, created_at TIMESTAMPTZ`. This is forward-compatible with full operation log requirements in Phase 5 (TRST-08).

## Sources

### Primary (HIGH confidence)
- RFC-0005 (Hybrid Retrieval Pipeline) — full pipeline specification, scoring formula, determinism requirements
- RFC-0006 (Retrieval Cost and Context Efficiency) — STR metric, token estimation, bin-packing algorithm, bundle structure
- RFC-0003 (Epistemic State Model) — epistemic types, retrieval behavior table, confidence weights
- RFC-0004 (Namespace and Scope Isolation) — namespace filtering requirements

### Secondary (MEDIUM confidence)
- Existing codebase: `src/prme/storage/vector_index.py`, `lexical_index.py`, `duckpgq_graph.py`, `engine.py` — verified current API signatures and capabilities
- [tiktoken on PyPI](https://pypi.org/project/tiktoken/) — token counting library for Python
- [dateparser on GitHub](https://github.com/scrapinghub/dateparser) — temporal expression parsing library

### Tertiary (LOW confidence)
- BM25 score normalization approaches — multiple sources suggest min-max per-query normalization; no single authoritative approach for hybrid retrieval systems. Marked as `[HYPOTHESIS]` per project conventions.
- USearch ef_search determinism — USearch supports fixed ef_search parameter for reproducible results, but exact determinism guarantees across platforms need validation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all libraries already in the project; no new major dependencies needed
- Architecture: HIGH - RFC-0005 and RFC-0006 are extremely prescriptive; pipeline structure is specified
- Scoring formula: HIGH - 8-input composite formula is fully specified in RFC-0005 §7
- BM25 normalization: MEDIUM - min-max per-query is the standard approach but marked [HYPOTHESIS]
- Token estimation: MEDIUM - character-based approximation is adequate for MVP; tiktoken is optional
- Epistemic integration: MEDIUM - requires adding `epistemic_type` field not yet on MemoryNode
- Pitfalls: HIGH - identified from RFC conformance requirements and existing codebase review

**Research date:** 2026-02-20
**Valid until:** 2026-03-20 (stable — RFCs are fixed specifications, not evolving APIs)
