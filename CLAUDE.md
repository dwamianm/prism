# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PRME (Portable Relational Memory Engine) is a local-first, embeddable memory substrate for LLM-powered systems. It combines event sourcing, graph-based relational modeling, hybrid retrieval, and organizer-driven memory reorganization (opportunistic and on-demand; see Organizer). The system is implemented (current release v0.9.0); design specs live in `docs/`.

## Architecture

Four storage layers behind a unified retrieval API. The default backend is local DuckDB-based; a PostgreSQL backend (`src/prme/storage/pg/`) is also wired and selected when `database_url` is set.

- **Event Store (DuckDB)** — Append-only immutable event log. All derived structures must be rebuildable from this log.
- **Graph Store (DuckDB)** — Typed nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) and typed edges with temporal validity windows (`valid_from`/`valid_to`), confidence scores, and provenance references. Implemented in `src/prme/storage/duckpgq_graph.py` using DuckDB tables with recursive CTEs for traversal (DuckPGQ SQL/PGQ is not available on the supported DuckDB build, so recursive CTEs are the only code path — there is no Kùzu dependency).
- **Vector Index (USearch HNSW)** — Approximate nearest neighbor search via the `usearch` library (`src/prme/storage/vector_index.py`), persisted to `vectors.usearch`, with versioned embeddings (model name, version, dimension tracked per embedding).
- **Lexical Index (Tantivy)** — BM25 full-text search via `tantivy-py` (`src/prme/storage/lexical_index.py`), persisted to a `lexical_index/` directory, over event content, facts, and summaries.

## Key Design Constraints

- **Append-only**: Events must never be overwritten or deleted except by policy-based archival. Conflicting assertions must not silently overwrite prior ones — use supersedence. Note: store-time supersedence is gated behind `enable_store_supersedence` (default `False`, `src/prme/config.py`); with the default, `store()` does not supersede and conflict resolution relies on retrieval-time `[LATEST]`/recency markers in the context formatter rather than graph-level supersedence edges. Predicate matching in the supersedence detector is exact-match plus three hardcoded equivalence classes (`src/prme/ingestion/supersedence.py`); paraphrased predicates are not currently superseded.
- **Deterministic**: Given identical event logs and config, retrieval results must be reproducible. Scoring weights must be configurable and versioned.
- **Portable artifact**: The memory pack — `memory.duckdb` (event store + graph tables), `vectors.usearch` (USearch index), `lexical_index/` (Tantivy directory), and `manifest.json` (encryption/version metadata) — must be copyable, encryptable, and rebuildable. Derived indexes can be regenerated from the durable graph with `prme rebuild`.

## Hybrid Retrieval Pipeline

Query → intent classification + entity extraction + time detection → candidate generation (graph neighborhood, stable facts, vector similarity, lexical, recent high-salience) → deterministic re-ranking → context packing into memory bundles (entity snapshots, stable facts, recent decisions, active tasks, provenance refs).

## Memory Object Lifecycle

Objects progress through: Tentative → Stable → Superseded → Archived. Each object carries: id, type, scope (personal/project/org), confidence, salience, validity window, evidence references, and supersedence pointer.

## Organizer

The organizer (`src/prme/organizer/`, RFC-0015) provides maintenance jobs that handle: salience/confidence recalculation, promotion/demotion of assertions, summarization, deduplication/entity alias resolution, policy-based archival with TTL enforcement, tombstone and index compaction sweeps, and snapshot generation. `ALL_JOBS` currently registers twelve jobs (some are full implementations, some stubs pending future RFCs).

Despite the name "scheduled," there is **no built-in cron or daemon scheduler**. Jobs run in two ways: (1) an opportunistic in-process pass triggered during retrieve/ingest, gated by a cooldown (`opportunistic_cooldown`, default 3600s) and a per-pass time budget (`opportunistic_budget_ms`, default 200ms); and (2) explicit invocation via `prme organize` (optionally `--jobs` and `--budget-ms`). Continuous scheduling, if needed, must be driven by an external cron/timer calling `prme organize`.

## RFCs

Design specifications live in `docs/` as numbered RFCs (RFC-0000 through RFC-0015). See `docs/INDEX.md` for the full listing. Key RFCs include:

- **RFC-0000** — Suite overview
- **RFC-0001** — Core data model
- **RFC-0002** — Event store
- **RFC-0003** — Epistemic state model
- **RFC-0005** — Hybrid retrieval pipeline
- **RFC-0014** — Portability, sync, and federation
- **RFC-0015** — Self-organizing memory (organizer execution model)

Always consult the relevant RFC before implementing or modifying a subsystem.

## Configuration Surface

Config is defined as Pydantic models in `src/prme/config.py` and `src/prme/retrieval/config.py` (loaded from `PRME_`-prefixed env vars, `.env`, or direct args). The surface is large (roughly 100 fields across both files). Several parameter defaults are explicitly tagged `[HYPOTHESIS]` in their descriptions — these are reasoned but not yet benchmark-validated and may change. Treat `[HYPOTHESIS]` knobs as provisional and prefer not to depend on their exact values. Notable defaults to be aware of: `enable_store_supersedence=False`, `enable_surprise_gating=False`, and `enable_reranker=False` (the cross-encoder reranker has not improved benchmark scores in practice).

## Storage Backends

- **DuckDB (default)** — local-first; no `database_url` set.
- **PostgreSQL** — used when `database_url` is set; implemented in `src/prme/storage/pg/`. Its test suite (`tests/test_pg_*.py`) is skipped unless `PRME_TEST_DATABASE_URL` points at a live database, so those tests do not run in the default local/CI environment.

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

- Do not make any claude attributions to git commits
