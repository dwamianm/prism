# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

PRME (Portable Relational Memory Engine) is a local-first, embeddable memory substrate for LLM-powered systems. It combines event sourcing, graph-based relational modeling, hybrid retrieval, and organizer-driven memory reorganization (opportunistic and on-demand; see Organizer). The system is implemented (current release v0.11.0); design specs live in `docs/`.

## Architecture

Four storage layers behind a unified retrieval API. The default backend is local DuckDB-based; a PostgreSQL backend (`src/prme/storage/pg/`) is also wired and selected when `database_url` is set.

- **Event Store (DuckDB)** — Append-only immutable event log. All derived structures must be rebuildable from this log.
- **Graph Store (DuckDB)** — Typed nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) and typed edges with temporal validity windows (`valid_from`/`valid_to`), confidence scores, and provenance references. Implemented in `src/prme/storage/duckpgq_graph.py` using DuckDB tables with recursive CTEs for traversal (DuckPGQ SQL/PGQ is not available on the supported DuckDB build, so recursive CTEs are the only code path — there is no Kùzu dependency).
- **Vector Index (USearch HNSW)** — Approximate nearest neighbor search via the `usearch` library (`src/prme/storage/vector_index.py`), persisted to `vectors.usearch`, with versioned embeddings (model name, version, dimension tracked per embedding).
- **Lexical Index (Tantivy)** — BM25 full-text search via `tantivy-py` (`src/prme/storage/lexical_index.py`), persisted to a `lexical_index/` directory, over event content, facts, and summaries.

## Key Design Constraints

- **Append-only**: Events must never be overwritten or deleted except by policy-based archival. Conflicting assertions must not silently overwrite prior ones — use supersedence. Note: store-time supersedence is gated behind `enable_store_supersedence` (default `False`, `src/prme/config.py`); with the default, `store()` does not supersede. Retrieval can present chronological markers and unresolved conflicts; recency alone is not treated as proof of truth or graph-level supersedence. Predicate matching in the supersedence detector is exact-match plus three hardcoded equivalence classes (`src/prme/ingestion/supersedence.py`); paraphrased predicates are not currently superseded.
- **Deterministic**: Given identical event logs and config, retrieval results must be reproducible. Scoring weights must be configurable and versioned.
- **Portable artifact**: The memory pack — `memory.duckdb` (event store + graph tables), `vectors.usearch` (USearch index), `lexical_index/` (Tantivy directory), and `manifest.json` (encryption/version metadata) — must be copyable, encryptable, and rebuildable. Derived indexes can be regenerated from the durable graph with `prme rebuild`.

## Hybrid Retrieval Pipeline

Query → intent classification + entity extraction + time detection → candidate generation (graph neighborhood, stable facts, vector similarity, lexical, recent high-salience) → deterministic re-ranking → context packing into memory bundles (entity snapshots, stable facts, recent decisions, active tasks, provenance refs).

`knowledge_at` is a timezone-aware ingestion cutoff over current graph and index
state. It is not exact historical replay. Responses expose
`metadata.historical_coverage` and inject the same boundary into packed model
context; lifecycle transitions, mutations, and evicted index entries are not
reconstructed.

`aggregate_assertions` is the complete structured counting/grouping path over
stored subject/predicate/object/polarity metadata; it does not use semantic
top-k retrieval. `aggregate_quantities` accepts only read-time-revalidated
`object_decimal_v1` quantity metadata, keeps unit in every group, uses exact
decimal addition, and never converts units or infers currencies. Both require an
owner, are complete only for an unchanged store, and report unknown extraction
and real-world coverage. HTTP and MCP expose corresponding aggregate tools.

`get_assertion_state` is the exact current-claim/timeline path for a structured
subject and predicate within one owner and scope. Callers must supply a timezone
aware `valid_at`. It reports event, ingestion and validity clocks,
supersedence, contradiction links, eligibility reasons and evidence. Differing
active claims remain `multiple` unless explicit conflict state makes them
`contested`; recency never establishes truth. `knowledge_at` retains the same
current-graph, non-replay limitation. HTTP and MCP expose corresponding state
operations.

## Memory Object Lifecycle

New event/direct-node metadata must be finite and JSON-serializable, with no
object keys that collide after JSON normalization. Admission
copies metadata before awaiting backend locks/connections; it does not validate
all low-level graph writes. Existing rows remain readable. Journal snapshots for
lifecycle, reinforcement and organizer merges use `_snapshot_json` to preserve
legacy non-finite values through a versioned path encoding; finite record bytes
and old raw checksums must remain unchanged. Do not restore Pydantic JSON
serialization that silently converts non-finite metadata to null. See
`docs/METADATA.md` for exact compatibility limits.

New extraction plans use `temporal_validity_v7`. A quantity survives only
when one supported decimal, its quantified phrase, and its verbatim unit occur
in both the claim object and source evidence. Invalid optional quantities are
dropped without discarding the claim. Older plan policies and checksums remain
unchanged; do not infer unit aliases or rewrite historical metadata. Extracted
facts start validity at their resolved source-effective time. Valid newer
replacements close the prior interval atomically when the boundary does not
precede its stored start; legacy intervals that would invert remain unchanged.

Objects progress through: Tentative → Stable → Superseded → Archived. Each object carries: id, type, scope (personal/project/org), confidence, salience, validity window, evidence references, and supersedence pointer.

## Organizer

The organizer (`src/prme/organizer/`, RFC-0015) provides maintenance jobs that handle: salience/confidence recalculation, promotion/demotion of assertions, summarization, deduplication/entity alias resolution, policy-based archival with TTL enforcement, tombstone and index compaction sweeps, and snapshot generation. `ALL_JOBS` registers only implemented jobs; proposed work such as the unvalidated centrality boost is not exposed as runnable maintenance.

Despite the name "scheduled," there is **no built-in cron or daemon scheduler**. Jobs run in two ways: (1) an opportunistic in-process pass triggered during retrieve/ingest, gated by a cooldown (`opportunistic_cooldown`, default 3600s) and a per-pass time budget (`opportunistic_budget_ms`, default 200ms); and (2) explicit invocation via `prme organize` (optionally `--user-id`, `--jobs`, and `--budget-ms`). Continuous scheduling, if needed, must be driven by an external cron/timer calling `prme organize`.

**Multi-tenant stores must pass a user scope.** `organize(user_id=...)` confines every job to that user's nodes. An unscoped run is an operator action over all users and is still the default. Duplicate/alias matching and consolidation preserve both owner and scope, including on unscoped runs; a shared application should still maintain tenants explicitly to confine which users are mutated. `DEFAULT_JOBS` excludes the legacy engine-global `feedback_apply` tuner, and `end_session()` runs promotion only. Explicit scoped requests containing `feedback_apply` raise `ValueError` before any work; explicit unscoped operator selection remains available and reports `"scope": "global"`.

`consolidate_knowledge(user_id=..., scope=...)` builds entity profiles within
one scope; omission visits every scope separately. It excludes generated profiles
from source evidence and retires obsolete profiles during an explicit rebuild.
Version 2 profiles preserve complete source excerpts and provenance under an
exact token limit, keep distinct episodes and mark generated associations as
inferred. Name matching remains heuristic. Each replacement uses atomic graph
publication after index preparation; errors propagate and the prior profile
remains active until commit. Source collection pages through the active scope
instead of treating the newest 5,000 nodes as complete history. Prepared profiles are journaled before staging; `profile_jobs`, `resume_profile`
and `process_profiles` provide owner-scoped Python inspection and explicit recovery
without new model calls. Matching retries reuse fixed inputs; changed requests
replace pending preparations under a native-stage fence. Missing work rows are
reconstructed from the immutable journal at startup. Owner-scoped `discard_profile` abandons unpublished work;
`collect_profile_staging` reclaims exact, uniquely owned abandoned indexes under a
work-epoch fence and preserves the journal. Invalid ownership records block
collection. There is no automatic profile scheduler.
This convenience API is separate from the organizer's `consolidate` job.

Organizer merges also enforce semantic/provenance compatibility, including
memory/entity types, source type, session, event time and metadata. Non-entity
copies require exact content and the same validity start. Vector similarity alone
does not authorize merging claims; purely semantic aliases remain unverified
`RELATES_TO` proposals. Extracted unresolved English personal references are
event-local under new `event_local_references_v4` plans; old plans replay unchanged.
See `docs/ENTITY-IDENTITY.md` for the precise boundaries and limitations.

Duplicate/alias merges publish evidence, relationship copies, source retirement
and one supersedence edge in a backend transaction. `ORGANIZER_MERGED` retains
checksummed complete inputs and outputs; repeated pair/kind operations do not
rewrite later graph state. PostgreSQL locks nodes in UUID order before reading
evidence. DuckDB conflicts can fail safely and require a fresh retry. External
index eviction follows commit and remains repairable by compaction. Do not
restore separate evidence/edge/lifecycle writes or infer rollback from a cancelled
caller. Historical organizer/manual mutations are not all replayable yet.

Consolidation retirement rechecks current source/summary coverage and policy
inside one transaction, then commits the supersedence edge and a checksummed
`CONSOLIDATION_RETIRED` before/after record. It must not fall back to archive on
supersedence failure. Local initialization removes `idx_nodes_lifecycle` because
indexed lifecycle replacement can bypass DuckDB column update claims. Do not
reintroduce mutable-column ART indexes without proving concurrent validation.
Hierarchical daily, weekly and monthly excerpts reuse the same prepared
publication machinery. Their lineage is owner, scope, level and UTC period;
unchanged inputs reuse one identity, changed selected inputs atomically publish a
new generation and archive the predecessor, and the first managed run atomically
retires legacy `source-excerpts-v1` summaries for that bucket. DuckDB stages
indexes after durable preparation; PostgreSQL publishes pgvector inside the graph
transaction. Do not restore separate summary, edge and index writes.

Single-node `promote`, `archive` and `deprecate` validate the current lifecycle
inside the same backend transaction as the update and a checksummed
`LIFECYCLE_CHANGED` before/after record. PostgreSQL holds the target row lock;
DuckDB holds its connection lock until native work finishes. This prevents a
stale promotion from reactivating a concurrently archived node. PostgreSQL
supports contested-to-deprecated transitions directly. Invalid transitions still
raise; these operations have no caller-supplied retry identity. Raw graph updates
and older mutations are not covered by this record or a complete replay engine.

`supersede`, `supersede_many`, `contradict` and `resolve_contradiction` validate
optional evidence inside their existing transactions. The event must exist in
the nodes' owner and scope; malformed, missing and foreign references all fail
with the same availability error. Invalid batch evidence rolls back the whole
batch. This is provenance membership, not semantic entailment or validation of
arbitrary low-level edges. `MemoryClient.supersede` exposes explicit corrections
to synchronous callers. See `docs/MEMORY-CORRECTIONS.md`.

Explicit `reinforce()` validates owner/scope evidence and commits current-value
increments with a checksummed complete before/after `REINFORCE` record in one
backend transaction. Successful concurrent calls accumulate; injected failures
roll back both graph and journal. Existing increment caps and above-cap values
are preserved. Optional owner-scoped `request_id` UUIDs make same-request retries
idempotent across restart, with changed node/evidence requests rejected. Unkeyed
calls remain separate signals. Version 2 records retain request identity; version
1 records keep their original checksums. This is not semantic evidence verification
or complete historical replay. See `docs/REINFORCEMENT.md`.

## RFCs

Design specifications live in `docs/` as numbered RFCs (RFC-0000 through RFC-0017). See `docs/INDEX.md` for the full listing. Key RFCs include:

- **RFC-0000** — Suite overview
- **RFC-0001** — Core data model
- **RFC-0002** — Event store
- **RFC-0003** — Epistemic state model
- **RFC-0005** — Hybrid retrieval pipeline
- **RFC-0014** — Portability, sync, and federation
- **RFC-0015** — Self-organizing memory (organizer execution model)
- **RFC-0016** — Durable derivation commits (normal ingestion uses durable extraction jobs, extraction/plan journaling, idempotent index staging and fenced atomic graph commit; explicit plan revision and fenced retired-stage collection are supported; ambiguous or unmanaged legacy staging is retained)

Always consult the relevant RFC before implementing or modifying a subsystem.

## Retrieval feedback and learning

New retrieval logs include owner-scoped `RetrievalReceipt` snapshots and report
`metadata.receipt_persisted`. `record_relevance(RelevanceSubmission(...), user_id=...)`
appends explicit candidate labels with an optional caller-selected retry identity.
These records survive restart and are exposed through Python, HTTP and MCP.
They do not mutate weights or feed the legacy engine-global `feedback_apply` job.
Version 2 receipts capture applied weights, neural/session score operations and
sort policy for exact returned-candidate replay with `receipt.replay_ranking()`.
Version 1 canonical JSON/checksums must remain unchanged; those receipts still
accept labels but cannot replay scores. Replay excludes unseen/filtered candidates.
Version 3 adds request parameters and reported feature identity in extensible
execution maps. Density/score pipeline receipts use version 4 to record the explicit
packing order; versions 1–3 retain their canonical bytes and implicit density
ordering. The default `multipath_ordering="balanced"` reserves the highest-scored
ordinary multi-path candidate, then uses score / full-entry-tokens**0.25.
It emits version 5 receipts with explicit ordering and execution; versions 1–4
cannot claim balanced and retain their canonical bytes. See `docs/PACKING.md`.
Explicit density and score policies remain available. The default follows a
complete 119-question development answer trial where balanced scored 83 versus
density at 67 and a separately registered 381-question answer confirmation where
balanced scored 250 versus 185, plus source-retention gains on both cohorts.
These examined cohorts do not establish universal superiority.
Python, HTTP and MCP retrieve accept explicit per-request `ranking_multipliers`
for full-pipeline trials; they are applied after query adjustment and do not activate a profile.
Python `evaluate_learning` fits an offline weight-multiplier proposal from a
bounded feedback snapshot with stable query splits, explicit pairs, coverage and
validation metrics. It does not activate weights. `evaluate_full_retrieval`
checks fresh paired baseline/candidate receipts against complete relevant-node
sets on a fixed holdout. Immutable owner/exact-scope ranking profiles require
positive results at both stages before activation. Activation, deactivation and
rollback are append-only, retry-safe and serialized per owner/scope; retrieval
applies a compatible active profile per request and reports explicit overrides
or feature/base-scoring incompatibility. See `docs/LEARNING.md` and RFC-0017.
These evaluations are retrieval evidence, not answer-quality or universal
superiority evidence.

## Configuration Surface

`vector_exact_search=True` applies to both storage backends. PostgreSQL must keep
eligibility inside a materialized scoring boundary before top-k ordering; merely
putting WHERE filters on an HNSW query can hide eligible memories. `False` allows
approximate search and its recall tradeoff. New receipts report the configured
mode in `execution.features.vector_search.exact`; old receipt bytes remain unchanged.

Config is defined as Pydantic models in `src/prme/config.py` and `src/prme/retrieval/config.py` (loaded from `PRME_`-prefixed env vars, `.env`, or direct args). The surface is large (roughly 100 fields across both files). Several parameter defaults are explicitly tagged `[HYPOTHESIS]` in their descriptions — these are reasoned but not yet benchmark-validated and may change. Treat `[HYPOTHESIS]` knobs as provisional and prefer not to depend on their exact values. Notable defaults to be aware of: `enable_store_supersedence=False`, `enable_surprise_gating=False`, and `enable_reranker=False` (the cross-encoder reranker has not improved benchmark scores in practice).

## Storage Backends

`MemoryWorkspace` manages named projects as identity-checked local packs or
PostgreSQL schemas, with a bounded cache and lease-scoped `NamespaceMemory` API.
`open_postgres()` shares one connection pool per workspace instance. Each checkout
verifies schema identity, clears temporary tables and excludes public-table
fallback. pgvector symbols must remain qualified to their installed schema.
Registry and schema initialization commit together; missing initialized relations
must fail before ordinary migration. Closing an engine drains its namespace facade,
not the shared root pool. Read
`docs/WORKSPACES.md` before changing registry, pack identity, or lease ownership.
Never evict an engine with live leases or lease operations; cancelled opens and
closes must settle ownership. A missing initialized database or mismatched pack
identity must fail explicitly. Registry names/IDs are plaintext metadata. This
does not implement hosted grants or malicious-local-code isolation. Normal unbound engine/CLI access remains trusted operator access.

- **DuckDB (default)** — local-first; no `database_url` set.
- Optional `duckdb_threads` sets workers when a local database opens; `None`
  preserves the native default. All concurrent engines for the same file must
  use matching settings. Do not use a later `SET threads` to silently reconfigure
  an already-open engine. This does not cap embedding/index threads or memory.
- **PostgreSQL** — used when `database_url` is set; implemented in `src/prme/storage/pg/`. Its test suite (`tests/test_pg_*.py`) is skipped unless `PRME_TEST_DATABASE_URL` points at a live database, so those tests are skipped locally without a database. CI runs them against a live PostgreSQL service on Python 3.11–3.13.

## MVP Phases (delivered)

The originally planned MVP scope is implemented as of v0.9.0:

1. Event store, graph schema, vector search, hybrid retrieval
2. Organizer jobs, stable fact promotion, snapshot generation, supersedence handling (store-time supersedence opt-in; see Key Design Constraints)
3. Encryption, CLI tooling, evaluation harness, deterministic rebuild validation (`prme rebuild`)

## Browser Automation

Use `agent-browser` for web automation. Run `agent-browser --help` for all commands.

Core workflow:

1. `agent-browser open <url>` - Navigate to page
2. `agent-browser snapshot -i` - Get interactive elements with refs (@e1, @e2)
3. `agent-browser click @e1` / `fill @e2 "text"` - Interact using refs
4. Re-snapshot after page changes

## Github

- Do not make any Codex attributions to git commits
