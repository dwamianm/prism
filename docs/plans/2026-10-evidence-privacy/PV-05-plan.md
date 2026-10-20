# PV-05 / #324 implementation plan

Status: implementing the approved bounded plan. Original acceptance is not complete.
Base: `b0e642834f59cd5ce9b69d1c76634df97efa09f0`, branch `issue-324-protected-journals`.
Root released the committed dependency stack on 2026-10-20. Final #321/#323
commits must be reconciled before final review. #325 current-ledger authority,
full source/copy erasure and #326 export/restoration remain required integration.
No protected-product claim precedes #327.

The previously approved preparation and its reingest addendum are reused.
Its 74 runtime paths are an upper boundary, to shrink using dependency code.
The current processing integration resides in `privacy/admission.py`; its
resume/ingest/retry hooks implement AC1/AC2/AC6. Root was notified of this source
location adjustment. Do not duplicate authority, custody, sanitizer or temporal
logic; preserve default behavior and old serialized bytes/checksums.

Root also approved the actual `privacy/reads.py` AC4 integration seam: remove
the temporary receipt-persistence disablement and dispatch closed protected
receipt results through #323's existing projection and guard machinery. Adopt
the final #323 disclosure/marker repairs at reconciliation, without replacing
them with a parallel authority check.

## Original acceptance criteria

- [ ] Accepted asynchronous sanitized input is encrypted under a processing-only purpose
      with a finite deadline. Worker access requires a current claimed work item;
      completed diagnostic evidence is outside that capability.
- [ ] Erase sanitized work on completion, cancellation or expiry. A strict no-durable-
      input policy has no persisted retry payload and reports that limitation.
- [ ] Executable derivation plans contain only admitted memory and permitted
      metadata. Necessary sanitized diagnostic attachments use protected references.
      Restart publication does not need to unlock the evidence archive.
- [ ] Version receipts so queries and text-bearing execution data are minimized
      or protected; ordinary metadata-only inspection remains possible. Raw or
      unsalted predictable-text hashes are not treated as anonymous metadata.
- [ ] Lifecycle, TTL, supersedence, profile and organizer snapshots obey the same
      copy/retention rules; erasure journals never retain deleted plaintext.
- [ ] Injected crashes preserve atomic/idempotent publication. An expired or
      erased input cannot be resurrected by journal replay or stale work.

Validation: restart injection at the existing prepare/stage/publish boundaries,
the corresponding leakage/recovery manifest cases, and scans of logical records,
logs and temp artifacts. Keep previously published receipt bytes valid; a new
schema carries redaction/unavailability instead of rewriting old checksums.


## Implementation order and concrete interfaces

1. Reuse `PrivacyRecord`, `PrivacyRepository`, `CryptoProtector`,
   `NativeKeyCustody`, `StoredProcessingAuthorizer`, mandatory clock-bound
   `UnwrapPermit`, `LocalSanitizer` and `BudgetContinuation`. Enforce actual
   current claims and original deadlines; durably account work retries and erase
   processing slots on completion, cancellation and expiry.
2. Use `admitted_statement_v1` projections for executable copies. Register
   typed journal dependencies and terminal/unavailable records inside existing
   transactions and native stage fences. Preserve legacy decode/checksum paths.
3. Make admitted profile/summary/lifecycle/organizer paths useful under the
   same policy boundaries; sanitize generated output and diagnostic paths.
4. Add minimized versioned receipts with numeric replay, relevance and citation
   membership; query-dependent evaluation explicitly reports unavailable.
   Implement separate `RetryCommitter` through `RetryKeyProvider` custody.
5. Record real component evidence and pending original integration obligations;
   root owns independent reviews and final full-suite/wheel/CI delivery.

## Finite validation plan (reused; component results below)


### Core scenario execution and AC mapping

| Frozen ID | #324 acceptance linkage | Concrete effects and positive control | Shared completion boundary |
| --- | --- | --- | --- |
| AP06 | AC1, AC2, AC6 | Accept actual no-evidence async work; inspect ciphertext plus exact claim/deadline, resume one injected failure, complete admitted memory and delete all processing payload/checkpoints. Wrong/current/stale claim and diagnostic-purpose substitution tests. No-durable sync succeeds without durable input; sync failure reports resubmission; async rejects before enqueue. Exercise all frozen adapters that are actually supported. | Reuse #320 plumbing; #321 real codec. |
| AC06 | AC1, AC3 | With evidence unwrap configured to fail, recover/publish an admitted plan and run the source-only query with optional components enabled. Show useful admitted result plus explicit source unavailability; no diagnostic decrypt or source-only transmission. Standard uses a distinct authorized runtime capability. | Ordinary component routing is #323; keep its exact case evidence. |
| CL03 | AC1-AC3, AC6 | Pause after extraction and after plan preparation; enumerate saved input/output checkpoint, batch diagnostics, direct store, references, vectors and lexical text; recover without new extraction/embedding once the plan exists. Include an actual accepted admitted fact and protected/omitted source-only diagnostics. | Real sanitation and classification from #322/#323. |
| CL04 | AC4-AC6 | Promote/archive/reinforce/supersede/expire/penalize plus conditional state on admitted nodes carrying separately classified source-only metadata. Inspect every before/after/chain/edge copy, then invoke real erasure and inspect unavailable envelopes. Preserve qualifying admitted state before erasure. | Full then-erase portion waits for #325; not an empty or mocked pass. |
| CL05 | AC3, AC5, AC6 | Actually prepare/publish entity profiles and daily/weekly/monthly/consolidation summaries, merge compatible memories, and accept/reject alias proposals with a bounded fake external assessment. Inject one abandoned generation. Enumerate prepared/current/previous/retired/nested proposal copies and use mixed-source dependencies. Safe admitted summaries must publish. | #322 output boundary; covered erasure/invalidation from #325. |
| CL06 | AC4 | Actual sanitized retrieval with a prohibited marker verifies both inner and outer receipt/temporal execution fields, candidate/context/source hashes, fast retry binding and citations. Numeric replay, labels and allowed citations succeed; raw query-dependent learning explicitly reports unavailable. Legacy receipt fixtures remain byte/checksum identical. | #322 sanitizer; no new quality benchmark or anonymous-hash claim. |
| CL07 | AC3, AC5, AC6 | Interrupt local vector/lexical staging and snapshot save, retry a live item and abandon another. Inspect actual SQL vector payloads/staging/ownership plus native/Tantivy/pgvector sources. A healthy admitted plan still publishes exactly once. Verify erasure of covered staged derivatives after the real erasure call. | #323 admission-only index routes; #325 full erase/compaction. |
| CL08 | AC1-AC5 | Inject marker-bearing provider, schema, DB and native-stage exceptions; capture stdlib/structlog, status/errors, HTTP/MCP output, CLI/explorer/framework handoff and controlled temp/export artifacts. Real successful and failed operations both run. Verify no diagnostic keys or source-only content reaches prohibited sinks. | #320/#322 public/provider errors; #323 read projection; #326 protected export. |
| ER05 | AC2, AC3, AC5, AC6 | Expire actual pending input or erase it after prepare/stage and before publish; delete only mutable test work/registry rows, restart and attempt stale claim/native stage/commit/replan. Reconstructed jobs cannot reopen old source copies or publish. A live control resumes with identical admitted IDs and no new model calls. | #324 expiry/processing purge is real; #325 authoritative erase integration stays required. |

### Finite fault and compatibility subchecks

- Reuse existing fault hooks/test patterns rather than a new broad benchmark: extraction checkpoint and plan save; local vector stage, lexical stage, graph node/replacement/receipt writes; committed-before-ack; profile/summary prepared/staged/published/discarded; lifecycle validated/updated/journal hooks. Use fixed fake clock values at deadline-minus-one, exact deadline and after deadline. Do not sleep to prove expiry.
- For derivation preserve the existing finite boundaries from `test_derivation_ingestion.py` (`plan`, `vector`, `lexical`, `node`, `edge`, `commit`) and abrupt-exit points in `test_derivation_commits.py`. Add the protected processing deletion/terminal transaction and missing-work-row variations only to the AC-linked cases. PostgreSQL concurrency uses independent connections; DuckDB uses actual independent connections/native fences and subprocess abrupt exits where existing harnesses support them. A component exception is not mislabeled as process-crash evidence.
- At each recovered live control: same admitted artifact IDs, exact single receipt, no partial graph visibility, no duplicate retirement, no extra extraction/embedding after a complete plan, exact vector values and intact original policy/deadline. At each denied/terminal case: no later native/graph write, no resurrected pending payload, no diagnostic key read, no plaintext terminal record. A committed receipt may remain inspectable as non-content identity; it never authorizes another publication.
- Batch subchecks: two current same-owner/scope/session members, foreign owner/scope, expired second member, completed predecessor window, lease takeover and split request omitting the head. Acquire/revalidate the exact members before payload reads; no claimed-head shortcut. A benign accepted batch must complete atomically according to the existing per-turn publication protocol; do not newly promise one transaction for all extracted turns.
- Retry commitment subchecks: same request/original policy yields same comparison and original IDs/deadline; changed content, metadata, scope or requested policy conflicts; identical content under another request/boundary yields different output; wrong purpose/key/version/retention fails; current erasure/expiry wins over a saved valid commitment. Missing key must not reprovision or fall back to raw SHA-256. Actual HMAC executes; no plaintext map is persisted.
- Compatibility goldens cover DirectStoreRecord v1/v2, extraction and batch optional-field omission, derivation revision 1 and later, receipt v1-v22 available fixtures, `_snapshot_json` finite/non-finite legacy encoding, lifecycle/reinforcement v1/v2 and profile/summary/alias old operation bytes. Preserve signed zero through PostgreSQL JSONB string storage. Existing ordinary source recovery and cancellation tests must still pass without privacy selected. Redacted/unavailable new envelopes explicitly cannot verify or replay original erased bytes.

### Logical and artifact scans

Use distinct synthetic prohibited-original, source-only and intentionally admitted markers. The admitted marker may exist in admitted plans/indexes/summaries; do not mistake that for a source leak. For encrypted diagnostic/processing objects, test purpose-authorized decode of the synthetic payload as well as at-rest bytes. Ciphertext alone does not prove that prohibited pre-sanitization text was never retained.

Scan C03,C05-C23,C31,C32,C37,C40-C42 directly and record a coverage disposition for all C01-C42 inventory entries. Enumerate every operations kind/payload, processing slot/checkpoint, job/registry/error, plan/reference/replacement, before/after/chain snapshot, nested proposal, profile/summary generation, receipt outer payload/execution/temporal map, label/citation/learning report and retry key/commitment reference. Scan the actual vector inputs/numerical payload enumeration, staging text and native index sources; byte-marker absence in a float vector cannot prove no semantic derivative. Scan owned logs/temp/WAL/export artifacts after success, failed preparation/publication and restart. Test-owned prohibited raw hashes are calculated in test memory only and included as scan probes, never as production reports.

Use only root-assigned disposable databases/paths. PostgreSQL logical records and a task-owned logical dump are mandatory; task-owned server WAL/temp/base-file access is an additional physical-artifact visibility question, not something to infer from SQL scans. Report unavailable visibility honestly. External backup/WAL archives, provider retention, already delivered client/browser caches and physical media remain outside the engine-only purge claim. No heap-zeroization or external cache revocation is promised.

### Targeted and required final checks

During implementation, run only new/affected tests from this finite existing regression set. The final full suite covers the passing unchanged set once; do not rerun a separate broad focused suite merely to duplicate that evidence:

- Work and admission: `tests/test_extraction_journal.py`, `tests/test_extraction_work.py`, `tests/test_extraction_work_interfaces.py`, `tests/test_extraction_retries.py`, `tests/test_extraction_failure_codes.py`, `tests/test_extraction_window.py`, `tests/test_batched_extraction.py`, `tests/test_extraction_source_recovery.py`, `tests/test_direct_store_recovery.py`, `tests/test_durable_ingestion.py`, `tests/test_batched_materialization.py`, `tests/test_materialization_cancellation.py`, `tests/test_engine_startup_recovery.py`.
- Derivations and stages: `tests/test_derivation_commits.py`, `tests/test_derivation_ingestion.py`, `tests/test_derivation_index_staging.py`, `tests/test_derivation_planning.py`, `tests/test_derivation_replanning.py`, `tests/test_derivation_registry.py`, `tests/test_obsolete_derivation_staging.py`.
- Histories and organizer: `tests/test_snapshot_json.py`, `tests/test_atomic_lifecycle.py`, `tests/test_atomic_ttl_expiration.py`, `tests/test_reinforcement_atomic.py`, `tests/test_reinforcement_retries.py`, `tests/test_atomic_oscillation_penalty.py`, `tests/test_supersedence_journal.py`, `tests/test_atomic_contradictions.py`, `tests/test_condition_evaluation.py`, `tests/test_profile_work.py`, `tests/test_profile_registry.py`, `tests/test_profile_publication.py`, `tests/test_profile_retirement.py`, `tests/test_profile_collection.py`, `tests/test_summary_publication.py`, `tests/test_atomic_organizer_merges.py`, `tests/test_atomic_alias_proposals.py`, `tests/test_alias_proposal_review.py`.
- Receipt consumers: `tests/test_packing_receipt_compatibility.py`, `tests/test_relevance_records.py`, `tests/test_relevance_interfaces.py`, `tests/test_answer_citations.py`, `tests/test_learning_snapshot.py`, `tests/test_relevance_learning.py`, `tests/test_ranking_profiles.py`, `tests/test_ranking_trial_interfaces.py`, `tests/test_ranking_profile_interfaces.py`, `tests/test_legacy_feedback_boundary.py`.
- Consume #320-#323's merged privacy regression modules and recorded evidence. Their final names come from the merged dependencies; no case may be declared passed from preparation alone.

On the final revision, with root's isolated locked environment and live dedicated PostgreSQL URL:

1. Run the six new privacy test modules listed above, including the shared scenario runner, with the nine frozen IDs and both required DuckDB layouts recorded. The erasure/export portions remain explicitly waiting until #325/#326, and the exact same frozen cases are completed there/#327 without changing definitions.
2. `uv run --no-sync python -m pytest tests/ -q` with required dev/postgres/api/mcp/framework/privacy extras. CI's Python 3.11-3.13 DuckDB and PostgreSQL matrices remain required. Skips are not backend passes.
3. `uv run --no-sync ruff check src/ tests/`, scoped `ruff format --check` on touched files, and `uv run --no-sync mypy --follow-imports=silent --ignore-missing-imports --strict tests/typing/public_api.py`.
4. `uv build`; installed-wheel/version/py.typed smoke in a separate temporary environment, never overwriting another worker's editable install.
5. Existing required `python -m scripts.run_simulations` under current defaults and under the CI previous-default environment (`PRME_SCORING__FUSION=weighted`, `PRME_PACKING__CONTEXT_FORMAT=auditable`, `PRME_PACKING__MULTIPATH_ORDERING=balanced`). These are bounded project checks, not permission for model calls or a long benchmark/default-change trial. Reuse root's exact current revision evidence when applicable.
6. Framework integration modules if not already executed with extras, and `python docs/plans/2026-10-evidence-privacy/validate-contract.py` for structural references only. Structural validation is not runtime privacy evidence.
7. Root schedules required independent review of the exact stable final snapshot and delivery. Preserve real failures and classify them against ACs/regressions/security; no extra quality round after agreed checks pass.


## Evidence rules

Exactly nine frozen IDs, eighteen backend case dispositions, with directory and
single-file DuckDB subchecks. No fixture/cohort changes, model calls or long
benchmarks. Positive workflows must run; a refusal is not a substitute for
accepted protected work. Fixture authority is component evidence only until
#325 integrates. Shared erase/export cases remain pending rather than passed.
Targeted tests run during implementation; root coordinates final full checks.
Two unsuccessful repair/validation cycles for one behavior stop that strategy.
Initial repair-cycle count: zero.

## First implementation checkpoint

Processing uses the existing per-event worker protocol: multi-input admission
partitions the original sanitizer continuation into disjoint allowances, then
each input needs its own real claim. No protected contextual batch opens a
completed or unclaimed predecessor. Durable ingress is enqueue-only. The
authenticated processing slot holds validated extraction checkpoints; an
interrupted unaccounted allowance is unavailable, never reset. Saved admitted
plans recover without processing or evidence keys.

The temporary PV-04 receipt_persisted=False expectation is now True (root
approved AC4 transition). The separate receipt family omits queries, execution
text and source/context/content commitments; fixed-exposure ranking replays
saved numeric scores, without claiming score recomputation or query replay.
Labels and UUID answer citations work on real persisted receipts. Legacy bytes
and the legacy fault-hook call signature remain unchanged.

Root resolved the retry input seam on 2026-10-20: retain PV-01's exact original
request conflict semantics. The private retry-purpose HMAC covers canonical
pre-sanitization request bytes plus the original policy and opaque boundary/
request identity. Original bytes are never persisted by that comparison. This
is an equality token for a holder of the separate retry key, not anonymous
metadata; public reports never expose it. Original retention/deadline and PV-06
erasure coverage still apply. This supersedes the preparation's narrower
"already-sanitized accepted shape" wording without weakening changed-input
conflict checks or granting any source replay capability.

Lifecycle/condition/reinforcement/supersedence/oscillation/merge writers reuse
their typed snapshots and native transactions. A protected envelope carries
projected admitted snapshots and original dependency/policy references. TTL
tombstones carry identity/retention metadata only and explicitly cannot replay
the retired text. These are components, not completed source erasure.

See PV-05-results.json for preserved failures, successful bounded repairs and
remaining owned work. No original acceptance box or frozen full case is passed.

## Profile and summary checkpoint

Protected profile lineage uses an actual admitted entity UUID within the owner
and scope (root approved); missing/ambiguous identity fails explicitly. V2
profile and consolidation plans carry a JournalManifest with original saved
policy/source/representation dependencies and admitted snapshots. V1 optional
fields are omitted, preserving legacy bytes. Daily, weekly, monthly and clustered
summaries use the shared local generated-output boundary and guarded embedder.
Prepared recovery uses no private evidence key. Consolidation retirement uses
explicit selected admitted identities and exact rendered source coverage; the
protected node omits the legacy text hash and coverage-fingerprint map.

Native staging retains its actual work/registry fence under current authority;
the queue consumer separately acquires current authority at SQL publication.
Holding the producer's non-reentrant authority across that queue deadlocked,
so those real effects are guarded separately. Failed evidence and the successful
correction are retained. Existing profile generation and collection semantics
remain; whole erasure and export gates still await PV-06/PV-07.

One extra source path is approved for CL08/AC3/AC5: ingestion/entity_merge.py
logs the admitted entity name during protected extraction (observed in the
profile-components-3.log capture). The implemented change is solely the common
operation-scoped logger wrapper, with a concurrent legacy logging positive.
No entity matching, provider behavior, scenario or cohort is changed.

## Final owned component checkpoint

The frozen nine scenarios and all six original acceptance criteria above remain
unchanged and pending as full integration gates. Actual component results cover
directory DuckDB, single-file DuckDB and the assigned PostgreSQL schema. The
inherited OS adapter and current-policy authority are fixtures; actual crypto,
claims, persistence, publication and cleanup run. Reviewed PV-03 native-adapter
and PV-13 pinned detector evidence is carried forward without a replacement
cohort. No whole purge/export or protected-product pass is inferred.

Protected alias proposals retain admitted snapshots and bounded numeric
assessment in a new version; reviews retain the manifest and decision without
nesting the proposal payload. Existing legacy codecs and operation types stay
readable. Actual accepted/rejected review, compatible merge, organizer and
session promotion remain useful. Protected profile discard and collection use
the existing native work/registry fences, remove real owned staging and preserve
immutable abandoned history. Missing mutable rows reconstruct that state;
an expired generation cannot publish, while a live generation succeeds keylessly.

JournalScope records its creating asyncio task and active lifetime. Only the
same task can reuse a held mutation scope. An inherited child reacquires real
publication authority; joined native work can still check active deadlines
without requiring a thread-local asyncio task. PV-06 owns the corresponding
current-ledger guard and exact internally derived node-target extension. Its
consumer should use manifest.admitted_node_ids, scalar lifecycle IDs, actual
oscillation chain_node_ids, merge/alias pair IDs and decoded proposal manifests;
none of those caller references grants authority by itself.

The last CL04 subcheck found a concrete contributor defect: the existing call
resolver included the oscillation target but omitted its copied chain. The
first correction resolves every actual chain_node_id through the same repository
identity resolver before publication. All three layouts then published and
retried idempotently. A separate new-test count error included two existing
fixed contradiction transitions in the four snapshot count; the corrected test
checks all six records and each distinct closed shape. Both failures are kept.

Protected diagnostic context follows the actual worker queue and joined native
work, with fixed error/status/report shapes. Real provider, schema, SQL and native
close faults are captured through stdlib/structlog plus HTTP/MCP responses.
Concurrent legacy logs remain unchanged. Every managed SQL table is included in
a test-owned logical dump and owned directory/pack/index/temp files are scanned
after close. PostgreSQL server physical WAL/base/temp files are not accessible
to this harness. This is no protected export or physical-erasure claim.

All C01-C45 inventory entries now have explicit implementation, inherited or
pending dispositions. The shared typed boundary supplies the copy treatment;
the original 74-path preparation remains an upper boundary, not a mandate for
blanket edits. The only added runtime path outside that upper bound is the
approved entity_merge diagnostic wrapper. No duplicate authority/custody/
sanitizer stack or new serializer was introduced.

Preserved evidence separates production repairs (profile queue authority,
PostgreSQL profile replay/discard guard placement, damaged-work handling,
scope=None routing and oscillation contributor resolution) from new test setup
corrections. Each resolved behavior passed its first correction; no behavior
has two unsuccessful repair cycles. The repeated PostgreSQL indentation issue
triggered a bounded review of the already changed publication/work/discard/
collection guard and return branches, reusing the existing three-layout cases.

Before root review: commit this owned checkpoint, normally merge the final
PV-04 test/docs delta c42af439, run affected component/static/compatibility checks
and freeze. Root owns independent affected-source reviews and the required final
full suite, wheel, CI and delivery. PV-06 authoritative erase/current-policy,
PV-07 export/restore and PV-14 combined release evidence remain required; the
delivery must reference #324 without closing it until those original gates pass.
