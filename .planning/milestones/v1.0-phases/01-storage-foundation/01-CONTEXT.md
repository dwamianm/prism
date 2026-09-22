# Phase 1: Storage Foundation - Context

**Gathered:** 2026-02-19
**Status:** Ready for planning

<domain>
## Phase Boundary

All four storage backends (event store, graph store, vector index, lexical index) with typed data model, temporal validity, lifecycle states, and user/session scoping. A developer can programmatically create, read, and query all backends through a unified memory interface.

</domain>

<decisions>
## Implementation Decisions

### Graph backend strategy
- **DuckDB primary via DuckPGQ, with GraphStore abstraction as escape hatch.** User wants to explore DuckPGQ to eliminate the Kuzu dependency entirely. Build on DuckPGQ but behind a GraphStore interface so an alternative can be swapped in if DuckPGQ has gaps.
- If research shows DuckPGQ can't handle critical graph operations (multi-hop path queries, temporal filtering): **Claude's discretion** to evaluate the tradeoff and pick the best fallback path.
- GraphStore abstraction should be **full-spec** — all graph operations the spec describes (supersedence chains, provenance traversal, confidence-weighted paths, neighborhood queries, temporal filtering). Not minimal CRUD.
- Portable artifact format: **doesn't matter** to the user — implementation detail. If graph lives in DuckDB, one fewer file is fine.

### Day-one data model scope
- **Full schema from day one.** All fields from the spec (confidence, salience, scope, evidence refs, supersedence pointers, validity windows) are present in the Phase 1 schema, even if not all are used until later phases.
- Node types: **7 fixed spec types + generic 'Note' type.** Entity, Event, Fact, Decision, Preference, Task, Summary are the core types. A catch-all Note type handles anything that doesn't fit. No open-ended extensibility.
- Scope: **All three scopes (personal/project/org) supported from day one.** Schema and isolation logic built for all three scopes immediately.
- Embedding model support: **Claude's discretion** on whether to support multiple embedding models simultaneously or one at a time. Evaluate based on spec requirements and practicality.

### Lifecycle state handling
- **Full transition API in Phase 1.** Developers can call promote(), supersede(), archive() with validation rules (e.g., can't go backwards from Stable to Tentative). Transition logic lives in Phase 1, not deferred to Phase 5.
- Superseded objects: **Stay in place, marked as superseded.** Not moved to a separate partition. Queries can filter them out or include them.
- Supersedence evidence: **Required for automated transitions, optional for manual.** When Phase 5's Organizer supersedes something, evidence is required. When a developer calls supersede() directly, evidence pointer is optional.
- Query defaults: **Filter to active (Tentative + Stable) by default.** Callers must explicitly opt in to see Superseded/Archived objects.

### Store API surface
- **Unified memory interface.** Single MemoryEngine entry point, not separate store objects. Developers interact with one interface that routes to the right backend.
- **Auto-propagate to all backends.** When a developer calls memory.store(), it writes to the event log AND updates graph AND indexes into vector AND lexical in one call. Developer doesn't think about which backends are involved.
- Async vs sync: **Claude's discretion.** Evaluate based on what downstream phases need (Phase 4 requires async) and what's practical for DuckDB/Tantivy.
- Configuration approach: **Claude's discretion.** Pick whatever serves portability and developer ergonomics best.

### Claude's Discretion
- Embedding model multiplicity (single vs. concurrent models in vector index)
- Async-first vs sync-first API design
- Configuration/initialization approach for the MemoryEngine
- DuckPGQ fallback strategy if gaps are found during research
- Portable artifact file layout

</decisions>

<specifics>
## Specific Ideas

- User specifically wants to explore DuckPGQ to reduce the dependency count — consolidating event store + graph into a single DuckDB instance is a strong preference if feasible.
- The Note catch-all type was chosen over full extensibility to avoid schema complexity while covering edge cases.
- The "auto-propagate" storage model means Phase 1 already handles what would normally be Phase 2's job for direct API writes — the ingestion pipeline (Phase 2) handles conversation-to-memory extraction specifically.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-storage-foundation*
*Context gathered: 2026-02-19*
