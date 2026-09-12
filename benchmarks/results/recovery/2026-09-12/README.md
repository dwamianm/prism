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
The frozen full run at `1461fb0` subsequently completed with **2,133 tests passed,
52 skipped**, actual exit zero, in 809.09 seconds under concurrent benchmark
load. It includes instruction reinforcement and relevance collection, but
predates the sync lifecycle additions and version 2 score provenance.

## Replayable score provenance

`aa8684d` adds version 2 receipts with the weights actually used by the scorer,
ordered neural/session adjustments, and the actual sorting policy. Validation
recomputes scores and returned order. Replay does not need today's graph, clock,
classifier or model. It covers the returned candidate set, not omitted candidates
or a full counterfactual retrieval. It does not fit or activate a learned profile.

The installed Python 3.13 wheel passed **199 checks with three skips** in 28.10
seconds, including live PostgreSQL. Additional pipeline tests passed on both
backends in source and against that installed wheel: query-specific weighting,
neural blending and session expansion replay together after restart. These tests
use a controlled neural output; they do not benchmark a cross-encoder's quality.
Version 1 fixture bytes/checksums from the frozen `1461fb0` runtime remain identical,
including references from relevance records on both backends.

The [real BGE sync workflow](relevance-receipts-aa8684d.json) passed with actual
process exit zero and replayed the saved ranking before and after archival and
restart. Its one-candidate receipt grew from 1,699 to 2,519 serialized bytes with
the additional provenance. This single example is not a storage or latency study.

A valid custom weighting reproduced negative semantic/lexical weights in the
previous runtime: initial `.01/.02` became `-.04/-.08` for a current-state query,
or approximately `-.02333/-.04667` for a recent-episodic query. Recency redistribution
now stops at the available weight, yielding recency `.13` for that configuration.
The existing default configuration is unchanged; this is a correctness fix, not
evidence for a better default ranking policy.

`7a796af` fixes a regression found during follow-up review: the new adjustment
model initially rejected a finite session multiplier above one, although the
existing packing configuration accepts it. The test failed against the installed
`aa8684d` wheel, and **45 source checks passed** after preserving those existing
multiplier semantics. Neural blend coefficients still require the established
zero-to-one range.

The final installed `7a796af` wheel passed **202 checks with three skips** in
29.84 seconds, actual exit zero, including live PostgreSQL and the combined
pipeline tests. Its [repeated real BGE workflow](relevance-receipts-7a796af.json)
also passed with actual exit zero. No extraction or answer-judging provider is
used by this persistence/replay diagnostic.

The receipt/provenance models and a strict installed-package consumer passed
typing checks. The broader six-file typing command reports seven existing errors
in the optional neural dependency/numpy annotations and connection-lock typing;
the same seven errors were reproduced at `1461fb0`. Changed-source lint passed.
A first full-test launch ran before its new worktree finished checking out and
exited 5 without collecting tests; the simultaneous wheel build also failed for
the incomplete checkout. After checkout completed, the wheel built successfully
and a fresh full suite at `aa8684d` was started. That run finished with **2,151
tests passed and 52 skipped**, actual exit zero, in 812.51 seconds under concurrent
benchmark load. It predates the later finite-multiplier compatibility fix and
offline learning implementation.

## Offline relevance learning

`55c865a` adds `evaluate_learning` to the async engine and synchronous client,
plus a standalone evaluator for exported receipt/label models. It captures an
owner's feedback in one bounded query, resolves immutable receipts in batches,
fits bounded multipliers for six additive weights and evaluates the proposal on
separate query groups. Reports include exact input identities/checksums, coverage,
conflicts, configuration, proposed multipliers and per-query metrics.

Tests cover owner and scope boundaries, missing/corrupt/ambiguous receipts,
explicit-label membership, retry collapse, conflicting judgments, fixed hash
splits, caller-specified paraphrase groups, and additions after the snapshot cut.
Missing labels are not negatives. Feedback overflow and excess pair counts fail
instead of silently sampling. A top-rank gain with worse mean pairwise ordering
is rejected. A validation-only label reversal leaves training and fitted weights
unchanged while rejecting the proposal. Query assignments remain stable when new
feedback is added.

The final source integration set passed **68 checks with one skip** in 20.08
seconds; a subsequent focused run included the additional pairwise-regression
gate and passed **16 checks**. The final installed Python 3.13 wheel passed
**89 checks with three skips**, actual exit zero, in 26.03 seconds, including
live PostgreSQL, cancellation and backend status. The three learning/repository
modules and a strict installed-package consumer passed typing. Changed-source
lint passed. The frozen full suite at `55c865a` subsequently completed with
**2,178 tests passed and 52 skipped**, actual exit zero, in 514.98 seconds under
concurrent benchmark load. It predates the full-pipeline trial changes.

The [authored learning controls](learning-controls-55c865a.json) passed with actual
process exit zero. They use 100 distinct query IDs with a deliberately constructed
feature pattern, split into 68 training and 32 validation groups. The positive
control fits that pattern, and reversing validation labels alone rejects it with
identical multipliers. These are mechanism checks, **not an independent task
benchmark or evidence of product retrieval improvement**. The report includes
fixture and learner hashes and explicitly states this limitation.

The [real BGE public-client workflow](relevance-receipts-55c865a.json) also passed
with actual exit zero. Its single positive label correctly yields insufficient
learning evidence; the report reproduces after graph archival and restart.
No extraction or answer-judging service is used by that workflow.

The learned proposal remains offline: candidate exposure, neural prefix membership
and session lineage are fixed to the saved observations. Default weights remain
unchanged. Full-retrieval/task evaluation, feature-model compatibility, durable
profiles, activation and rollback are still required before production learning.

## Full-pipeline ranking trials and execution receipts

`669967e` adds explicit `ranking_multipliers` to engine, pipeline and sync-client
retrieval. The same adjustment function used by the offline learner runs after
query-specific weight redistribution. Neural prefix selection, session expansion,
selection and context packing then run normally. No profile is activated and
engine-global weights remain unchanged. The sync client also gains the engine's
event-time bounds, explicit base weights and fidelity controls.

New pipeline receipts use version 3 to retain request filters, adjustments and
reported feature identity. Version 1 and 2 canonical bytes/checksums and relevance
references remain unchanged. Features include declared model names/versions,
implementation names, dependency versions and source-file hashes; they are not
proof that remote model weights are pinned. Pipeline latency now includes receipt
work and separately reports `receipt_logging_ms`.

The source integration set passed **223 checks with one skip** in 37.84 seconds.
After adding the logging-latency check, a focused set passed **57 checks with one
skip** in 19.84 seconds. The final installed Python 3.13 wheel passed **126 checks
with three skips**, actual exit zero, in 46.83 seconds, including live PostgreSQL,
cross-scope filtering, cancellation and receipt recovery. Controlled tests show
that changed weights can select a different adjacent session turn and different
context; unity reproduces the baseline and simultaneous requests retain their
own settings. Two initial failures were incorrect test expectations for the mock
embedding provider name; assertions now use the fixture's declared identity.

The [real BGE sync workflow](relevance-receipts-669967e.json) passed with actual
exit zero. A request-time trial at the same clock matched rescoring of the saved
features. Its one-candidate receipt was 4,229 bytes; this is an audit-data example,
not a storage or latency benchmark. The typed execution/receipt modules and
pipeline passed typing after resolving JSON-map typing and replacing the older
untyped operation-pool annotation with a narrow protocol. Changed-source lint
passed, as did a strict installed-package consumer using the new sync controls.
The full frozen suite at `669967e` completed with actual exit zero: **2,201
passed, 52 skipped** in 834.22 seconds, using live PostgreSQL.

These checks establish an experimental full-pipeline path, not learned task
improvement or deployed adaptive profiles. The separately registered
[packing confirmation](../../packing/2026-09-12/CONFIRMATION.md) evaluates a
frozen score-ordering candidate using the original retrieval runtime.


## Organizer owner and scope boundaries

The prior revision (`1486638`) already rejected cross-owner duplicate/alias
pairs. It did not consistently reject pairs spanning PERSONAL and PROJECT
within the same owner. Supersedence itself rejected those pairs, but the
organizer had already transferred evidence and could create an edge first;
lower-confidence alias links were accepted across scopes. Discovery also
proposed cross-scope matches from both text and vector similarity.

Matching now includes scope in the exact/string partition, requests scope from
the vector index, and verifies candidate namespaces against graph nodes. Apply
functions reject mixed namespaces before evidence or edge writes, including
manually supplied candidate lists. Existing same-scope merging remains enabled.
No migration changes old data, and this does not implement namespace ACLs.

The corrected 22-case regression file produces **18 failures and four passing
controls** against `1486638`, actual exit one. The updated organizer integration
set passes **219 checks**, actual exit zero, in 105.25 seconds. These cover both
DuckDB and live PostgreSQL for the new regressions, direct apply calls, stale or
misbehaving vector results, valid same-scope matches, evidence preservation and
restart. The earlier draft of these tests confused returned event IDs with node
IDs; the corrected baseline and final runs use graph node identities. Changed
source lint and typing for both organizer modules pass.

The installed Python 3.13 wheel at `6ea522a` passed **73 targeted checks** in
45.93 seconds, actual exit zero. The run used the installed package (not the
source tree), including live PostgreSQL and the new namespace regressions.

## Legacy entity-profile scope preservation

The separate `consolidate_knowledge()` helper previously gathered every scope
for an owner and emitted PERSONAL graph/lexical entries. It could also consume
its own generated profiles on later passes. Ten regression cases failed on both
backends before correction. The helper and sync client now accept `scope`; an
omitted scope visits the six namespaces separately. Generated entity profiles
are excluded from source evidence, replacements keep their source scope, and
explicitly scoped calls leave other scopes and owners unchanged.

An additional two-backend regression showed that partitioning alone left an
obsolete legacy personal profile active when all real sources were in PROJECT.
Explicit rebuilding now reconsiders existing profile names and archives/evicts
profiles without two eligible same-scope sources, preserving original source
nodes and events. The final source regression set passed **73 checks** in
21.55 seconds, actual exit zero. It covers graph/index/retrieval scope, restart,
repeat-generation stability, namespace-local source thresholds, scoped sync
calls, and retirement of the unsupported legacy view.

Lint passes. Direct typing of engine and client reports **25 pre-existing
diagnostics**; the prior `6ea522a` tree reproduces the same diagnostics after
line-number normalization. This is not a globally clean typing result. The
profile path still uses heuristic name matching and approximate character-based
budgets, and publication is not transactional. These scope fixes are not evidence
of answer-quality improvement or complete profile correctness.

The installed Python 3.13 wheel at `87679e7` passed **95 checks** in 31.17 seconds,
actual exit zero, combining profile/organizer regressions with the earlier
scope-boundary cases on both backends. A strict installed-package consumer of
async and sync `consolidate_knowledge(scope=Scope.PROJECT)` also passed. These
installed-consumer checks do not remove the pre-existing internal typing debt.

## HTTP and MCP ranking trials

HTTP retrieval now accepts the bounded `ranking_multipliers` object and minimum
fidelity. MCP adds the same trial adjustment, a fixed aware scoring clock,
validity/event-time bounds, multiple scopes, fidelity, epistemic mode and
cross-scope controls. `include_context=true` returns the rendered packed context
alongside legacy results/metrics. The context budget is not a size limit for the
complete JSON tool response. Neither interface activates a learned profile.

The corrected interface regression file fails **six cases with six passing
validation controls** on `74f891b`. The final source trial/identity/filter set
passes **78 checks** in 29.78 seconds, and existing HTTP/MCP/write-compatibility
checks pass **86 checks** in 32.03 seconds; all final runs exited zero. Tests use
actual DuckDB and PostgreSQL with a deterministic embedding fixture, compare
transport context and exact receipt scores to the Python pipeline at one clock,
check baseline weights after a trial, and reject malformed trials before engine
execution. Two draft assertions used a nonexistent receipt score attribute;
the corrected comparison uses the persisted `score` field. An initial broader
command referenced a nonexistent test filename and ran no tests; the corrected
commands above completed normally.

Lint passes. Fresh, nonincremental typing reproduces the same **19 pre-existing
MCP diagnostics** on baseline and current code. An earlier incremental run also
surfaced cached engine/client diagnostics, so it is not used for that comparison.
These are API-contract tests, not new retrieval-quality or model-performance
measurements. The registered packing confirmation remains separate and frozen.

## Retrieval scope input normalization

A Python scope string was previously ignored by the pipeline, while an empty
list became an unfiltered index query. Mutating a passed list during the engine's
first materialization await could similarly broaden the search. The shared
normalizer now accepts enums, names and nonempty sequences, copies the request,
and rejects empty/unknown/unsupported input before pending-work processing.
Direct pipeline calls use the same validation; HTTP declares nonempty scope
arrays. The normalizer is included in new execution source-hash observations.

The 22 new cases failed on `a802d02` before the fix, including both backends,
valid string/tuple inputs, malformed inputs, list mutation, HTTP and sync calls.
The source scope/trial/filter/receipt set passed **101 checks with one skip** in
37.17 seconds, actual exit zero. The normalizer and pipeline pass targeted
typing, and changed-source lint passes. These checks establish request-scope
behavior, not full ACL or index-side-channel isolation.

The combined installed Python 3.13 wheel at `6bc2763` passed **100 targeted
checks** in 34.38 seconds, actual exit zero, including PostgreSQL. A strict
installed consumer accepts enum lists, string names and tuples plus ranking
trials through the typed public interfaces.

The [real BGE HTTP/MCP workflow](transport-trials-6bc2763.json) also completed with
supervised process exit zero. Both authenticated transports returned context
identical to Python at one clock; exported receipts retained the adjustment and
replayed exact scores. Foreign receipt reads and empty HTTP scopes were rejected.
The report records the installed package path and observed execution features.
This is one authored developer workflow, not answer-quality evidence. The full
frozen suite at `6bc2763` completed with actual exit zero: **2,269 passed,
52 skipped** in 877.28 seconds. This predates only the subsequent benchmark
reader harness and legacy judged-context correction.


## Qualified source evidence in entity profiles

At `88cc5e2`, entity profiles preserve whole source records, source/event IDs,
original epistemic labels, provenance and temporal metadata. Complete-name
matching prevents `Ann` from matching `Joanna`; records with identical prefixes
or identical text remain distinct episodes. Generated associations are INFERRED,
with confidence capped by their included sources and configured inferred value.
Full profile text obeys its named tokenizer budget; an oversized source does not
prevent a later complete source from fitting. Omitted sources remain active.

Eight regression cases reproduced the old behavior across DuckDB and PostgreSQL.
The corrected source profile/excerpt set passed 43 checks in 18.25 seconds and
the existing organizer upsert check passed. The installed Python 3.13 wheel
passed 101 profile/organizer checks in 29.50 seconds, native exit zero, including
live PostgreSQL. The helper's focused mypy check passed. These checks establish
behavioral contracts, not downstream answer accuracy or calibrated confidence.

The first [installed BGE workflow attempt](profile-fidelity-88cc5e2-attempt1.json)
failed with a TypeError: the sync client did not expose `max_profile_tokens`.
Two new backend regressions reproduced that missing argument. The sync wrapper
now forwards the same default and explicit token budget to the async engine.
At that checkpoint, profile publication was still nontransactional; the atomic
replacement work below supersedes that limitation. Entity association remains
heuristic, and older artifacts take the new format only when explicitly rebuilt.


The final installed Python 3.13 wheel at `e011d7d` passed 45 profile/excerpt checks
in 18.09 seconds, native exit zero. Strict installed consumer typing accepts the
same token-budget argument on sync and async clients. The corrected
[real BGE client workflow](profile-fidelity-e011d7d.json) completed with supervised
native exit zero: both qualified source statements survive creation, restart,
retrieval and rebuild; source events remain unchanged; owner/scope boundaries,
inferred labels, confidence cap and exact token counts hold. The preceding
TypeError attempt remains retained above. This is an authored workflow rather
than a benchmark-quality result. The frozen full regression run at `88cc5e2` completed with native exit zero:
**2,359 passed, 52 skipped in 926.35 seconds**, using Python 3.11 and live
PostgreSQL. It covers the profile source-fidelity change and preceding evaluation
work; the later sync budget forwarding is covered by the 45 installed checks
and real client workflow above.

## Atomic entity-profile replacement

Profile replacement now prepares indexes before graph publication. Both backends
atomically insert the replacement, archive its predecessors, advance a profile
publication generation and retain a checksummed publication operation. Sources,
previous profiles, owner and scope are revalidated at commit. Preparation errors
propagate, preserving the old profile; lost acknowledgements do not trigger
cleanup that could delete an already committed replacement. `StaleProfileError`
is exported for explicit retries using fresh source snapshots.

Four backend regressions reproduced the previous early-retirement and swallowed
embedding failures. Fault coverage now includes provider/index errors, source
changes, failure after insertion and after predecessor archival, independent
readers, concurrent connections, cancellation, repeated commit after archival,
and abrupt local process exit after staging, during insertion and after commit.
A concurrent DuckDB commit also reproduced rollback masking an already-ended
transaction; the original conflict is preserved and exposed as a stale rebuild.
The final focused source run passed **138 checks, 5 skipped in 66.86 seconds**,
native exit zero, with live PostgreSQL. The skips are backend-specific local
index/WAL cases. Focused mypy and Ruff checks pass for the new model/storage code.

This is per-profile atomic publication, not a durable organizer queue. Failed
local staging is conservatively retained, uncommitted plans are not automatically
resumed, and a multi-entity call can publish earlier entities before a later one
fails. Explicit index rebuild remains the cleanup path. Installed-wheel and full
regression validation of this change are pending at this checkpoint.

The installed Python 3.13 wheel at `430e1b3` passed the same **138 checks, 5 skipped
in 78.42 seconds**, native exit zero, including live PostgreSQL and the local
process-exit cases. Strict installed consumer typing accepts `StaleProfileError`
and both sync/async profile APIs. The [real BGE client workflow](profile-fidelity-430e1b3.json)
also passed with supervised native exit zero and confirmed the installed package
path; profile content, inference labels, source events and scope isolation
survive restart, retrieval and a subsequent replacement. The separate frozen `tests/` run at `430e1b3` subsequently passed **2,281 tests,
57 skipped in 860.82 seconds**, native exit zero. That invocation did not include
benchmark harness tests outside `tests/`, so it is not the full repository collection.

## Complete profile source scans

Two further regressions reproduced a silent history-size failure on both
backends: after adding 5,001 unrelated newer nodes, rebuilding an older supported
profile returned zero and retired it. Consolidation now scans scoped immutable-ID
pages instead of treating the newest 5,000 nodes as the complete source history.
Explicit names retain only matching source nodes; incomplete scans fail before
publishing or retiring anything. This is pagination, not a snapshot isolation
claim for the entire read pass; publication still revalidates its dependencies.
The expanded focused source suite passed **142 checks, 5 skipped in 77.63
seconds**, native exit zero, including scan interruption and the larger history.


The installed Python 3.13 wheel at `107f535` passed **142 checks, 5 skipped in
77.79 seconds**, native exit zero, including both storage backends. The
[real BGE workflow](profile-fidelity-107f535.json) also passed with supervised
native exit zero. The first Python example in `docs/ENTITY-PROFILES.md` executed
unchanged against this installed package in a temporary directory with default
configuration, published one profile and retrieved its context. Earlier tests
also checked the exported retry exception through strict installed consumer typing.

A new frozen run at `107f535` invokes `pytest -q` at the repository root to cover
both product and benchmark harness tests. It is still running at this checkpoint;
the preceding `tests/` result and focused passes must not be reported as its outcome.

The project `.env` credentials were explicitly rechecked at 17:26 UTC on
September 12. Both `gpt-4o-2024-08-06` and `gpt-4o-mini` returned HTTP 429 /
`RateLimitError`, with no recognized provider error code. The
[sanitized health record](openai-health-172655.json) contains no credentials,
endpoint, response body or billing inference. Local validation continues.

The complete repository collection at frozen `107f535` finished with native
exit zero: **2,400 passed, 57 skipped in 986.95 seconds** on Python 3.11 with
live PostgreSQL. This includes the final profile pagination change and the
benchmark harness tests omitted from the earlier `tests/` invocation. The
installed Python 3.13 focused checks and real embedding/guide workflows above
cover the final packaged implementation as well.

## Embedding cache invariance

The pinned Mem0 compatibility work exposed padding-sensitive local embeddings.
The real BGE probe reproduced a maximum component difference of 0.000223577 for
identical input requests with cold versus partially warm caches. Two isolated
regressions failed before the fix. FastEmbed now uses numerical batch size one;
the same real probe reports exact equality for cold/warm caches, batched/single
calls and reversed input order, with native exit zero. Existing numerical
payloads and model identifiers remain readable; no historical vector rewrite
occurs on open. The rebuild pagination argument was not the cause.

`embedding-invariance-before.json` preserves the failing real result and
`embedding-invariance-after.json` the successful result, including model asset
and provider-source hashes. Seventeen embedding/rebuild tests passed in 8.43s.
The diagnostic retains three alternating-order timing samples after warmup.
Short-text throughput falls with individual inference; mixed-length padding
cost can instead make it faster. These small local timings and four equality
inputs do not prove hardware-independent reproducibility or end-to-end speed.

The installed `e297d08` wheel on Python 3.13 also passed the real-model cache
invariance and profile workflow, both with native exit zero. Its focused
embedding, rebuild, derivation and vector suite passed 77 tests with seven skips
in 43.83s against the configured local PostgreSQL service. The earlier complete
repository pass remains tied to `107f535`, before this embedding change.

## Configuration loading and packing receipts

Non-finite scoring and packing values now fail during normal model/environment
validation (`4d787c6`), including nested node-type boosts and scoped weights.
Twenty-one regressions failed before the fix; the scoring/configuration/learning
check set passed 209 tests with four skips. Finite defaults and weight version
identifiers did not change. A fresh explicit root `.env` probe at 18:07 UTC still
returned HTTP 429 for both configured OpenAI judge candidates; the sanitized
report is `openai-health-180749.json`.

The opt-in public packing ordering required receipt schema version 4. Historical
versions 1–3 keep exact canonical bytes/checksums and implicit density semantics;
new receipts preserve the explicit ordering and execution descriptor. Nine
compatibility checks cover canonical bytes, selective exports, legacy-policy
rejection and version-4 roundtrips. Backend checks cover both orderings, existing
feedback links, graph mutation and restart. The complete focused packing,
provenance, receipt and learning set passed 215 tests with one skip in 37.56s.
The selective-export guard subsequently passed its nine compatibility checks.

That run first exposed a time-dependent test: its fixed 18:00 UTC knowledge
cutoff correctly excluded newly admitted data after that instant. The same test
failed on the earlier `e297d08` runtime. It now records admission time after the
write and retains the separate fixed historical query/event-time bounds.

Installed `0ed72a8` Python 3.13 checks passed 169 tests with one skip in 37.59s,
including both storage backends, version-4 policy persistence and historical
feedback links. The first installed invocation lacked the fixture directory on
the crash subprocess's import path (168 passed, one failed, one skipped);
adding the frozen harness root to `PYTHONPATH` corrected that invocation without
adding its `src` tree. Production imports remained from the installed wheel.
The installed public packer also reproduced all 714 frozen contexts with native
exit zero (`public-packing-option-installed-0ed72a8.json` in the packing results).

`5afcf8b` exposes the existing `config_from_directory` helper at the package root
for custom settings without manually assembling storage paths. The unchanged
integration-guide snippet executed from the installed wheel with real BGE
retrieval. Reopening preserved score-ordering receipts and feedback checksums,
and foreign-owner receipt reads returned no record. The probe's first path
containment assertion confused macOS `/var` and `/private/var` aliases; resolving
both paths corrected the harness. The successful native-exit report is
`directory-config-installed-5afcf8b.json`. Its installed consumer passed strict
mypy checking; the configuration and receipt source modules also passed mypy.

The complete repository suite at frozen `0ed72a8` exited 1: 2,452 passed,
57 skipped and two HTTP ranking-trial failures in 925.68s. Both tests used a
fixed knowledge cutoff that excluded later test writes; both also failed on
pre-option `e297d08`. The fixtures now capture a shared clock after admission
and apply it identically across Python/HTTP/MCP. The MCP check additionally
requires a nonempty result and context, preventing vacuous parity. The corrected
transport and tool-provenance set passes all 20 checks in 3.67s. The clean frozen `a2eb87d` full repository run then exited zero:
2,462 passed, 57 skipped in 349.15s, including live PostgreSQL. The separately
added reader-evidence diagnostic subsequently passed six integrity checks.

## Tool provenance

Direct `store(role="tool")`, deferred raw-source processing and extracted facts
previously classified the source as `user_stated`. The common inference helper
now selects `tool_output` case-insensitively and uses its configured confidence
matrix entry. Explicit direct-write overrides and saved historical plans remain
unchanged; unverified relationship proposals still use system-inferred provenance.
Six new regressions failed before this correction, while two explicit-override
checks passed. The focused storage, extraction, grounding and HTTP set passed
102 tests with six skips in 40.74s, including live PostgreSQL and DuckDB recovery,
restart, source citations and rendered provenance. An initial test typo referenced
an unimplemented `IMPORTED` enum; correcting the override control to the existing
`EXTERNAL_DOCUMENT` enum exposed the six product failures above.

Ruff passes on the changed modules. Mypy reports six existing ingestion-pipeline
issues (missing dateparser stubs, lambda inference, an object-typed confidence
matrix and DuckDB-specific attributes on the graph protocol); checking the prior
pipeline source reproduces the same six diagnostics. These are not a passing
project-wide type check.

The installed `a2eb87d` wheel passed all 53 tool-provenance, relationship and
HTTP/MCP fidelity checks on Python 3.13, including live PostgreSQL. Production
imports came from `site-packages`; only test fixtures came from the frozen
checkout. The [installed report](tool-provenance-installed-a2eb87d.json) records
the wheel/log hashes and native exit zero. These authored-provider checks do not
substitute for live extraction quality.

A fresh explicit root `.env` reload at 18:48 UTC still returned HTTP 429 for
`gpt-4o-mini` and `gpt-4o-2024-08-06`, with retries disabled. The sanitized
[health report](openai-health-final.json) contains no credentials or response
bodies. The earlier HTTP 401 is gone; the 429 reason remains unclassified.

## PostgreSQL lexical candidate completeness

The lexical backend previously applied its SQL limit before removing duplicate
node/index copies in Python. Two copies of the strongest memory could consume
both slots of a two-result query and omit another matching memory. Equal-score
selection also lacked a stable identity tie-break. Both authored regressions
failed before the fix (the three existing lexical tests passed). SQL now applies
owner/type/scope filters, selects the highest-scoring copy of each identity, then
orders by score and node ID before the limit. Tied copies prefer the graph row.
All 55 PostgreSQL backend and related HTTP/MCP checks passed in 5.31s, and Ruff
passed. This does not claim PostgreSQL and Tantivy have identical query semantics.

Installed `5a77fd5` Python 3.13 verification passed all 39 lexical and transport
checks in 3.83s with native exit zero. The [installed report](pg-lexical-limit-installed-5a77fd5.json)
pins the wheel and log hashes; production imports were verified in `site-packages`.

## Pending-work failure logging

An authored provider failure reproduced a disclosure through raw-materialization
warning tracebacks on both backends, despite sanitized persisted status. The
serialized DuckDB write queue also formatted provider exception messages.
Both paths now report the event/job identity and bounded failure category.
They do not format exception messages or attach tracebacks. Original job
exceptions still propagate to callers; a provider exception whose `__str__`
raises no longer prevents the queue from accepting the next healthy job.

The two backend regressions failed before the change. Recovery, failure-category
and concurrency verification passed 30 tests with three expected backend-specific
skips in 9.55s, using live PostgreSQL. Ruff passed. This covers these logging
paths only; it does not establish package-wide log sanitization.

The installed `40c375e` wheel also passed all 30 checks with three expected skips
in 10.29s on Python 3.13 and live PostgreSQL. The [installed record](pending-logs-installed-40c375e.json)
pins wheel and log hashes and records the observed native exit zero.

## Batched durable raw-source processing

`process_pending()` now combines local lexical replacements into a bounded
atomic commit after graph/vector preparation. No source is acknowledged before
that commit. Vector failures remain individual; a lexical batch failure falls
back to separate document writes, allowing healthy sources to finish. Direct
`store()` keeps immediate indexing and PostgreSQL keeps its individual writes.
Cancellation or process exit leaves unacknowledged source jobs available for
replay. The pass budget is cooperative between sources, with final commit and
fallback repair allowed to extend it.

The frozen `c89fb94` real-BGE diagnostic copied the same unprocessed artifact
into each of three serial and three batched trials. All six completed with exact
candidate and product-context parity for both authored queries. For 32 sources,
serial processing committed Tantivy 32 times versus once for batched processing.
Median processing times were 7.01s and 1.06s in the source runtime; the installed
Python 3.13 wheel measured 6.35s and 0.89s and passed the same parity checks.
The [source report](materialization-batch-source-c89fb94.json) and
[installed workflow](materialization-batch-installed-workflow-c89fb94.json)
retain all trial times, model asset hashes and observed native exits of zero.

These are small authored histories on one host with the embedding model warmed
before timing. Startup is excluded, and concurrent tests/retrieval work may
affect timings. Event identities are fresh per run; parity is checked between
copies within each run. This is evidence of fewer durable commits and local
workflow improvement, not a competitive speed or memory-quality result.

The [installed recovery tests](materialization-batch-installed-c89fb94.json)
passed 63 checks with 10 backend-specific skips in 34.94s on Python 3.13 and live
PostgreSQL. Tests cover atomic rollback, lost acknowledgements, cancellation,
owner filtering, selective failures, process exits before/after lexical commit,
direct-write recovery and exact serial/batched retrieval parity. Ruff passed.

The [first full run](materialization-batch-initial-full-c89fb94.json) is retained:
2,487 passed, 61 skipped and five failed. Four outage tests still intercepted
only the prior per-document indexing/flush path; they now fail the actual batch
commit and fallback paths. A logging assertion depended on a global structlog
filter left by earlier tests; it now verifies the queue's log arguments directly
while preserving the unprintable-exception and next-healthy-job checks.

`773a2f9` also fixes two typing regressions and adds public-drain cancellation
coverage. The three checked storage modules now have the same 24 pre-existing
mypy diagnostics as `40c375e`, after line-number normalization. This is not a
passing project-wide type check. The [updated installed verification](materialization-batch-final-installed-773a2f9.json)
passed 87 tests with 14 backend-specific skips in 40.99s, with native exit zero.
