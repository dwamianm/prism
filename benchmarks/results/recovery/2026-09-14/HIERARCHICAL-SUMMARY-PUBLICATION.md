# Atomic hierarchical summary publication

Daily, weekly and monthly source excerpts previously wrote their graph node,
provenance edges, vector and lexical document as separate operations. Existing
periods were skipped permanently, so a late high-salience source could not enter
an already-created excerpt. Concurrent engines could also create duplicate
summaries for one owner, scope and period.

## Implemented contract

The owner, exact scope, summary level and UTC period now define a stable lineage.
An immutable request hash binds the selected source snapshots, rendered content,
scores, policy version and embedding identity. Summary and edge identities are
deterministic for that request generation.

The hierarchy shares the consolidation publication protocol. DuckDB journals the
complete prepared input and stages the vector and lexical document under a
database fence before graph publication. PostgreSQL writes pgvector inside the
graph transaction. The summary, all `DERIVED_FROM` edges, generation advance,
predecessor archival and checksummed publication record commit together. An
interrupted retry reads the prepared plan and does not call the embedding model
again.

An unchanged run reuses the one active summary. If the selected inputs change,
the next run publishes a new generation and archives the previous one atomically.
Concurrent calls and independent engine instances converge on the same identity.
The first managed publication treats an active legacy `source-excerpts-v1`
summary for the same bucket as a predecessor, preventing an upgrade from leaving
two active period summaries.

## Verification

Focused DuckDB tests passed **35 tests with 15 PostgreSQL cases skipped**. Ruff
passed all changed source and test files, and focused mypy passed the publication
model, organizer and storage modules.

The regression selection covers daily, weekly and monthly behavior, full source
qualifiers and provenance, unchanged reuse, same-engine and independent-engine
concurrency, late selected-source replacement, legacy migration, injected
publication failure, durable restart without re-embedding, and the pre-existing
consolidation publication and recovery contracts.

These checks establish consistency and recovery behavior. They do not show that
extractive hierarchical summaries improve reader answers, and the configured
per-summary item limit still makes each excerpt explicitly non-exhaustive.
