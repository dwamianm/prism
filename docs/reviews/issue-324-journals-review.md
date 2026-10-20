# Issue #324 protected journals prerequisite review

Status: all four required independent reviews are COMPLETE and APPROVE the
declared prerequisite only. This is a reviewable branch checkpoint, not completed
delivery of #324. No newly established blocking finding was reported in the owned
prerequisite. Known dependency failures and original integration requirements
remain mandatory and unresolved as described below.

Refs #324

## Reviewed identity and scope

- Reviewed HEAD: `b552c56f8706ff1757ddb40151bf05983f0108ad`
- Reviewed tree: `2bd0a04c55c79b01af69a7182b12e97d32831d38`
- Comparison base: `c42af43926ca95a42f8b05facc8606b2df61023c`
- Review snapshot: `fefcc14c790adce529fb6c499125956f4a4b22baf8409873c375ebb04be87c9e`
- Scope: 61 changed paths, 49 runtime paths and 5,342 changed lines.
- Tier L; SECURITY, DATA, PERSISTED_FIELD and NEW_SYMBOLS apply. INFRA does not.
- Sole exclusion: `docs/reviews/issue-324-journals-review.md`. It carries these
  reports only; no implementation, tests or supplied dependencies are excluded.
- The original review plan and collection-time plan have the same snapshot.
  An artifact-only commit may advance HEAD/tree without changing reviewed source.

The finite checkpoint plan was recorded before this file was assembled. This
packaging pass changes only this tracked review artifact and an ignored draft PR
body. It does not execute tests or repair dependencies, change the original
acceptance criteria, add review scenarios, or claim a final full-suite result.

## Reviewer roster and checklist mapping

| Review | Report identity | Checklists | Result |
| --- | --- | --- | --- |
| Core | `/root/review_324_core` | 1 correctness; 3 quality/test coverage; 4 patterns/full-config comparison; 5 redundancy/new symbols | COMPLETE, APPROVE prerequisite |
| Security | `/root/review_324_security` | 2 security | COMPLETE, APPROVE prerequisite |
| Data | `/root/review_324_data` | 7 data/storage safety and persisted writers/readers | COMPLETE, APPROVE prerequisite |
| Adversarial | `/root/review_324_adversarial` | 8 adversarial | COMPLETE, APPROVE prerequisite |

Checklists 3, 4 and 5 are merged into the core report, not skipped. Checklist 6
infrastructure is not applicable under the recorded plan. The adversarial review
is capped at the eight existing cases AP06, AC06 and CL03 through CL08. ER05
remains part of the original obligation covered by core/data, not a ninth
adversarial case. Four completed reviews are reused; no extra review or cohort
was requested for this artifact.

The full reports below are preserved byte-for-byte between their BEGIN and END
markers. Their original source-file SHA-256 hashes are:

| Source report | SHA-256 |
| --- | --- |
| `review-324-core.md` | `486d1c1fea850fc32753bbc53a6a0c0c3b7df0d798c25c0879f88413af68d5ea` |
| `review-324-security.md` | `e42ea025b4c180e3aa9550b6db5136b414c3cccf360406cab2aee6ad307dd9a8` |
| `review-324-data.md` | `38f526c20635a2efd1644327202987da848c6003538c49ef9365919fa6aed6fa` |
| `review-324-adversarial.md` | `506640dd6edd93779464286a0fe0fa886a72fb57c8419d9a25026c840c845f26` |

## Original acceptance criteria

All six criteria remain open. The text below is unchanged from the original issue.
Component evidence and prerequisite approval do not mark these complete.

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

All nine complete cases remain pending: AP06, AC06, CL03, CL04, CL05, CL06,
CL07, CL08 and ER05. Their original setup, outcomes and adapter requirements
remain unchanged. Layout subchecks and useful component controls do not count
as whole-case passes.

## Existing verification evidence

The reports reuse the implementation evidence; this packaging pass does not
rerun it. The evidence directory is the worktree's ignored
`.github-ticket-loop/privacy-324/`, with outcomes retained in the tracked
`docs/plans/2026-10-evidence-privacy/PV-05-results.json`.

| Evidence | Recorded result | Limits |
| --- | --- | --- |
| `final-components.log`: six new privacy modules | 49 passed, exit 0, 73.28 pytest seconds | Runtime revision `3016d80dc25447a506c2c24e6db6f435ffa17d6c`; directory DuckDB, single-file DuckDB and real dedicated PostgreSQL components |
| `final-compatibility.log`: profile collection/retirement/scopes and encryption | 154 passed, 11 skipped, exit 0, 51.21 pytest seconds | Skips are not PostgreSQL passes |
| `final-scoped-ruff-repair.log` | Scoped Ruff passed, exit 0 | Owned Python paths and public consumer contract only |
| `final-types-repair.log` | Strict public consumer mypy passed, exit 0 | Exact recorded static consumer contract |
| Bounded format and AST checks | 12 new files plus three current-only formatting regressions passed; production formatting AST-equivalent | 37 inherited format failures are preserved in `final-existing-format-baseline.json`; no full-format pass is claimed |
| Structural contract check | Passed, 45 copy classes and 182 exact anchors | Structural evidence only, not runtime privacy proof |
| Earlier unchanged alias/organizer compatibility | 228 passed | Reused evidence, not a new run on this checkpoint |

After the runtime component revision, the frozen handoff records only
AST-identical production formatting, imported-fixture annotations, exact public
type assertions and documentation/evidence changes. Runtime tests were not rerun
at the reviewed `b552c56f` HEAD or this artifact checkpoint.

The component harness uses real crypto, SQL, work claims, persistence, native
staging, publication and cleanup. External models, clocks/faults, inherited OS
get/set/delete and current-policy authority are fixtures. Logical dumps are scan
artifacts, not implemented protected exports. PostgreSQL server WAL/base/temp
files and unmanaged backups were not observed. These reports make no claim of
physical wipe, anonymity, heap zeroization, same-process isolation or revocation
of already delivered copies.

Failed evidence remains failed. In particular,
`lifecycle-siblings-components-repair.log` repaired contributor publication but
exited 1 on a separate count assertion; its later successful correction remains
separate. Original lint/type/format failures and repair accounting remain in the
recorded results. No repair counter is reset by packaging this review.

## Mandatory remaining checks and dependency blockers

1. The known #323 mixed-legacy/owner-global classifier and AP06 expectation
   failures, plus the native-close SIGABRT investigation, are real final-test
   failures. They are not dismissed because they originate in a dependency.
   The source-read correction has two unsuccessful cycles; user approval for
   one extra bounded attempt is pending. This checkpoint performs no repair.
2. Actual #325 ledger/lock composition at `storage/privacy_journals.py:17`,
   receipt logical-ID integration at `privacy/admission.py:832` and
   `models/privacy_receipts.py:142`, and exact contributor/erasure integration
   remain unresolved requirements. Fixture-backed component passes do not prove
   those effects. The reports preserve the existing separate DATA repair limit.
3. #325 must supply current-policy/erase integration and covered-copy purge;
   #326 must supply protected export/restoration and complete artifact/adapter
   coverage; #327 must complete the combined original whole-case validation.
   These are original obligations, not discretionary follow-up work.
4. A concrete corrected #323 checkpoint and later integration changes require
   affected-scope validation and reuse of the existing required reviewers.
   No further broad review round is authorized by this artifact.
5. The reconciled final revision still requires the full DuckDB/PostgreSQL test
   suite with required extras, full Ruff and applicable format checks, strict
   public types, package build and installed-wheel/py.typed smoke, current and
   previous-default simulations, framework checks where not already covered,
   structural contract validation and required CI. No final full suite,
   package/wheel smoke, simulations or CI is claimed for this checkpoint.
6. PR creation and merge remain deferred. The local draft uses `Refs #324`
   only, with no issue-closing reference. No issue is closed by this review.

Before the checkpoint push, both repository workflows were inspected in full.
`ci.yml` runs only on a push to `main` or a pull request targeting `main`;
`publish.yml` runs only for a published release or explicit workflow dispatch.
No PR existed for this branch at preflight. A branch-only push therefore does
not start those workflows. No check is bypassed and no skip-CI marker is added.

## Complete original reports

The reports retain their own headings, findings, identities, evidence limits and
completion markers. BEGIN/END markers delimit exactly the source-file bytes.

<!-- BEGIN review-324-core.md -->
Status: COMPLETE
Verdict: APPROVE
Reviewer: /root/review_324_core (independent core reviewer; one agent, read-only source review)

This verdict applies only to the declared nonclosing PV-05 prerequisite. It does not approve completion or closure of issue #324, the protected product, or final delivery before the remaining required checks. No new blocking defect was established in the owned delta. No source/test edits, test executions, provider calls, installs, GitHub mutations, or additional agents were used.

## Exact review identity

- HEAD: b552c56f8706ff1757ddb40151bf05983f0108ad
- Tree: 2bd0a04c55c79b01af69a7182b12e97d32831d38
- Comparison base: c42af43926ca95a42f8b05facc8606b2df61023c
- Assigned review-plan snapshot: fefcc14c790adce529fb6c499125956f4a4b22baf8409873c375ebb04be87c9e
- Coverage: 61 changed paths, 49 runtime paths, 5,342 changed lines. Local git identity and clean tracked status confirmed during review.
- Only exclusion: docs/reviews/issue-324-journals-review.md, future review transport. No implementation or test exclusion.

Read the home and engine/worktree AGENTS.md, consolidated review policy, substantive start-issue workflow, original issue JSON, approved preparation, frozen handoff, affected-source plan, tracked PV-05 plan/results, and the implementation/evidence delta. The finite review scope is original AC1–6 as implemented by this prerequisite, introduced regressions, and concrete security defects; missing future integration remains an original requirement, not a newly invented acceptance criterion.

## Attributable merged checklists

1. Correctness, edge cases, async behavior and utility contracts: covered. Traced actual work claims through ProcessingJournal.begin/checkpoint, authenticated processing-only recovery, admitted-plan publication and terminal slot cleanup. The checkpoint reservation is consumed before work; orderly failures save remaining allowance, interrupted reservations do not silently reset it. Protected ingress is enqueue-only and avoids plaintext Event retry closures. Each batch input receives a separate claim and disjoint continuation. The saved-plan recovery branch does not open diagnostic or processing keys. Missing work/admission rows deny protected publication. Deadline/terminal checks extend existing backend transactions rather than replacing generation fences.

3. Quality and test assertions: covered. Read all six new component modules and their harness plus the modified source-read/static consumer assertions. Checks assert useful admitted facts, persisted numeric receipts, idempotent labels/citations, actual profile/summary publication, accepted and rejected aliases, compatible merges, staged-index removal, restart reconstruction and a live recovery control. They also assert absence of source-only markers in real logical operation records and owned artifacts. Fixtures are explicitly limited to external models, clock/faults, OS adapter and current-policy authority. The tests do not present blanket refusal, mocks of SQL/crypto, a logical dump, or an injected Python exception as proof of whole integration, physical erasure, export, or process-crash coverage.

4. Existing patterns and full-config comparison: covered. Compared the legacy and protected branches, DuckDB directory/single-file and PostgreSQL paths, protected opt-in/default classification, public Python/HTTP return unions, queue diagnostics, generated-memory readers and query-dependent consumers. Protected receipts use a separate discriminator and explicit not-retained/unavailable fields; the legacy make_receipt serializer and scoring replay remain separate. Profile/consolidation v1 omit the new optional manifest, and existing raw snapshot encoding and legacy checksum validation are retained. PostgreSQL keeps exact publication JSON inside the existing string wrapper. Native stage fences and queue-consumer publication retain separate authority acquisition, avoiding producer-held non-reentrant authority across submit. Default configuration and the unavailable source-dependent effect list were examined; enabling journals does not enable protected reingest or owner-group-link bypasses.

5. Redundancy/new symbols: covered. The new helpers reuse PrivacyRecord/Repository, policy authority, native key custody, sanitizer continuations, admitted-node/edge projections, original transaction/fence implementations and _snapshot_json. JournalManifest is a typed copy-dependency record, not another authority system. The retry HMAC is purpose/request/original-policy bound and has no raw-hash fallback or silent reprovisioning on verify. ScopedLogger and queue capture give operation-local diagnostics without a global logger toggle. No unnecessary parallel storage, sanitizer, replay, or authority stack was found.

Basic security/runtime wiring screening: covered in the core scope; dedicated security/data reviews remain independent. Runtime coordinator assignment reaches both graph and profile work stores. New persisted formats have actual writers and readers: processing_attempt in ProcessingJournal and completion consumers; SnapshotEnvelope in typed mutation writers/read_record dispatchers; protected profile/consolidation manifests in generation and publication/recovery validators; protected receipt/citation discriminators in retrieval/citation writers and repository readers; protected alias proposal/review versions in their transaction writers and inbox decoders. Lifecycle, TTL, reinforcement, condition, supersedence, oscillation, merge and generated copies use the shared typed boundary. JournalScope is task-owned and invalidated on exit; child tasks cannot reuse it as a mutation capability, while joined native work checks its active deadlines.

## Premise and compatibility assessment

The original problem is durable copied content outside the source slot, not merely missing encryption on one queue. The delivered split addresses that premise: finite encrypted processing checkpoints, admitted executable/journal copies with original dependencies, and content-minimized inspection/receipts. TTL records explicitly retain identities rather than deleted plaintext. Numeric replay is correctly labelled as saved-score ordering, not score recomputation or query replay. Query-dependent learning remains unavailable instead of using a blank query. Admitted memory and its journal copies remain sensitive and subject to the still-required PV-06 erasure work.

The known inherited #323 owner/global classifier and AP06 expectation findings are not duplicated as #324 defects. The inherited native-close investigation is likewise not relabelled. The concrete corrected #323 checkpoint must be reconciled and affected scopes re-reviewed before final delivery.

## Shared verification evidence

Reused, did not rerun, the owner’s recorded commands and inspected final log summaries in WT/.github-ticket-loop/privacy-324/:

- final-components.log: six new privacy modules, 49 passed, exit 0, 73.28 pytest seconds (74.92 command seconds). Directory, single-file and actual dedicated PostgreSQL component paths are explicitly parameterized.
- final-compatibility.log: profile collection/retirement/scope plus encryption/single-file encryption, 154 passed and 11 skipped, exit 0, 51.21 pytest seconds (52.32 command seconds). Skips are not backend passes.
- final-scoped-ruff-repair.log: all owned Python paths and consumer typing file, exit 0.
- final-types-repair.log: strict public consumer mypy contract, exit 0.
- Tracked results record passing bounded format/contract checks, earlier unchanged organizer/alias compatibility, exact commands, log locations and true failure statuses. Structural contract validation is not runtime privacy evidence.

Runtime component checks ran at composition 3016d80d. The frozen handoff records subsequent production changes as AST-identical formatting only, with fixture annotations, exact static return-type assertions and evidence/docs changes. This review did not claim those checks were rerun at b552c56f. The final full suite/wheel/simulations/CI remains root-owned and pending on the reconciled final revision.

Failed evidence is preserved: lifecycle-siblings-components-repair.log remains an overall failure despite fixing contributor resolution; the later count repair passed its separate assertion correction. The fixture lint history retains one unsuccessful narrow repair and one successful full-scope repair. Distinct PostgreSQL profile replay/discard corrections are recorded separately and prompted the bounded guard/return reassessment. This reviewer did not reset counters, relabel failures, or authorize another repair cycle.

## Findings and mandatory open gates

Blocking findings in the declared prerequisite: none established. No additional exploratory quality round is requested.

All six original acceptance criteria and all nine full scenarios AP06, AC06, CL03–CL08 and ER05 remain open. In particular, authoritative current-policy/erase integration and covered-copy purge belong to #325; export/restoration and complete artifact/adapter coverage belong to #326; the combined protected-product gate belongs to #327. Current component fixtures do not satisfy those requirements. Physical PostgreSQL WAL/base/temp files and unmanaged backups were not observed. No anonymity, physical wipe, heap zeroization, same-process isolation, or already-delivered-copy revocation is inferred. Delivery must use Refs #324, not a closing reference.

## Reviewed changed paths


### processing_claims_retry_and_effect_boundaries

- `src/prme/api/routes.py`
- `src/prme/client.py`
- `src/prme/ingestion/pipeline.py`
- `src/prme/privacy/admission.py`
- `src/prme/privacy/records.py`
- `src/prme/privacy/retry_commitments.py`
- `src/prme/storage/durable_queue.py`
- `src/prme/storage/extraction_work.py`
- `src/prme/storage/privacy.py`
- `src/prme/storage/privacy_journals.py`
- `src/prme/storage/write_queue.py`

### admitted_journal_codecs_receipts_and_public_projection

- `src/prme/models/privacy_receipts.py`
- `src/prme/privacy/journals.py`
- `src/prme/privacy/reads.py`
- `src/prme/storage/citations.py`
- `src/prme/storage/relevance.py`

### backend_transactions_native_staging_and_recovery

- `src/prme/storage/alias_proposal.py`
- `src/prme/storage/alias_review.py`
- `src/prme/storage/condition_evaluation.py`
- `src/prme/storage/consolidation_publication.py`
- `src/prme/storage/consolidation_retirement.py`
- `src/prme/storage/duckpgq_graph.py`
- `src/prme/storage/engine.py`
- `src/prme/storage/lexical_index.py`
- `src/prme/storage/lifecycle.py`
- `src/prme/storage/organizer_merge.py`
- `src/prme/storage/oscillation_penalty.py`
- `src/prme/storage/pg/graph_store.py`
- `src/prme/storage/profile_collection.py`
- `src/prme/storage/profile_publication.py`
- `src/prme/storage/profile_registry.py`
- `src/prme/storage/profile_retirement.py`
- `src/prme/storage/profile_work.py`
- `src/prme/storage/reinforcement.py`
- `src/prme/storage/retention.py`
- `src/prme/storage/supersedence.py`
- `src/prme/storage/vector_index.py`

### organizer_generated_memory_and_diagnostics

- `src/prme/ingestion/entity_merge.py`
- `src/prme/models/consolidation.py`
- `src/prme/models/profile.py`
- `src/prme/organizer/alias_resolution.py`
- `src/prme/organizer/consolidation.py`
- `src/prme/organizer/deduplication.py`
- `src/prme/organizer/jobs.py`
- `src/prme/organizer/maintenance.py`
- `src/prme/organizer/summarization.py`
- `src/prme/privacy/diagnostics.py`
- `src/prme/retrieval/pipeline.py`
- `src/prme/retrieval/reformulation.py`

### evidence_and_consumer_contract

- `docs/plans/2026-10-evidence-privacy/PV-05-plan.md`
- `docs/plans/2026-10-evidence-privacy/PV-05-results.json`
- `docs/plans/2026-10-evidence-privacy/content-copy-inventory.json`
- `tests/privacy_journal_support.py`
- `tests/test_privacy_journal_records.py`
- `tests/test_privacy_journal_recovery.py`
- `tests/test_privacy_journal_scenarios.py`
- `tests/test_privacy_processing_work.py`
- `tests/test_privacy_receipts.py`
- `tests/test_privacy_retry_commitments.py`
- `tests/test_privacy_source_reads.py`
- `tests/typing/public_api.py`

Completion marker: REVIEW_324_CORE_COMPLETE
<!-- END review-324-core.md -->

<!-- BEGIN review-324-security.md -->
Status: COMPLETE
Verdict: APPROVE
Reviewer: /root/review_324_security
Checklist: 2, independent SECURITY review

This approval applies only to the declared nonclosing PV-05 prerequisite. No new blocking security defect was established in its owned delta. It is not approval to close #324, claim a protected product, or ship without the remaining required final checks and dependency reconciliation.

## Exact identity and bounded review

- HEAD: b552c56f8706ff1757ddb40151bf05983f0108ad
- Tree: 2bd0a04c55c79b01af69a7182b12e97d32831d38
- Base: c42af43926ca95a42f8b05facc8606b2df61023c
- Assigned review snapshot: fefcc14c790adce529fb6c499125956f4a4b22baf8409873c375ebb04be87c9e
- Scope: 61 paths, 49 runtime paths, 5,342 changed lines.
- Only exclusion: docs/reviews/issue-324-journals-review.md, future review transport. No implementation or test excluded.

Confirmed HEAD/tree and clean tracked status with read-only git commands. Read applicable home and worktree AGENTS.md, consolidated review policy, substantive start-issue workflow, original issue, approved preparation including reingest addendum, frozen handoff, affected-source plan, tracked PV-05 plan/results, and shared core review evidence. Independently inspected all 49 runtime path deltas, the principal new modules in full, relevant inherited claim/crypto/thread/read utilities, and security assertions/harness limitations. The finite criterion is original AC1–6 as implemented by this prerequisite, introduced regressions, and concrete security issues. No new cohort, broad evaluation, third repair, or quality round was authorized or performed.

One reviewer was used within the approved four-review L roster. No subagents, source/test changes, test executions, database accesses, installs, live provider calls, benchmarks, GitHub changes, commits, or delivery actions were performed. Only this requested report was written.

## Independent security conclusions

**AC1/AC2: processing authority and cleanup.** Traced AdmissionCoordinator.resume through current repository/work identity checks, ProcessingJournal.begin reservation, authenticated processing-slot open, checkpoint generation advancement and claim-bound compare-and-swap. StoredProcessingAuthorizer checks exact payload, saved snapshot, singleton membership, running work generation and lease. The crypto utility enforces purpose-specific access and deadlines after waits; this branch does not open evidence for processing recovery. Interrupted allowances remain consumed. Orderly failures persist actual remaining allowance; failed persistence terminates the slot. Completion consumes processing state in the existing backend publication transaction. Cancellation/expiry removes the slot and advances terminal work generation with a content-free journal. Missing work/admission state cannot authorize protected publication. Renewal is capped by the original deadline. Protected asynchronous ingress is enqueue-only and does not retain the plaintext Event in delayed automatic retry closures. Multiple admitted inputs receive disjoint continuation allowances and their own actual claims.

**AC3/AC5: admitted copies and dependent authority.** Examined typed SnapshotEnvelope/JournalManifest writers/readers and all changed lifecycle, reinforcement, supersedence, condition, oscillation, retention, merge, profile, summary and alias routes. Projection validates origin references, owner/scope and admission kind; generated nodes use a closed admitted metadata shape. Snapshot traversal operates on typed node/edge models and strips source-only node/edge metadata through existing projections. The manifest retains original source/representation/policy dependencies and exact copied node identities. Oscillation includes the actual chain contributors. Generated publication validates admitted copies and exact source/previous/output identity sets. Protected profiles use admitted entity UUID lineage; summaries use admitted inputs, generated-output sanitation and guarded embedding. Existing legacy serializer/checksum branches remain separate. TTL records contain identities and retention metadata rather than retired plaintext. Alias proposals preserve admitted nodes plus bounded numeric assessment; reviews preserve the decision and manifest without nesting the complete proposal payload. Supplied free-text reasons are sanitized and actor/reviewer fields are bounded to fixed identities or UUIDs on protected mutation entry.

**AC4: receipts and private equality state.** ProtectedRetrievalReceipt has an explicit discriminator and closed schema. Both its inner receipt and outer retrieval operation omit the raw query, execution text and source/context/content commitments. Numeric ranking replay is truthfully identified as saved-score ordering. Protected citation records reject answer hashes and free-text answer identities; relevance/citation writers require guarded publication and owned receipt membership. Readers verify owner/request/checksum and dispatch legacy records without changing their bytes. Query-dependent learning remains unavailable. NativeRetryCommitter uses a separate retry purpose, original context/policy binding, request identity and retention reference, a versioned length-delimited HMAC-SHA-256 message, constant-time equality, and finally closes key handles. Verification requests the exact version and does not provision replacement keys. It is private equality state, not anonymous metadata; it does not confer source replay authority.

**AC6: async/native lifetime, effects and recovery.** JournalScope checks active lifetime and owning asyncio task before a held scope can be reused as mutation authority. A ContextVar-inheriting child acquires its own authority. Native threads retain deadline checking while joined work settles; scope exit invalidates the shared object. Existing run_to_completion waits for native work before releasing parent locks even on cancellation. Profile/consolidation staging remains under native work/registry fences and current publication authority; queue publication acquires its own authority instead of reentering the producer's non-reentrant lease. Protected plans require mutable work state, and terminal/abandoned/expired recovery is denied. Saved admitted-plan publication avoids processing and evidence keys. Replay paths do not imply a new content effect merely because an old marker exists; return fencing remains at the public boundary.

**Diagnostics, public boundaries and scope isolation.** ScopedLogger substitutes a fixed event without exception text, traceback, names, arguments or attached context on protected paths. Its context is operation-local, follows joined native work, and is explicitly captured by WriteJob for the independent queue consumer. Protected provider/backend errors use fixed capability failures; processing/profile status and organizer/collector reports use closed projections. Closing diagnostics classify reopened protected stores. Inspected actual HTTP/Python type unions and existing projection/error wrappers; no raw private key or processing payload was introduced into those responses. Organizer generation uses admitted graph/search adapters and explicit owner propagation; matching/merging still checks owner and scope. Unsafe source-dependent optional operations remain unavailable. These mechanisms retain useful positive publication rather than treating blanket refusal as success.

## Evidence reused and its limits

Inspected the recorded final summaries in WT/.github-ticket-loop/privacy-324/ and reused the owner's exit-status evidence:

- final-components.log: 49 passed, exit 0, 73.28 pytest seconds. Actual directory DuckDB, single-file DuckDB and dedicated PostgreSQL component paths cover processing, retries, minimized receipts, snapshots, generated publications, cleanup/recovery and diagnostic faults.
- final-compatibility.log: 154 passed, 11 skipped, exit 0, 51.21 pytest seconds. Skips are not backend passes.
- final-scoped-ruff-repair.log and final-types-repair.log: exit 0; scoped lint and strict consumer types passed.
- Tracked results retain commands, exact outcomes, earlier unchanged compatibility and structural contract checks. Structural anchors are not runtime security proof.

Runtime components ran at 3016d80d. The frozen handoff records later production changes as AST-identical formatting, with fixture annotations/type assertions/evidence changes. This reviewer did not rerun runtime checks at b552c56f and does not claim otherwise. Tests assert real positive admitted facts, profiles/summaries, accepted/rejected aliases, merges, citations/labels, staging removal and restart controls, as well as source-only marker absence. The inherited OS adapter and current-policy authority remain fixtures; real crypto, claims, SQL, publication and native staging run. Logical dump scanning is not export or physical-erasure proof. PostgreSQL physical WAL/base/temp files and unmanaged backups were not observed.

Failed evidence remains failed: the lifecycle sibling repair log has a distinct later test-count failure; later correction evidence is separate. Lint repair history and distinct PostgreSQL replay/discard corrections remain in the results. This review does not reset any repair count or authorize another attempt.

## Findings and mandatory remaining gates

Blocking findings newly established in the declared prerequisite: none. No extra targeted execution requested.

The already reported actual-ledger/connection-lock composition deadlock at src/prme/storage/privacy_journals.py:17 is the pending #325 integration correction, not a newly discovered owned #324 finding. Its concrete correction requires affected re-review and validation before delivery. Likewise, the inherited #323 mixed-legacy classifier/AP06 final-test failures and native-close SIGABRT investigation remain unresolved root-owned delivery evidence. The requested extra bounded repair requires the existing user approval; this report neither duplicates that repair nor authorizes it.

All six original acceptance criteria and the nine complete scenarios AP06, AC06, CL03–CL08 and ER05 remain pending as full integration requirements. #325 must supply actual current-ledger/erasure integration and covered-copy purge; #326 must supply export/restoration and complete artifact/adapter evidence; #327 must supply whole-case protected-product gates. These are original obligations, not optional follow-ups. The final reconciled revision still needs the required full suite, wheel/smoke, simulations, CI and applicable affected re-review. Use Refs #324, not a closing reference. No physical wipe, anonymity, heap zeroization, same-process isolation, or revocation of already-delivered copies is inferred.

## Reviewed path inventory

### processing_claims_retry_and_effect_boundaries

- `src/prme/api/routes.py`
- `src/prme/client.py`
- `src/prme/ingestion/pipeline.py`
- `src/prme/privacy/admission.py`
- `src/prme/privacy/records.py`
- `src/prme/privacy/retry_commitments.py`
- `src/prme/storage/durable_queue.py`
- `src/prme/storage/extraction_work.py`
- `src/prme/storage/privacy.py`
- `src/prme/storage/privacy_journals.py`
- `src/prme/storage/write_queue.py`

### admitted_journal_codecs_receipts_and_public_projection

- `src/prme/models/privacy_receipts.py`
- `src/prme/privacy/journals.py`
- `src/prme/privacy/reads.py`
- `src/prme/storage/citations.py`
- `src/prme/storage/relevance.py`

### backend_transactions_native_staging_and_recovery

- `src/prme/storage/alias_proposal.py`
- `src/prme/storage/alias_review.py`
- `src/prme/storage/condition_evaluation.py`
- `src/prme/storage/consolidation_publication.py`
- `src/prme/storage/consolidation_retirement.py`
- `src/prme/storage/duckpgq_graph.py`
- `src/prme/storage/engine.py`
- `src/prme/storage/lexical_index.py`
- `src/prme/storage/lifecycle.py`
- `src/prme/storage/organizer_merge.py`
- `src/prme/storage/oscillation_penalty.py`
- `src/prme/storage/pg/graph_store.py`
- `src/prme/storage/profile_collection.py`
- `src/prme/storage/profile_publication.py`
- `src/prme/storage/profile_registry.py`
- `src/prme/storage/profile_retirement.py`
- `src/prme/storage/profile_work.py`
- `src/prme/storage/reinforcement.py`
- `src/prme/storage/retention.py`
- `src/prme/storage/supersedence.py`
- `src/prme/storage/vector_index.py`

### organizer_generated_memory_and_diagnostics

- `src/prme/ingestion/entity_merge.py`
- `src/prme/models/consolidation.py`
- `src/prme/models/profile.py`
- `src/prme/organizer/alias_resolution.py`
- `src/prme/organizer/consolidation.py`
- `src/prme/organizer/deduplication.py`
- `src/prme/organizer/jobs.py`
- `src/prme/organizer/maintenance.py`
- `src/prme/organizer/summarization.py`
- `src/prme/privacy/diagnostics.py`
- `src/prme/retrieval/pipeline.py`
- `src/prme/retrieval/reformulation.py`

### evidence_and_consumer_contract

- `docs/plans/2026-10-evidence-privacy/PV-05-plan.md`
- `docs/plans/2026-10-evidence-privacy/PV-05-results.json`
- `docs/plans/2026-10-evidence-privacy/content-copy-inventory.json`
- `tests/privacy_journal_support.py`
- `tests/test_privacy_journal_records.py`
- `tests/test_privacy_journal_recovery.py`
- `tests/test_privacy_journal_scenarios.py`
- `tests/test_privacy_processing_work.py`
- `tests/test_privacy_receipts.py`
- `tests/test_privacy_retry_commitments.py`
- `tests/test_privacy_source_reads.py`
- `tests/typing/public_api.py`

Completion marker: REVIEW_324_SECURITY_COMPLETE
<!-- END review-324-security.md -->

<!-- BEGIN review-324-data.md -->
Status: COMPLETE
Verdict: APPROVE
Reviewer: /root/review_324_data
Checklist: 7, independent DATA / PERSISTED_FIELD review

This approval covers only the declared, nonclosing PV-05 foundation prerequisite. It does not close issue #324 or discharge its original six acceptance criteria and nine complete integration scenarios.

## Identity, scope, and evidence

- Frozen HEAD `b552c56f8706ff1757ddb40151bf05983f0108ad`; tree `2bd0a04c55c79b01af69a7182b12e97d32831d38`; comparison base `c42af43926ca95a42f8b05facc8606b2df61023c`; assigned review-plan snapshot `fefcc14c790adce529fb6c499125956f4a4b22baf8409873c375ebb04be87c9e`.
- Confirmed HEAD/tree and clean tracked worktree read-only. The plan covers 61 paths, 49 runtime paths and 5,342 changed lines. The sole exclusion is future review transport `docs/reviews/issue-324-journals-review.md`; no source or test is excluded. DATA, SECURITY, PERSISTED_FIELD and NEW_SYMBOLS are raised.
- Read applicable engine `AGENTS.md`, review policy, start-issue workflow, original issue, reused preparation, frozen handoff, affected plan, tracked PV-05 plan/results and prior core/security reports. Inspected the changed storage/codec, SQL, journal, native-stage and reader paths and their relevant inherited interfaces. Used one reviewer from the approved four-review L roster. No mutation outside this report, extra agent, test run, DB call, live provider, benchmark, install or broad evaluation.
- Reused recorded `final-components.log`: 49 passed, exit 0, 73.28 pytest seconds, including real directory DuckDB, single-file DuckDB and dedicated PostgreSQL component paths. Reused `final-compatibility.log`: 154 passed, 11 skipped, exit 0, 51.21 pytest seconds; skips are not backend passes. Scoped Ruff, strict public consumer types, format/AST equality and structural contract checks are recorded passing. The runtime component revision was `3016d80d`; later production edits are documented AST-identical formatting. No claim of a runtime rerun on this HEAD. The final full suite, wheel/smoke, simulations and CI remain root-owned.

## Persisted writers and compatible readers

| Durable field or format | Runtime writer | Runtime reader / compatibility boundary |
| --- | --- | --- |
| Encrypted processing checkpoint v1 (`source`, optional `extraction`) and `processing_attempt` in the existing admission body | `privacy/journals.py:625` serializes the checkpoint; `privacy/admission.py:706` seals and saves it; `storage/privacy_journals.py:198` and `:212` reserve/checkpoint by claim and generation. | `privacy/admission.py:737` opens the processing slot under an actual claim, then `privacy/journals.py:639` decodes it; a kind-less authenticated v1 input remains readable. `storage/privacy_journals.py:121` reads the admission body for state checks, and `storage/extraction_work.py:212`, `:232`, `:294`, `:330` consumes that state for claim/renew/replan/retry. Completion/expiry/cancel clears the payload and advances work generation in one transaction at `storage/privacy_journals.py:131`. |
| Admitted source/representation/policy dependencies and node IDs (`JournalManifest`), generated-node reference and v2 prepared profile/summary plans | `privacy/journals.py:24`, `:47`, `:68`, `:273`, `:314`, `:732` construct and project dependencies; `storage/engine.py:4328` and `organizer/consolidation.py:473` create v2 plans; `storage/profile_work.py:33` and `storage/consolidation_publication.py:54` persist immutable checksummed preparations. | `privacy/journals.py:181`, `:224`, `:250` revalidate origin/admission/owner/scope and exact node set; `storage/profile_work.py:39` and `storage/consolidation_publication.py:60` decode/check checksum and owner; `storage/profile_publication.py:42` and `storage/consolidation_publication.py:466` check current dependency snapshots. v1 fields remain optional/defaulted in `models/profile.py:74` and `models/consolidation.py:79`, preserving old plan serialization and checksum paths. |
| Protected before/after journal envelope for lifecycle, reinforcement, supersedence, condition evaluation, oscillation, merge and consolidation retirement | `privacy/journals.py:343`, `:501` projects typed snapshots and writes a family-tagged, checksummed envelope. Writers call it in `storage/lifecycle.py:56`, `reinforcement.py:111`, `supersedence.py:51`, `condition_evaluation.py:141`, `oscillation_penalty.py:75`, `organizer_merge.py:64`, `consolidation_retirement.py:67`. | `privacy/journals.py:567` verifies family, operation, digest, node set and admitted projection. Each corresponding module's `read_record` dispatches protected and legacy records (`lifecycle.py:65`, `reinforcement.py:120`, `supersedence.py:58`, `condition_evaluation.py:150`, `oscillation_penalty.py:116`, `organizer_merge.py:71`, `consolidation_retirement.py:74`). Legacy `_snapshot_json` bytes and checksum calculations stay on their original branch. |
| Protected TTL identity-only tombstone | `storage/retention.py:116` writes `ProtectedRetentionRecord` at `:55` with dependency manifest, identity and time fields, without source node or deleted-content hash. | `storage/retention.py:144` dispatches protected v2 and original checksummed full v1. The protected record deliberately has unavailable original replay; full erasure traversal is pending #325. |
| Protected alias proposal v3 and review v2 | `storage/alias_proposal.py:214`, `:520` persists admitted projected nodes, manifest and bounded numeric assessment; `storage/alias_review.py:75`, `:370` persists decision/manifest without nesting the full proposal payload. | `storage/alias_proposal.py:309` dispatches v1/v2/v3 and verifies checksums, deterministic operation identity and projected nodes; `storage/alias_review.py:211` dispatches legacy/new review versions and validates proposal/decision/edge identities. Existing versions retain their old raw serialization. |
| Protected retrieval receipt and citation discriminators, minimized operation payloads | `retrieval/pipeline.py:1130`, `:1228` writes the protected receipt and operation under a publication guard; `models/privacy_receipts.py:130` builds candidate/dependency fields. `storage/citations.py:74`, `:90` writes protected citation records under the same receipt membership contract. | `models/privacy_receipts.py:186` dispatches protected/legacy receipts; `storage/relevance.py:44`, `:91` checks owner, request and checksum; `storage/citations.py:30`, `:111` dispatches protected/legacy citation records and checks digest and saved receipt membership. `privacy/reads.py:600` projects metadata for protected public inspection. Legacy receipt and citation codec branches remain intact. |
| Private retry equality commitment (`key_version`, retention reference, value) | `privacy/retry_commitments.py:67` provisions a purpose-bound key, computes a length-delimited versioned HMAC and closes the handle. | `privacy/retry_commitments.py:85` requests the exact saved version/reference, compares in constant time and closes the handle; no replacement key is provisioned on verify. The commitment is private equality state, not source replay authority. |

`PRIVACY_PROCESSING_TERMINAL` at `storage/privacy_journals.py:153` is a content-free audit marker. It is not a source of recovery authority; the live reader uses `privacy_admissions` and `event_extractions` as above. No schema migration or backfill is introduced for these new JSON families. Existing operation tables, admission body, work rows, stage fences and artifact-owner registry carry them.

## Transaction, isolation, and recovery assessment

`storage/privacy_journals.py:17` holds the policy guard through the SQL transaction; the existing current-ledger/connection-lock composition issue is already owned by #325 and requires affected re-review before delivery. Processing reservations and slot deletion use the same work transaction and generation checks (`storage/privacy_journals.py:185`, `:131`). DuckDB conflict and PostgreSQL row-lock behavior remain distinct rather than being treated as equivalent retries. A failed orderly processing attempt saves actual remaining allowance, and an interrupted reserved allowance is spent (`privacy/admission.py:706`, `:755`; `storage/privacy_journals.py:201`). Terminal/expired work cannot recover the old slot via retry/replan; saved admitted plans can publish without opening the processing or evidence payload.

Profile and consolidation preparation is journaled before staging, with immutable checksum/owner and current dependency comparison (`storage/profile_work.py:39`, `:392`; `storage/consolidation_publication.py:60`, `:465`). Local vector/lexical staging uses work epochs, exact embedding/plan identity and joined native work (`storage/profile_work.py:418`; `storage/consolidation_publication.py:472`; `storage/engine.py:4345`). Abandoned profile collection first removes lexical then durable vector stage, checks registry completeness and preserves immutable journal/work discovery for retry (`storage/profile_collection.py:200`, `:223`, `:243`). `privacy/journals.py:362` limits a held journal scope to its active owning task; native deadline checks remain active through joined work. Owner and scope are checked in admitted node projection (`privacy/journals.py:181`) and in prepared/review/receipt readers. Positive controls in the reused components publish admitted facts, profiles, summaries, merges, aliases, receipts and citations, including restart and staging cleanup paths.

## Findings and open gates

No new blocking DATA/PERSISTED_FIELD finding was established against this owned prerequisite. No extra targeted execution is requested. The inherited #323 mixed-legacy classifier/AP06 failures and native-close SIGABRT remain recorded as inherited; the requested third classifier repair awaits user approval and is not recast here. The distinct failed lifecycle sibling/count and lint histories remain failed evidence until their separately recorded corrections; no repair counter is reset.

The original AC1–6 and complete AP06, AC06, CL03–CL08 and ER05 scenarios remain pending. #325 owns actual current-ledger/erasure integration and covered-copy purge; #326 owns export/restore and complete artifact/adapter coverage; #327 owns the combined protected-product gate. Component fixtures and logical dumps cannot prove those obligations, physical PostgreSQL WAL/base/temp erasure, or unmanaged-backup deletion. This report authorizes only the `Refs #324` prerequisite, with corrected dependencies receiving affected re-review before delivery.

Completion marker: REVIEW_324_DATA_COMPLETE
<!-- END review-324-data.md -->

<!-- BEGIN review-324-adversarial.md -->
Status: COMPLETE
Verdict: APPROVE
Reviewer: /root/review_324_adversarial
Checklist: 8, independent ADVERSARIAL review

Approval applies only to the declared nonclosing PV-05 prerequisite using Refs #324. It does not approve issue closure, the protected product, or final delivery. No newly established blocker in the owned prerequisite was found. Known dependency defects and all original whole-case obligations remain open.

## Identity, done criteria and budget

- Worktree: /Users/dwamianm/Sites/prism/.claude/worktrees/privacy-324-journal-isolation
- HEAD: b552c56f8706ff1757ddb40151bf05983f0108ad
- Tree: 2bd0a04c55c79b01af69a7182b12e97d32831d38
- Comparison base: c42af43926ca95a42f8b05facc8606b2df61023c
- Assigned snapshot: fefcc14c790adce529fb6c499125956f4a4b22baf8409873c375ebb04be87c9e
- Reviewed scope: 61 changed paths / 49 runtime paths / 5,342 changed lines in the supplied review plan. This report traces the eight assigned cases through relevant source and test consumers; it does not claim a separate all-path core/security review.
- Sole exclusion: docs/reviews/issue-324-journals-review.md, future review transport. No source or tests excluded. HEAD/tree and clean tracked state independently checked.

Done for this review: preserve original AC1–6; trace exactly AP06, AC06, CL03–CL08 against their unchanged setup/outcome, challenge each against useful positive controls and existing evidence, report actionable criterion-linked blockers and remaining limits. Finite validation was read-only source/tests/git and recorded evidence, with no additional execution absent a specific approved concern. Budget used: one final reviewer, eight existing scenarios, zero tests/database accesses/live calls/installs/benchmarks/subagents. Only this report was written. ER05 remains part of the original integration obligation assigned to core/data; citing shared recovery assertions here does not add it as a ninth adversarial case.

Read applicable home and engine/worktree AGENTS.md, consolidated review policy and substantive start-issue workflow, original issue, approved preparation, frozen handoff/affected plan, tracked PV-05 plan/results and frozen scenario manifest. Reused core/security APPROVE reports for shared verified evidence while independently tracing the relevant implementations. Original six acceptance criteria remain pending exactly as recorded in PV-05-plan.md: encrypted finite claimed processing; terminal cleanup/no-durable limitation; admitted plans/protected diagnostic references/keyless publication; minimized versioned receipts; copy/retention including erased plaintext; atomic/idempotent publication with no expired/erased resurrection.

## Exact eight-case challenge table

The setup and expected columns below are copied verbatim from the frozen manifest. Layouts and individual assertions are subchecks, not new cases. Every whole scenario remains pending; “Accept” is the bounded prerequisite review disposition only.

| ID / original AC linkage | Original setup | Original expected | Source-traced adversarial challenge and useful evidence | Disposition and limits |
| --- | --- | --- | --- | --- |
| AP06 / AC1, AC2, AC6 | Run no-evidence sync and async with fake failure; separately request no durable processing input. | No-evidence may use encrypted finite pending work, removed on completion; strict no-durable input rejects async before enqueue and explains resubmission after sync failure. | Challenge: treat capture=none as permission to persist plaintext, substitute diagnostic access for a claim, replay a cancelled claim, or replenish an interrupted budget. AdmissionCoordinator.resume validates actual work generation/lease before reading; encrypted checkpoint generation advances through ProcessingJournal.begin/checkpoint; terminal cleanup removes processing and invalidates work. Source: privacy/admission.py:644,684; storage/privacy_journals.py:63,135,197; privacy/journals.py:590,625. Positive control: tests/test_privacy_processing_work.py:23 completes tea memory after a real failed claimed attempt; :101 denies cancelled access; :191 exercises separately claimed members with bounded total allowances. | Accept for prerequisite. Strict no-durable/sync and adapter evidence is inherited, not rerun here. Inherited #323 mixed-legacy/AP06 failures remain failures. Actual current-ledger authority and all original adapters remain integration gates. |
| AC06 / AC1, AC3 | Ask a quotation/source-only query with session expansion, augmentation, temporal reasoning and answerability enabled; decrypt provider raises if called. | Sealed ordinary processing makes zero evidence decrypt calls and never transmits source-only marker; reports source unavailability. Standard source access is separately authorized while available. | Challenge: optional expansion or a saved plan quietly opens evidence, and rejection alone masquerades as useful retrieval. privacy/reads.py:286 sanitizes queries; :450 installs admitted graph/vector/lexical views and requires explicit selected-source access; :478 inspects availability without decrypting. privacy/admission.py:696 publishes saved plans before opening processing. tests/test_privacy_source_reads.py:15 uses an evidence-open trap, useful admitted results, explicit source limitation, session/episode/projection/augmentation controls and standalone answerability; tests/test_privacy_processing_work.py:154 recovers a useful saved plan with both private key classes locked and unchanged provider counts. | Accept for prerequisite. This is source-traced component/inherited evidence, not execution of the complete original quotation query and all temporal/standard-access branches. Source limitation is not proof of source deletion; #327 whole-case gate remains. |
| CL03 / AC1, AC2, AC3, AC6 | Pause after extraction and preparation; inspect model responses, references, prepared embeddings and lexical content, then recover. | Executable plan is admitted-only; sanitized diagnostics are protected references; no hidden raw-input archive in extraction/batch/direct-store operations. | Challenge: extraction JSON, batch output or retry input becomes a second operation-log archive; interrupted planning silently re-extracts; absence of a work row grants replay. privacy/journals.py:625,639 keeps source/extraction in the versioned processing checkpoint; privacy/admission.py:701-760 seals it under the existing purpose and original snapshot, with durable reservation and remaining budget. Admitted saved-plan recovery uses the publication path. tests/test_privacy_processing_work.py:154 checks tea content, source-only absence and zero new extraction/embedding; :257 rejects missing/currently unclaimed work/admission publication rather than reconstructing permission. | Accept for prerequisite. Exception injection is not an abrupt process-kill proof. Real erased-input recovery and all original extraction/batch/direct-store scan boundaries remain pending with #325/#327. No new ninth recovery scenario was created. |
| CL04 / AC4, AC5, AC6 | Promote/archive/reinforce/supersede/expire/penalize nodes with source-only metadata, then erase. | All before/after and chain snapshots obey profile and erasure; fixed metadata-only erasure records do not preserve erased snapshot text or public predictable hashes. | Challenge: source-only metadata survives in before/after nodes, chain siblings, edges or TTL tombstones; historical idempotency reactivates old state. privacy/journals.py:477-587 projects typed node/edge copies and binds exact owner/scope/origin and manifest node sets; privacy/admission.py:802 includes actual chain_node_ids. Lifecycle/reinforcement/oscillation siblings invoke protected mutation and deadline checks. storage/retention.py:117 converts the protected snapshot to the identity-only tombstone. tests/test_privacy_journal_records.py:24 injects source-only metadata, performs useful promotion/reinforcement/archive/condition/supersedence/oscillation/contradiction effects and checks four snapshots plus fixed transitions; :168 checks TTL identity-only output. | Accept for prerequisite. Then-erase is not performed by these components. Erased snapshots/unavailable history and current exact contributor fences remain mandatory #325 integration; never infer a physical wipe from logical absence. |
| CL05 / AC3, AC5, AC6 | Prepare and publish profiles, daily summaries, merges and accepted/rejected alias proposals from mixed sources; inject an abandoned generation. | Published, prepared, retired and abandoned copies respect admitted-only policy and output validation; no forgotten source-only excerpts or external assessment copies. | Challenge: nested aliases preserve external assessment text; generated/retired profiles or weekly summaries retain source-only material; mixed owner/scope merges bypass compatibility. privacy/journals.py:181,224,273,302 validate admitted generated plans and sanitize outputs; :764 keeps owner/scope and existing semantic compatibility. storage/alias_proposal.py:196,213,520 retains bounded numeric assessment and admitted nodes; storage/alias_review.py:74 retains decision/manifest, not nested proposal payload. tests/test_privacy_journal_records.py:203 resumes a useful entity-UUID profile twice without private keys or new calls; :277 publishes daily/weekly/monthly and consolidation summaries, then retires; :404 exercises rollback, accepted/rejected review and compatible duplicate merge with useful positive effects. | Accept for prerequisite. External assessment/current authority are fixtures. Missing-work abandoned-generation controls are shared with CL07. Exact contributor and erasure integration, including all nested/generated dependencies, remain #325 gates; approved legacy compatibility evidence does not prove those gates. |
| CL06 / AC4, AC6 | Use a query containing a prohibited synthetic value; inspect receipt execution maps, candidate/context hashes, retry binding and citations. | Only sanitized/minimized query reaches providers/receipts; sensitive unkeyed commitments are protected/erased, not described as anonymous. Legacy intact checksum bytes remain unchanged. | Challenge: sanitized query still leaks through outer operation payload, predictable context hashes, citation identity or retry commitment; numeric replay pretends to reconstruct original execution. models/privacy_receipts.py:42,106,142 defines closed minimized records and truthful saved_numeric_scores replay. privacy/admission.py:832 guards owned receipt dependencies; privacy/retry_commitments.py:45,94 uses purpose/boundary/request/policy/retention-bound HMAC and exact-version lookup without reprovision on verify. tests/test_privacy_receipts.py:28 persists useful numeric receipts, idempotent labels and citations; rejects text answer IDs/hashes and query learning; scans query/source/context hashes. Legacy read_receipt dispatch preserves the separate old codec. | Accept for prerequisite. Real #325 receipt logical-ID/authority composition is a known unresolved integration correction, not passed by fixture-backed receipt tests. Keyed equality is private state, not anonymous data. Legacy compatibility and final affected validation remain required. |
| CL07 / AC3, AC5, AC6 | Interrupt vector/lexical staging and native snapshot save; retry and abandon one work item. | No sealed source indexed or left in prepared/abandoned content. Inspect all live SQL/native/Tantivy/pgvector copies, then verify covered erasure; physical residue limitations remain explicit. | Challenge: native staging outlives authorization, an abandoned registry row is recreated as live, or a child task inherits a parent scope as permission. privacy/journals.py:363-475 checks active lifetime and creating task, forcing children through authority acquisition; native deadlines retain the parent joined lease. storage/profile_publication.py:85,202 and profile_work.py:116,394 retain actual transaction/stage guards; profile_collection.py:183 collects exact abandoned stages. tests/test_privacy_journal_recovery.py:21 interrupts after real local staging, verifies vector/Tantivy presence then removal, reconstructs abandoned history after reopen, denies resume, and publishes a useful fresh profile; expiry denies effects without new provider calls. :121 verifies child waits for its own authority and dead parent scope is unusable. | Accept for prerequisite. The child-task assertion is evidence reused within this case, not a ninth case. These components do not complete original interrupted native snapshot-save, all live native copies and covered-erasure obligations. Native-close SIGABRT remains unresolved delivery evidence. #325/#327 gates stay open. |
| CL08 / AC1, AC2, AC3, AC4, AC5 | Inject provider/validation exceptions with marker text and inspect logging, tracing, response bodies, preview, export and explorer state. | No original/prohibited or sealed source-only text appears in prohibited sinks after success/failure. Default protected export has no keys or plaintext evidence; already delivered external copies are disclosed. | Challenge: marker-bearing exception class/text escapes via worker status, native close, queue logs or public adapters, while global log suppression hides compatibility damage. privacy/diagnostics.py:75,88,112 substitutes fixed protected events and restores context; storage/write_queue.py:104,128 carries protected diagnostic state into the independent consumer. tests/test_privacy_journal_scenarios.py:30 injects actual SQL-publication, provider/schema and native-close failures; successful retry and HTTP/MCP organizer calls remain useful; concurrent legacy logging stays visible. It scans operation rows, managed logical dump and owned local artifacts. | Accept for prerequisite. CLI/explorer/framework/preview/export paths are not newly executed here. Logical dump is not protected export. #326 export/restoration, #327 complete adapter sinks and visibility of unmanaged/server physical artifacts remain explicit limits. Already delivered copies are not revoked. |

## Evidence preserved

The final-components.log summary is 49 passed (73.28 pytest seconds), with recorded exit 0. final-compatibility.log is 154 passed and 11 skipped (51.21 seconds), recorded exit 0; skips are not PostgreSQL passes. The component harness actually exercises directory DuckDB, single-file DuckDB and dedicated PostgreSQL, real encryption/sanitation/claims/SQL/native staging/publication/cleanup. External models, clock/faults, OS get/set/delete adapter and current-policy authority are explicit fixtures. Separate PV-03 adapter and PV-13 detector evidence carries forward, without a new evaluation here.

Runtime checks ran at 3016d80dc25447a506c2c24e6db6f435ffa17d6c. The handoff records later source changes as AST-identical formatting plus static annotations/types/docs; this reviewer did not execute tests at frozen HEAD. Scoped Ruff, strict public types and contract checks are recorded passing. Contract anchors are structural evidence only. The root still owns final required full-suite/wheel/smoke/simulations/CI on the reconciled revision.

Failed logs remain failed: lifecycle-siblings-components-repair.log reached a separate count assertion failure and its later passing correction is separate evidence; original lint/type/format failures are not relabeled. The inherited mixed-legacy/AP06 failures and native-close SIGABRT remain unresolved. No retry count was reset and no repair was attempted.

## Findings and integration gates

New Fix-now findings in the declared #324 prerequisite: none established. No extra targeted execution is requested. Accepted bounded mechanisms and limits are recorded in the eight rows above; no discretionary quality follow-up was added.

The following are mandatory original integration gates, not optional follow-ups or newly invented acceptance criteria:

1. **AC1/AC2/AC6, existing #325 lock composition defect:** storage/privacy_journals.py:17 acquires the database connection/lock before invoking the policy guard. The real authority composition is already under bounded #325 repair. Resolve using the agreed actual-ledger transaction/lock integration and validate the affected effect paths before delivery. Fixture-backed passing processing tests do not dismiss the deadlock.
2. **AC4/AC6, existing #325 receipt logical-ID composition defect:** privacy/admission.py:832 and models/privacy_receipts.py:142 are the receipt/authority seam. The actual-ledger receipt identity correction remains under #325 repair. Require affected receipt/citation/relevance re-review and validation when the concrete dependency checkpoint changes.
3. **AC5/AC6, exact contributors and erasure:** privacy/journals.py:250,273,501 and privacy/admission.py:802,849 carry contributor/authority integration. Final exact-node and contributor erasure/fence behavior is not proven by this fixture authority. The root reports two unsuccessful DATA repair attempts and pending user approval for one further exact-contributor attempt. This report neither duplicates the attempts nor authorizes a third workaround. Preserve the data review's concrete findings and final affected review requirement.
4. **AC1–6, whole-case closure:** #325 must complete actual erase/current-ledger/covered-derivative gates; #326 must complete protected export/restoration and artifact gates; #327 must execute the complete original shared cases, including the separately assigned ER05 recovery obligation. The partial controls here cannot mark any full case complete or close #324. Final corrected dependencies require affected re-review.

No physical wipe, anonymity, heap zeroization, same-process isolation or revocation of already delivered external copies is inferred. PostgreSQL server WAL/base/temp files and unmanaged backups were not observed. Source absence in a byte scan of vectors does not alone prove absence of semantic derivatives.

Completion marker: REVIEW_324_ADVERSARIAL_COMPLETE
<!-- END review-324-adversarial.md -->
