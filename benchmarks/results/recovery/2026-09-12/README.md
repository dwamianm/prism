# Recovery and developer workflow evidence

These checks cover failure recovery and public package workflows. They do not
measure answer accuracy, full graph replay, or superiority over another memory
product. The latest frozen full suite at `aaa7ee1` passed **1,698 tests, 27 skipped**
with live PostgreSQL (128.41 seconds). A Python 3.13 wheel at `aaa7ee1` passed
installed sync-client, default local embedding, restart, source/provenance,
selection/budget, HTTP identity/filter and MCP HTTP workflow checks. The older
`7a1e864` wheel additionally ran real local-model extraction, recorded below.

## Availability and identity fault checks

`tests/test_index_availability.py` reproduced local full-text indexing being
skipped when embeddings failed. Independent index attempts now preserve the
healthy path, including durable flushing for deferred raw sources. A failed
backend keeps the job pending; restart/retry converges on one source node.
Both backends, both-failing cases, owner isolation and local flush failures are
covered. Direct `store()` still logs failures without scheduling a repair job.

`tests/test_entity_resolution.py` reproduced duplication beyond the newest
100 entities on both backends. Exact scoped entity matching now walks stable-ID
pages and reuses older matches. Reuse no longer changes a vector from an
uncommitted description. This remains conservative matching, not an atomic
cross-worker entity uniqueness guarantee or a semantic entity-resolution score.

`tests/test_retrieval_backend_status.py` verifies empty searches, model/version
changes, legacy PostgreSQL metadata, ordinary backend outages, restart and
HTTP/MCP diagnostics. Incompatible vector hits fall back to other paths; an
empty successful search is no longer mislabeled as a mismatch. Status uses fixed
reason codes without provider messages. The combined index/recovery/status
subset passed 64 tests with 3 skips, and status/MCP checks passed 40 with 1 skip.

`tests/test_retrieval_cancellation.py` reproduced connection-lock release while
a cancelled retrieval's logging thread was still writing. It now waits for that
write before releasing the lock. The focused cancellation suite at `66a0a1b`
passed 4 checks with 1 PostgreSQL-specific skip. Cancelling does not undo an
already-started write. After these changes, 47 vector/rebuild checks also passed
under the minimum USearch 2.16.0 / SimSIMD 5.9.11 environment described below.

`tests/test_materialization_cancellation.py` then reproduced orphaned partial
derivations when cancellation arrived after a database write or during indexing.
At `0443ba8`, queued node/edge writes finish and record their IDs before cancellation
propagates; cleanup waits for pending index writes and tolerates repeated
cancellation. A final committed replacement is retained. The combined targeted
suite passed 44 checks with 1 skip on Python 3.11; an installed Python 3.13 wheel
passed 31 cancellation/backend-status checks with 2 skips, including live
PostgreSQL. Intermediate graph visibility and process-crash recovery still require
the planned atomic derivation protocol.

## Prepared graph commit component

At `aaa7ee1`, the internal plan journal and `commit_derivation()` primitive passed
63 focused checks with 6 skips, including live PostgreSQL. An installed Python
3.13 wheel passed 43 graph/journal/startup checks with 6 skips. These tests cover
atomic rollback after every node/edge position, unchanged views for independent
readers, concurrent retry returning one receipt, archived results staying retired,
changed dependencies, invalid source/scope/vector inputs, timezone changes and
signed-zero preservation through PostgreSQL's operation log.

Real DuckDB child processes exited during node insertion, after a replacement
write and after commit before acknowledgement. Reopening exposed zero partial
nodes before commit, or the complete fixed-ID graph after commit. Replaying the
saved plan reused its inputs and receipt. These are component-level tests:
ordinary `ingest()` is not yet routed through the primitive, and persistent
extraction scheduling, staging-aware compaction and lease/revision fencing remain
unimplemented.

The crash tests initially failed because startup altered dependency nodes.
`3be24f3` repaired that separate bug: existing explicit epistemic assignments,
metadata and timestamps survive reopening; heuristic migration applies only to
legacy NULL epistemic values. An explicitly hypothetical fact no longer becomes
asserted merely because the pack was opened again. Earlier overwritten values
are not automatically recoverable from a migration marker.

## Grounded extraction journal

`extraction-fault-7a1e864.json` records a real Ollama workflow: grounded output
was saved, an injected index failure triggered a retry, and that retry used the
same output without another `provider.extract` invocation. Restart read the
identical saved record. `extraction-installed-7a1e864.json` verifies the public
sync ingestion/inspection API from a fresh Python 3.13 wheel installation.

The final reusable diagnostic produced `extraction-repro-c828607.json`. Ollama
was version **0.34.0**, using **qwen3.5:4b**, digest
`2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`.
Sources were synthetic Aster/Cedar service statements; no user data was sent.
Provider-call counts refer to the extraction interface, not independent HTTP
request or billing telemetry. The model produced different fact counts across
trials; these are workflow checks, not a semantic correctness score.

One prototype failed: it allowed only one pipeline retry, which a provider/schema
failure consumed before the injected storage failure. That incomplete run is
retained in `extraction-prototype-failed.json`. The final harness uses the normal
three-retry count with zero delays and checks that provider calls do not increase
**after the index fault**, allowing legitimate earlier provider failures. A missing
model also produced a nonzero exit and a failed JSON report, verifying that the
diagnostic does not silently claim success on incomplete work.

Reproduce with an available local Ollama model:

```bash
python -m benchmarks.diagnostics.extraction_recovery --output extraction-recovery.json
```

The HTTP-compatible Ollama endpoint defaults to `http://127.0.0.1:11434/v1`.
Core fault-injection, ownership, concurrency, abrupt-exit and sync/API checks are
in `tests/test_extraction_journal.py`; the PostgreSQL cases also run when
`PRME_TEST_DATABASE_URL` is set. The journal is separate from raw NOTE processing
status. Atomic graph derivations and persistent extraction work remain pending
under [RFC-0016](../../../../docs/RFC-0016-Durable-Derivation-Commits.md).

## Vector startup measurements

The diagnostic seeds random float32 vectors (384 dimensions, NumPy seed 42),
then measures `VectorIndex` construction with an already-open DuckDB connection.
Files are warm, inference/ingestion are excluded, and other work was running.
These are component diagnostics, not cold-start times or service latency bounds.

| Vectors | Control `2ea4400`, median intact reopen | Optimized `c828607`, median intact reopen |
|---|---:|---:|
| 1,000 | 7.1 ms | 2.1 ms; repeat 4.1 ms |
| 10,000 | 321.5 ms | 7.4 ms; repeat 9.7 ms |
| 100,000 | Not measured | 59.9 ms |

The change exports native keys in one call, scans metadata once, and reads
numerical payloads only for missing vectors. Control and optimized reports record
implementation hashes and matching DuckDB 1.4.4, USearch 2.23.0 and NumPy 2.4.2.
The earlier optimized run overlapped the 100,000-vector setup; the repeat overlapped
a local extraction probe. Both also overlapped the original-version held-out
benchmark. Treat the individual timings as observations, not an isolated speedup
estimate. All recorded runs are retained.

Rebuilding a wholly missing 10,000-vector snapshot took 5.0 seconds for the control
and 5.8/7.7 seconds in optimized trials. No recovery-speed improvement is claimed;
the improvement targets normal reopen overhead. The 100,000-vector trial measured
intact reopening only. Source embeddings were reused without model calls.

```bash
python -m benchmarks.diagnostics.vector_startup --output vector-startup.json
python -m benchmarks.diagnostics.vector_startup --counts 100000 --skip-recovery --output vector-100k.json
```

The vector recovery/index/rebuild subset passed **61 checks** both on USearch
2.23.0 and on the declared minimum 2.16.0 with its required SimSIMD 5.9.11.
The latter environment reused the other core dependencies. Its scalar key
iteration and slicing behavior differs, so the implementation uses NumPy's bulk
array protocol. Startup/encryption fault checks also passed after the recovery
change. Numerical recovery does not reconstruct graph mutations that were never
journaled, and it does not guarantee power-loss durability of the filesystem.
