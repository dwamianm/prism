# Phase 3: Retrieval Pipeline - Context

**Gathered:** 2026-02-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Queries return ranked, explainable, context-packed memory from all backends. A developer can query memory and receive ranked results that combine graph, vector, and lexical signals with explainable scores and token-budgeted context packing. Covers RETR-01 through RETR-06.

</domain>

<decisions>
## Implementation Decisions

### Query interface design
- Single unified `retrieve()` entry point — no separate methods for vector, lexical, or graph search
- System auto-detects intent from the query string and selects which backends to invoke — no caller hints or mode parameters
- First-class parameters: `retrieve(query, namespace=..., scope=..., time_from=..., time_to=..., token_budget=...)`  — explicit, discoverable, not nested in a filter object
- Returns a structured `RetrievalResponse` with results list + metadata (candidate counts per backend, scoring config used, timing, token usage)

### Scoring weight defaults
- Accept RFC-0005 proposed defaults: semantic=0.30, lexical=0.15, graph=0.20, recency=0.10, salience=0.10, confidence=0.10, epistemic=0.05 (multiplicative), paths=0.00 (tiebreaker)
- Per-request weight overrides: default weights in config, but caller can pass weight overrides on any retrieve() call
- Weight configurations are versioned — each config gets a version ID stored alongside retrieval results for reproducibility

### Context packing strategy
- Caller sets minimum fidelity: caller specifies a minimum representation level (REFERENCE, KEY_VALUE, STRUCTURED, PROSE, FULL) — system won't downgrade below it, just includes fewer results
- Truncation is explicitly signaled: response includes how many items were dropped, their IDs, and the budget remaining when cutoff happened
- Returns structured MemoryBundle with grouped sections: entity snapshots, stable facts, recent decisions, active tasks, provenance refs

### Trace & observability
- Full candidate audit: log every candidate ID, its scores, and the reason it was excluded — complete debugging picture for filtered/low-scored candidates
- Retrieval replay: each retrieval gets a request_id, and you can re-run it by ID to verify identical results — supports the deterministic constraint

### Claude's Discretion
- Score traces (per-result breakdown): Claude decides whether always-on or opt-in based on RFC-0005 §9 requirement
- RETRIEVAL_REQUEST event storage: Claude decides whether same DuckDB event store or separate ops log based on event sourcing architecture
- Embedding version mismatch handling: Claude picks the safest approach per RFC guidance
- Bin-packing priority order configurability: Claude balances simplicity and flexibility for the 3-tier priority system

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Implementation should follow RFC-0005 and RFC-0006 specifications closely.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-retrieval-pipeline*
*Context gathered: 2026-02-20*
