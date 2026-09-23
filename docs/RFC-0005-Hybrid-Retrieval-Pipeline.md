# RFC-0005: RMS Hybrid Retrieval Pipeline

**Status:** Draft
**Tier:** 2 — Retrieval
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0002, RFC-0003, RFC-0004

---

## 1. Abstract

This RFC specifies the retrieval pipeline: the process by which a query is transformed into a ranked, namespace-filtered Memory Bundle ready for injection into an LLM context. Retrieval is the operational core of RMS. A perfect memory store with a mediocre retrieval pipeline produces mediocre results; a good retrieval pipeline can compensate for a sparse store.

The pipeline is hybrid — it combines graph neighbourhood traversal, vector similarity search, and lexical search — and is designed to be deterministic given identical inputs, scoring weights, and index state.

---

## 2. Pipeline Overview

```
Query
  │
  ▼
[Stage 1] Query Analysis
  │  Entity extraction, intent classification, temporal detection
  │
  ▼
[Stage 2] Candidate Generation (parallel)
  ├── Graph traversal (1–N hops from query entities)
  ├── Vector similarity search
  ├── Lexical search (FTS)
  └── Pinned / active tasks (direct lookup)
  │
  ▼
[Stage 3] Candidate Merging
  │  Deduplicate by object ID, resolve conflicts
  │
  ▼
[Stage 4] Epistemic Filtering
  │  Apply RFC-0003 retrieval rules by epistemic type
  │
  ▼
[Stage 5] Scoring and Ranking
  │  Compute composite score per object
  │
  ▼
[Stage 6] Context Packing
  │  Apply configured measured packing policy, assemble Memory Bundle
  │
  ▼
Memory Bundle → LLM Context
```

Each stage is specified in the following sections.

Both backends honor `vector_exact_search=True` by default. PostgreSQL materializes
eligible scored rows before top-k ordering, with native UUID tie ordering, so
foreign/filtered HNSW neighbors cannot consume its candidate budget. Approximate
search is opt-in and may miss eligible neighbors. See the
[PostgreSQL mode guide](POSTGRES-VECTOR-SEARCH.md) for the cost tradeoff and receipt
observations. This does not replace named-project isolation or access grants.

---

## 3. Stage 1: Query Analysis

The query analysis stage extracts structured signals from the raw query to guide candidate generation.

**Outputs of query analysis:**

```
QueryAnalysis {
  raw_query:          String
  entities:           [{ name: String, type: EntityType, confidence: Float }]
  temporal_signals:   [{ type: TemporalType, value: String }]
  intent:             QueryIntent
  namespace_scope:    [NamespaceID]
  retrieval_mode:     RetrievalMode    -- DEFAULT | EXPLICIT | AUDIT
}
```

**QueryIntent values:**

| Intent | Description |
|---|---|
| `FACTUAL_LOOKUP` | Looking for a specific fact (e.g., "What language does Alice use?"). |
| `PREFERENCE_CHECK` | Checking a user preference (e.g., "How does Alice like this presented?"). |
| `TASK_STATUS` | Checking the state of a task or goal. |
| `HISTORICAL` | Querying past events or decisions. |
| `CONTEXTUAL` | Building general context (e.g., beginning of a session). |

Intent classification is `[BEST-EFFORT]`. Implementations SHOULD treat unknown or low-confidence intent as `CONTEXTUAL`.

**TemporalType values:** `ABSOLUTE` (specific date), `RELATIVE` (e.g., "last week"), `DURATION` (e.g., "over the past month"), `RECURRING` (e.g., "every Tuesday").

Query analysis is NOT a blocking LLM call by default. Implementations SHOULD use a lightweight classification model or rule-based extraction for this stage. A full LLM call is permitted only if the retrieval budget allows it.

Dates inferred from query text guide temporal relevance scoring. They MUST NOT
implicitly filter assertion validity: a past episode can be imported after the
episode occurred. Explicit `time_from`/`time_to` filter validity windows across
all retrieval paths, retaining the existing ENTITY/PREFERENCE exemption.
Explicit `event_time_from`/`event_time_to` filter episode dates without that
exemption; missing event times fall back to ingestion time.

---

## 4. Stage 2: Candidate Generation

Candidate generation runs three search paths in parallel. Implementations MUST run all three paths unless a path is explicitly disabled by namespace policy.

### 4.1 Graph Traversal

Starting from entities identified in Stage 1, traverse the entity-object graph (RFC-0001, Section 9) to find related memory objects.

**Traversal parameters:**

```
max_hops:           Integer   -- Maximum graph traversal depth. Default: 2. Hard limit: 3.
max_candidates:     Integer   -- Maximum objects returned from graph traversal. Default: 50.
min_edge_confidence: Float    -- Minimum edge confidence to follow. Default: 0.40.
```

**The 3-hop hard limit is important.** A 3-hop traversal on a graph with tens of thousands of nodes can return hundreds of thousands of candidates, making Stage 5 scoring unworkable. If deeper traversal is needed, it MUST be performed iteratively with pruning between hops, not in a single query.

Graph traversal MUST respect namespace isolation (RFC-0004, Section 6). Edges that cross into restricted namespaces MUST NOT be followed without explicit cross-namespace read permission.

### 4.2 Vector Similarity Search

Embed the query using the same model and version recorded in the event store's `embedding_meta.json`. Run an approximate nearest-neighbour search using the HNSW index.

**Search parameters:**

```
top_k:              Integer   -- Number of candidates to retrieve. Default: 30.
min_score:          Float     -- Minimum cosine similarity. Default: 0.65.
namespace_filter:   [NamespaceID]  -- Applied at index level (RFC-0004, Section 6).
```

**Embedding model version pinning:** If the query embedding is generated by a different model version than the stored embeddings, results will be unreliable. Implementations MUST detect this condition and either:
- Reject the retrieval with a `EMBEDDING_VERSION_MISMATCH` error, or
- Fall back to lexical search only, flagging the bundle with `embedding_mismatch: true`.

Silently returning results from a mixed-model comparison is NOT permitted.

**Implementation status (2026-09-12):** local vector hits validate their stored
model, version, and dimension; PostgreSQL writes model/version with each node's
embedding and validates retrieved rows. Unknown legacy PostgreSQL metadata is
incompatible until re-embedding. A detected mismatch empties the vector candidate
path and sets retrieval metadata `embedding_mismatch=true`. Other primary-path
failures use `backend_failures` reason code `backend_error`; an empty successful
search is neither a failure nor evidence of model mismatch. This status covers
primary candidate generation, not every optional expansion or reranker. Changing
vector dimensions can require index/schema migration and is not an automatic
model upgrade.

Provider response admission checks require one vector per input, the declared
dimension and finite float32-compatible values. Entire batches are validated
before cache insertion; malformed results and identity changes during encoding
fail without partial cache admission. Query encoding, direct index writes and
durable preparation use the same checks on both backends. These checks establish
shape and numeric compatibility, not the semantic correctness of provider output.

### 4.3 Lexical Search

Run full-text search over memory object `value` fields and event content using the lexical index (BM25 or FTS5).

```
top_k:              Integer   -- Default: 20.
namespace_filter:   [NamespaceID]
boost_fields:       [String]  -- Fields to boost. Default: ["value", "entity_canonical_name"]
```

Lexical search is particularly valuable when:
- The query contains proper nouns or domain-specific terms that embedding models may not distinguish.
- The embedding model is outdated or mismatched.
- The query is short (single words or names) where semantic embedding is less reliable.

### 4.4 Pinned and Active Objects

Retrieve directly:
- All memory objects with `salience == 1.0` (user-pinned) in the namespace scope.
- All TASK objects with lifecycle_state == ACTIVE in the namespace scope.
- All INTENT objects that are ACTIVE (requires RFC-0013).

These are included unconditionally, subject to context budget. They bypass scoring.

### 4.5 Adjacent session context

After primary scoring, the configured session window promotes adjacent active
nodes around top candidates. The signal applies whether an adjacent node is new
to the pool or was already found by vector, lexical, or graph generation; a
broad candidate pool must not turn session expansion into a no-op. Inherited
scores retain the triggering node and decay operation in replayable score
provenance. Expansion remains owner and exact-scope constrained and is filtered
again before packing. Built-in stores compute bounded neighborhoods in SQL for
each exact `(session_id, scope)` partition; the behavior does not depend on a
fixed whole-session read limit.

### 4.6 Two-stage episode context

`PackingConfig.episode_context_top_k` enables deterministic episode routing.
The default is `0`, which preserves the existing retrieval path. When enabled,
PRME treats exact `(scope, session_id)` groups as episode boundaries, scores the
candidate-backed text of each episode against the query with BM25, and marks at
most `episode_context_local_k` locally relevant records from each selected
episode as `EPISODE_CONTEXT`. Selected records inherit a bounded score from the
strongest candidate in their episode through a replayable `episode_decay`
operation.

This stage performs no model calls. It operates after temporal and epistemic
filtering, never crosses scope, and only sees records already present in the
candidate pool (including records added by adjacent-session expansion). It is
therefore a bounded evidence-routing strategy, not an exhaustive session scan or
historical replay. The packer reserves selected episode evidence after system
instructions, pins, and active tasks and before the ordinary multi-path tier.
Receipt schema version 8 records all three episode settings; versions 1–7 mean
episode routing was disabled.

### 4.7 Direct evidence projection

`PackingConfig.evidence_projection_top_k` enables deterministic source
projection. The default is `0`. PRME takes the strongest candidate in each of
the top exact nonempty `evidence_refs` groups as a routing anchor, loads only the
active graph nodes whose own IDs occur in that evidence set, and replaces the
group with at most `evidence_projection_max_sources` direct sources. The source
inherits the anchor score through a replayable `evidence_projection` operation
and is marked `EVIDENCE_CONTEXT` for bounded packing priority.

Projection performs no model calls. A source must match the request owner and
anchor scope and pass the same ingestion, event-time, validity and epistemic
filters as ordinary candidates. A group remains unchanged when no eligible
direct source is available. This lets extracted claims route a source without
allowing short siblings or entities to hide its complete wording. It can also
replace a concise claim with a much longer passage, so it remains opt-in while
answer trials establish workload-appropriate bounds. Receipt schema version 10
records all three settings and the applied score lineage; versions 1–9 mean
projection was disabled.

`PackingConfig.evidence_augmentation_top_k` provides the dual-representation
variant. It uses the same eligibility rules but adds each bounded source beside
the derived group instead of replacing the claims. The source inherits a
replayable `evidence_augmentation` score and the packer uses the same
`EVIDENCE_CONTEXT` priority. Projection and augmentation are mutually exclusive.
Augmentation keeps current-state and contradiction abstractions available while
adding full wording for omitted details; its additional candidates and tokens
still require workload evaluation. The optional `non_entity` anchor policy
prevents entity-name candidates from routing a whole passage and backfills the
quota from later non-entity evidence groups. Receipt schema version 11 records
the three augmentation settings; version 12 also records the anchor policy.
Versions 1–10 mean augmentation was disabled, while versions 1–11 mean the
anchor policy was `all`.

---

## 5. Stage 3: Candidate Merging

Merge the candidate sets from all four paths. Deduplication is by `object_id`.

When the same object appears in multiple paths, record which paths it appeared in. This multi-path membership is used in Stage 5 scoring:

```
candidate.path_count:   Integer   -- Number of retrieval paths that returned this object.
candidate.paths:        [PathType]  -- GRAPH | VECTOR | LEXICAL | PINNED
```

An object that appears in both GRAPH and VECTOR retrieval is almost certainly more relevant than one appearing in only one path. This signal is used explicitly in scoring.

After ranking, callers may set `max_per_source` to a positive integer. The
selection stage then limits candidates that have both the same exact nonempty
`evidence_refs` set and byte-identical content, and fills the requested result
limit from later source groups. This is useful for LLM extraction, where several
claim nodes can cite the same complete source passage. Matching text from
different events and different passages from one event remain distinct. The
default is disabled until answer trials establish a broadly safe value. Applied
values and `source_limit` exclusions are retained in retrieval telemetry.

Callers that need stronger source diversity may also set `max_per_evidence` to
a positive integer. This caps every candidate with the same exact nonempty
`evidence_refs` set even when candidate content differs, while leaving nodes
without evidence independent. It fills the requested limit from later evidence
groups and records `evidence_limit` exclusions. It is opt-in because a single
source can contain several independently useful extracted claims.

---

## 6. Stage 4: Epistemic Filtering

Apply RFC-0003 retrieval rules. This stage removes objects that MUST NOT appear in the current retrieval mode.

For DEFAULT retrieval mode, the following MUST be removed before scoring:
- Objects with `epistemic_type == DEPRECATED`.
- Objects with `epistemic_type == HYPOTHETICAL`.
- Objects with `epistemic_type == UNVERIFIED` and `confidence < namespace_threshold`.
- Objects with `lifecycle_state != ACTIVE`.

Removed objects MUST be logged in the bundle's `retrieval_metadata.excluded` map with the reason.

---

## 7. Stage 5: Scoring and Ranking

Each remaining candidate receives a composite score. The score determines rank order for context packing.

**Composite score formula:**

```
score(obj) =
  (w_semantic  × semantic_similarity)  +
  (w_lexical   × lexical_relevance)    +
  (w_graph     × graph_proximity)      +
  (w_recency   × recency_factor)       +
  (w_salience  × obj.salience)         +
  (w_confidence × obj.confidence)      +
  (w_epistemic × epistemic_weight(obj.epistemic_type))  +
  (w_paths     × min(obj.path_count / 3.0, 1.0))
```

**Score inputs:**

| Input | Definition | Range |
|---|---|---|
| `semantic_similarity` | Cosine similarity between query embedding and object embedding. | [0, 1] |
| `lexical_relevance` | Normalised BM25 score. | [0, 1] |
| `graph_proximity` | 1.0 for 1-hop, 0.7 for 2-hop, 0.4 for 3-hop. 0 if not retrieved via graph. | [0, 1] |
| `recency_factor` | `exp(-λ × days_since_last_access)` where λ = 0.02 by default. | (0, 1] |
| `obj.salience` | Current salience score from RFC-0007. | [0, 1] |
| `obj.confidence` | Current confidence score from RFC-0008. | [0, 1] |
| `epistemic_weight` | Multiplier by epistemic type from RFC-0003, Section 8. | [0.1, 1.0] |
| `path_count` | Number of retrieval paths that surfaced this object. | [1, 4] |

Every lexical path, including aggregation keyword scans and entity-focused
fan-out queries, normalises its BM25 result set before composite scoring. Raw
BM25 values are unbounded and are not comparable across separate queries, so
they must never enter `lexical_relevance` directly.

**Default weights `[HYPOTHESIS — require empirical calibration]`:**

```
w_semantic:   0.30
w_lexical:    0.15
w_graph:      0.20
w_recency:    0.10
w_salience:   0.10
w_confidence: 0.10
w_epistemic:  0.05 (applies the multiplier, not an additive weight)
w_paths:      0.00 (used as a tiebreaker only, not additive by default)
```

Weights MUST sum to 1.0 (excluding w_epistemic and w_paths which are multiplicative/tiebreaker). Implementations MUST validate this constraint at configuration load time.

Scoring and packing models reject non-finite numerical values during validation,
including nested node-type boosts and per-scope weight configurations. `NaN`
must not bypass the additive-sum check or enter ranking; infinity must not reach
decay calculations or candidate-budget conversion. This validation does not
change finite defaults or scoring version hashes. As with other Pydantic models,
trusted `model_construct`/`model_copy(update=...)` calls bypass normal validation.

**Calibration requirement:** Default weights are design estimates. Implementations MUST expose weight configuration and SHOULD tune weights based on feedback loop data (RFC-0009). `[HYPOTHESIS — optimal weights are use-case dependent and require A/B testing to validate]`

### 7.1 Explicit current updates

For a current-state query, the newest candidate may receive a bounded
post-score multiplier when its text explicitly presents itself as an update,
such as “moved and now lives,” “changed jobs,” or “updated budget.” The default
`ScoringWeights.current_update_multiplier` is `1.30` and remains a
`[HYPOTHESIS]`; `1.0` disables the behavior. The relevance floor still caps the
adjusted score when semantic plus lexical relevance is below the configured
threshold.

The operation is eligible only for the newest candidate timestamp in the pool
and is stored as an ordered `current_update` adjustment with its exact applied
coefficient and source node. It means that the record presents itself as a
current update. It does not verify the claim, establish graph supersedence, or
silently retire an earlier assertion. Explicit corrections and contradiction
resolution remain the authoritative mechanisms for those state changes.

A bounded 100-pair AgentMemBench development diagnostic motivated the default:
the prior scorer returned the new fact first in 20% of rapid-update pairs, while
the adjusted scorer returned it first in 100%. This single synthetic diagnostic
does not establish the multiplier as universally optimal; retained workloads
must evaluate it directly.

---

## 8. Stage 6: Context Packing

Context packing selects which scored objects to include in the final bundle, respecting the token budget. This is specified in detail in RFC-0006. The interface between Stage 5 and Stage 6 is the ranked object list.

The bundle MUST be assembled in the following priority order within the token budget:

1. Pinned objects (salience == 1.0) and ACTIVE tasks — always included first.
2. Objects with `path_count >= 2` — higher confidence of relevance.
3. Objects ranked by composite score (descending).

Within each priority tier, the Signal-to-Token Ratio (RFC-0006) determines which objects are included when the budget is tight.

---

## 9. Generation Model and Measurement

**Superseded 2026-09-12:** the earlier section's 80.1%/92.5% table and
claims that temporal/aggregation queries are generation-bound are withdrawn.
The historical runs do not establish a comparable current-release baseline,
and oracle histories cannot establish retrieval through distractors. They do
not justify a retrieval quality ceiling or attributing all score differences
to generation quality.

Evaluate candidate evidence retrieval, context packing, and generated answers
separately. Record dataset variant and split, source/configuration provenance,
models, prompts, actual token budgets, category coverage, and errors. Compare
techniques on development questions before a frozen held-out run. See
[BENCHMARKS.md](../BENCHMARKS.md) for the current measurement contract.

An optional post-retrieval answerability evaluator is separate from the six
deterministic retrieval stages. It decomposes compound questions (and an
optional draft answer) into requirements, validates model-selected citations
against compact labels, reader `[m3]` references (with `context_citations`), or
auditable full UUIDs in the exact packed bundle, and
derives `answerable`, `partial`, `insufficient`, or `conflicting` in code. Empty
bundles are deterministically
insufficient; provider failures are errors rather than verdicts. Assessments
record prompt/model identity and content hashes but do not mutate memory or the
retrieval receipt. See [ANSWERABILITY.md](ANSWERABILITY.md).

---

## 10. Retrieval Logging

Every retrieval request MUST generate a retrieval log record in the operation log:

```json
{
  "op_type": "RETRIEVAL_REQUEST",
  "payload": {
    "request_id": "<uuid>",
    "namespace_scope": ["<namespace_id>"],
    "retrieval_mode": "DEFAULT",
    "query_intent": "FACTUAL_LOOKUP",
    "candidates_generated": 87,
    "candidates_after_filter": 54,
    "objects_included_in_bundle": 12,
    "tokens_used": 1840,
    "context_budget": 4096,
    "scores": { "<object_id>": 0.82 },
    "excluded": { "<object_id>": "DEPRECATED" },
    "embedding_model_version": "text-embedding-3-large-v2"
  }
}
```

Retrieval logs are used by the feedback loop (RFC-0009) to track which objects were injected, which were used, and which were wasted budget.

---

## 11. Determinism

Given identical:
- Reference clock for relative query dates and scoring decay
- Query embedding
- Index state at retrieval time
- Scoring weights
- Namespace policy versions
- Epistemic types and scores of all candidates

The retrieval pipeline MUST produce identical results. This is achievable because all score inputs are deterministic given the above.

Callers can supply a timezone-aware `reference_time` to `retrieve()`. Omission
captures UTC time once at request start. Use the same clock for query analysis,
primary scoring, reformulations, and cross-scope hint scoring; record it in
response metadata and the retrieval log. `knowledge_at` is a separate cutoff
for ingestion time and is not implicitly changed by this clock.

PRME serializes both query and ingestion dateparser calls under one shared lock.
Older supported parser versions share mutable settings and locale caches; separate
locks would allow mixed ingestion/retrieval threads to exchange reference clocks.
The lock guards PRME calls, not unrelated application calls to dateparser.

Non-determinism that MUST be guarded against:
- Floating-point ordering instability (use tie-breaking by `object_id` as a stable sort).
- HNSW approximate search non-determinism (use a fixed `ef_search` parameter and seed where supported).
- Graph traversal order instability (sort edges by `id` before traversal).

---

## 12. Conformance Requirements

`[REQUIRED FOR TIER 2]`

- All four candidate generation paths MUST be implemented.
- Epistemic filtering MUST occur before scoring, not after.
- The composite score formula MUST include all eight inputs.
- Weights MUST sum to 1.0 (excluding epistemic multiplier and path tiebreaker).
- Embedding version mismatch MUST be detected and handled.
- Every retrieval request MUST generate a retrieval log record.
- Retrieval MUST be deterministic given identical inputs.
- Graph traversal depth MUST NOT exceed 3 hops per query.

---

## 13. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Retrieval relevance benchmark: precision@5 and recall@10 on a labelled long-horizon conversational dataset (minimum 500 queries, minimum 2000 memory objects).
- Latency benchmark: p50/p95/p99 retrieval latency for bundles of 10, 50, and 200 memory objects.
- Namespace isolation verification: zero cross-namespace leakage across 10,000 sampled queries.
- Determinism test: identical results across 100 repeated identical queries on the same index state.
- Comparison baseline: retrieval quality compared to a vector-only baseline (no graph or lexical path). `[HYPOTHESIS — hybrid retrieval outperforms vector-only for long-horizon queries]`

`benchmarks.operational_eval` implements the latency-size, 100-repeat
determinism, and 10,000-query owner-isolation measurement contract through
public store/retrieve operations. Its default isolation corpus has five owner
partitions, meeting RFC-0004's minimum partition count at the implementation's
current owner/scope boundary. It records an incomplete artifact before work and
fails closed on any mismatch. A concrete host result must still be published;
the harness alone is not benchmark evidence.

---

## Stored-record enumeration

Top-k retrieval is not an enumeration or counting API. PRME exposes
`iter_nodes(user_id=..., scope=..., node_type=..., batch_size=...)` in async and
sync clients for complete traversal of matching stored records. `scan_nodes`
provides explicit pages with an `after_id` cursor. Both backends order by immutable
UUID and apply tenant, scope, type, and lifecycle filters before each page limit.
The default lifecycle set is active; an empty lifecycle filter returns no nodes.
HTTP exposes the same contract at `GET /v1/nodes/scan`; MCP exposes
`memory_scan_nodes`. Both remote surfaces require an explicit or bound owner and
return `has_more`, `next_cursor`, immutable-ID ordering, and page-level
consistency. The older `GET /v1/nodes` route remains a bounded query convenience.

Enumeration is complete for an unchanged store and uses bounded application
memory. It is not a cross-page transaction snapshot or a count of distinct
real-world events. Concurrent inserts, deletions, or lifecycle changes can alter
the matching set. Audited exports should use an unchanged pack and complete
pending ingestion first. Semantic aggregation still requires deciding which
stored assertions describe the same item and what evidence is missing.

### Structured assertion aggregation

`aggregate_assertions(AssertionQuery(...), user_id=...)` scans all selected
FACT, DECISION, and PREFERENCE pages and groups structured `subject`,
`predicate`, `object`, and `polarity` claim metadata. The sync client exposes the
same method. HTTP uses `POST /v1/assertions/aggregate`; MCP uses
`memory_aggregate_assertions`. Each remote call requires an explicit or bound
owner. This is the exact counting primitive for already structured claims; it
does not use vector, lexical, graph-neighborhood, or model-generated candidate
selection.

Selectors are ANDed across fields and ORed within a field. Values use NFKC,
case-fold, trim, and whitespace normalization; predicates additionally map
spaces and hyphens to underscores. Matching remains exact after normalization
and does not infer aliases or semantic equivalence. The query supports scope,
node-type, lifecycle, epistemic retrieval mode, inclusive event-time bounds,
`valid_at`, and the current-state `knowledge_at` cutoff. Explicit lifecycle
states are authoritative for this exact API and are not discarded by default
retrieval lifecycle rules. The default polarity is positive. An explicitly
empty selector matches all values for that field.

The response separates occurrence count from distinct-group count, returns
bounded node/evidence samples, and reports group/sample truncation and exclusion
reasons. `stored_set_exhaustive=true` means every selected structured record was
visited while the store remained unchanged. The independent coverage fields
report `source_extraction_coverage="unknown"`,
`semantic_equivalence="normalized_exact_only"`, and
`real_world_coverage="unknown"`. The application scan is not a multi-page
transaction snapshot; callers requiring an audited count must prevent concurrent
mutation and finish pending ingestion first. Numeric parsing and sums are
outside this contract.

### Grounded quantity aggregation

`aggregate_quantities(QuantityAggregationQuery(...), user_id=...)` applies the
same owner, assertion, scope, lifecycle, epistemic, and temporal filters, then
admits only quantity metadata with `grounding="object_decimal_v1"`. It validates
the stored decimal, verbatim unit, quantified source text, claim object, and
evidence again at read time. Missing and invalid quantities receive separate
exclusion counts.

Quantity queries may additionally provide `predicate_prefixes`. Each value uses
the same normalized predicate representation as exact selectors and matches
only itself or an underscore-delimited suffix: `raised` matches `raised` and
`raised_amount_for`, but not `fundraised`. Exact predicates and prefixes are
ORed within the predicate field; other selector fields remain ANDed. This is an
explicit lexical composition tool for model-authored predicate detail, not
synonym, alias or embedding inference. Responses using it report
`semantic_equivalence="normalized_exact_and_predicate_prefix"`; queries without
it retain `normalized_exact_only`.

Every group key must include `unit`; normalized units use only NFKC, case-fold,
trim, and whitespace normalization. No currency inference, plural resolution,
dimensional analysis, or unit conversion occurs. This prevents a total from
silently mixing values such as `$`, `USD`, `kg`, and `lb`. The response returns
an exact decimal `total`, `minimum`, `maximum`, contribution count, distinct
evidence count, event-time bounds, and bounded samples that keep each value with
its node, source text, unit, and evidence references. Decimal addition uses
scaled integers rather than the process decimal context, so totals do not round
at 28 significant digits. JSON represents decimal results as strings.

The completeness and consistency boundary is identical to structured assertion
aggregation: all matching grounded quantities are visited for an unchanged
store, while source-extraction and real-world coverage remain unknown. HTTP
exposes `POST /v1/quantities/aggregate`; MCP exposes
`memory_aggregate_quantities`.

`aggregate_quantities_from_text(question, user_id=...)` is a separate
fail-closed planner and executor for a fixed set of complete, qualifier-free
amount/count question shapes. It returns a typed plan with the exact structured
query, action, assumptions and either an aggregation or an unsupported reason.
Trailing qualifiers, negation, future wording, named subjects, unknown actions
and unsupported units do not scan memory. Action inflections map through a fixed
inspectable prefix table; first-person plans preserve the question's exact `I`
or `we` subject.
HTTP exposes `POST /v1/quantities/aggregate-text`; MCP exposes
`memory_aggregate_quantities_from_text`. Natural-language retrieval does not
automatically route to either exact operation.

Natural-language count and list retrievals expose this boundary directly as
`RetrievalMetadata.aggregation_coverage`. It reports unique candidates before
explicit selection, returned candidates, context-included candidates, stable
limitation codes (`semantic_matching`, candidate/backend limits, score/count
selection, and token budget), and any backend path observed at its candidate
cap. `exhaustive` is always false for semantic retrieval. The token-counted
memory bundle starts with the same coverage warning; if that warning cannot fit,
the packer emits no unqualified evidence. The alternate `format_for_llm()` path
also emits the boundary, including when zero candidates were found.

Coverage metadata and the exact structure are recorded in the retrieval
operation and versioned execution descriptor. HTTP and MCP return it under
`metrics.aggregation_coverage`. These diagnostics explain the retrieved set;
they do not turn semantic matching into corpus enumeration or deduplicate
multiple assertions about one real-world item.

Elapsed-time quantities such as “how many days ago” and “how many weeks passed”
are temporal arithmetic, not set aggregation. They must not widen the candidate
pool or emit incomplete-enumeration warnings. “How many times” remains an
aggregation query because it asks for event cardinality.

*End of RFC-0005*


## Implementation amendment — explicit selection (2026-09-12)

Ranking and selection are separate stages. Callers may set a finite nonnegative
`min_score` and integer nonnegative `limit`; both default to unset. After optional
reranking/session expansion, the pipeline accepts scores greater than or equal
to the floor, then retains at most `limit` primary candidates in ranked order.
Pinned nodes, instructions and active tasks do not override an explicit caller
bound. Packing sees only selected candidates. An empty selection stays empty.
Cross-scope hints apply the same floor and retain their independent count cap.
Responses expose epistemic/selection exclusions with reasons and scores; the
operation log records the floor, limit and selection exclusions. Token-budget
exclusions remain in the bundle. Request options do not mutate shared config.

Scores depend on the embedding model, corpus and scoring/reranking configuration;
they are not relevance probabilities. Defaults require held-out precision/recall
calibration before a universal acceptance floor is justified. The external
PrecisionMemBench diagnostic exposed high recall with excessive unrelated
candidates; offering an explicit floor fixes control, not semantic calibration.

HTTP retrieval previously accepted and ignored `filters`, `mode`, and `limit`.
These now forward to the pipeline, with typed scope/time fields and unknown-key
rejection. Explicit mode relaxes epistemic filtering only inside the generated
candidate pool; evicted historical indexes are not reconstructed by that flag.

`knowledge_at` is likewise an ingestion-time cutoff over the current candidate
pool, not historical-state replay. It requires a timezone-aware value. Every such
response exposes `metadata.historical_coverage` with `exact_snapshot=false` and
stable limitations for current lifecycle state, current derived indexes, and
unreplayed mutations. The same boundary is recorded in the retrieval receipt and
operation log and is included in the measured model context before any memory
records. If the warning cannot fit, the packer emits no unqualified evidence.

### Local embedding batch invariance (2026-09-12)

FastEmbed inference now uses one text per numerical batch. With the supported
quantized BGE model, batch padding changed a component by 0.000223577 in an
authored example. Since the LRU wrapper batches only cache misses, identical
requests could previously produce slightly different vectors depending on cache
residency. One-text inference removes this dependence in the tested runtime and
keeps direct writes, multi-node ingestion, queries and re-embedding consistent.
This is not a cross-hardware or cross-runtime bitwise guarantee.

Model weights, dimensions and the existing model-version identifier are
unchanged. Previously committed numerical payloads remain valid and are not
rewritten on opening a pack. Re-embedding older batched content may produce
small numerical differences; preserving recorded vectors remains necessary for
exact historical replay. The rebuild `batch_size` argument controls database
pagination, not embedding batch size, and already indexes one node per call.

An empty USearch snapshot is reopened into a fresh native index before durable
payload recovery. USearch 2.23.0 can crash when adding the first vector to a
loaded snapshot whose final vector was removed before saving; discarding that
empty native object loses no search data and keeps archive/delete followed by
restart and reuse safe. Nonempty snapshots retain the normal recovery path.

The throughput tradeoff depends on text lengths. A local 64-text authored probe
measured roughly 153 ms instead of 43 ms for short texts, but 435 ms instead of
571 ms for mixed lengths. These are illustrative ONNX-only timings, not end-to-end
performance guarantees. `benchmarks.diagnostics.embedding_invariance` retains
raw alternating-order timing trials, runtime/model asset hashes and numerical
cache/batch comparisons; it includes native process shutdown in its result.

Concurrent first use now serializes native model construction. The guard stays
held by the worker during initialization, even if its awaiting task is cancelled;
a failed initialization can be retried. The embedding cache stores private
immutable snapshots and returns independently owned lists on misses and hits.
Previously, caller/provider mutations could change cached vectors, and concurrent
calls could construct the native model twice. Authored regressions reproduced
both defects before these guards. Neither change alters model identity or
numerical vectors; it does not establish better semantic retrieval.

### Explicit embedding providers and query encoding

Both engine factories and the sync client accept a caller-owned
`embedding_provider`. Its validated metadata overrides the effective embedding
configuration without mutating the supplied config. Custom providers must be
supplied again on reopen; their implementation/resources are not persisted.
An optional `embed_query(text)` is used by both vector backends. Document writes
still use `embed(texts)`. Existing providers without query encoding preserve
their original behavior. Optional caching separates task entries and query
errors cannot silently fall back to document encoding. Receipts report the
actual provider identity and optional query method. See
[the public contract and example](CUSTOM-EMBEDDINGS.md). Built-in encoding
defaults are unchanged; new recipes require versioning and quality evaluation.

### PostgreSQL lexical candidate limits

`PgLexicalIndex` deduplicates matching graph and non-node index copies by node
identity inside SQL, after owner/type/scope filters and before applying the
candidate limit. The highest-scoring copy is retained; equal-scoring copies
prefer the graph row. Final score ties use node ID with C collation before the
limit. Duplicate source/index copies therefore cannot consume slots intended
for other matching memories. This corrects candidate completeness and stable
selection; it does not change PostgreSQL's existing `plainto_tsquery` semantics.
