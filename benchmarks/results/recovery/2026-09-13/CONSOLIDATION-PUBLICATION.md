# Atomic consolidation publication

Commit `6db7893` replaces the organizer's interleaved summary writes with a
checksummed, restart-safe publication protocol.

## Failure reproduced

The previous path created the summary, changed its scores and evidence, and then
created provenance edges as separate operations. A crash could expose a partial
summary, and rerunning the same unchanged cluster could create another summary.
Concurrent engine instances had no shared publication identity or generation
fence.

## Implemented guarantee

The owner, scope, and exact sorted source identities define a consolidation
lineage. A request hash covers each immutable source snapshot, the selected source
order, rendered content, scores, policy version, and embedding identity. The
summary and `DERIVED_FROM` edge IDs are deterministic for that request and
generation.

DuckDB journals the complete prepared publication, reserves the artifact, and
stages its vector and lexical document under a database-backed fence. Publication
then creates the summary and edges, archives the prior generation, advances the
lineage head, and writes the receipt in one database transaction. A real source
timestamp claim makes concurrent supported source mutation conflict until the
validated transaction finishes. PostgreSQL locks dependency and generation rows
and writes pgvector inside the graph transaction.

An interrupted retry reads the prepared publication and reuses the saved
embedding. Identical concurrent attempts, including independent engine instances,
converge on one active summary identity. Artifact cleanup retains unpublished
consolidation staging until its ownership plan is registered.

Lineage identity is exact for a fixed source set. Adding or removing a source
creates another lineage; lifecycle maintenance must archive the earlier generated
view. The implementation does not infer semantic cluster identity across changing
members.

## Verification

- Full Python 3.11 suite with live PostgreSQL and pgvector: **3,238 passed, 93
  skipped** in 515.69 seconds.
- Focused DuckDB profile and consolidation suites: **88 passed, 67 skipped** in
  48.14 seconds.
- Final DuckDB-heavy publication and retirement selection: **48 passed, 27
  skipped**.
- Final PostgreSQL plus DuckDB publication selection: **46 passed, 8 skipped**;
  the live PostgreSQL publication file alone passed all 8 tests.
- Ruff passed across `src` and `tests`; focused mypy passed for the publication,
  organizer, and immutable model modules.
- A Python 3.13 wheel built from the committed runtime was installed into a fresh
  environment. Two identical consolidation calls produced one active summary
  identity and exactly three provenance edges through the installed package.

Regression tests cover unchanged reuse, changed-source replacement, restart
without re-embedding, same-engine concurrency, independent-engine concurrency,
and a DuckDB source mutation racing the publication claim.
