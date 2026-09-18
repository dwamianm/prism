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
two active period summaries. Organizer discovery now pages through every active
input in immutable-ID order, removing the previous 5,000-node daily and
1,000-summary weekly/monthly caps. Unscoped maintenance opts into an internal
operator scan; the public enumeration API remains owner-required.

## Verification

Focused DuckDB tests passed **43 tests with 23 PostgreSQL cases skipped**. Ruff
passed all changed source and test files, and focused mypy passed the publication
model, organizer and storage modules.

The exact source head passed the complete local suite with **2,949 tests passed
and 745 skipped** in 579.67 seconds. The first live PostgreSQL 3.13 run passed
3,484 tests before one new suite-global operator test asserted that only its two
owners existed in the shared database. The operator scan correctly returned
owners created by earlier tests. The test now proves the 501-source paging
boundary within its unique owner and checks cross-tenant operator visibility
read-only, without mutating unrelated namespaces; the replacement matrix run is
the acceptance result.

The regression selection covers daily, weekly and monthly behavior, full source
qualifiers and provenance, unchanged reuse, same-engine and independent-engine
concurrency, late selected-source replacement, legacy migration, injected
publication failure, durable restart without re-embedding, a 501-source scoped
pagination boundary, explicit cross-tenant operator enumeration, public
owner-required enumeration, and the pre-existing consolidation publication and
recovery contracts.

These checks establish consistency and recovery behavior. They do not show that
extractive hierarchical summaries improve reader answers, and the configured
per-summary item limit still makes each excerpt explicitly non-exhaustive.
