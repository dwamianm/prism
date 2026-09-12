# Recovery and developer workflow evidence

These checks cover failure recovery and public package workflows. They do not
measure answer accuracy, full graph replay, or superiority over another memory
product. The frozen full suite at `7c53645` passed **1,932 tests with 51 skips**
on Python 3.11 and live PostgreSQL (247.26 seconds), including research and
example integration tests. Installed Python 3.13 and real-model workflows,
including retained failures, are described below.

## Availability and identity fault checks

`tests/test_index_availability.py` reproduced local full-text indexing being
skipped when embeddings failed. Independent index attempts now preserve the
healthy path, including durable flushing for deferred raw sources. A failed
backend keeps the job pending; restart/retry converges on one source node.
Both backends, both-failing cases, owner isolation and local flush failures are
covered. Direct `store()` now also queues repair work; see the direct-store
recovery evidence below.

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
PostgreSQL. At that revision, intermediate graph visibility and process-crash
recovery still required the atomic derivation protocol implemented later below.

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
remain eligible for eviction. At `ef046f9`, abandoned staging had no automatic
collection policy; the later retired-revision policy is described below. These
early checks exercised components, not the complete ingestion path.
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
Replanning is explicit. Retired-stage cleanup was a separate gap at that revision
and is covered by the later evidence below; complete operation-log replay remains
unfinished. These synthetic workflows establish neither semantic
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


## Extraction failure categories

At `5ff7234`, blocking ingestion exposes `ExtractionError.reason_code` alongside
the persisted source `event_id`; the exception is also importable from `prme`.
Durable status retains the same failure category across restart. A reproduced
provider timeout previously became `CancelledError` because the failure handler
followed asyncio's exception chain to the cancelled inner call. Both backend
regressions now retain `TimeoutError`; actual caller cancellation retains its
separate `Cancelled` work code.

Provider authentication/rate-limit and structured-validation categories take
precedence over lower transport causes. Unknown categories retain bounded class
names. Traversal handles cyclic or very long exception chains, and invalid class
names cannot break the work store's bounded-code validation. No provider message,
response body or credential is persisted in the failure code.

The frozen full suite passed **1,868 tests with 42 skips** on Python 3.11
and live PostgreSQL (164.19 seconds). The source recovery/interface/provider
subset passed **38 checks with 1 skip**
using live PostgreSQL. An installed Python 3.13 wheel passed **52 checks with
1 skip**, including the review experiment's guards (6.12 seconds). Tests inject
real OpenAI SDK exception types and a stalled async provider through public
ingestion; they do not contact the remote provider. Retry limits and scheduling
remain unchanged. Previously saved failure codes are not reclassified. These are
recovery/DX checks, not memory-accuracy evidence.


## Direct typed stores and synchronous API controls

At `d9c6392`, `store()` atomically saves its event, complete initial typed node
and a materialization job. A graph creation failure exposes the accepted source
ID through `MaterializationError`; an index outage leaves a durable pending job.
Public scoped processing repairs the request after restart without LLM extraction.
Existing IDs, classification, confidence, timestamps and retention settings are
preserved. Existing retired nodes remain retired. Tantivy replacement commits
its delete/add together; vector replacement publishes a new durable generation
before removing the old one, so a failed retry retains healthy search results.

Validation at this storage commit:

- **116 passed, 8 skipped** in the cross-backend recovery/index subset (23.07s).
- **1,901 passed, 45 skipped** in the full Python 3.11 suite with live PostgreSQL
  (240.59s). The implementation was pinned to the detached `d9c6392` worktree;
  tests ran from the unchanged repository at the same commit.
- **91 passed, 8 skipped** in the Python 3.13 installed-wheel recovery subset
  (22.51s). Abrupt-exit subprocess tests deliberately import the corresponding
  frozen checkout's source; the parent tests exercise the installed package.

Real process exits cover source/request commit, graph creation, lexical commit,
vector save and completion acknowledgement on both backends. Native Tantivy
add/commit faults, native vector insertion failure, alternating index outages,
concurrent vector replacement, a corrupted request checksum, tenant boundaries
and rollback after operation-ID collision are also covered. PostgreSQL lexical
publication is already durable with its graph row; its exit hook is the lexical
index call, rather than a nonexistent Tantivy-style flush.

`eadb6e1` then adds `epistemic_type`, `source_type` and `ttl_days` to
`MemoryClient.store()`, with the async engine's existing semantics. It exports the
two classification enums from `prme`. The client suite passed **25 checks**,
including restart recovery for configured TTL, explicit TTL and no expiry.
The final installed Python 3.13 wheel passed **58 checks with 3 skips** (9.30s),
covering the client and direct-store recovery suites with live PostgreSQL.
The full suite was not rerun for these additive forwarding/export changes.

[The supervised installed-wheel workflow](direct-store-eadb6e1.json) uses real
BAAI/bge-small-en-v1.5 embeddings through FastEmbed. Two synthetic requests survive
an indexing outage and a graph creation outage; scoped synchronous processing
repairs both after reopening the pack and retrieval finds the instruction.
Explicit classification and TTL survive recovery. LLM extraction is replaced
with a function that fails if called. The supervisor records success only after
normal child exit, including native-library shutdown. Reproduce with:

```sh
python -m benchmarks.diagnostics.direct_store_recovery --output /tmp/direct-store.json
```

These checks establish specific recovery and API behavior, not extraction
accuracy, comparative leadership, latency or filesystem power-loss guarantees.
Direct-store completion excludes optional reinforcement, supersedence and QA
pairing; it is not a fenced multi-worker derivation commit. Historical direct
stores without these records are not retroactively queued, and full organizer
and manual-operation replay remains unfinished. Index durability adds synchronous
commit work; this run does not isolate its latency cost.


## Plan identity ownership and the staging-fence regression

At `78ecb69`, a regression on each backend showed that a newly queued revision
could journal the previous revision's node IDs. Both tests failed because the
expected rejection did not occur. That behavior makes deletion of an older
revision's staged entries unsafe if another plan has reused them.

`71a7fd7` reserves each new node ID for the saved plan's exact prepared-operation
identity, atomically with its journal and work binding. Conflicting sources,
tenants and revisions cannot reserve the same identity. Reusing the old plan UUID
in a new revision does not bypass this check. Concurrent candidates for the same
source/revision still converge on the first journaled plan.

The registry is derived from immutable plans, with incremental startup backfill
in pages of 128 records. Legacy overlapping allocations receive an ambiguous
owner and remain readable; no journal is rewritten or winner guessed. Invalid
legacy plans remain unregistered, with raw-source reads still available. A future
collector must retain ambiguous ownership and refuse reclamation while journal
registration is incomplete. Index deletion is not enabled by this change.

Validation:

- **122 passed, 17 skipped** in the cross-backend derivation/recovery subset
  (29.13s), including identity collisions, concurrent distinct-source admission,
  legacy overlap, corrupt legacy records, transactional rollback and existing
  abrupt-exit recovery tests.
- **1,916 passed, 45 skipped** in the full suite from the frozen `71a7fd7` checkout
  on Python 3.11 and live PostgreSQL (230.34s).
- **68 passed, 9 skipped** using the installed Python 3.13 wheel (17.69s).
  Parent tests import the installed package; existing abrupt-exit subprocess
  tests explicitly load the matching checkout's source.

[A follow-up isolation probe](obsolete-staging-71a7fd7.json) exposed the next
necessary change: a worker resuming an obsolete revision still wrote four vector
staging records and lexical entries before its graph commit was rejected.
No graph nodes were published. The strict expected-failure version of
`tests/test_obsolete_derivation_staging.py` recorded that precise gap; unrelated
exception types are not accepted as the expected failure. Its separate run has
**1 expected failure and 1 PostgreSQL skip**. It was added after the full-suite
run above and is not evidence that stage isolation passes.

At `71a7fd7`, collection therefore remained pending: external index staging had
to respect the work revision/lease fence, including workers paused across a revision change,
and cleanup must retain live, committed, ambiguous or unregistered identities.
These are integrity and recoverability checks, not comparative memory-accuracy
or performance evidence.


## Fenced native staging and retired-revision cleanup

`7c53645` closes the obsolete-worker staging regression. Managed DuckDB staging
holds a separate cursor transaction on the work row throughout each native
write. The exact saved plan, staged inputs and claim are checked before writing;
the lease is checked again afterward. A concurrent connection cannot advance
its generation or plan revision while the native operation remains in flight.
Cancellation keeps the relevant locks held until that operation finishes.
An operation that expires in flight can leave durable plan-owned inputs, but
ownership cannot transfer until its native work is finished. PostgreSQL already
publishes prepared index columns inside its fenced graph transaction.

The local `index_compaction` job now reclaims up to 500 uniquely reserved,
graph-invisible node identities from explicitly replaced revisions per pass.
It verifies source/plan journals and respects the original event owner. Current
failed, pending and running plans remain recoverable; ambiguous legacy ownership
is retained, and incomplete or invalid registration blocks stage reclamation
with a reason in job details. Sources, plans and identity reservations survive.
Unmanaged component staging and general operation-log replay remain outside this
collection protocol.

Deletion commits lexical changes before removing vector metadata. This preserves
a durable retry anchor across lexical failures, native vector failures and an
abrupt process exit between deletions. Lifecycle eviction now uses the same order;
a failed lexical delete no longer becomes undiscoverable after vector metadata
has already been removed. Native removal failures propagate, and maintenance
reports errors rather than counting them as successful reclamation.

Validation:

- **209 passed, 21 skipped** in the targeted cross-backend suites (39.65s).
- **1,932 passed, 51 skipped** in the frozen full Python 3.11 suite with live
  PostgreSQL (247.26s). The previous expected failure is now a passing regression.
- **78 passed, 15 skipped** in the installed Python 3.13 wheel subset (27.06s).
  Existing subprocess tests load the corresponding frozen checkout's source;
  their parent tests import the installed wheel.

New tests hold vector and lexical operations across lease expiry, reject
concurrent takeover/replanning, exercise repeated cancellation, recover with a
successor claim, reject mismatched staged inputs, preserve tenant boundaries and
current plans, and retry failed or interrupted reclamation. Existing native-write
and public-ingestion crash tests continue to pass.

[The supervised real-model workflow](stage-cleanup-7c53645.json) used the installed
wheel, `qwen3.5:4b` through Ollama and real BAAI/bge-small-en-v1.5 embeddings. It
recovered a stale dependency through revision 2, verified foreign maintenance left
Alice's staging alone, reclaimed two old node identities through Alice's public
`organize()` call, preserved current indexes and immutable journals, and retrieved
the database fact. Extraction was forbidden during recovery. The process exited
normally, including native-library shutdown; the report records model digest,
implementation hashes and dependencies.

```sh
python -m benchmarks.diagnostics.derivation_replanning --output /tmp/replan-cleanup.json
```

This is one synthetic recovery/cleanup workflow, not extraction-accuracy,
competitive superiority, throughput or filesystem power-loss evidence. Native
stage fencing adds transaction work; these checks do not isolate its latency cost.

## Direct-store snapshot write reduction

`597f041` removes an unconditional whole-vector-index save after every direct
store and deferred raw-source materialization. Vector payloads and metadata
already commit together before the index operation returns. Startup restores
unsaved keys from these durable payloads, without an embedding call. The existing
configured snapshot interval and engine-close flush now govern snapshot writes.
Lexical commits and failed-work accounting are retained.

The [before](snapshot-before-afc1539.json) (`afc1539`) and
[after](snapshot-after-597f041.json) (`597f041`) diagnostics each stored and
acknowledged 256 synthetic records with deterministic 384-dimensional embeddings,
a snapshot interval of 64, and isolated packs. Both processes exited zero and
verified the node/payload counts after reopening. Harness source is `597f041`;
the before run loaded the frozen earlier package. Reports retain implementation
hashes, dependency versions and individual snapshot sizes.

| Measurement during stores | Before | After |
|---|---:|---:|
| Complete vector snapshot writes | 256 | 4 |
| Serialized snapshot bytes | 55,342,504 | 1,076,804 |
| Snapshot component time | 6,753 ms | 91 ms |
| Total time for 256 stores | 59,249 ms | 61,418 ms |
| Additional snapshot writes on close | 1 | 1 |

Serialized snapshot output decreased by **98.05%**. This measures serialized
file sizes, not physical disk traffic, write amplification inside the filesystem
or fsync behavior. It is not a throughput improvement claim: total store time
was higher in the candidate run, and concurrent development evaluation and test
workloads differed. Fixed embeddings isolate persistence work; they do not
measure embedding-service or end-to-end ingestion performance. Periodic full
snapshots still have size-dependent costs.

Three new checks failed before the change: configured cadence was overridden,
and public stores could not reach the intended unsaved-snapshot crash boundary.
Afterward, public-store subprocesses exited abruptly with no snapshot or an older
partial snapshot. Reopening with an embedding provider that raises on every call
restored all acknowledged vectors and retained owner isolation, complete work
status and lexical access. Existing crash injection now targets the durable
vector index operation rather than the removed per-event `save()` call. A forced
scheduled-snapshot failure still leaves processing retryable without skipping the
healthy lexical backend.

On `597f041`, the focused source checks passed 72 with 6 skipped; availability
checks passed 12 with 2 skipped; the four cadence/diagnostic checks also passed.
The installed Python 3.13 wheel passed **85 checks, 8 skipped** in 41.32 seconds,
process exit zero. The [real-embedding synchronous-client workflow](snapshot-live-recovery-597f041.json)
also completed and exited zero, repairing both accepted requests and retrieving
the original instruction after reopening. Its package path confirms an installed
wheel; it is a two-workflow recovery diagnostic, not a quality benchmark.

## HTTP object identity validation

`ededc6d` validates all event/node path identifiers as UUIDs before calling storage,
matching the newer extraction endpoints. Malformed IDs now produce HTTP 422;
valid but unknown or foreign IDs remain 404. Canonical string UUIDs are forwarded
to the engine. The OpenAPI error model accepts both application error strings
and FastAPI's structured validation-detail arrays, so declared error schemas
match those responses.

The new boundary tests reproduced ten failures before the change; three existing
UUID extraction routes already passed. All thirteen checks pass afterward,
including no storage calls for malformed IDs and canonicalization of uppercase
UUIDs. The source API/identity/provenance suite passed 69 tests. The final
installed Python 3.13 wheel (`ededc6d`) passed **73 tests** in 27.19 seconds,
covering the API suite and the four snapshot-cadence/recovery checks. It emitted
one Starlette/AnyIO deprecation warning about the `BlockingPortal` alias; the
process exited zero. These tests do not establish benchmark leadership.

## HTTP source fidelity and empty-source admission

At `e3f3c8d`, HTTP store preserves source classification, session, metadata,
confidence, event time and TTL (omitted, explicit null and integer overrides).
Read and retrieval responses retain temporal and provenance fields. Ingest
preserves session and metadata and supports blocking extraction. Unsupported
write fields are rejected before admission. Saved-work failures return a scoped
event receipt, and raw materialization status and repair are accessible over HTTP.
Restart checks recover the original saved request without creating another event
or calling an extraction provider. See [the HTTP contract](../../../../docs/HTTP-API.md).

The frozen full suite at `e3f3c8d` passed **2,042 tests with 51 skips** in
292.65 seconds, using Python 3.11 and live PostgreSQL. The earlier `597f041`
full run is retained as **2,005 passes, 51 skips, one failure**: a maintenance
scope test depended on completing work within a real 200ms deadline under load.
`018618e` gives that scope test a fixed clock; the separate advancing-clock test
continues to exercise budget exhaustion. Production time budgets were unchanged.

`376178a` then corrected empty-source hashing on admission. The targeted
source, recovery and provenance suite passed **45 tests with three skips** on
both backends. An installed Python 3.13 wheel from `1f5375a` passed **104 HTTP,
source, identity, empty-hash and maintenance checks** with live PostgreSQL in
14.68 seconds. It emitted one upstream Starlette/AnyIO deprecation warning.
The full-suite result above predates the one-line empty-source fix; these
installed and focused checks cover it. No historical event rows were rewritten.

## Historical ingestion clock

`f810c31` adds timezone-aware source clocks to LLM ingestion through the engine,
sync client, HTTP and MCP, plus per-message clocks in Python batches. Relative
dates use that source clock; ingestion timestamps retain admission time. Failed
extraction and raw indexing recover the original source clock after restart.
Older-effective imported updates do not retire later facts. Existing journaled
plans keep their saved dates on retry. Batch admission remains sequential and
can partially complete; this does not infer missing timezones or resolve every
calendar ambiguity.

The initial regression tests failed 14 cases against the previous implementation.
After the change, **70 historical, replacement, HTTP and MCP integration checks**
passed on Python 3.11 with live PostgreSQL in 29.55 seconds. The installed
Python 3.13 wheel passed **89 checks with seven backend-specific skips**, including
derivation failure/restart tests, in 50.31 seconds. The full frozen suite is
recorded separately when complete; these are focused results.

The [real-model installed-wheel workflow](historical-ingestion-f810c31.json)
uses Ollama Qwen3.5:4b and BGE-small on one authored source. “Yesterday” relative
to 2024-03-10 01:30 -06:00 became 2024-03-09 07:30 UTC. After an injected failure
following vector staging, the graph had no partial derivation. Reopening and
explicit processing published the saved three-node plan with identical dates
and both model providers disabled. The supervised process exited zero; workflow
time was 18.98 seconds. This demonstrates one temporal/recovery workflow, not
extraction accuracy or a competitive advantage.

## Scoped instruction repetition

`2fea945` stops treating similarity as automatic confirmation of an instruction.
The regression setup forces a similarity score of 1.0 and verifies that a negated
rule, another scope, an ordinary note, a conditional statement, a model-derived
source or an assistant echo cannot change the existing instruction's evidence
or confidence. The initial tests reproduced **14 failures with two passes**
on the previous implementation, across DuckDB and PostgreSQL.

Automatic reinforcement now requires exact text, explicit instruction types,
user-stated observed/asserted records, a user/human source, the same owner and
scope, and a source no earlier than the existing instruction. It checks active
state and existing evidence before crediting a source. Opt-in semantic re-mention
reinforcement excludes instructions and respects scope. An explicit repetition
is credited once even with both paths enabled. This remains a repetition
heuristic, not independent corroboration or empirically calibrated confidence.
Previously applied boosts are retained.

The source integration run passed **81 checks with two skips** in 49.59 seconds.
The final installed Python 3.13 wheel passed **83 checks with two skips** in
38.50 seconds, including the additional positive tests with opt-in re-mention
enabled and live PostgreSQL. These checks include index availability and snapshot
recovery. Changed-source lint passed. Ordinary notes now make no instruction
similarity search, verified by a call-count test; no throughput improvement is
claimed from these concurrent test timings. The ongoing full suite at `f810c31`
predates this reinforcement fix and must not be represented as covering it.

## Durable relevance collection and client lifecycle

`1461fb0` adds owner-scoped retrieval snapshots and explicit relevance records
through Python, HTTP and MCP. Receipts preserve returned candidate identities,
content hashes, score traces, configuration, clock and context membership.
Relevance labels bind to that saved response; they do not use later graph state
or treat unlabelled candidates as negative. The existing query operation holds
the receipt, and retrieval reports a failed receipt log without failing search.
A caller-selected feedback UUID is an idempotent retry identity within an owner.

Independent DuckDB connections reproduced both commit-time transaction conflicts
and statement-time unique-constraint conflicts for the same feedback identity.
Bounded retries of that same autocommit insert converge on the original record;
conflicting judgment content still fails. The focused suite passed **24 tests
with one skip** on both backends. A real local child process exits after the
feedback insert commits and before acknowledgement; reopening finds one record
and retry preserves it. Owner boundaries, legacy logs, checksum corruption,
context pointer exclusion, pagination and real HTTP/MCP transports are covered.

The installed Python 3.13 wheel at `1461fb0` passed **97 tests with three skips**
in 36.12 seconds, including live PostgreSQL, cancellation and interface checks.
It emitted one upstream Starlette/AnyIO deprecation warning. The initial
[real-embedding sync workflow](relevance-receipts-1461fb0.json) failed with
`AttributeError` because `MemoryClient.archive` was absent. That failure is retained.

`c7f62a6` adds public `promote` and `archive` client methods using the engine's
owner checks. Its installed wheel passed **24 checks with one skip** in 11.93
seconds, including foreign-owner rejection, retirement from retrieval, source
preservation and receipt reads after restart. Strict public-consumer typing
and changed-source lint passed. The [repeated real-embedding workflow](relevance-receipts-c7f62a6.json)
then passed with an actual zero process exit. Its one-candidate receipt was
1,699 serialized bytes; this tiny example is not a storage/latency benchmark.

Collection leaves weights and graph beliefs unchanged. The legacy global tuner
does not consume these records. Evaluated fitting, per-owner/scope profiles,
activation gates and rollback remain pending under RFC-0017; these results do
not demonstrate learned retrieval quality.

The earlier frozen full suite at `f810c31` finished with **2,071 tests passed,
51 skipped**, actual exit zero, in 804.91 seconds under concurrent benchmark
load. It covers historical ingestion, not the later reinforcement/receipt changes.
A full run at `1461fb0` is tracked separately and remains in progress.
