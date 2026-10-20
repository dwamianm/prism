# #321 / PV-03 implementation evidence for scheduled review

Status: grouped AC5/EM06 review repair prepared for affected review; unpublished. This is
implementation evidence, not an independent approval or a protected-product gate
claim. Original six acceptance criteria and finite EM01–EM08 definitions remain
in `docs/plans/2026-10-evidence-privacy/PV-03-plan.md` unchanged.

## Base and scope

Worktree: `privacy-321-evidence-encryption`; branch
`issue-321-evidence-encryption`; delivered prerequisite/base
`50d22d4e365b52ce78300fe2d2e21d6f753b3a78` (#320). Root verified its tree equals
provisional b84a866c. The first implementation checkpoint was reconciled by
unpublished rebase; source tree stayed `059fa47dcead4c8caf6a13eae9bae5d9921898ed`.
No force push, cloud calls/keys, paid/live model calls, deployment, release or
privacy default change occurred. Only the assigned venv and database were used.

The implementation owns `privacy/{crypto,keys,local_keys,rotation,cli}.py` and
narrow protector/admission/storage/CLI/public API wiring. Existing admission
passes `original_snapshot` explicitly and holds `writer_guard` through all three
acceptance paths. The existing fake and one failing-capability stub were updated
for the required keyword; the original error assertion was preserved. The source
plan records the held local activation lock, exact private permit and metadata-only
final response lease additions. No retrieval/candidates implementation was copied.

## AC-linked evidence and limits

| Original criterion | Owned implementation/evidence | Remaining integration boundary |
| --- | --- | --- |
| AC1 authenticated random per-object encryption | Real PyCA AES-256-GCM; RSA4096 OAEP SHA256/MGF1; frozen CAVP and complete-envelope compatibility vectors; all identity/snapshot/wrapper selectors checked; inner AAD, OAEP label, swaps, wrong key, corruption and exact private-permit checks | Hosted SDK/provider conformance belongs to #281; no cloud success claim |
| AC2 ciphertext-only evidence storage | Real engine admission on DuckDB directory, .prme and PostgreSQL; source-only marker absent from actual rows/artifacts; failure before AES, after wrap, atomic transaction and lost acknowledgment; owned PG base/WAL scan | Whole historical migration/export/restore remains EM07/08 and #326; existing test doubles cover sanitizer/provider interfaces, not their release gates |
| AC3 provisioned keys/no fallback/recovery | Readiness before protected file/provider creation; exact native allowlist; verified bounded parts; interrupted provision/cleanup; deliberately encrypted PKCS8 recovery and permanent loss behavior | Windows/Linux native execution unverified; no mock-as-native claim |
| AC4 public writer/private diagnostics | Public registry and metadata inspector perform no private lookup; separate public subprocess; selected finite diagnostic capability, durable local audit, explicit native unlock | Ordinary source projection is #323; standalone crypto tests are not its positive retrieval gate |
| AC5 explicit standard/sealed authority | Explicit runtime vs diagnostic grants; same_process report; actual held response, expiry/revoke/fence checks; multi-source metadata final lease with no unwrap | #323 must hold the lease through its real providers/return; #281 owns actual transport/remote claim guard |
| AC6 resumable exact-version rotation | Existing OS registry lock holds through acceptance/activation; old prepared writes refuse; refreshed public descriptor; saved exact versions and full-body CAS; interruption before/after CAS, blocked row, cancellation through native completion | No online-cutover claim; uncoordinated/static/remote writers cannot claim local quiescence; retained old keys/backups remain readable |

Pseudonym and retry_commitment keys are separate random 32-byte custody purposes,
with exact boundary/epoch or original-snapshot/request/retention/version binding,
expiry/retirement refusal and inventory references. #322 owns HMAC/token semantics;
#324 owns durable retry comparisons/cohorts/terminal cleanup. The included
StoredProcessingAuthorizer verifies the existing real single-event current slot
and running claim; it does not claim the later multi-event lifecycle gate.

Original EM07/EM08 and #323/#324/#325/#326 shared positive gates remain pending.
No blanket refusal, fake crypto, fixture sanitizer or fixture model response is
counted as completing those gates.

The `real_engine` helper uses `FakeAuthority` for the outer `PolicyAuthority`;
standalone crypto cases use the component `Fence` guard. Those checks establish
real encryption, actual ciphertext storage/CAS, native custody and the descriptor
FileLock's acceptance/activation ordering. They do not establish real current-ledger,
policy-revision or erasure-fence enforcement. The concrete local authority remains
#325's implementation of the existing contract; hosted authority belongs to #280.
Full outer-authority acceptance for AC5/AC6 and EM04/EM06 stays pending that real
integration, including #323's final response. No separate authority stack is added.

## Runtime validation

Full logs, including failures, are retained in the ignored run directory:
`/Users/dwamianm/Sites/prism/.github-ticket-loop/privacy-318-279/validation-321/`.
Database: `postgresql://prme_test:prme_test@127.0.0.1:55432/privacy_engine_321`.
Native adapter doubles replace only OS get/set/delete in component fixtures;
AES/RSA and persisted ciphertext are real. Extraction/embedding fixtures make no
live model calls.

- `crypto-1.log`: initial real codec cohort, 26 passed.
- `keys-2.log`: native adapter/custody components, 15 passed.
- `storage-rotation-1.log`: 6 real rotation cases passed; 9 storage tests failed
  in their setup helpers (see preserved failure ledger below).
- `storage-2.log`: corrected storage helper cases, 9 passed across all three layouts.
- `cli-crypto-1.log`: concrete CLI/crypto checks, 30 passed.
- `native-1.log`: actual macOS native conformance, 1 passed. macOS 26.6.1 arm64,
  keyring 25.7.0, cryptography 50.0.2. Unique task-owned entries were provisioned,
  read back, explicitly decrypted, lost/restored via encrypted recovery, then
  removed. Public status ran in a separate interpreter. OS prompting was not
  asserted. Windows and Linux native behavior remain unverified.
- `failure-cancellation-physical-1.log`: 13 passed (15 irrelevant cases deselected).
  Real failure boundaries and cancellation/CAS authority covered every backend.
  A read-only scan streamed **144,568,320 bytes** from the assigned PostgreSQL
  database's base files and the task-owned server's WAL, plus bounded owned
  container logs; no synthetic source/DEK markers were found. The container label
  `prme.task=privacy-318-279` was verified before scanning. No database restart or
  unrelated container/artifact scan was performed. This is finite marker evidence,
  not proof of all future storage/backup retention.
- `targeted-regressions-1.log`: **561 passed, 10 skipped, 1 failed** in the selected
  owned/privacy/encryption/CLI/recovery/metadata/backend/workspace regressions.
  The failure was the old test stub's missing `original_snapshot` keyword.
- `targeted-regressions-2.log`: that unchanged error assertion and two finite
  readiness/runtime expiry cases passed (3 passed). The passing 561 tests were
  not repeated solely for this test-stub correction.
- `native-cleanup-1.log`: exact-version cleanup resumes after native deletion
  interruption, 1 passed (15 deselected).
- `final-response-lease-1.log`: multiple-selected-source lease plus revoke/expiry/
  current-fence gap checks, 4 passed. A bounded timeout records that nested opens
  wait on the existing intentionally non-reentrant authority lock. The supported
  final-response lease avoids that nesting; no reentrant lock redesign was made.
- `permit-integration-1.log`: final exact-source permit, ciphertext storage,
  rotation and CLI integration passed: **65 passed, 1 skipped** in 47.92 seconds.
  The skipped owned-container physical scan was already executed separately.
- `typing-development-2.log`: strict public consumer typing passed.
- `ruff-development-7.log`: all changed runtime/test scopes passed lint.
- `public-import-2.log`: lazy public crypto/custody import passed.

Root subsequently ran full-tree Ruff, build and both required simulation
configurations (74/74 each), all passed. Its preserved contract-1 failure was
limited to stale anchors/missing mappings; d179fd35 corrected traceability only,
and contract-2 passed with 45 copies/160 anchors and unchanged scenarios.
The obsolete full-pytest-1 run was intentionally interrupted after the confirmed
expiry defect: exit 2, 1 failed/1801 passed/45 skipped. Its only failure was the
unchanged plain-clone checkout assumption when run in a worktree; root confirmed
that same test passes in a genuine temporary plain clone. No checkout test was
changed. Root will coordinate the final full suite in a genuine plain clone at
the reviewed final revision, using the assigned venv/database. Final wheel smoke
and delivery remain root-owned; targeted results do not substitute for them.

## Grouped AC5/EM06 review repair

All four frozen d179fd35 source reviews completed. Core, security and adversarial
requested changes for the same audit/provider-await expiry defect; data approved
its scope while retaining that overall blocker. This is one grouped repair, not
new acceptance criteria. The original complete reports remain in the runroot as
`review-321-{core,security,data,adversarial}.md`.

`expiry-reproduction-1.log/json` preserves **27 failed** deterministic EM06
subchecks before the runtime correction. Every failure records an expired
permit/plaintext/key/rewrap/final-lease caller sentinel. Clock changes happen in
awaited audit, real crypto wrapper completion and processing validation; no real
sleep, live provider or new cohort was used. `expiry-repair-1.log/json` then
passed **108 tests, 1 skipped** on the first correction, including the affected
crypto/custody/rotation/storage/CLI tests. Scoped lint and strict consumer typing
passed. No unsuccessful fix-validation cycle was hidden or reset.

Every `UnwrapPermit` now requires the authority clock and derives its finite
bound from the original immutable snapshot/access. `require_current()` is
checked after processing validation, after audit/provider waits, before the
permit/key/plaintext/rewrap handoff and before a processing-key callback. Exact
source binding, existing held authority and non-reentrant locks remain.

Root then approved the #323 integration correction within the same EM06 contract:
the metadata final lease holds the finite grant lock through the yielded request,
but takes current-policy guards only at entry and exit. Its trusted caller must
guard each actual effect and check selected deadlines after waits and before
effects/final output. The existing two multi-source/gap subchecks first preserved
the actual old-policy-lock failures in `lease-policy-reproduction-1.log/json`.
This is the authorized second strategy for that existing non-reentrant interaction;
no permissive/reentrant guard or separate authority was introduced.

`expiry-completion-reproduction-1.log/json` preserves two more failures for the
same audit-await invariant at convenience-open/final-lease return. Completion
audit waits now receive another deadline check before return, with a failed audit
outcome appended on late refusal. This keeps completion audit from extending
authority while retaining the original exit checks. Final affected validation
passed **111 tests, 1 skipped** in 54.94 seconds, recorded in
`expiry-lease-integration-1.log/json`. `expiry-ruff-2.log/json` and
`expiry-typing-2.log/json` passed. The skipped physical scan was already executed;
no native platform or artifact-scan claim is renewed by these component tests.
Unchanged full-suite and simulation work was not duplicated. Affected independent
review remains pending.

## Preserved failures and bounded repairs

No behavior exceeded two unsuccessful fix-and-validation cycles.

1. `fixture-preparation-1.log`: the first fixed-vector extraction script split
   an empty vector field incorrectly before writing fixtures. One partition fix
   succeeded (`fixture-preparation-2.log`). The frozen vectors/key were not
   regenerated to accommodate runtime failures.
2. `keys-1.log`: new tests unpacked the helper tuple incorrectly; the correction
   also matched the actual pseudonym provision method name. One validation cycle
   passed all 15 cases in `keys-2.log`.
3. `storage-rotation-1.log`: a test helper used an async generator in bytes.join;
   the retry fixture supplied an unsupported FastIngestItem retrieval field.
   Corrected the helpers to await a list and let the existing fixture sanitizer
   provide admitted-memory text. Original marker/commit assertions stayed intact.
   One validation cycle passed all nine storage cases in `storage-2.log`.
4. `ruff-development-1.log`: unused import, lambda style and fixture-import
   shadowing. Scoped corrections passed; no runtime criterion was weakened.
5. `public-import-1.log`: eager privacy exports exposed an existing config/storage
   import cycle. Lazy exports fixed it without a storage package refactor;
   `public-import-2.log` passed.
6. `targeted-regressions-1.log`: required keyword no longer matched one test stub.
   Added `**kwargs`; the same private-error/no-durable-effects assertion passed
   in `targeted-regressions-2.log`. No production exception was relabeled.

The low-level permit substitution concern was identified during implementation
before freeze and closed by binding the permit to exact source ref/context/wrapped
bytes, with a direct provider-level EM02 test. Engine codec callers already used
the correct values; hosted adapters must call the same permit verifier.

## Frozen synthetic fixtures

- `tests/fixtures/privacy/crypto-vectors-v1.json` SHA-256:
  `2745673c59036c85feb79770a0481ad8796f4d8e321addb93d65cffb31cf3346`.
- `tests/fixtures/privacy/rsa-4096-test-key.pem` SHA-256:
  `c80715bdba3a238714cd78904b52c68d209230e3bce37a2133272d616200a50e`.

These files are explicitly public synthetic test material, never production
keys. Runtime private keys/DEKs were not printed into the evidence report.
