# Recovery and developer workflow evidence

These checks cover failure recovery and public package workflows. They do not
measure answer accuracy, full graph replay, or superiority over another memory
product. At `b8bac4b`, the frozen checkout passed **1,733 package tests with
42 skips** and live PostgreSQL (164.85 seconds), plus **101 research tests**
(1.06 seconds) and **14 example integration tests** (3.09 seconds), run separately.
That is **1,848 passing checks** across the repository. Installed Python 3.13 and
real-model workflows, including retained failures, are described below.

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
saved plan reused its inputs and receipt. These were component-level tests at
`aaa7ee1`; the subsequent public-ingestion integration is described below.

The crash tests initially failed because startup altered dependency nodes.
`3be24f3` repaired that separate bug: existing explicit epistemic assignments,
metadata and timestamps survive reopening; heuristic migration applies only to
legacy NULL epistemic values. An explicitly hypothetical fact no longer becomes
asserted merely because the pack was opened again. Earlier overwritten values
are not automatically recoverable from a migration marker.

## Idempotent index staging

At `ef046f9`, the staging component passes 25 focused checks, including real process exits
after a durable vector payload, after native vector insertion, and after a
lexical commit. Retries reuse saved numerical inputs and identities without
calling a model. Failure after a lexical commit preserves the committed batch;
failure before it preserves unrelated documents and rolls back only new adds.
Repeated cancellation retains storage locks until the native work finishes.

The vector-payload exit test exposed a sequence recovery bug: the persisted key
survived while the next sequence allocation reused it. Startup now rebases the
sequence beyond recovered keys. Sixty staging/vector recovery/retrieval checks
also pass under USearch 2.16.0 / SimSIMD 5.9.11. Ordinary orphan compaction still
works; unpublished staging claims are retained, and published archived results
remain eligible for eviction. Abandoned staging has no automatic collection
policy yet. At `ef046f9` these checks exercised components, not the complete ingestion path.
The Python 3.13 wheel passed 59 staging/graph component checks with 6 skips,
including live PostgreSQL. Its public sync-client, default embedding, restart,
source/provenance, selection/budget, authenticated HTTP and MCP HTTP workflow
also passed. Strict public-consumer typing and changed-file lint checks passed.

## Journaled plans in normal ingestion

Normal ingestion now plans in a scoped memory overlay, computes embeddings in
one batch, journals the complete plan and publishes it through the tested commit
path. Entity reuse leaves existing index content unchanged. Only referenced
existing nodes become dependencies, and older-effective assertions cannot retire
later state. A saved plan skips inference; a completion receipt skips staging and
graph writes, including after archival. Unjournaled legacy derived nodes require
explicit migration instead of being silently duplicated.

The planning, public-ingestion fault and index-staging subset passes 62 checks
with 7 skips, including live PostgreSQL. Tests inject failures after plan/index
writes, during graph writes and after commit; concurrent attempts converge on
one plan. DuckDB child processes exit inside public ingestion after plan save,
vector staging, lexical staging, node insertion and commit. Explicit retry after
reopening preserves artifact identities and calls neither provider. Cancellation
tests now target the atomic transaction boundary instead of the removed
interleaved graph writer.

At that revision, pending extraction discovery, persistent attempts, lease
generations and plan revisions remained unimplemented. The durable-work evidence
below covers the subsequent scheduling and fencing implementation.

The installed Python 3.13 wheel at `4474a7e` passed 52 planning, public-ingestion
and replacement checks with 8 skips and live PostgreSQL. The planner retains a
bounded entity scan page rather than every scanned entity; a 600-entity lookup
test covers that bound on both backends. Its installed client/API workflow also
passed with real default local embeddings.

[`derivation-ingestion-4474a7e.json`](derivation-ingestion-4474a7e.json) records
real Ollama `qwen3.5:4b` extraction and BGE-small embeddings through the installed
wheel. One extraction-provider call and one embedding batch prepared six nodes
from a synthetic source, including two grounded facts. After an injected failure
following durable vector staging, the graph had no partial derived nodes.
Reopening and explicitly retrying reused the exact plan without either provider,
retrieval found the database fact, and archived completion skipped all staging.
This is a workflow check, not an accuracy score or scheduling claim. The earlier
[`31798c5` probe](derivation-ingestion-31798c5.json) is retained with its original
revision and timing; those elapsed times are not comparative latency measurements.

## Durable extraction work and public recovery

At `339ab60`, source acceptance queues both raw indexing and LLM extraction in
one transaction. Work status, append ordering within each owner/scope, bounded
retry attempts, leases and generations survive restart. Generation checks occur
inside extraction/plan journaling and graph publication transactions; completion
and its receipt commit together. Separate connections test takeover during an
expired publication. Failure injection after each admission insert verifies that
the source and both jobs roll back together.

DuckDB testing reproduced concurrent indexed-status updates bypassing the needed
write conflict. Mutable work fields are now unindexed, and the cross-connection
fence passes on DuckDB 1.4.4 and 1.5.5. PostgreSQL locks the row and samples lease
time after any lock wait, preventing delayed renewal from reviving expired work.

The frozen full suite at `339ab60` passed **1,800 tests with 40 skips** in
163.69 seconds, using Python 3.11 and live PostgreSQL.

The installed Python 3.13 wheel passed 31 local/interface/recovery checks with
35 skips, followed by 19 PostgreSQL checks with 3 skips and 18 deselections.
The separate PostgreSQL run covers the cases skipped for lack of a database in
the first invocation; remaining skips are backend-specific tests. Strict public
consumer typing and source lint passed.

[`extraction-work-339ab60.json`](extraction-work-339ab60.json) records the reusable
local-model diagnostic against that installed wheel. The synthetic source
produced six prepared nodes and two facts before an injected staging failure.
Public status reported failed publication. After reopening, `retry_extraction`
and `process_extractions` completed the exact saved plan with both providers
replaced by failing sentinels. Retrieval found the database fact, and a retry
after archival left completed work untouched. This harness now injects failure
at the plan staging boundary and exercises public recovery after restart.

Retrieval never invokes LLM recovery. Explicit processing discovers queued jobs;
there is no daemon. At that revision, legacy sources were not backfilled and
stale-plan revision and abandoned-stage collection remained open. These workflow
checks establish no extraction accuracy or comparative leadership claim.

## Explicit stale-plan recovery

At `d73f326`, `retry_extraction(..., replan=True)` queues a new immutable plan
revision from saved grounded extraction. Status includes `plan_revision`; HTTP,
MCP and CLI expose the same option. Replanning records the revision switch and
advances the generation atomically. It cannot preempt an active worker or redo
completed work. Existing v1 checksums remain unchanged, and old plans are still
readable through their owner's boundary.

Tests cover stale dependency rejection followed by public recovery after restart,
concurrent revision requests, superseded worker rejection, rollback after a
revision-journal failure, abrupt exit after the switch, and legacy journal/schema
compatibility. They exposed a DuckDB 1.4.4 WAL replay failure after adding a
column; fresh schemas now include the column and legacy migration checkpoints
before accepting work. The migrated-pack crash test verifies old work survives.

The frozen full suite at `d73f326` passed **1,810 tests with 42 skips** in
162.66 seconds, using Python 3.11 and live PostgreSQL.

The installed Python 3.13 wheel passed **47 checks with 5 skips**, including live
PostgreSQL, the new revision transitions, migration recovery and public transports.
Strict consumer typing and source lint passed.

[`derivation-replanning-d73f326.json`](derivation-replanning-d73f326.json) records
a real Ollama/BGE-small workflow against that wheel. After one successful
synthetic ingestion, a second ingestion reused existing memory. A dependency was
changed immediately before publication, producing `StaleDerivationPlanError`
with no partial graph. After restart, a public revision request and processing
committed revision 2 while extraction was replaced with a failing sentinel.
The original extraction and plan remained identical, the receipt matched the new
plan, and retrieval returned the database fact. New embeddings were permitted.
However, after emitting that report the process aborted (exit 134). The macOS
crash report identifies an ONNX Runtime 1.30.0 telemetry HTTP callback on a native
worker thread, ending in a recursive mutex error. The retained JSON distinguishes
passed workflow assertions from this failed end-to-end process result.

A subsequent trial exited with an assertion failure because the model emitted
inconsistent entity/subject names, leaving no dependency for the intended fault.
[`derivation-replanning-unlinked-trial.json`](derivation-replanning-unlinked-trial.json)
retains that result. The diagnostic now uses a simpler Alice/Atlas source to
exercise revision recovery; this does not resolve the observed entity-linking
quality gap or establish a model accuracy result.

The diagnostic now runs inference in a child process and publishes success only
after that process exits zero. PRME defaults `ORT_DISABLE_TELEMETRY=1` before
loading FastEmbed, preserving explicit host settings. ONNX Runtime documents that
this startup switch prevents the non-Windows uploader from being created;
calling its API after initialization may leave an initialization event active.
See the [upstream runtime documentation](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md).

The final frozen full suite at `d542266` passed **1,816 tests with 42 skips**
in 161.01 seconds, using Python 3.11 and live PostgreSQL.

The rebuilt Python 3.13 wheel at `d542266` passed **24 targeted checks with 2
skips**, including native initialization defaults, process-result handling,
revision recovery, migration and transports with live PostgreSQL. The supervised
[installed-wheel diagnostic](derivation-replanning-d542266.json) completed with
**process exit code 0** on ONNX Runtime 1.30.0. It records the embedding-provider
source hash and the active telemetry setting, as well as the pipeline hash.

Reproduce with:

```bash
python -m benchmarks.diagnostics.derivation_replanning --output replanning.json
```

This revises materialization, not the original model output or grounding policy.
Replanning is explicit. Abandoned index staging and complete operation-log replay
remain separate gaps. These synthetic workflows establish neither semantic
accuracy nor competitive superiority.

## Extraction reference integrity

At `b8c4ffc`, built-in provider responses require each fact subject and
relationship endpoint to resolve to a listed entity. Optional type qualifiers
disambiguate identical names with different types. Invalid references trigger
bounded schema retries; the materializer no longer uses a last-write-wins name
map. Custom/historical facts with missing or ambiguous subjects remain searchable
and record `subject_link_status`, without receiving a guessed graph edge.
New plans use `typed_references_v2`; saved v1 plan checksums still load unchanged.

The frozen full suite at `b8c4ffc` passed **1,830 tests with 42 skips** in
166.32 seconds, using Python 3.11 and live PostgreSQL.

The targeted source suite passed **71 checks with 9 skips**. The installed
Python 3.13 wheel passed **52 checks with 2 skips**, including live PostgreSQL.
These cover reordered namesakes, typed relationship endpoints, retained custom
facts, provider validation, supersedence and historical-plan replay.

The supervised [installed-wheel diagnostic](entity-references-b8c4ffc.json)
passed all three synthetic structural cases with process exit 0: the original
Aster service example, Jordan the person versus Jordan the country, and a
conditional Alice/Atlas statement. Each case materialized one fact with a subject
edge. The conditional fact retained its hypothetical classification.

The raw outputs also expose limitations: `part_of` was emitted for residence,
and relationships accompanying the hypothetical fact lack epistemic qualifiers.
Entity typing varied between trials. These are separate semantic failures, not
resolved by the reference checks, and no accuracy or competitive claim follows
from this diagnostic. Aliases and same-name/same-type identity resolution also
remain outside this change.

Reproduce with:

```bash
python -m benchmarks.diagnostics.entity_references --output entity-references.json
```

## Epistemic relationship claims

At `b8bac4b`, new relationship extractions become source-cited FACT nodes with
normal epistemic filtering. HAS_FACT and MENTIONS associate their subject and
object; model predicates remain metadata rather than direct semantic edge types.
Built-in providers must cite and classify relationships. Legacy/custom outputs
without classification become UNVERIFIED model proposals with the configured
SYSTEM_INFERRED confidence (default 0.20), avoiding the absent user-stated cell's
0.50 fallback. A covering fact for the same resolved endpoints and passage takes
precedence over duplicate relationship labels. The extraction journal preserves
both outputs. Qualified object references avoid arbitrary namesake links.

The focused source, reference, update, publication and recovery tests passed
**128 checks with 9 skips**; the expanded legacy-plan subset passed **12 checks
with 2 skips**. The installed Python 3.13 wheel passed **50 checks with 2 skips**,
including live PostgreSQL. Tests check default versus explicit retrieval of
hypothetical/unverified claims, preserved source conditions, no inferred causal
edges, tenant scoping and old policy checksums. Lint passed for all source/tests.

The first supervised [real-model trial](relationships-b8bac4b-failed.json)
**failed**: the service case passed; namesake extraction failed with `ExtractionError`; the conditional usage statement became a hypothetical PREFERENCE,
not the expected FACT. Source passages and association edges were preserved in
the completed cases. The worker exited 1, so the parent correctly retained a
failed result. Model classification and namesake reliability remain open.

A repeat used the same installed `b8bac4b` wheel and model settings, with
additional reporting from harness `7fbfa51`. Its namesake case completed, but the
conditional source still produced a hypothetical PREFERENCE and an additional
hypothetical relationship FACT. The old diagnostic reported success because it
only checked FACT nodes. That [original report](relationships-b8bac4b-repeat-invalid-pass.json)
is retained as **an invalid pass, not success evidence**. The diagnostic now
checks every FACT/PREFERENCE/DECISION node, requires FACT for all three fixtures,
and checks actual subject edges. A regression test verifies that an extra FACT
cannot mask an incorrectly typed or unqualified claim. The diagnostic process
subset passed **4 checks**. Neither model behavior nor the prompt changed between
these two trials; they show remaining nondeterminism and classification errors.

These checks do not establish semantic predicate accuracy. Association paths do
not imply logical entailment. Existing committed graphs and saved v1/v2 plans
retain their original artifacts; this is not a historical-edge migration.

## Grounded extraction journal

`extraction-fault-7a1e864.json` records a real Ollama workflow: grounded output
was saved, an injected index failure triggered a retry, and that retry used the
same output without another `provider.extract` invocation. Restart read the
identical saved record. `extraction-installed-7a1e864.json` verifies the public
sync ingestion/inspection API from a fresh Python 3.13 wheel installation.

The earlier reusable diagnostic produced `extraction-repro-c828607.json`. Ollama
was version **0.34.0**, using **qwen3.5:4b**, digest
`2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`.
Sources were synthetic Aster/Cedar service statements; no user data was sent.
Provider-call counts refer to the extraction interface, not independent HTTP
request or billing telemetry. The model produced different fact counts across
trials; these are workflow checks, not a semantic correctness score.

One prototype failed: it allowed only one pipeline retry, which a provider/schema
failure consumed before the injected storage failure. That incomplete run is
retained in `extraction-prototype-failed.json`. That earlier harness used the normal
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
status. Subsequent atomic publication and durable work implementations are
covered above and below under [RFC-0016](../../../../docs/RFC-0016-Durable-Derivation-Commits.md).

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
