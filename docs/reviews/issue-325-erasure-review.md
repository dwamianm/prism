# Issue #325 erasure review record

Refs [#325](https://github.com/dwamianm/prism/issues/325), PV-06.

## Exact reviewed identity

- Repository: `dwamianm/prism`; branch: `issue-325-erasure-ledger`.
- Original issue comparison base: `418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa`.
- Original frozen HEAD/tree: `ee620296f2af8a81fa1df2b6f428a7344fea5b2f` / `e2a4f39648e3fa3bf8bb74e1e018b766697cebe7`.
- Original snapshot: `19b0e9e70c39764b4498b4174d37e084d21fa7c69139573eccdba03fd738e723`.
- Corrected reviewed HEAD/tree: `36ddd11ac68940952a379b8bebe8730630943e22` / `e2af8f48a0ae2413dda9451a04fdfcd2ec651cd9`.
- Corrected reviewed snapshot: `fa7f6d4257e5158770d32c584b9bd793d380757f3c14c14d40fd744dad960a05`.
- Runtime-only diff SHA-256, `git diff 418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa 36ddd11ac68940952a379b8bebe8730630943e22 -- src`: `c992dd39f8141669291a29d65591b798728412170a62ac9b66d66b69a4cadef1`.
- Source-and-test diff SHA-256, same command with `-- src tests`: `b8a9f5895021d609237773dbb570cf6526cdbd57c80dd708eff5da1623035443`.
- Correction scope: 15 files, 527 additions and 42 deletions from the original frozen HEAD. Whole issue review: 41 files, 4323 changed lines.

This file is an artifact-only transport of the completed reviews. It is the sole
explicit exclusion from the review snapshot; implementation, tests and plan/results
remain included. The corrected source was normally pushed and independently
reviewed before this file was added. Adding or committing this file does not
change the reviewed source, tests or public claims.

The exact verification command, run before and after adding this artifact, is:

```sh
.venv/bin/python /Users/dwamianm/.codex/loop-tools/housekeeping.py review-plan --base 418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa --risk SECURITY --risk DATA --risk PERSISTED_FIELD --exclude docs/reviews/issue-325-erasure-review.md
```

## Original acceptance criteria and remaining gates

The following six criteria are copied from the original issue. They remain open;
a bounded correction approval does not establish whole-issue completion.

- [ ] Evidence expiry immediately denies evidence reads at the deadline even if
      purge has not run. A source-only purge explicitly reports that admitted
      facts remain; a memory-erasure request includes their affected derivatives.
- [ ] An acknowledged erasure establishes a transactional read/write fence.
      In-flight reveals revalidate; old extraction/organizer jobs cannot publish
      affected content after the fence, including after restart.
- [ ] Purge covers every live copy in [PV-01](https://github.com/dwamianm/prism/issues/319), including graph/history, summaries,
      indexes, staged work, receipts and text-bearing operation snapshots.
- [ ] Multi-source derivatives retain only content justified by surviving
      available sources or explicit admission. Uncertain separation invalidates
      the derivative, without erasing unrelated owners' records.
- [ ] Repeated requests are idempotent, interruptible work resumes, and failures
      stay visible. Metadata-only tombstones contain no deleted payload or
      publicly testable hash of predictable sensitive content.
- [ ] Public status separates denial, live purge, backup expiry and external-copy
      limitations. SQL removal/compaction is not labeled secure media wiping.
      Startup checks expiry before disclosure; offline time is not promised work.

The original validation contract remains all eight ER01-ER08 scenarios on both
backends, with directory/pack layout subchecks and shared CL04/CL05/CL07 support,
within the approved 22-backend-row limit. No ninth adversarial case, new cohort,
paid provider call or benchmark was added.

The two concrete reviewed blockers were corrected within that contract. A
node-selected memory request now durably resolves its owned retained sources
before acknowledgement, then uses that same closure for fencing, recovery,
cleanup and verification while retaining exact request identity and independent
admission survivors. Automatic native key destruction now requires durable
exclusive-use lineage evidence under the provider lock plus the existing local
reference check. A second managed store's actual evidence unwrap, pseudonyms and
retry commitments survive. Shared, legacy or unbound keys remain usable and
visibly pending at the keys stage with the erasure access fence intact. Fresh
exclusive keys still retire and retry interrupted private-part removal. The
monotonic lineage set is not complete cross-store live reference counting.

The original reviews requested changes or blocked completion. Their full reports
are preserved below, including withdrawn interpretations and corrections. The
same four required roles completed affected re-review of those concrete findings
and approved or accepted the corrected scope. This is a nonclosing prerequisite,
not a new broad review round. Core covers checklists 1/3/4/5; security covers 2;
data covers 7; adversarial covers 8. Infrastructure checklist 6 is not applicable.

Remaining gates are unchanged:

- #323 dependency composition is held after two unsuccessful classifier repairs;
  a third requires user approval. Its original full-suite/native SIGABRT evidence
  remains unresolved. The reviewed #325 source does not repair that strategy.
- #326 must supply actual ER07 pre-erasure restore/import with current independent
  authority and ER08 managed backup/operator-export and archive/chat-clear cases.
- #327 must complete the whole protected-product gate.
- Required final full suite, package/simulation checks and CI, then PR/merge and
  issue closure, remain root-owned and pending. This artifact does not authorize
  relaunching known-failing dependency CI.
- `acceptanceComplete=false` and `protected_product_ready=false` remain accurate.
  Logical purge does not certify physical media wiping, unmanaged copies,
  external revocation or rollback protection of both local authorities. Shared
  or unknown custody can remain pending indefinitely without complete authority.

## Preserved validation and repair accounting

Exact commands, logs and source timing are in
[`PV-06-results.json`](../plans/2026-10-evidence-privacy/PV-06-results.json).
The unchanged-runtime witnesses had 6 failures. The first correction command
had 9 passes and 6 failures; its independent-admission and native-custody passes
are reused explicitly while the failed source/stricter-code assertions remain
failures. The source correction then passed 6 selected ER03/ER04 cases. The final
private provenance normalization and real extracted-node selector passed all
3 ER03 layout cases. Existing native key/crypto/rotation/retry compatibility
passed 93 tests on the final provider source, before only the private resolver
and ER03 refinement. Scoped Ruff and strict public typing passed with the
recorded timing. There was no final combined 15-case rerun or full suite.

Source closure has one unsuccessful correction; custody has zero. Historical
repair counts and all failures are preserved. This transport step changes no
runtime or tests and reruns no passing validation.

## Verbatim report hashes

Each report below is included byte-for-byte from the root run directory
`.github-ticket-loop/privacy-318-279`. Hashes cover the original report bytes,
including original line endings and trailing newline, excluding this file's
transport headings and boundary markers. All eight full reports follow.

| Original report | Bytes | SHA-256 |
| --- | ---: | --- |
| `review-325-core.md` | 12491 | `26345ef4cc3d3f2a225a489053e1999101dd2e672ac4289667d6ea4c5386c96b` |
| `review-325-security.md` | 14122 | `b83cfa97bcc0ab8cf25aa4d7286f0d121f8b9e58096794e39052e1a89733f08c` |
| `review-325-data.md` | 11019 | `2431414a84e63f4eec256866432680463f6550abec0fa58abcb6f351c2e73f79` |
| `review-325-adversarial.md` | 13477 | `e37287cc60da0ea8cb9e1e539c9a8cf3022fe1e971b0850ae69590ce13b533a2` |
| `review-325-core-correction.md` | 9256 | `37fc8e2f77d16f12f13c6f8d850b6b3111eaaa4aab5b1ff8bd6b70b920861c88` |
| `review-325-security-correction.md` | 10116 | `a7b40d3439623cfd9520b7ca39ecdcfd628f9a5250becd0188cbf5abca866909` |
| `review-325-data-correction.md` | 8610 | `3eaf442e0d56f5f8a3d38dde08b1536eb6c0608281b875e44bc87466556dddff` |
| `review-325-adversarial-correction.md` | 12905 | `432b45bd8e5abe6415a035fe76d8c539868b53d5f059fdd436c72ef33fa7f285` |

## Original frozen review: core

<!-- BEGIN VERBATIM review-325-core.md -->
Status: COMPLETE
Verdict: REQUEST_CHANGES
Role: CORE, tier L; checklists 1 (correctness/premise/sentinel/runtime), 3 (quality/tests), 4 (patterns/configuration), 5 (redundancy/new symbols)
Issue: dwamianm/prism#325 / PV-06
Head: ee620296f2af8a81fa1df2b6f428a7344fea5b2f
Tree: e2a4f39648e3fa3bf8bb74e1e018b766697cebe7
Comparison base: 418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa
Review snapshot: 19b0e9e70c39764b4498b4174d37e084d21fa7c69139573eccdba03fd738e723
Worktree: /Users/dwamianm/Sites/prism/.claude/worktrees/privacy-325-retention-erasure

## Scope and method

Read the applicable home and engine/worktree instructions, installed start-issue skill/workflow and consolidated review policy, original issue body, review plan/budget, full frozen handoff, PV-06 plan/results and shared-custody constraint. Inspected the actual owned implementation diff, new modules, relevant admission/read/crypto/key/storage producers and consumers, and test fixtures/assertions. HEAD/tree were independently checked and tracked status was clean. The supplied review snapshot covers 38 files/3,806 changed lines; only the future review transport document is excluded. I did not regenerate loop state or run tests, databases, live providers, benchmarks, installs, or git/GitHub mutations. Only this report was written.

Done remains the original six ACs, ER01–ER08 on both backends with required layouts and CL04/CL05/CL07, final required checks and authorized delivery. This review may approve a nonclosing prerequisite, but cannot waive a defect in implemented behavior. Both findings below concern current source, not the still-pending #326/#327 integrations. They are static findings; no new reproduction execution is claimed.

## Blocking findings

### CORE-325-01 — P1: node-selected memory erasure leaves its source copy readable while reporting live purge

Location: `src/prme/storage/erasure.py:455-467` (target seeding), `:536-565` (source cleanup only for event targets), `:94-101` (record fence selector), and `src/prme/privacy/reads.py:503-548` (source reveal).

Original requirement: AC3 requires every managed live copy; AC2 requires covered content to be fenced after acknowledgment. The approved PV-06 design explicitly says a memory selector includes source representations that preserve the deleted payload and invalidates an inseparable representation. This is not an added source-deletion feature or a physical-residue requirement.

Trigger: use the supported `store()` path with captured evidence containing a unique admitted fact, select its returned node through `ErasureRequest(mode='memory', node_ids=(node.id,))`, then run `process_erasures()` to completion. The request accepts the owned protected node. Discovery seeds a node target but never adds the owning event as an event/source target. Its operations can be tombstoned and its graph/native copies deleted, but the event's `PrivacyRecord.evidence`, processing/retry references and event content metadata are only cleared inside the event-target branch. `_verify_purge` checks only the saved targets and can return `live_purged` without inspecting that retained source.

The access fence also misses it: `affected()` matches explicit event IDs or owner cutoff, not the source representation of node selectors. `read_source()` passes the event record and purpose='evidence', with no node identity, so current policy plus a valid diagnostic/runtime grant can still reveal the original fact. This is stronger than a residual encrypted file: it is a live, supported source read of the forgotten content. Existing tests actually encode part of the gap: `tests/test_privacy_erasure_concurrency.py:94-104` says a node-only fence leaves its source available and asserts `sealed`; `tests/test_privacy_erasure_recovery.py:81-92` verifies node removal and idempotency but not source absence/reveal denial. Those passing assertions do not discharge AC3.

Bounded correction: resolve the exact owned source/representation dependency of a node request before acknowledging it, persist that closure for both access fencing and cleanup, and invalidate the inseparable retained source while preserving independently justified admissions. If the current implementation cannot establish that coverage, refuse that selector with a fixed coverage error instead of acknowledging full memory erasure. Extend the existing ER02/ER04 node-selector subcheck to retain a positive source witness, assert post-ack source denial and post-purge source-copy absence, and keep the same-owner survivor/idempotency assertions. No new scenario family or live model is needed.

### CORE-325-02 — P1: store-local reference counting can destroy another managed store's live keys

Location: `src/prme/storage/erasure.py:610-648`, especially `SELECT body FROM privacy_admissions` at line 616 and unconditional exact-version retirement at line 648. Relevant contracts: `src/prme/privacy/keys.py:44-56,399-404`; `src/prme/privacy/local_keys.py:310-325,358-368`; `src/prme/privacy/admission.py:48-78`; `src/prme/storage/erasure.py:107-155`.

Original requirement: AC4 and its approved survivor implementation preserve unrelated evidence, pseudonym continuity and retry state; a shared purpose/epoch is retired only within the authorized scope or with no surviving managed reference. Destruction outside the selected store is also an introduced data-loss regression, irrespective of how future hosted deployment is configured.

Trigger: independently open two fresh stores A and B through the current engine API, using the same project/owner/scope, public registry/custody and active wrapping version, with distinct persisted store identities. A shared independent ledger may register both. Neither runtime preflight nor store preparation binds the custody/version exclusively to one store. The public registry's writer lookup is only `(KeyBoundary, purpose)` and KeyBoundary has no store identity, so both accepted stores can seal independent admissions with the same version. Erase A's last reference. `_retire_keys` sees no reference in A, inventories the shared custody, and calls `retire(version)`. That method marks the version retired and removes native private parts. B's untouched live ciphertext then cannot be unlocked through custody. A can still finish successfully because verification never checks B. The same ambiguity applies to shared pseudonym epochs.

This is an accepted current construction, not a hypothetical implementation of a future global key service. The documentation's instruction to use distinct boundaries does not enforce ownership, and the public runtime/store identities permit the unsafe construction. The supplied constraint correctly admits this lack of proof but cannot waive the destructive behavior. `tests/test_privacy_erasure_retirement.py` proves same-store references; the separate-store control in `tests/test_privacy_erasure_recovery.py:103-114` provisions a separate policy/custody and therefore does not cover shared-version survival.

Bounded correction: require durable exclusive managed-lineage ownership or an authoritative complete reference decision before destructive retirement; otherwise refuse/defer the keys stage with a visible failure and retain the existing access fence. Do not invent a global service or silently label ambiguity complete. A bounded regression can reuse the existing ER02 retirement fixture with a second independently opened store sharing the version and verify its actual unwrap/retry/pseudonym survivor, plus the refusal/status behavior. That proof was not run here.

## Checklist coverage and evidence

- Correctness/premise: archival is not reused as erasure; explicit source/processing/memory modes and metadata-only tombstones are appropriate. Ledger intent precedes local acknowledgment; current entries apply before effects and startup recovery. Same-task reentrancy does not lend a lease to inherited child tasks. Cancellation uses existing joined-work helpers. Exact node/chain mutation wiring and receipt lock-order changes were traced. The two findings show where selected-identity and reference-domain premises still fail.
- Runtime/sentinel contracts: missing runtime/ledger or stale lineage denies; old protected admissions without binding require migration; completed node request retries consult the owned exact durable request before nonexistent live nodes. Public status truthfully avoids physical-wipe and verified-backup claims. Engine, synchronous client, CLI factory ownership and PostgreSQL namespace/catalog plumbing are wired. Existing legacy operations/checksums are not globally rewritten.
- Configuration/patterns: compared the full relevant protected/default, directory/single-file/PostgreSQL, bound/unbound, optional-custody and CLI engine-factory/raw-legacy branches. No config.py/retrieval-default change or deployment is present. Uses the existing authority, sanitizer, crypto, typed publication codecs, shared SQL sessions and thread-completion helpers. Native vectors reconcile against durable payloads; lexical swaps register exact artifacts and preserve unrelated stages. No replacement cipher or extra provider service was introduced.
- Persisted additions were traced at a core screening level: store head and jobs have live ledger/apply readers/writers; discovery/SQL progress, artifact state and epoch participate in real purge/recovery; evidence_disposition is written by purge and read by availability; pseudonym_epoch is captured at admission and consumed by retirement. Dedicated DATA review remains independent.
- Tests use real production sanitizer/crypto/custody/HMAC/ledger/SQL/native code with explicit model/policy/OS-adapter fixtures. ER02 creates actual mixed summaries, receipts/citations, reinforcement and foreign-owner positives. CL04/05 invoke existing real workflows, then scan logical tables. CL07 tests actual staging, persistent I/O failure, cancellation and retained pending stages. ER04 preserves failed acknowledgment evidence and retries; ER06 uses actual stale native snapshots and rebuild controls. These are substantial component/integration evidence, not whole-product completion.
- Read the actual preserved passing logs: final-copy composition 80 passed/1 existing local-process-only PG skip; staged positives 3 passed; retirement/CLI/control 12 passed/6 deselected; node retry 3 passed/6 deselected; strict public consumer types and scoped Ruff passed. Exact commands and timing are in PV-06-results.json. The 80-pass result predates the private 17-line retry fix; only affected ER04 and scoped Ruff were rerun afterward. I do not label those older checks a final full-suite pass.
- Read preserved failure traces for private-part retirement, completed-node retry and receipt/survivor closure. Native retirement and node retry each used one successful correction. Receipt closure used two correction cycles, one unsuccessful, then passed. The empty deadlock log remains incomplete, not passing. Setup/budget failures remain recorded. No failure was relabeled or hidden by this review.

Nonblocking quality follow-up: `_node_identities` in `src/prme/storage/erasure.py:334-354` has only recursive self references and no current caller; `_allocated_nodes` supplies the live typed-codec path. Removing that unused helper would reduce ambiguity but is not an acceptance blocker and should not expand this ticket's required repair scope.

## Remaining gates and limits

ER07 actual restore/import and ER08 actual backup/export/archive/chat-clear integrations remain incomplete in #326. #327's whole-product gate remains incomplete. Required full suite/package/simulation/CI checks remain root-owned. The #323 mixed-scope classifier has two unsuccessful repair cycles and no approval for a third; this review performed no repair or validation of another attempt. The earlier full-suite SIGABRT remains unresolved, with allocator guard-corruption evidence; unchanged 3+6-test probes do not clear it. Those limits cannot be converted into whole-suite success.

No new broad review round, agent, paid/live provider call, benchmark or scenario cohort is requested. Resolve the two concrete findings within the existing bounded workflow, reuse the affected reviewers/evidence, and retain every original integration/delivery gate. This checkpoint is not approved even as a nonclosing prerequisite until its current source-copy and destructive-retirement behavior is corrected or safely refused.

REVIEW_325_CORE_COMPLETE

<!-- END VERBATIM review-325-core.md -->

## Original frozen review: security

<!-- BEGIN VERBATIM review-325-security.md -->
Status: COMPLETE
Verdict: REQUEST_CHANGES
Review: Independent SECURITY, tier L, checklist 2
Issue: dwamianm/prism#325 (PV-06)

## Reviewed snapshot and authorization

- Worktree: /Users/dwamianm/Sites/prism/.claude/worktrees/privacy-325-retention-erasure
- HEAD: ee620296f2af8a81fa1df2b6f428a7344fea5b2f
- Tree: e2a4f39648e3fa3bf8bb74e1e018b766697cebe7
- Comparison base: 418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa
- Snapshot: 19b0e9e70c39764b4498b4174d37e084d21fa7c69139573eccdba03fd738e723
- Root supplied the frozen, remote-verified checkpoint and 38-file/3,806-line plan. Only future review transport docs/reviews/issue-325-erasure-review.md is excluded; no source/test exclusion.
- One authorized independent security review. Source and evidence were read-only. No tests, DB access, installs, providers, benchmarks, extra agents, git/PR mutations, or repair cycles were performed. This report is the only owned write.

Done for this review means adjudicating implemented security behavior against the original six ACs and recording a verdict, not certifying whole-ticket completion. Read the original issue, accepted PV-06 plan/results, full frozen handoff, applicable instructions, start-issue workflow and review policy, and shared evidence/custody manifests. ER01–08 and CL04/05/07 remain the original finite obligations.

## Blocking finding S1: retirement lacks authority over all consumers of the selected key

Severity: High. Classification: Fix now. Original requirement: AC4 survivor preservation and the approved plan's explicit requirement to preserve unrelated evidence, pseudonym continuity and retry state during scoped retirement. This also introduces a concrete availability/data-loss security issue through a destructive capability whose scope exceeds the checked store.

Locations:

- src/prme/storage/erasure.py:616 reads only the current repository's privacy_admissions; :625 and :627 derive retirement candidates from that local absence; :648 calls custody.retire(version).
- src/prme/privacy/keys.py:44 defines KeyBoundary as project/owner/scope, with no store lineage. KeyRegistry.writer at :399 selects the active wrapper by boundary/purpose, not by store.
- src/prme/privacy/local_keys.py:310 provisions the shared boundary/purpose wrapper; :358–367 marks an exact version retired and removes its native private parts. Pseudonym lookup at :419 uses the same boundary plus epoch, again without store ownership.
- src/prme/storage/erasure.py:104–172 binds ledger/store identity but does not bind key-custody ownership to that lineage. There is no refusal before the destructive call when exclusive ownership is unproven.

Trigger: open two independent live protected stores A and B under the same project/owner/scope using the same NativeKeyCustody/public registry, with each retaining evidence under its active wrapping version. These are inputs accepted by the current interfaces; fresh stores obtain independent ledger store IDs. Erase A's final local reference and run its key stage. A's admissions scan finds no remaining reference, so the shared version is retired even though B retains live evidence. Sharing a pseudonym epoch has the analogous continuity failure. No malicious process, physical access, rollback or unmanaged backup is needed.

Impact: future native unlock/key lookup for B is refused and the underlying shared private key is removed; B's still-authorized evidence becomes unavailable, and shared pseudonym continuity is lost. A can subsequently report live_purged because verification only checks its own targets. An already-unlocked handle is not a guarantee of future recoverability. Separate ledger locks, current-policy authorization for A, and a same-store survivor test do not establish destructive authority over B's key use.

Evidence level: static execution-path proof, not a new runtime reproduction. No cross-store shared-custody experiment was run or claimed. The existing retirement test at tests/test_privacy_erasure_retirement.py:23–83 verifies two records inside ONE engine, including last-reference failure/retry; it does not disprove this path. The separate-store native control has different provider/store setup and is not a shared-key ownership test. The handoff and shared-custody constraint already acknowledge the missing proof, but documentation cannot make the destructive implementation safe.

Bounded alternative: before calling retire, require a verifiable exclusive managed-lineage ownership capability for that key/epoch, or a complete authorized reference authority serialized with all relevant admissions. If neither is available, refuse the destructive key stage with visible pending/failure status while keeping the access fence. The minimal safe alternative is refusal; this review does not authorize a global service, new cohort, or extra repair round. Do not mark the managed key stage successful by merely documenting shared use as unsupported while accepting it without an enforceable ownership guard.

## Blocking finding S2: node-selected memory erasure leaves the covered source readable

Severity: High. Classification: Fix now. Same root cause as CORE-325-01. Original requirements: AC2's acknowledged read/write fence and AC3's live-copy coverage, as expressly specified by approved PV-06-plan.md:92: "A memory selector includes covered source representations that would preserve the deleted payload; uncertain separation invalidates that representation and affected derivatives." AC6 also requires truthful live-purge status. This is an original obligation, not a stricter exploratory criterion.

Locations and trigger: create a directly admitted memory whose retained source contains that memory, then request mode=memory using only its node_id. ErasureCoordinator.request checks node ownership but does not enroll the covered source. In src/prme/storage/erasure.py:94–101, affected() matches only explicit event_ids or owner_all; node selection does not deny the source record. Discovery at :455–467 seeds node targets but adds event targets only for explicit event selection or owner cutoff. SQL cleanup at :536–565 clears evidence/processing slots and retained event content only for event targets. Consequently deleting the node and replacing its operation copies leaves its original source representation retained. read_source at src/prme/privacy/reads.py:506–525 can still open that evidence with an otherwise valid current grant, because its exact-record guard is evaluated against the unchanged source record. Verification does not require this missing event target and can permit live_purged (:704 onward; completion at :688–691).

Impact: an acknowledged memory deletion does not fence the source containing the same deleted payload, and subsequent maintenance can report live purge while that managed representation remains decryptable. This is not the permitted external-copy or media-residue limitation. Exact node/chain mutation denial does not compensate for the source disclosure path.

Evidence level: bounded source-only reconciliation; no new execution. tests/test_privacy_erasure_concurrency.py:100–101 explicitly asserts sealed source availability after node-only acknowledgement, and tests/test_privacy_erasure_recovery.py:88–90 asserts completed node-only purge/retry without checking the covered source. Those assertions establish the fixture's intended behavior, not acceptance compliance. My initial report failed to reconcile that test intent with the quoted approved plan; that dismissal is withdrawn.

Bounded alternative: resolve and persist the covered source-representation dependencies under the existing owned request/ledger fence before acknowledgement, apply those dependencies to evidence/processing read and publication guards, and carry them through purge/verification. Preserve only source content with proven separability; otherwise invalidate the covered representation and affected derivatives as the approved plan requires. If that coverage cannot be safely established, refuse the node-only memory request rather than acknowledge an incomplete fence. Any correction and affected validation remain root-owned and subject to the existing repair budget; this reconciliation authorizes neither.

## Checklist 2 coverage and findings not promoted to blockers

- Current authorization and tenant boundaries: inspected explicit owner/scope selection and real record/node resolution in ErasureCoordinator.request (:268–300), effective policy checks and composed AdmissionCoordinator.guard (:295), owner-filtered status/job reads, and exact request retry before resolving a now-deleted node. Request metadata is opaque; conflicting identity reuse refuses. Configured engine/module factory remains an explicit trusted operator capability, not an untrusted plugin upload.
- Current ledger and startup: inspected external ledger O_EXCL creation, existing-file open, durable intent-before-ack, sequence/token continuity, project checks and lease serialization. prepare_store precedes schema registry recovery/native opening in DuckDB, PostgreSQL and workspace namespace wiring. Missing runtime/ledger and old protected admissions without binding fail closed. This is local independent authority, not proof against rolling back both pack and ledger.
- Actual effects and sibling paths: traced source reveal, ordinary read projection, embedding/extraction transmission, profile/consolidation staging, lifecycle/chain/alias mutations, receipt SQL, index save/close/rebuild, and CLI inspect/unlock/rotation. Actual resolved records, manifests and node dependencies feed the guards. Receipt authority precedes connection acquisition and lifetime checks run inside the transaction. Unsupported protected effects remain denied.
- Async scope: inspected task-identity-aware reentrancy in ErasureCoordinator.guard and run_async_to_completion/run_to_completion ownership. Inherited ContextVars do not themselves grant a child the parent's lease. Joined native work retains locks while cancellation waits for completion. Native fence/SQL reconciliation and registered lexical swap recovery handle the tested stale-save and interruption paths. This does not claim hostile same-process isolation or revocation of returned copies.
- Erasure dependency handling: inspected exact target persistence, fixed-point discovery, typed allocated outputs, full original explicit-admission survivor comparison, SQL copy cleanup, native verification and resumable stage/status writes. Source-selected memory erasure removes source slots and affected memory copies. Node-only source retention conflicts with the original approved source-representation coverage requirement; see S2. The initial review incorrectly treated the explicit test intent as dispositive. A test cannot waive the approved requirement.
- Secrets, diagnostics and truthful outcomes: CLI protected raw-repository access refuses without an engine; guarded unlock/output uses exact records and closes owned engines/private wrappers. New failure/status fields use fixed codes, not raw exceptions or deleted content. ErasedOperation replaces old payload/checksum with explicit unavailable values. No new raw secret logging or public predictable payload hash was identified in this change. Public status distinguishes logical live purge, unmanaged backup/external copies and physical_wipe=false; it does not emit verified backup completion here.

## Shared validation evidence and limits

Inspected actual preserved log output and PV-06-results commands, plus corresponding source assertions for expiry, concurrency, exact node fences, CLI/native-read denial, interrupted native stages, restart, same-store key survivors and private-removal retries. Root's byte index confirms all 31 preserved logs match originals; this reviewer did not rerun them.

- Final composition: 80 passed, 1 existing local-process-only PostgreSQL skip. That skip is not an ER/CL backend-row pass and does not erase pending integrations.
- Native staged survivor: 3 passed, including actual unrelated pending stages through compaction/rebuild.
- Retirement/CLI/control: 12 passed, 6 deselected. Native custody, cryptography, HMAC, independent LocalErasureLedger and SQL/native effects are production code; policy/model inputs and OS keyring adapter are fixtures.
- Final private node-request retry correction: 3 passed, 6 deselected, plus scoped Ruff. Earlier strict public types are reused because public signatures did not change. Most broad runtime evidence precedes that private correction; no final full-suite claim is made.

All original failed/stalled evidence remains in PV-06-results and the preserved logs. Receipt closure's second correction succeeded after one unsuccessful cycle; native private removal and node-only retry each passed their first correction. This review changes none of those counts. Dependency #323 has two unsuccessful repair cycles, actual CI failures and a full-suite native SIGABRT; no third correction or validation is authorized here. Allocator guard-corruption detection does not identify its origin, and passing unchanged small probes do not clear the original failure.

Actual #326 ER07 restore/import and ER08 retained backup/operator-export integration, #327's complete product gate, final dependency reconciliation and root-owned full/package/CI/delivery checks remain pending. This checkpoint is a nonclosing prerequisite, and protected_product_ready remains false. No physical wipe, global backup revocation or same-user isolation is certified.

## Final disposition

REQUEST_CHANGES for S1 and S2 on the exact snapshot above. S2 was added by the authorized bounded source-only reconciliation of the original approved requirement; the initial node-only dismissal is explicitly withdrawn. Resolving S1 requires an enforceable ownership/refusal boundary, and S2 requires covered-source fencing and cleanup or refusal. Neither can be waived by documentation or test intent. Completion of this review does not close the original six acceptance criteria or pending whole-case gates.

REVIEW_325_SECURITY_COMPLETE

<!-- END VERBATIM review-325-security.md -->

## Original frozen review: data

<!-- BEGIN VERBATIM review-325-data.md -->
# #325 data and persisted-field review

Status: COMPLETE
Checklist: 7, DATA / PERSISTED_FIELD
Verdict: REQUEST_CHANGES
Base: `418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa`
Reviewed HEAD/tree: `ee620296f2af8a81fa1df2b6f428a7344fea5b2f` / `e2a4f39648e3fa3bf8bb74e1e018b766697cebe7`
Reviewed snapshot: `19b0e9e70c39764b4498b4174d37e084d21fa7c69139573eccdba03fd738e723`

This is an independent review of the frozen implementation, not acceptance or product readiness. The original six issue criteria and ER01–ER08 plus CL04/05/07 remain the contract. ER07/08 require actual #326 restore/export integration, and #327 owns the complete product gate. I read the engine/worktree instructions, review policy, start-issue workflow, frozen handoff, approved PV-06 plan/results, source, relevant consumers and preserved logs. I did not run code, tests, databases, installs, or live services.

## Blocking finding

**D1 — destructive provider retirement can break unrelated live content in a second managed store (AC4; static source trace, not an executed cross-store test).** `KeyBoundary` contains only project, owner, and scope, not store lineage (`src/prme/privacy/keys.py:44-56`). The active registry selects one version by that boundary and purpose (`src/prme/privacy/keys.py:374-404`); protected preflight validates the project and namespace and ledger path, but imposes no exclusive custody/store binding (`src/prme/privacy/admission.py:48-75`). Independent unbound stores register distinct store IDs (`src/prme/storage/erasure.py:104-140`), so sharing the same runtime custody/registry across two stores under one project, owner, and scope is supported by these interfaces. After erasing A's last local reference, `_retire_keys` scans only A's `privacy_admissions` to decide that the selected version is unused (`src/prme/storage/erasure.py:608-648`). `NativeKeyCustody.retire` then marks that exact version retired and deletes its native private part (`src/prme/privacy/local_keys.py:358-367`); decrypt requires the private key (`src/prme/privacy/local_keys.py:369-384`, `src/prme/privacy/crypto.py:200-218`). A surviving ciphertext in B still names that version and becomes unreadable. The same local-only gap applies to pseudonym epochs and retry key versions. This violates the original multi-source/unrelated-survivor requirement, rather than introducing a new requirement. The same-store positive in `tests/test_privacy_erasure_retirement.py:19-65` proves only local reference preservation; the separate-store native control in ER06 uses a distinct policy/provider and does not exercise shared custody.

Bounded correction: before `retire(version)`, require proof that the version/epoch belongs exclusively to this managed store or that every authorized managed reference to it has been counted. When that proof is unavailable, refuse destructive retirement and leave the request visibly pending with `provider_unavailable`. Do not claim a globally safe provider inventory from `KeyBoundary` or a local zero-reference scan. A focused two-store shared-version positive and negative proof would validate the correction; no new service or broad evaluation cohort is implied. This is also recorded as a still-open integration constraint in the shared-custody artifact, which is evidence, not approval or a waiver.

## Runtime writer → compatible reader map

| Durable item | Non-migration runtime writer | Reader/retry consumer and compatibility |
| --- | --- | --- |
| External SQLite `identity.ledger_id`, `stores.store_id/project_id/sequence/token`, and `entries.store_id/sequence/request_id/body` (`LedgerEntry` schema 1, intent/head/token chain) | Explicit fresh provision and store registration in `src/prme/privacy/ledger.py:35-59,98-108`; `append_intent` inserts the entry and advances the head in one immediate SQLite transaction at `:143-163`. | Existing-file constructor checks identity at `:24-33`; `current_head`/`entries_since` read ordered entries and validate sequence, previous token, ledger/store identity, and final head at `:110-141`. Repeated request identity compares the saved request/owner at `:152-157`. Missing/stale ledger refuses. |
| Pack/PG `privacy_store_state.project_id/body` (`LedgerHead`) and `privacy_erasure_epoch.epoch` | `prepare_store` registers a fresh unbound store and writes the head/epoch at `src/prme/storage/erasure.py:126-133`; `ErasureRepository.apply` advances the saved head and epoch with the job in one local transaction at `:52-71`; SQL/native mutations advance epoch at `:599` and `src/prme/storage/erasure_indexes.py:27-39`. | `state` parses the head at `src/prme/storage/erasure.py:36-43`; `prepare_store`, `apply_current_ledger`, and effect guard compare lineage/project and replay independent entries at `:134-189,211-225`. Native fencing consumes the changing epoch. Existing protected admissions without binding refuse migration at `:111-125`. DuckDB and PG factory calls occur before native/registry recovery at `src/prme/storage/engine.py:640-644,838-845`; PG namespace/catalog passes the bound identity at `src/prme/storage/pg/namespace.py:117-125` and `workspace_catalog.py:62-74`. |
| `privacy_erasure_jobs.request_id/owner_id/scope/intent/status` (`ErasureIntent`, `ErasureStatus` schema 1) | `apply` inserts blocked status under the independent entry at `src/prme/storage/erasure.py:52-71`; `save_status` persists stage/failure/attempts at `:88-91` and processing transitions at `:651-700`. | `intents`, `status`, and `existing_request` parse saved JSON at `:45-50,73-86`; exact completed request retry compares the saved request before resolving a deleted node at `:268-300`; `process_erasures` reads status and resumes its stage at `:658-700`. Status defaults in `src/prme/privacy/erasure.py:84-96` make omitted optional fields readable. |
| `privacy_erasure_targets.request_id/kind/object_id`, `privacy_erasure_progress.request_id/stage/body` | Exact roots/closure and provider references insert durable targets at `src/prme/storage/erasure.py:387-389,447-510,542-550`; discover and SQL stages save keyset cursors at `:435-444,447-510,519-535`. | `_targets` and `_progress` load them at `:378-384,435-438`; SQL deletion/operation replacement consumes saved target pages at `:519-600`, native cleanup consumes node targets at `src/prme/storage/erasure_indexes.py:222-253`, and verification revisits saved targets at `src/prme/storage/erasure.py:704-755`. Progress and targets commit together in each transaction. |
| `privacy_erasure_artifacts.request_id/artifact_id/state` and deterministic native temporary/retired names | Purge records a prepared artifact, compacts, and marks complete at `src/prme/storage/erasure_indexes.py:222-253`; rebuild does the same at `:310-323`; exact native path names/swap are at `:151-199`. | Startup `recover_lexical_swaps` reads incomplete artifact IDs before opening the native index at `:202-220`; purge retry uses the same request-derived artifact ID at `:240-253`. This is logical artifact recovery, not proof of wiping old pages or backups. |
| `PrivacyRecord.evidence_disposition` and `pseudonym_epoch` in existing `privacy_admissions.body` | Admission writes a source epoch at `src/prme/privacy/admission.py:383-386`; SQL purge writes disposition and clears epoch at `src/prme/storage/erasure.py:537-559`. | The existing admission repository parses the record at `src/prme/storage/privacy.py:281-291`; source availability reads disposition at `src/prme/privacy/reads.py:264-281`; key retirement reads the live epoch at `src/prme/storage/erasure.py:608-624`. Both added fields default to omitted `None` (`src/prme/privacy/records.py:46,54`), preserving old record reads/bytes. Old pseudonymized records without an epoch refuse complete memory/key purge at `src/prme/storage/erasure.py:544-546`; no silent migration is claimed. |
| `PRIVACY_ERASED` operation payload (`ErasedOperation` schema 1) and sanitized event reservation | SQL purge replaces affected operation type/payload at `src/prme/storage/erasure.py:590-599` and empties erased event content/hash/metadata at `:560-565`. | Discovery excludes already erased operations at `:489`; verification requires the replacement at `:732-735`; `ErasedOperation` fields deliberately make replay/checksum unavailable (`src/prme/privacy/erasure.py:99-110`). Legacy untouched operation records retain their codecs and are selected by runtime closure, rather than rewritten wholesale. |

The table maps live writers and readers, not just DDL or structural manifests. `privacy_erasure_targets` and progress are separate from content-bearing stores; the closure uses actual operation payloads, node metadata/evidence references, direct-store codecs, and receipt logical request IDs (`src/prme/storage/erasure.py:311-431,447-510`). Native vector reconciliation treats current SQL payload ownership as authoritative and refuses missing payloads (`src/prme/storage/erasure_indexes.py:42-60`); lexical compaction enumerates actual indexed documents and preserves unrelated staged additions (`:91-199`). PostgreSQL vector/lexical data are in SQL and removed with nodes (`src/prme/storage/erasure.py:568-578`). These paths support the stated logical-copy claim subject to D1 and the pending integrations.

## Evidence and limits

The byte-verified preserved `privacy325-final-copy-composition-1.log` ends `80 passed, 1 skipped`; its original command in `PV-06-results.json` exercised ER02/04/05/06 and CL04/05 before the final node-only retry change. `privacy325-staged-positive-1.log` ends `3 passed`; `privacy325-retirement-cli-control-2.log` ends `12 passed, 6 deselected`; `privacy325-native-cli-exact-1.log` ends `12 passed`; `privacy325-receipt-second-and-alias.log` ends `6 passed`. The final private retry correction has its own `privacy325-node-retry-2.log` result of `3 passed, 6 deselected` on directory, pack and PG; the earlier `privacy325-node-retry-1.log` records three failures, and the 80-pass composition was not rerun on final HEAD. The 1 skip is the existing PG local-process-only case, not an ER/CL backend row. Current-policy/model and OS keyring adapters are explicit fixtures while local ledger, SQL/native effects, sanitizer, crypto, custody and HMAC run. I inspected the actual test assertions and logs; I performed no independent execution.

Preserve the original failed/stalled evidence: receipt closure took two correction cycles, one unsuccessful, and the deadlock's empty log is incomplete; native deletion and node retry each passed after their first correction. The CL05 PG default-budget failure is retained, followed by a pass with an explicit 60000 ms fixture budget without a proven causal explanation. Full suite/CI, #323's unresolved dependency failures/native abort, ER07/08, and #327 remain pending. Backups, exports, recovery artifacts and physical SQL/native residue are outside this logical-copy proof and must stay visible in public status. No additional test or repair cycle was run in this review.

REVIEW_325_DATA_COMPLETE

<!-- END VERBATIM review-325-data.md -->

## Original frozen review: adversarial

<!-- BEGIN VERBATIM review-325-adversarial.md -->
# #325 PV-06 adversarial review (checklist 8)

Status: COMPLETE
Verdict: BLOCKED against original acceptance; component evidence is useful but does not establish completion.

## Reviewed identity and bounds

- Worktree: `/Users/dwamianm/Sites/prism/.claude/worktrees/privacy-325-retention-erasure`
- HEAD: `ee620296f2af8a81fa1df2b6f428a7344fea5b2f`
- Tree: `e2a4f39648e3fa3bf8bb74e1e018b766697cebe7`
- Comparison base: `418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa`
- Exact review snapshot: `19b0e9e70c39764b4498b4174d37e084d21fa7c69139573eccdba03fd738e723`
- `git rev-parse` confirms HEAD/tree; `git status --short` was empty. Snapshot is the supplied frozen review-plan identity, not a newly computed helper result.
- Reviewed applicable AGENTS.md, start-issue skill and workflow, consolidated review policy, issue/plan/budget, frozen handoff, results, shared evidence index and custody constraint. Applied the explicitly assigned read-only reviewer subset of that workflow.
- Exactly ER01–ER08 below; CL04/05/07 evidence supports those rows, not additional scenarios. No tests, databases, installations, benchmarks, live calls, GitHub changes, implementation changes or subagents. Only this report was written.

Done remains the six original ACs: deadline denial and source/memory distinction; transactional read/write fences; all inventoried live copies; justified survivors; idempotent recoverable work with safe tombstones; honest live/backup/external/physical status and startup gating. Validation remains the original eight cases on DuckDB and PostgreSQL (directory and single-file are DuckDB layouts), prescribed shared-copy checks, final project/package/full/CI gates and delivery. This review does not add acceptance criteria.

## Actionable findings within the original cases

### A1 — Fix now / high: node-only memory erasure retains a readable source copy (ER02; AC2/AC3)

`src/prme/storage/erasure.py:454` seeds events only from explicit event selectors and nodes only from node selectors. The event scan at line 461 adds events only for owner-wide selection. Operations reached from a node at lines 495–506 add operation/copy/allocated-node targets, never its original source event. Consequently a direct stored memory selected by `node_ids` has its node/journal removed, but its privacy admission evidence/processing slots and event representation are not cleared: clearing is exclusively inside the event-target loop at lines 535–567. `_verify_purge` at lines 714–734 verifies only saved targets, so this omission can still yield `live_purged`.

The fence also misses that retained source: `affected` at lines 94–101 recognizes event selection or owner cutoff, not the selected node's source. `privacy/reads.py:506–547` opens evidence by event and calls that record-only guard. An authorized source reveal can therefore recover the deleted text from the surviving envelope; no deleted graph node is needed. This is a source-traced consequence, not a newly executed reproduction.

Original plan `docs/plans/2026-10-evidence-privacy/PV-06-plan.md:92` explicitly says a memory selector includes source representations preserving deleted payload and invalidates uncertain separation. The new `tests/test_privacy_erasure_concurrency.py:94–101` instead documents source retention and asserts `sealed`; the passing node-only retry check at `tests/test_privacy_erasure_recovery.py:83–90` verifies node removal/status, not source disposal. These narrower assertions cannot revise the original acceptance contract.

Bounded correction: derive the covered source/representation closure for a node selector before acknowledging its fence; invalidate inseparable representations and their affected derivatives, preserving independently justified controls. Extend the existing ER02 node-selection subcheck on its existing layouts to prove preexisting source content, immediate reveal denial and post-purge envelope/event/processing absence, plus unchanged independent survivors. Do not count this as a ninth case. No repair authorized or performed by this review.

### A2 — Fix now / high: local last-reference scan can destroy a surviving managed store's keys (ER02; AC4 and original provider-disposal requirement)

`src/prme/storage/erasure.py:608–625` computes live key versions/epochs solely from the current repository's `privacy_admissions`, then lines 626–648 retire selected-minus-local-live versions and matching pseudonym epochs. `privacy/local_keys.py:358–368` marks the exact shared provider version retired and removes its native private material. It has no store-reference check.

This is an accepted configuration path, not merely a hypothetical future hosted service: `privacy/keys.py:44–55` binds keys to project/owner/scope, and `writer` at line 399 selects by that boundary and purpose. Fresh stores register distinct IDs at `storage/erasure.py:127–132`; runtime preflight (`privacy/admission.py:48–79`) checks policy/project and external ledger placement, not exclusive custody ownership. `privacy/ledger.py:98` supports separate store registrations; no custody-to-store exclusivity is established. Thus two live stores can accept the same runtime/key boundary/version. Erasing the last local reference in A retires B's still-needed evidence/processing key; shared pseudonym or retry keys are also vulnerable. An owner-wide request is scoped to A's store intent and does not authorize loss in B.

The same-store positive test `tests/test_privacy_erasure_retirement.py:19–75` proves local reference preservation and failure retry only. The separate-store native control at `tests/test_privacy_erasure_recovery.py:109–113` creates a fresh fixture/runtime/custody and does not establish shared-custody survival. The handoff and shared constraint correctly call this unresolved, but documentation cannot waive an actual permitted managed-survivor loss.

Bounded correction: establish exclusive managed lineage for destructive retirement, obtain a complete authorized shared-reference decision, or fail closed on retirement without such proof. No global key service is demanded. Validate the original ER02 survivor obligation with the existing two-store control configured to share actual custody/version, including retained evidence/pseudonym/retry availability. Source tracing proves the missing protection; no cross-store destruction test was run here. Resolve with existing #321/#326 integration ownership before claiming AC4, without silently reclassifying accepted live stores as external backups.

## Exactly eight original scenarios

| Case | Source trace and actual preserved evidence | Verdict and limits |
|---|---|---|
| ER01: exact deadlines and startup | `privacy/reads.py:506–528` checks evidence deadline before opening; `privacy/admission.py:306` checks memory/processing deadlines. `storage/erasure.py:143–170` applies due intents before recovery; engine entry points call `prepare_store` at 642/839. `tests/test_privacy_expiry.py:22` exercises pre-deadline, exact deadline with ciphertext still present, memory deadline and reopen. `privacy325-purge-expiry-checkpoint-1.log`: 9 passed. | Accept component evidence for AC1/6; not whole-product or final dependency validation. No offline scheduler/physical wipe claim. |
| ER02: source-only versus memory, copy closure and survivors | `_discover_step`, `_independent_direct_support` (line 392), SQL purge and keys trace actual direct admission proof, uncertain derivative invalidation, receipt logical IDs and tombstones. `privacy325-receipt-second-and-alias.log`: 6 passed, 9 deselected; `privacy325-retirement-cli-control-2.log`: 12 passed, 6 deselected; final composition: 80 passed/1 skipped. CL04/05 exercise actual lifecycle/profile/summary/alias copies. | Fix now A1/A2. Event-selected and same-store positives do not establish node-source closure or cross-store shared-custody safety. |
| ER03: stale work/reveal and cancellation fences | `ErasureCoordinator.guard` lines 199–225 binds lease to current task, checks before/after; request lines 268–300 appends independent intent before local apply and settles async commit. Read guards and `publication_guards` consume actual records/node IDs. `privacy325-er03-1.log`: 3 passed; `privacy325-native-cli-exact-1.log`: 12 passed, including actual paused work/diagnostic/child-task and CLI components. | Accept demonstrated fence components; A1 leaves source-read fencing incomplete for node-selected payload. Native concurrency evidence is not a full-suite replacement. |
| ER04: repeat/conflicting requests, staged interruptions and visible failures | `existing_request` at line 79 and request lines 279–285 handle completed node-only retry before resolving deleted nodes. Stage transitions/save failures at lines 651–701 and exact custody retirement retry preserve failures. `privacy325-er04-2.log`: 3 passed; native fault/staged-positive logs: 12 and 3 passed; final correction `privacy325-node-retry-2.log`: 3 passed/6 deselected. | Accept bounded retry/fault components. Node-only status success still has A1's coverage omission; successful repeated status is not all-copy proof. |
| ER05: deleted mutable work cannot resurrect preparations | Startup applies ledger and purges SQL before registry recovery (`storage/erasure.py:104–171`); erased operations use an unavailable envelope. `tests/test_privacy_erasure_recovery.py:219–281` seeds actual profile/extraction/direct preparations, deletes mutable trackers, reopens, invokes processors, verifies no new provider sends and an independent survivor. Included in 80-pass composition. | Accept exercised recovery components. Final #323 composition, all original recovery obligations and #327 remain open; no claim that a small recovery probe clears prior suite failures. |
| ER06: stale native artifacts/handles, rebuild and survivors | `storage/erasure_indexes.py` reconciles vectors from SQL, compacts actual lexical documents and exact registered swap paths; save/close fencing and attach reconcile before exposure. Recovery test line 103 covers stale file/BLOB, live native handles and real PG in-row vector/lexical paths. `privacy325-er06-3.log`: 3 passed/3 deselected; composition 80/1; `privacy325-staged-positive-1.log`: 3 passed. | Accept demonstrated native components and staged survivors. PG has no claimed DuckDB rebuild. Separate-store native control does not resolve A2. SQL/native logical absence is not media/WAL/free-space erasure. |
| ER07: actual pre-erasure restore/import and independent authority | `prepare_store` and `apply_current_ledger` supply lineage/current-head hooks; ledger checks checkpoint chain and current independent authority. These are prerequisites. | Pending #326 actual restore/import, snapshot-only/stale/missing/wrong-lineage refusal and restored-survivor verification. No whole-case pass is claimed. |
| ER08: archive/chat-clear distinction, retained backup/export and honest status | `privacy/erasure.py:84–96` separates live, backup, external status and fixes `physical_wipe=False`; processing emits `live_purged`, not verified backup completion. CLI components appear in the 12-pass log. | Pending #326 actual archive/chat adapter distinction and retained backup/operator-export workflow, verified managed backup states, plus #327. Enum/status/CLI component checks do not substitute for those effects. |

## Evidence integrity and remaining gates

Read the actual preserved log tails for all 31 entries in `325-shared-evidence-index.json`, not merely its reported outcomes. Logs live under the frozen worktree's `.github-ticket-loop/privacy-325/evidence/`. The successful final composition ran before the final node-retry edit; only its affected ER04 three-layout subcheck and scoped Ruff were rerun afterward. Type checking reports success for one public consumer file; scoped lint is not a full package/full-suite gate. The existing PostgreSQL local-process-only skip in the 80-pass run is retained and is not an ER/CL backend-row pass or failure.

Preserved failures include initial copy closure (4 failed/5 passed), the empty/incomplete deadlock log, ER04 (3 failed), ER06 (3 failed on each of two initial runs), native retirement (3 failed), node retry (3 failed), and setup failures. The 6-pass receipt correction supports the final targeted assertions; it does not erase the earlier over-selection failure. CL05's original default-budget failure remains recorded; later passing with explicit 60000ms fixture budget does not independently establish its original cause. No stricter exploratory check was relabeled as an original requirement.

The original #323 full-suite/classifier failures and native crash remain unresolved per shared handoff/root constraints; their raw full-suite logs are outside this 31-log #325 evidence set, so I do not claim independent clearance or a new diagnosis. Two unsuccessful #323 cycles and pending user approval remain in force. This review neither performs nor authorizes a third repair, rerun, broad review or evaluation cohort.

ER07/08, #327 whole-product gate, final dependency composition, required final package/full/CI checks and delivery remain pending. Keep `acceptanceComplete=false` and `protected_product_ready=false`. Resolve A1/A2 against original scope before accepting #325; prerequisite-only integration, if root chooses, must not close the issue or advertise completed erasure acceptance.

REVIEW_325_ADVERSARIAL_COMPLETE

<!-- END VERBATIM review-325-adversarial.md -->

## Affected correction review: core

<!-- BEGIN VERBATIM review-325-core-correction.md -->
Status: COMPLETE
Verdict: APPROVE (bounded affected source review; nonclosing prerequisite only)
Role: CORE tier L, affected checklists 1, 3, 4 and 5
Issue: dwamianm/prism#325 / PV-06
Head: 36ddd11ac68940952a379b8bebe8730630943e22
Tree: e2af8f48a0ae2413dda9451a04fdfcd2ec651cd9
Correction base: ee620296f2af8a81fa1df2b6f428a7344fea5b2f
Whole-issue base: 418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa
Whole-issue snapshot: fa7f6d4257e5158770d32c584b9bd793d380757f3c14c14d40fd744dad960a05
Worktree: /Users/dwamianm/Sites/prism/.claude/worktrees/privacy-325-retention-erasure

## Review scope

This is the authorized affected re-review of CORE-325-01 and CORE-325-02, not another broad review. The original report remains unchanged in review-325-core.md. Reused its acceptance criteria, instructions and unchanged-scope review; read the complete corrected handoff/checkpoint, correction diff (15 files, 527 additions/42 deletions), relevant producer/consumer paths and nine new preserved logs. Independently checked HEAD/tree and clean tracked status. No tests, database operations, installs, providers, agents, source edits or delivery/state mutations were performed. Only this report was written.

No remaining blocking finding was identified in these corrected scopes. The two original findings are resolved as described below. This approval does not close the six ACs or waive integration/full validation gates.

## CORE-325-01: resolved

Original basis remains AC2/AC3, with truthful availability under AC6: node-selected memory erasure must fence and remove the covered retained source, not merely remove its graph/index copies.

`storage/erasure.py:117-162` now resolves the selected node's actual owned source ancestry and generated-node ancestry, checks owner/scope/project and admission identity, validates optional typed sanitization provenance separately, and fails closed on missing or mismatched provenance. `request()` resolves that closure under the current ledger/policy lease before the independent append (`:332-367`). `ErasureIntent.source_event_ids` persists it separately from the caller's selectors, preserving exact retry identity. Local apply consumes the saved closure; old unresolved node intents require surviving provenance or refuse continuation (`:45-80`, startup repair at `:204`). No late best-effort source discovery substitutes for acknowledgment coverage.

The same persisted closure now participates in `affected()` (`:105-114`), discovery event targets (`:521`), existing event/source cleanup and final verification (`:773-804`). Verification requires the requested nodes and resolved events to be present in saved targets, rejects empty node closure, and checks missing evidence/processing/retry/pseudonym material and blank event content/hash. `privacy/reads.py:496-510` reports an observed source fence as erased immediately; `read_source` retains its real exact-record guard. Original independent-admission survival logic remains in place.

Tests now exercise a positive real source reveal before the node request, persisted source closure before an injected lost local apply, restart, source denial and erased availability, physical absence from the logical source slots, completed exact retries and changed-selector rejection. The ER02 survivor test now uses a node selector while retaining its independently justified same-owner admission and foreign-owner controls. ER03 selects a real extracted node carrying the existing sanitization field and still denies the paused profile publication; its paused diagnostic reveal also uses a node selector. The earlier incorrect assertion that node-only erasure leaves its source sealed has been replaced with the original required erased behavior.

## CORE-325-02: resolved by safe refusal plus exclusive-use proof

Original basis remains AC4 and prevention of introduced survivor loss. Local zero-reference counting still alone cannot authorize provider-global destruction.

Fresh native entries begin with empty known usage; old entries default to unknown. `local_keys.py:260-274` monotonically records ever-used store lineages, and unbound use or unknown prior history remains unknown. `keys.py:35-61` accepts lineage only from an active task-owned scope established within the erasure lease (`storage/erasure.py:282-286`); inherited child context cannot borrow it as valid proof.

I traced real writers: `CryptoProtector.seal`, final `writer_guard`, and `rewrap_held` record exact wrapping versions under the registry publication lock; `NativeKeyCustody.key_for`, `provision_retry_key` and `get_retry_key` record auxiliary use under the native registry lock. Fast admission/retry paths now hold the current erasure/admission guard while making HMAC calls. Recording is attached to actual providers, not the optional engine destruction capability, so store B with `key_custody=None` still contributes its usage. Native registry updates retain existing atomic write/fsync behavior. Activation/recovery preserve usage history.

`retire_managed` checks exact singleton lineage and performs state retirement/private-part removal under the same registry FileLock (`local_keys.py:395-411`). The coordinator calls only this capability and has no fallback to operator `retire` (`storage/erasure.py:707-717`). Shared, unknown, empty or unsupported proof refuses before destruction. The existing maintenance failure path leaves the job pending at keys, with fixed provider failure and its denial fence intact. Exclusive retirement can still retry a failed private-part removal with preserved lineage. This is conservative exclusive-use evidence, not a global live-reference count, and indefinite pending status for shared/unknown custody is correctly documented.

The corrected ER02 fixture opens an independent store on the same actual registry, boundary, wrapping version, pseudonym epoch, retry identity and ledger. Before and after A's purge attempt, it proves B's real source unwrap, exact HMAC retry and pseudonym continuity; it repeats after B reopens and A retries. It checks pending/failure status and A's denial. Real unbound sealing and legacy missing usage history also remain usable while automatic destruction refuses. The existing same-store exclusive test still proves successful retirement and persistent private-removal failure/retry, avoiding a blanket-disable workaround.

## Evidence and repair history

Read all nine actual new logs and the exact commands in PV-06-results.json. The initial unchanged-runtime witness remains 6 failed/9 deselected. The first correction remains 6 failed/9 passed/6 deselected: the 9 passing cases cover independent admissions and exclusive/shared native custody. Its ER03 inspection failure is real. Its ER04 trace ends in `privacy_capability_unavailable` at the native context exit; the recorded explanation is that the context masked the newly stricter exact-code assertion mismatch. I do not relabel that failed execution as passing. The final test requires effective PrivacyDenied, consistent with the original AC rather than inventing a mandated error code.

The later affected source run passed 6/6 selected cases. The final extracted-node normalization and strengthened selector passed 3/3 ER03 cases. Existing keys/crypto/rotation/retry compatibility passed 93 tests on the final provider source. Scoped Ruff passed all 11 changed Python files, then the final two changed paths; strict public consumer typing passed before the private normalization, with no later public signature change. There was no final combined 15-case rerun or full suite. Source timing is explicit and the unchanged passing provider/other cases are reused only within their affected scope.

Source repair history is three bounded correction checks with one unsuccessful correction; custody has zero unsuccessful corrections. The original failure logs, original report, earlier receipt closure cycles and other historical failures remain preserved. No repair limit was reset by this review.

## Limits and delivery gates

Affected CORE1/3/4/5 coverage is complete: source identity/sentinel behavior, actual runtime writers/readers, failure/retry semantics, related configurations, existing codec/provider/lock reuse, and focused regression assertions were reviewed. No new default/configuration/deployment mechanism or global service was introduced. No additional cleanup or quality round is requested; the original unused-helper follow-up remains nonblocking.

ER07/ER08 actual #326 restore/import and managed backup/export/archive/chat-clear integration, #327 whole-product validation and final required suite/package/simulation/CI remain open. The #323 classifier remains held after two unsuccessful repairs; no third attempt was repaired or validated here. The old full-suite allocator-corruption SIGABRT remains unresolved, and the unchanged 3+6-test probes do not clear it. Shared/unknown key jobs remain pending, not falsely complete. Physical media, external copies and rollback of both local authorities retain their documented limits.

APPROVE the corrected affected implementation as a nonclosing prerequisite. This is not permission to close #325 or claim protected_product_ready/full-suite success.

REVIEW_325_CORE_CORRECTION_COMPLETE

<!-- END VERBATIM review-325-core-correction.md -->

## Affected correction review: security

<!-- BEGIN VERBATIM review-325-security-correction.md -->
Status: COMPLETE
Verdict: APPROVE
Review: Affected SECURITY re-review, checklist 2, S1/S2 only
Issue: dwamianm/prism#325

## Exact checkpoint and scope

- Worktree: /Users/dwamianm/Sites/prism/.claude/worktrees/privacy-325-retention-erasure
- HEAD: 36ddd11ac68940952a379b8bebe8730630943e22
- Tree: e2af8f48a0ae2413dda9451a04fdfcd2ec651cd9
- Whole snapshot: fa7f6d4257e5158770d32c584b9bd793d380757f3c14c14d40fd744dad960a05
- Correction base: ee620296f2af8a81fa1df2b6f428a7344fea5b2f
- Original issue base: 418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa
- Root supplied clean tracked state and exact normal remote push verification.

This is the authorized bounded affected re-review of the two findings in review-325-security.md. That original report, including its withdrawn node-only dismissal and correction, is preserved unchanged. Read corrected-handoff.md and corrected-checkpoint.json in full, the correction diff, affected source producers/consumers, modified assertions, actual nine new logs and their commands/results. No tests, DB execution, installation, providers, agents, repairs, GitHub/state changes or writes outside this new report occurred. Unchanged security coverage is reused from the original review. Approval here is approval of these corrections, not acceptance completion or permission to skip final required gates.

## S1 resolved: exclusive-use proof or refusal precedes automatic key destruction

The destructive coordinator path now calls only retire_managed(version, store_id=intent.store_id), and refuses a provider without that capability (src/prme/storage/erasure.py:714). It retains the original local surviving-reference check. NativeKeyCustody._retire requires managed_lineages == (store_id,) while holding the native registry FileLock before either the retired marker or private-part deletion (src/prme/privacy/local_keys.py:392–412). It does not fall back to explicit operator retire.

The proof has runtime writers, not just a new inventory field:

- Fresh provisioning starts with an empty history; absent legacy history reads as None. _record_usage unions new managed lineages monotonically, and any unbound use makes history unknown permanently (local_keys.py:257–274, :318).
- CryptoProtector records wrapping use before producing an envelope, records final envelope publication under its writer guard, and records rewrap destination use before wrapping that version (privacy/crypto.py:279, :306, :408). public_registry supplies the callback and shared publication FileLock; the callback checks provider identity and ready exact references before writing.
- Native pseudonym and retry key access records use under the same custody lock before returning a private handle (local_keys.py:451–512). Admission sanitization is already inside the current admission guard, and retry commit/replay branches now explicitly hold that guard (privacy/admission.py:313, :903, :915, :933).
- ErasureCoordinator establishes managed_key_usage only while holding its current ledger lease (storage/erasure.py:282). current_key_lineage requires the active lease's exact asyncio task; inherited child context alone does not borrow it (privacy/keys.py:35–60). The lineage is captured before dispatching the synchronous registry/native worker. Cancellation joins native work through existing completion helpers.

Race assessment: for another store B, registration preceding retirement records B under the same registry lock, so A's exact-single-lineage test refuses. Retirement preceding B's registration makes the entry retired, so B cannot record/use that ready version through these managed producer paths. A's own erasure ledger lease serializes its admissions and retirement; final envelope publication validates and registers the writer before durable admission. Rewrap records usage within its existing source/current guard before the real replacement. No unprotected check-then-delete gap was found in the corrected native path.

Unknown, shared and legacy histories remain usable and fail the key stage visibly. process_erasures preserves pending logical copies, a fixed provider_unavailable failure and the access fence. Fresh exclusive histories still retire; the same proof remains on a retired entry so private-part deletion can be retried. The monotonic set is exclusive-use evidence, not global live reference counts: it deliberately cannot make a formerly shared key exclusive again. No arbitrary custom-provider correctness, copied/rolled-back custody authority or hostile same-user isolation is certified.

Shared evidence now directly exercises two independent stores on the same provider/boundary/ledger, including B without destructive custody capability, actual post-A native unlock, pseudonym continuity, HMAC retry, reopening B, and shared/unbound/legacy refusal. The existing exclusive test still checks last-reference retirement and persistent native deletion failure/retry. These assertions address the original AC4 blocker without waiving it.

## S2 resolved: node-selected source closure is fenced before acknowledgement

_resolve_intent_sources resolves actual owner/scope node provenance, exact admitted source references and generated-node ancestry, validates project/owner/scope, and refuses missing/ambiguous provenance (src/prme/storage/erasure.py:117–163). It validates optional extracted-node SanitizationReport separately before comparing the underlying source identity. Generated profiles/summaries use the corresponding reference producer in privacy/journals.py:302–329, so source_node_ids are interpreted through the typed format.

ErasureCoordinator.request computes this closure before the independent ledger append and local apply; the caller's request remains unchanged for idempotency (storage/erasure.py:357–365; privacy/erasure.py:49). The closure is therefore durable even when the independent intent succeeds and local application is interrupted. affected() includes persisted source_event_ids for memory, evidence and processing checks. read_source still opens through the exact-record current guard; inspect_source now reports the acknowledged source fence immediately rather than returning stale sealed availability (privacy/reads.py:503–510).

Discovery seeds those same source events, so the existing SQL cleanup clears their evidence, processing, retry and pseudonym references and retained event content/hash. Final verification requires all explicitly selected and resolved source targets and checks those source slots and bytes (storage/erasure.py:521, :778–807). This closes the previous mismatch between node-only completion and retained managed source. Sources are treated as indivisible where separation is uncertain, consistent with original approved PV-06-plan.md:92; independent explicit admissions retain their original survivor rule.

Old node intents without closures require available original provenance for reconstruction. intents() otherwise fails closed; missing original nodes cannot be treated as proof of coverage. New completed retries use the saved owned request and closure without requiring deleted node rows. This review does not certify migration of every old intent or #326 restore/import behavior.

Affected ER03/ER04 assertions now include actual positive source reveal, saved independent intent closure before lost apply, immediate erased availability, paused post-fence source reveal refusal, reopen denial, cleared source slots/content/hash, exact completed retry and independent survivor controls. Final ER03 selects a real extracted node carrying existing optional sanitization provenance. These address AC2/3/6 and the original plan, rather than relying on the prior contradictory test intent.

## Evidence, failure history and limits

Inspected actual new logs, their recorded commands and source timing. No new runtime execution was performed by this reviewer.

- Baseline unchanged ee620296: 6 failed, 9 deselected. Both original security failures are preserved as failures.
- First composite correction: 9 passed, 6 failed, 6 deselected. Reused passes are the three independent-admission cases and six exclusive/shared native cases. Immediate inspect_source and the stricter error-code expectation failures remain recorded, not relabeled successes.
- Source correction: 6 passed, 6 deselected. Effective source denial is the original requirement; a particular denial code is not substituted for it.
- Final extracted-source normalization: 3 passed on final source/test revision.
- Provider compatibility: 93 passed on final provider source. Only private source-reference normalization/test selection followed it.
- Scoped Ruff logs and strict public consumer mypy pass. Final two changed source/test files were linted again; public signatures did not change after mypy.

There was no final combined rerun of all 15 corrected cases or full suite. The preserved source timing supports affected evidence reuse; it does not represent that missing run as completed. Source correction has one unsuccessful correction, custody zero; prior repair histories remain unchanged. #323 remains held after two unsuccessful classifier cycles, with its original CI failures and native SIGABRT unresolved. No third repair or testing is authorized by this review.

Original six ACs and complete ER01–08/CL04/05/07 obligations remain intact. Actual #326 ER07 restore/import and ER08 managed backup/operator-export, #327 product gate, final full/package/CI checks and delivery remain pending. acceptanceComplete and protected_product_ready remain false. No physical wipe, external-copy revocation, global backup/key reference accounting or same-user isolation is claimed.

## Disposition

S1: resolved by enforced native exclusive-use proof or visible refusal.
S2: resolved by durable pre-acknowledgement covered-source closure and consistent fence/purge verification.
No additional concrete original-AC regression or security blocker was identified in the changed/affected scope. APPROVE this affected correction snapshot, with the unchanged remaining gates above.

REVIEW_325_SECURITY_CORRECTION_COMPLETE

<!-- END VERBATIM review-325-security-correction.md -->

## Affected correction review: data

<!-- BEGIN VERBATIM review-325-data-correction.md -->
# #325 affected data and persisted-field re-review

Status: COMPLETE
Checklist: 7, DATA / PERSISTED_FIELD, bounded correction review
Verdict: APPROVE affected correction scope
Original issue base: `418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa`
Correction base: `ee620296f2af8a81fa1df2b6f428a7344fea5b2f`
Reviewed HEAD/tree: `36ddd11ac68940952a379b8bebe8730630943e22` / `e2af8f48a0ae2413dda9451a04fdfcd2ec651cd9`
Whole reviewed snapshot: `fa7f6d4257e5158770d32c584b9bd793d380757f3c14c14d40fd744dad960a05`

I reviewed the corrected 15-file diff, its runtime consumers and the nine preserved new logs against the original D1 report and frozen handoff. This approves only the affected correction. It does not close the original six acceptance criteria or ER07/08, #323's held failures, #327, full checks/CI, or delivery. I ran no tests, databases, installers, external calls, or additional agents, and changed no source or prior report.

## D1 disposition and custody safety

**D1 resolved for automatic managed retirement in this checkpoint.** Fresh `NativeKeyCustody` entries start with an empty, known `managed_lineages` tuple (`src/prme/privacy/local_keys.py:314-324`). Actual wrapping selection, final publication and rewrap record the current task-owned store lineage through `KeyRegistry.record_usage_held` (`src/prme/privacy/crypto.py:278-285,300-317,400-416`; `src/prme/privacy/keys.py:448-459`), while pseudonym and retry uses record it through `_auxiliary` (`src/prme/privacy/local_keys.py:451-512`). `ErasureCoordinator.guard` obtains the current independent ledger lease before installing the task-owned lineage, and keeps it through the effect (`src/prme/storage/erasure.py:260-289`); child tasks or work after lease exit get no inherited live lineage (`src/prme/privacy/keys.py:35-59`). Usage and retirement serialize on the same public-registry FileLock (`src/prme/privacy/local_keys.py:193-203,282-286,400-411`). The use history only grows: a second managed store adds its distinct ID; an unbound use or an old entry with no field remains `None` (`:141-157,260-274`). The lineage set is exclusive-use evidence, not a count of live references.

The existing current-store live admission scan remains necessary (`src/prme/storage/erasure.py:675-695`). Automatic deletion now calls only `retire_managed(version, store_id=...)`, and that method re-reads the registry under the native lock and refuses anything except exactly `(store_id,)` before setting `retired` and removing the private part (`src/prme/storage/erasure.py:696-717`; `src/prme/privacy/local_keys.py:391-411`). There is no fallback to the explicit operator `retire`. Shared, legacy, unbound, or missing custody therefore leaves `process_erasures` at `keys` with `provider_unavailable`, `blocked_from_access`, and `logical_live_copies=pending` (`src/prme/storage/erasure.py:720-770`). A failed native removal is retried through the same exact-version operation, including when the public state already says retired. The preserved three-layout shared-custody test uses the same evidence version, pseudonym epoch and retry key in a second independent store; B can still unwrap, pseudonymize and retry after A stays pending, after B reopens, and after an unbound or legacy-history transition (`tests/test_privacy_erasure_retirement.py:76-147`). The same-store test retains live versions until the last local reference and then verifies retirement and native denial (`:19-73`). This is a correction of the AC4 survivor defect; shared versions may remain pending indefinitely without a complete authorized reference authority, as the docs state (`docs/PRIVACY-KEYS.md:174-188`).

## Changed durable writer → compatible reader map

| Durable field or identity | Non-migration runtime writer | Reader/retry consumer and old-record behavior |
| --- | --- | --- |
| `ErasureIntent.source_event_ids` in external ledger `entries.body` and local `privacy_erasure_jobs.intent` | A new node request resolves exact owned node origin, evidence references, and typed generated-node ancestors before `append_intent` at `src/prme/storage/erasure.py:117-162,347-365`; the independent ledger writes the full intent before acknowledgment (`src/prme/privacy/ledger.py:143-163`), and `ErasureRepository.apply` writes the resolved intent and local head together at `src/prme/storage/erasure.py:65-84`. | `affected` uses saved source IDs for immediate denial (`:107-114`); discovery saves source event targets (`:515-523`), SQL purges them (`:594-633`), and verification requires them and the selected node (`:773-804`). `ErasureRequest` remains the exact caller retry identity (`:338-346`); the internal closure is not substituted for it. Old event/owner intents parse with the optional field absent (`src/prme/privacy/erasure.py:41-56`). Old node intents with missing closure are re-resolved from surviving original provenance in `apply` or `prepare_store.repair_source_closures` (`src/prme/storage/erasure.py:45-84,197-205`); if no safe provenance exists, `intents()` refuses protected continuation (`:45-53`), and `_resolve_intent_sources` refuses missing or mismatched ownership (`:128-162`). The #326 `apply_current_ledger` interface remains a pending integration consumer, not current ER07 proof. |
| `KeyEntry.managed_lineages` in existing atomic `PublicKeyStore.entries` | Native provisioning initializes `()` (`src/prme/privacy/local_keys.py:314-324`); `public_registry.record_usage` and `_record_usage` durably union store IDs, or set `None` for unbound use, via atomic fsync/replace under the registry lock (`:206-274`). Crypto seal, publication and rewrap call that writer (`src/prme/privacy/crypto.py:278-285,300-317,400-416`); native auxiliary use calls it (`src/prme/privacy/local_keys.py:451-512`). | `read_public_store` parses the field through `PublicKeyStore`/`KeyEntry` (`src/prme/privacy/local_keys.py:141-188`); `_retire` re-reads and requires exactly the caller's one store ID under the same FileLock (`:400-411`). Missing old field defaults to `None`, and later use cannot turn unknown history into exclusive history (`:148,260-274`). `KeyEntry.binding()` still canonicalizes the wrapping/auxiliary reference only (`:159-165`), so adding usage history does not change existing native secret binding. A public entry produced before this correction stays readable but cannot be automatically retired. |

The unchanged ledger sequence/token, local store-state head, targets, progress, artifact and status writer/reader mappings remain as recorded in `review-325-data.md`. The correction strengthens verification of saved source targets, cleared retry/epoch fields and empty event content/hash (`src/prme/storage/erasure.py:773-804`). It does not add a new schema table, bypass the existing independent ledger's append-before-local-fence ordering, or turn structural manifests alone into source ancestry proof. The source resolver reads actual owned rows and compares the normalized origin reference to the admission record (`:128-162`); the optional typed `sanitization` provenance is validated and excluded from the identity comparison (`:141-160`), matching existing generated-node readers (`src/prme/privacy/reads.py:231-245`).

## Evidence, remaining limits, and verdict

Preserved `privacy325-review-initial-witnesses.log` records six failures on the old runtime. `privacy325-review-correction-1.log` remains a failed composite command: nine passed and six failed; only its three independent-admission cases and six custody cases are reused. `privacy325-review-source-correction-2.log` then records six affected ER03/ER04 passes, and `privacy325-review-extracted-source-1.log` records the three final extracted-node ER03 passes after the last private normalization. `privacy325-review-provider-regressions-1.log` records 93 passes on final provider source; scoped Ruff and public mypy logs pass with the documented source timing. The final combined 15-case command and full suite were not rerun. The source repair used three bounded correction checks with one unsuccessful; custody used one with zero unsuccessful. The first composite's failed exact-code assertion remains a failure, not a retroactive pass.

No additional blocker was found in the changed data scope. The original D1 destructive-retirement path is closed for the supported native managed path by exclusive-use proof or visible refusal. This verdict does not certify global live reference counting, physical deletion, unmanaged copies, old ambiguous key history, a restored store under #326, or the still-required final product gate.

REVIEW_325_DATA_CORRECTION_COMPLETE

<!-- END VERBATIM review-325-data-correction.md -->

## Affected correction review: adversarial

<!-- BEGIN VERBATIM review-325-adversarial-correction.md -->
# #325 bounded adversarial correction review

Status: COMPLETE
Verdict: ACCEPT A1/A2 corrections within the affected scope; original issue acceptance remains incomplete.

## Exact identity and scope

- Worktree: `/Users/dwamianm/Sites/prism/.claude/worktrees/privacy-325-retention-erasure`
- HEAD: `36ddd11ac68940952a379b8bebe8730630943e22`
- Tree: `e2af8f48a0ae2413dda9451a04fdfcd2ec651cd9`
- Correction base: `ee620296f2af8a81fa1df2b6f428a7344fea5b2f`
- Whole-issue base: `418c7ed8e1c99b3bbbb7b90725bf88c8591e42aa`
- Exact whole-issue snapshot: `fa7f6d4257e5158770d32c584b9bd793d380757f3c14c14d40fd744dad960a05`

HEAD/tree and clean tracked status confirmed by read-only Git commands. The snapshot is the supplied corrected review-plan identity. Root verified the normal remote push, 15 changed files and nine new logs; I did not repeat remote operations or run snapshot tooling. Read the full corrected handoff/checkpoint, correction diff and relevant consumers, updated review budget/plan, shared evidence index and actual nine preserved logs. Reuse the original instructions, six ACs, eight scenarios and unchanged original report `review-325-adversarial.md` and frozen handoff. Original report remains intact.

Only A1/A2 and affected ER02/03/04 paths were re-reviewed. No ninth case, broad round, new cohort, test execution, database, installation, provider call, agent, implementation/GitHub/state mutation. The sole write is this report.

## A1 disposition: corrected / Accept

Original basis: AC2/3 (with immediate availability/status under AC6), particularly original plan line 92 requiring source representations that retain node-selected deleted payload. The original finding remains valid historical evidence; it is resolved by source behavior, not an acceptance rewrite.

`src/prme/storage/erasure.py:116–165` now resolves actual owned node provenance into source event identities. It validates owner/scope/project, compares the original admission reference, validates optional extracted-node `SanitizationReport` independently, follows typed generated-node ancestry, and includes evidence dependencies. It uses visited identities rather than guessing separation by text. Missing/ambiguous provenance refuses. This conservatively invalidates indivisible source representations; the existing independently admitted direct-support proof at `_independent_direct_support` preserves complete justified controls.

`ErasureCoordinator.request` at lines 356–364 resolves this closure before appending the independent intent or acknowledging its local application, while holding current ledger/policy guards. `privacy/erasure.py:49–51` persists closure separately from the caller request, retaining exact request retry semantics. `affected` at line 113 uses closure immediately; `_discover_step` at line 521 enrolls it for SQL/native traversal; `_verify_purge` at lines 778–805 requires original node/source targets and verifies source envelopes, retry/pseudonym references and event content/hash are absent. This closes the original gap where successful verification merely omitted the source.

The independent ledger serializes the resolved intent. A lost local apply therefore replays the same source closure before recovery rather than depending on an already-deleted node. Older unresolved node intents are repaired only while actual provenance remains (`ErasureRepository.repair_source_closures`, line 56); missing provenance fails closed. Exact completed retry still compares the original request before live target resolution.

`privacy/reads.py:495–510` now reports immediate source erasure under the current guard; `read_source` retains the actual pre-open evidence fence. The corrected ER03 covers a real extracted node with typed sanitization provenance and paused profile publication, plus node-selected paused diagnostic reveal and untouched same-owner controls. ER04 proves a source reveal before deletion, resolved closure in the independent pending ledger, reopen denial, absent source/event copies and exact retry after node deletion. ER02 now selects the node through the real mixed-copy/independent-survivor fixture. No additional A1 blocker found in these changed effects.

## A2 disposition: corrected / Accept with explicit pending retirement semantics

Original basis: AC4 and the original reference-aware provider disposal/survivor requirement. Local absence is still not global absence. The correction no longer treats it as authority to destroy shared keys.

`privacy/local_keys.py:146–157` adds monotonic `managed_lineages`: fresh provision initializes an empty set; absent old history is `None`. `_record_usage` at lines 260–276 unions actual store usage and makes unbound/unknown history permanently unknown. It never shrinks the set or claims current global reference counts. Existing records remain readable; the default does not invent exclusive ownership for old keys.

Runtime registration is connected to actual effects: crypto seal, writer publication and rewrap (`privacy/crypto.py:279–286, 303–309, 407–410`); native pseudonym and retry key issuance/use (`privacy/local_keys.py:451–512`); actual fast retry calls now hold admission guards (`privacy/admission.py:903–936`). Public native registry registration works even if the consuming engine lacks the optional destructive custody capability. This matters for the second managed store in the corrected survivor fixture.

Task ownership is explicit: `privacy/keys.py:35–60` records task identity and an active flag. `storage/erasure.py:280–286` establishes the usage context only inside the current erasure lease. An inherited child context cannot borrow the parent's identity; it gets no lineage until it acquires its own guard. Lineage is captured before native thread execution, avoiding an attempt to identify an async task inside the thread.

Race boundary: public usage writes hold `_publication_guard` (`privacy/local_keys.py:195–206`), and native auxiliary use and managed retirement hold the same registry-path FileLock. `retire_managed`/`_retire` at lines 396–414 re-read exact usage and require `(store_id,)` under that lock before recording retirement and removing private parts. Another store registering first makes destruction refuse; retirement first makes subsequent registration/key acquisition reject rather than publish using a newly retired key. Managed final envelope publication revalidates/records usage under the writer lock. Same-store local admission/reference work is serialized by the current erasure lease. This is source-traced race reasoning, not a new concurrent stress result.

`storage/erasure.py:714–717` requires `retire_managed`; it never falls back to destructive operator `retire`. Shared, old, unbound and unsupported providers therefore stay at visible pending provider failure, preserving the access fence. A fresh exclusive version may still be retired after zero local references, and failure after recording retirement retries exact native private-part removal. The existing retirement fixture confirms the exclusive history and removal-failure retry; the new subcheck uses two independent live stores sharing the real custody/version/ledger/boundary and proves the second store can still reveal evidence, reproduce pseudonyms and verify its retry after purge attempts and reopen.

This accepts the original bounded alternative of refusing destruction without adequate proof. It does not establish complete shared-key cleanup: shared/unknown jobs may remain pending indefinitely, even after all stores eventually stop using a version. That is accurately surfaced, not claimed successful erasure. Actual restore/import lineage and provider integration remain #326 obligations. No new A2 blocker found in the affected native managed path.

## Preserved eight-case dispositions

| Original case | Correction disposition and retained limits |
|---|---|
| ER01: exact deadline/startup | Original component evidence retained. No new whole-case claim; final dependency/full/product checks pending. |
| ER02: source versus memory, copy closure, survivors | A1/A2 corrected as above. Reuse nine passes in the first failed composite command: independent-admission and native exclusive/shared/old/unbound components. Shared custody reports pending instead of false completion; no global reference-counting claim. Original CL04/05/07 evidence remains supporting evidence, not new cases. |
| ER03: stale publication/reveal/cancellation | Corrected direct and extracted node-source fences accepted. Six source checks passed after inspection correction; final three ER03 layout checks passed after optional provenance normalization. Original task/cancellation evidence retained; no new race execution claimed. |
| ER04: idempotency/failure/restart | Persisted pre-ack closure, lost apply/reopen, source absence, exact retry and native removal retry accepted on targeted evidence. Failures remain preserved. Acknowledged denial and successful live purge remain distinct. |
| ER05: recovery without mutable work | Original component disposition retained; startup consumes saved closure and refuses unresolved old ancestry. No new full recovery run and no claim that corrected components clear #323 failures. |
| ER06: stale native/rebuild/survivors | Original native evidence retained. A2 shared-custody proof now complements the older separate-custody control; no rerun of all native/rebuild checks or secure-media claim. |
| ER07: actual restore/import and current authority | Still pending #326 actual pre-erasure restore/import, wrong/stale/missing/snapshot-only refusal and survivor verification. Exclusive ever-used history is not a replacement for restore lineage proof. |
| ER08: archive/chat-clear, backup/export/status | Still pending #326 actual adapter distinction, retained backup/operator export and verified backup states, plus #327. Visible pending key disposal does not certify backup expiry or external revocation. |

## Actual evidence and timing

All logs below are preserved under the worktree's `.github-ticket-loop/privacy-325/evidence/`; I read their actual output, including the failed ER04 traceback, rather than treating a summary as a new pass.

- `privacy325-review-initial-witnesses.log`: **6 failed, 9 deselected**, 11.33s, exit 1, against unchanged original runtime. These establish retained-source and shared-survivor failures; not correction cycles.
- `privacy325-review-correction-1.log`: **6 failed, 9 passed, 6 deselected**, 42.05s, exit 1. The three ER03 failures assert sealed versus erased. The three ER04 failures surface `privacy_capability_unavailable` from the native unlock context's exception conversion; the handoff attributes the underlying failure to the stricter exact denial-code assertion. I preserve the observable traceback and do not turn that attribution into an independently proven exception history or a pass.
- `privacy325-review-source-correction-2.log`: **6 passed, 6 deselected**, 19.25s, exit 0, after immediate inspection correction and effective-denial assertion. The final assertion requires denied access rather than a particular reason string, matching the original AC; the previous failed log remains failed.
- `privacy325-review-provider-regressions-1.log`: **93 passed**, 32.73s, exit 0, on final provider source. No provider changes followed.
- `privacy325-review-extracted-source-1.log`: **3 passed**, 9.09s, exit 0, after final private resolver normalization and real extracted-node selector.
- Three scoped Ruff logs pass; public mypy reports success in one source file. Full changed-file lint/public types preceded the last private normalization; the two affected files passed lint again afterward. No public signature changed in that final normalization.

There was no final combined 15-case rerun or full suite. The 93 compatibility tests predate only the private source resolver/test refinement. Original 80-pass composition and all original failures retain their earlier timing and limitations. Passing subsets do not clear native crashes or full/package/CI obligations.

Repair accounting is unchanged: source unsuccessful corrections **1**, custody **0**. No reset, no third unsuccessful repair authorized. #323 remains stopped after its two unsuccessful classifier repairs; its third repair still requires user approval and the original full-suite/native SIGABRT evidence remains unresolved. #326 ER07/08, #327 whole-product gate, final dependency composition, required full/package/CI checks and delivery remain pending. Keep `acceptanceComplete=false` and `protected_product_ready=false`.

No new acceptance blocker found in this bounded correction review. Original A1/A2 are closed for the reviewed implementation path; this report does not approve issue closure, a product readiness claim or pending integrations.

REVIEW_325_ADVERSARIAL_CORRECTION_COMPLETE

<!-- END VERBATIM review-325-adversarial-correction.md -->
