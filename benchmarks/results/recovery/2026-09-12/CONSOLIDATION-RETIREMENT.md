# Atomic consolidation retirement

Commit `d450160` prevents consolidation from retiring a source against stale
summary coverage. The original caller checked eligibility before invoking
supersedence, and on `ValueError` fell back to unconditional archival. A summary
retired in that gap could therefore cause its source to be archived too. Source
pinning, confidence, dates and summary coverage changes could also be missed.

The replacement validates current source and summary state inside a backend
transaction. It commits the source transition, one supersedence edge and a
checksummed `CONSOLIDATION_RETIRED` record together. The record preserves complete
before/after source values, the summary snapshot, policy clock and edge, including
legacy non-finite metadata through the existing snapshot encoding. Newly added
source evidence absent from the summary prevents retirement. Ineligible and
already retired sources are no-ops; storage errors propagate without fallback
archival. External index eviction follows commit. Publication of summaries and
idempotent reuse of unchanged summaries remain separate outstanding work.

## Native concurrency finding

PostgreSQL uses ordered row locks. The first DuckDB implementation used an
`updated_at` column claim but its cross-connection summary-archive test failed.
Changing and restoring that timestamp also failed. A reduced native SQL probe
located the distinction in the lifecycle ART index: an update to the indexed
lifecycle field could replace the row and bypass the claim. It was not simply
an optimized-away no-op. DuckDB documents indexed updates as delete/insert
rewrites in its [index limitations](https://duckdb.org/docs/current/sql/indexes#constraint-checking-in-update-statements).

Local initialization now drops `idx_nodes_lifecycle` for both new and existing
packs; owner/type/scope indexes remain. Native tests verify that concurrent
summary archival, source pinning and summary-coverage edits conflict with the
retirement transaction after migration. This covers supported graph mutations,
which also write `updated_at`, not arbitrary external SQL or custom indexes.
The PostgreSQL schema is unchanged.

## Verification

- Corrected pre-change tests: **5 failed, 5 skipped**, native exit 1. Every local
  case showed retirement despite a late eligibility change. The original test
  draft also tried unsupported content mutations; those failures are retained
  but are not used as proof of the retirement bug.
- Final focused tests with live PostgreSQL: **84 passed, 6 skipped**, 21.63 seconds,
  native exit 0. Covers changed inputs, injected rollback at three transaction
  stages, six concurrent calls, restart, legacy-index migration and lifecycle
  regressions. DuckDB-specific checks and an existing PostgreSQL-only race
  account for backend-inapplicable skips.
- Fresh installed Python 3.13 wheel: **84 passed, 6 skipped**, 34.64 seconds,
  native exit 0. All **130** installed Python files matched frozen source before
  and after testing. Source tests use DuckDB 1.4.4; this installed environment
  uses DuckDB 1.5.5.
- Repository Ruff and strict public-client mypy passed.
- Full live PostgreSQL suite is tracked separately; its native completion must
  be recorded before calling that check passed.

A separate 100,000-row synthetic SQL check returned identical ordered IDs for
three query shapes before and after index removal. Median milliseconds changed
from 0.489 to 0.522 for tenant-active queries, 4.600 to 4.855 for global-active
queries and 0.214 to 0.218 for rare contested queries. This ran on a shared host,
with fixed indexed-then-unindexed order and possible cache effects. It is a small
local diagnostic, not end-to-end latency, capacity validation or a universal
performance claim. The runnable probe is
[`lifecycle_index.py`](../../../diagnostics/lifecycle_index.py).

No packing default, quality score or simulation assertion changed. The existing
70/74 scenario gate and incomplete answer studies remain unresolved.
