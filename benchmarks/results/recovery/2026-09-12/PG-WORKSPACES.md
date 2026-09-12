# PostgreSQL workspace isolation and installed restore workflow

Runtime source: `b7521bc`. The full frozen-source collection passed **2,877 tests,
81 skipped** in **425.70 seconds**, with native exit 0. It includes live
PostgreSQL, research and examples. Package code has not changed in the subsequent
benchmark, test-fixture or evidence commits. The Python 3.13 installed wheel was
byte-checked against all **124 package Python files** at that source.

The [workspace guide](../../../../docs/WORKSPACES.md) describes the public
`MemoryWorkspace.open_postgres()` API and its limits. One bounded pool serves
identity-checked project schemas through the same engine cache/lease lifecycle
as local workspaces. Every checkout verifies identity, clears temporary tables
and excludes public-table fallback. pgvector symbols are qualified to their
installed schema. Registry and initial schema creation publish atomically;
missing initialized relations or schemas fail before normal migration.

## Contract evidence

- The initial native pool/search/schema selection passed 19 checks.
- The expanded local/PostgreSQL workspace, derivation and profile selection
  passed 142 tests with 13 skips (an intermediate worktree).
- Final namespace/pool/filtered-search contracts passed 34 tests before freezing
  `b7521bc`. Changed-module typing and the strict public API consumer passed.
- The complete frozen-source suite passed 2,877 tests with 81 skips.
- The final installed selection passed **143 tests, 13 skipped** in **44.18
  seconds**, native exit 0, after the test-only subprocess fix at `c7d8a1a`.

Coverage includes same-owner contradictory projects, controlled structured
extraction, separate entity identities, organizer merges, receipts, foreign-ID
read/mutation rejection, pending raw/extraction work, eviction/reopen, concurrent
instances, one-connection pools, startup rollback, cancellation, abrupt process
exit and a relocated pgvector extension in a schema containing quotes and spaces.
Public workspace operations preserve the existing owner/scope rules inside each
project. These are authored contracts, not empirical memory-answer accuracy.

## Installed real-embedding workflow

The [registered protocol](PG-WORKSPACE-PROTOCOL.md) and runner `75f441f` use one
shared real BGE provider, four cached engines and a pool bounded to three
connections. Each project has two identical Aurora entity sources, their atomic
merge, and a unique pending raw retention fact. Concurrent retrieval checks
verify own sources and reject the adjacent project's fact, events and entity ID.
Then native `pg_dump`/`pg_restore` copies the closed workspace into a fresh
database, and all project checks run again.

| Projects | Result | Elapsed | Peak sampled client RSS | Threads | File descriptors | Observed pool maximum |
|---|---|---:|---:|---:|---:|---:|
| 2 | Complete, native exit 0 | 1.89 s | 312.27 MiB | 47 | 12 | 3 |
| 100 | Complete, native exit 0 | 41.05 s | 315.16 MiB | 47 | 12 | 3 |

PostgreSQL was 16.15 (Debian), asyncpg 0.31.0, FastEmbed 0.8.0 and Python 3.13.3.
The 100-project authored dump is 4,439,430 bytes; both dumps and hashes are retained
with the JSON outputs. Measurements sample the client after stages, exclude
server and native utility memory, and can miss transient peaks. Other regression
work ran concurrently. Do not compare these figures directly with local-pack RSS
or treat them as production throughput, total resource cost or a scalability limit.

Post-run review found a missing positive assertion for the merged entity's own
identity/evidence after restore in the shared public check helper. The protocol
amendment records this gap rather than silently weakening the guard. A separate
verifier at `97cac3b` restores each saved dump without engine initialization and
reads it in a read-only transaction. It verifies every registry/schema UUID,
exact active entity ID, both evidence references, embedding metadata, single
supersedence edge/retired target, and original event content/hash/owner/scope.
Both verifications completed with native exit 0: **all 102 project identities and
merged-evidence records passed** across the two retained dumps.
This supplements the original artifacts and does not change their measurements.

## Failures retained

- First workspace selection: one failed test and 46 passes. A test referenced a
  misnamed helper; corrected before the expanded selection.
- First typing pass: one optional connection-string narrowing error; fixed with
  an explicit assertion before opening the PostgreSQL pool.
- First installed selection: six failures, 137 passes and 13 skips. The new
  process-exit fixture and five existing profile recovery cases imported a test
  helper unavailable to standalone subprocesses outside the checkout. `c7d8a1a`
  embeds the controlled provider's source in those test subprocesses. It changes
  no package code. A focused reproduction retained the original import failure.
- Corrected installed selection: 143 tests passed, with 13 skips,
  but one teardown failed while establishing a new native PostgreSQL connection
  (60-second timeout before any teardown SQL). The isolated catalog rollback test
  subsequently passed in 0.35 seconds. The exact orphaned authored `retry success`
  fixture was validated and cleaned up; no unrelated schemas were removed. The
  server showed no restart/OOM event. The timeout's cause is unproven; concurrent
  diagnostic load is context, not an established diagnosis. The final rerun was
  started after the full source suite and original workflow had finished.

The [machine-readable record](pg-workspaces-b7521bc.json) retains source/wheel
identity, native exits, log hashes, artifact hashes and the failed runs.

## Boundaries and next decisions

This is an opt-in storage/workspace API for trusted Python applications. It does
not provide HTTP/MCP project grants, database RLS, hierarchy, shared-table hosted
scale comparisons, namespace rename/delete/import, or complete RFC-0004
conformance. Native PostgreSQL backup/restore is distinct from portable local
packs. Privileged SQL and application code remain privileged.

The latest explicit root `.env` provider check at 23:39:15 UTC still returned
HTTP 429 for `gpt-4o-mini`. No key, endpoint or provider error body is retained.
Cloud judged comparisons remain unavailable; local reader studies and comparative
retrieval work can continue. These workspace results do not establish overall
leadership or close the remaining quality, grant and historical replay gaps.
