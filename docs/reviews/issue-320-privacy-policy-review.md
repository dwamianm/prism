# Issue 320 evidence policy admission review

Status: the bounded main reconciliation and reingest callback correction pass
the agreed local checks. Core, security, data and adversarial affected reviews
are complete and approve the identical source snapshot. Required exact-head CI
on the updated pull request remains pending. The preceding failing selection
and fixture setup failures remain recorded below.
Base: `4f23c01e53cc534c78f72421c0f62867ca1f8e98`.
Current helper snapshot: `95f4b45d492286a61348adcc241d3f0aea74d09c27d4ee442f8ce7d2fe8b070b`.
Previous framework-layout snapshot: `85c15e907f6054b92498945296e30f1f16d5d9c161782597f3049cd1461e8b9c`.
Previous reviewed runtime/test-layout snapshot: `852c9f72468207a25e204f94a034fcda31d7460437579c700ce8a24e74cde36a`.
The previous local full suite passed 7040 tests with 104 skips, and both simulation
sets passed 74 checkpoints. Those results retain their original source, base and
test layout; they are not relabeled as final evidence for this merge.
This document is the sole review-hash exclusion. Earlier failures remain below.

The original six acceptance criteria and unchanged AP01 through AP08 definitions
are in [the approved plan](../plans/2026-10-evidence-privacy/PV-02-plan.md).
This review covers the opt-in admission component. It does not certify the
separately owned sanitizer, cryptography, sealed projections, journal cleanup,
erasure, migration or protected-product release gate.

The approved maximum is 44 paths after integrating the newly added reingest
entry points. The implementation changes 37 paths plus this review document (4908 helper-counted changed lines before the report). Existing event-store modules, `fast_ingest`, durable
queue and framework adapters need no edits because the coordinator uses their
existing primitives or intercepts their public engine/client boundary. The
legacy retrieval pipeline remains explicitly unavailable for protected
operations pending its separately owned integration; it was not expanded here.

## Acceptance evidence

| Criterion | Real implementation and component evidence |
| --- | --- |
| AC1, AP01/AP02 | Closed frozen policy validation; actual policy composition and evidence selection; positive public store with none/minimal/full on directory, pack and PostgreSQL. Invalid whole batches leave no events or provider sends. |
| AC2, AP03 | Effective immutable snapshots are persisted with actual events. Caller restrictions compose monotonically. Concurrent request-ID retries and reopen retain the original policy, deadline and envelopes; changed requests or lost commitment keys reject. |
| AC3, AP08 | Required sanitizer/provider/authority interfaces, fixed rejection codes, and guarded actual transmission/publication/disclosure boundaries. Fake denying authority prevents real event/node writes or provider sends, including stale deferred work after reopen and a stale read after its await. |
| AC4, AP04/AP05 | Whole sanitized source unit selection preserves named speaker, effective time, negative polarity and condition. Bounded or ambiguous units reject without truncation or promotion to full capture. Generated structured qualifiers remain in admitted nodes. |
| AC5, AP06 | Real asynchronous pending work has its own protected slot and actual work claim. Successful backend derivation consumes the slot atomically while retaining memory. Transient synchronous preparation succeeds with no durable input, and paused/failed preparation leaves no accepted source/jobs and requires resubmission. |
| AC6, AP07 | Engine and vision entry points reject protected visual input before decoder/provider/blob work. Inspection reports provider names, finite/unlimited choices, failure recovery and explicit incomplete product integration status without opening evidence. |

`tests/privacy_fakes.py` labels the sanitizer, payload encoding, model providers,
clock and request commitment as nonproduction component doubles. Policy
composition, deadlines, evidence choice, backend transactions, node publication,
transport dispatch and fence decisions are real. Model/detector accuracy and
cryptographic isolation are not inferred from these tests.

## Validation record

All paths below are relative to the implementation worktree. Full logs are
retained under the ignored `.claude/worktrees/` task directory. PostgreSQL tests
use only the isolated local `privacy_engine_320` database on port 55432.

| Check | Result | Log |
| --- | --- | --- |
| New policy/admission/fence/ephemeral tests | Initial collection failed with incorrect fixture-module import; corrected once | `privacy320-targeted-1.log` |
| First backend acceptance selection | Exit 0; 45 passed | `privacy320-targeted-2.log` |
| Python/HTTP/MCP/framework/workspace adapters | Exit 0; 6 passed | `privacy320-interfaces-1.log` |
| Expanded acceptance, concurrent retry and restart | Exit 0; 57 passed | `privacy320-targeted-3.log` |
| Ruff initial | Exit 1; new import placement/unused imports corrected once | `privacy320-lint-1.log` |
| Ruff corrected | Exit 0 | `privacy320-lint-2.log` |
| Contract structural validator | Exit 0; frozen scenario expectations and statuses retained | `privacy320-contract-1.log` |
| Final Ruff | Exit 0 | `privacy320-lint-final.log` |
| Strict public API typing, including privacy examples | Exit 0 | `privacy320-mypy-final.log` |
| Wheel and source distribution, offline build | Exit 0 | `privacy320-build.log` |
| Installed wheel with frozen runtime dependency versions | Exit 0; version, public privacy exports and `py.typed` verified | `privacy320-wheel-locked.log` |
| Initial legacy adapter/storage regression selection | Exit 1; 11 failed, 370 passed, 15 skipped in 395.50s | `privacy320-regression-1.log` |
| Current-default simulations | Exit 0; 74/74 checkpoints | `privacy320-simulations-current.log` and `.json` |
| Previous-default simulations | Exit 0; 74/74 checkpoints | `privacy320-simulations-previous.log` and `.json` |

The initial installed-wheel import check also passed (`privacy320-wheel.log`),
but its offline resolver selected newer cached dependencies. The required wheel
check was repeated after synchronizing the isolated wheel environment to a
frozen export of `uv.lock`; no project lock or shared environment changed.
The full pytest suite is deliberately pending until independent review changes
are complete, so its pass covers the final revision.

The initial regression failures had two introduced causes: two assertions caught
a changed legacy invalid-request-ID error message; nine PostgreSQL cases caught
an overbroad guard that treated unrelated legacy objects as protected. The first
repair restores the existing parser and resolves enrollment from each selected
object's own opaque privacy reference/evidence dependencies. It does not infer
authorization from discovered ownership. Same-owner and different-owner legacy
controls, protected denial and missing/foreign ID behavior are included in the
affected validation. Repair cycle 1 passed: 123 tests, 9 expected skips (`privacy320-regression-repair-1.log`, exit 0). The changed metadata lookup also passed 21 legacy metadata tests with 1 expected skip (`privacy320-metadata-repair-1.log`, exit 0). Ruff and the refreshed contract validator passed (`privacy320-lint-repair-1.log`, `privacy320-contract-repair-1.log`). Both introduced causes are resolved. No full selection is rerun
merely to reprint earlier passes.

The 15 original skips come from existing backend-specific cases: three native
Tantivy/USearch faults on PostgreSQL; three durable ingestion cases on their
inapplicable backend; six local derivation/index/timezone/process-exit cases on
PostgreSQL; two PostgreSQL row-lock cases on DuckDB; and one nonfinite legacy
metadata case on PostgreSQL. The database was available. The existing relocated
extension test passed using a disposable test database on the same isolated
server and removed it afterward.

Packaging and simulation results above precede the compatibility repair. The
final wheel and full suite will cover the reviewed final source revision.

No unresolved behavior has reached two unsuccessful repair/validation cycles.
No paid/live model calls or long benchmarks are part of this validation.

## Persisted field compatibility

The additive `privacy_admissions` table stores `PrivacyRecord` version 1. Its
live writer is `PrivacyRepository.accept`; readers are `get`, `request`,
`for_owner`, deferred preparation and disclosure guards. Saved snapshots bind
project, owner, scope, constituent policy IDs/revisions, original admission time
and independent deadlines. Separate evidence and processing slots carry opaque
payload envelopes, immutable object/slot/source context and wrapping selectors.
`replace_envelope` compares the complete expected old body and only changes a
matching slot at the next envelope revision. Successful derivation publication
consumes processing inside the existing DuckDB/PostgreSQL transaction.

Public events keep an empty content envelope and opaque source references. Their
existing hash covers the empty string. Existing receipt, extraction, direct
intent and derivation checksum formats are unchanged. New executable direct
intents and plans contain admitted memory; the content-copy inventory records
these copies and their remaining lifecycle obligations. The retry commitment
uses its own explicit scoped capability and contains no public raw-input hash.

## Independent review

The initial tier-L review covered snapshot
`29a183d7a8f0a0df5398222115927e91ba0e4da4e1c34829b640a0a4769b7116`
against original base `33222b54a85fa90fd23d47ae9a9170564bf24828`. All four reports are COMPLETE. Core combined
correctness, quality, configuration/pattern comparison and runtime wiring;
security and adversarial independently confirmed the same five findings. Data
and persisted-field review passed with no additional finding. The adversarial
review examined exactly eight scenarios, accepting three and requesting the
same five repairs. No infrastructure changes apply.

| Finding | Classification and bounded repair | Focused evidence |
| --- | --- | --- |
| CORE-1 | Introduced temporal API regression: reuse existing validity validator before protected preparation. | Naive, reversed, equal and end-only intervals reject before sanitizer/provider/storage; an ordered aware interval is preserved. |
| CORE-2 | AC3/AP08 dependency bypass: refuse protected operation with optional owner-group linking before effects. | Default startup creates no store; a current-policy request cannot associate with a revoked-policy owner entity. Existing legacy group tests remain included. |
| S1 | AC3/AP08 queue cancellation bypass: reuse `run_async_to_completion` while holding the publication authority lease. | Block real directory/pack vector staging and graph commits, cancel caller, and attempt revision activation. Queued effects settle before activation; cancellation still propagates. |
| S2 | AC3/AP02 raw-input error leak: validate protected MCP arguments at dispatch before FastMCP formats an exception. | Real connected SDK calls with malformed content/nested items, for default and explicit policy, return fixed errors without content or admission/provider/storage effects. |
| S3 | AC1/AC3 pending-work expiry bypass: carry processing status into embedding precheck and the supplied per-send guard. | Expire after extraction and while paused before guarded embedding; no embedding send or publication. Admitted direct memory remains governed by its separate memory deadline. |

The pre-fix reproduction is retained at `privacy320-review-repro.log`: exit 1,
16 failed and 2 positive controls passed, 44 deselected, 35.82 seconds. These are
confirmed prior failures, not passing evidence. Each finding started with zero
repair cycles. All five passed their dedicated cases in the first repair batch. No third cycle has been attempted. The original two compatibility repairs remain resolved and
retain their separate history.

The eight changed review paths are `privacy/admission.py`,
`ingestion/planning.py`, `mcp/server.py`, the admission/fence/interface privacy
tests, `docs/PRIVACY-POLICIES.md`, and the content-copy inventory (only relocated
MCP anchors). No persisted representation, dependency version, historical
checksum or frozen scenario changes in this repair batch.

This review transport file is excluded from snapshot hashing so attaching
reports does not change reviewed source. No source, tests or dependencies are
excluded. Final affected-review verdicts and checks will be recorded before
delivery.


The first repair selection finished with exit 1: 134 passed, 1 failed and 6
expected DuckDB skips in 436.87 seconds (`privacy320-review-repair-1.log`). All
78 privacy tests, including 18 new reproductions/controls, and the existing
owner-group controls passed. The overall selection is not represented as a
pass. The sole failed test was unchanged graph replay
`TestRecordedDevRun.test_without_an_option_the_cities_claim_ranks_low_on_the_hosted_backend[duckdb]`: the fixture reads after a cooperative five-second
extraction budget, while the cities claim is still queued and finishes during
close drain. The same `KeyError: claim` reproduces on the unmodified base in
`privacy320-review-base-graph.log` (exit 1, 21.76 seconds). The base import was
asserted to come from a local archive of commit `33222b54`; executable and lock
hash are recorded in `privacy320-base-provenance.json`. A setup-only archive
extraction compatibility error and resulting no-tests-collected exit 4 remain
separate evidence in `privacy320-review-base-graph-setup.log`. No legacy test
or production behavior was changed to hide the timing assumption.

Ruff and the refreshed contract validator pass on the repaired source
(`privacy320-review-lint-1.log`, `privacy320-review-contract-1.log`, both exit 0).

The paired current-source run also failed with the same `KeyError: claim`
(`privacy320-review-current-graph.log`, exit 1, 22.21 seconds). Its asserted
import path, executable and identical lock hash are recorded in
`privacy320-current-provenance.json`. Both comparisons use one isolated DuckDB
test and the same task database setting. This establishes a failure present
on both sources in this environment; it does not assert behavior on every
machine or convert either result into a pass.

Affected core review is COMPLETE and APPROVED at `f518c9c`
(`privacy320-review-core-repair1.md`): CORE-1 and CORE-2 are resolved; no new
finding. The review also verified the live cancellation, expiry and MCP
dispatch wiring. Other affected reviews and final validation remain pending.

Affected security review is COMPLETE and APPROVED at `f518c9c`
(`privacy320-review-security-repair1.md`): S1, S2 and S3 are resolved; no
additional finding or evaluation requested. Final adversarial confirmation
and required full validation remain pending.

Affected adversarial review is COMPLETE and APPROVED at `f518c9c`
(`privacy320-review-adversarial-repair1.md`): all eight scenario dispositions
are Accept, with the five original findings resolved. Data inventory coverage
is retained from the original complete review because no persisted fields or
schema changed. All required review dimensions are approved. Final Ruff,
strict public typing and wheel/source-distribution build pass; the full suite
is running on this reviewed revision.

Final installed-wheel verification also passes (exit 0,
`privacy320-wheel-reviewed.log`). The isolated site-packages import, privacy
exports/signatures, `py.typed`, version and every installed application
dependency match the locked package environment. The full suite remains the
only active local validation check.

## First final full-suite result

The reviewed snapshot passed final Ruff, strict public API typing, offline
wheel/sdist build and the installed locked-wheel smoke check. The full suite
finished with exit 1: 13 failed, 6939 passed, 104 skipped in 1062.49 seconds
(`privacy320-full-final.log`). No failure is relabeled as a pass. All skip
reasons are retained in the ignored validation manifest from the `-rs` summary.

Seven PostgreSQL feedback failures arise from new protected component fixtures
leaving records in the shared test schema. The global guard correctly denies a
mixed protected store. Two reinforcement failures expose an overbroad fallback
when supplementary evidence is absent for a known legacy node. Two MCP failures
come from the first S2 repair caching environment configuration across reused
lifespans. One new grounding-log assertion assumes a stdout sink. These require
bounded repairs and affected review before delivery. The next S2 and object
enrollment repairs are conservatively cycle 2; the original leak and selected
object assertions passed.

The remaining checkout assertion also fails on unchanged base `33222b54`, with
verified base imports and source bytes (`privacy320-base-checkout.log`, exit 1;
`privacy320-base-checkout-provenance.json`). It assumes a plain clone while this
run uses a worktree. Its out-of-scope behavior is not changed here. The earlier
paired graph-fusion timing failure passed in the full suite, but its preserved
base/current failures remain a follow-up.

## Bounded final-suite repairs

The pre-fix regression selection exited 1 with 6 failed and 2 passed
(`privacy320-final-repro.log`). It reproduces repeated MCP lifespan caching,
actual-lifespan protected dispatch validation and missing/foreign/scope
supplemental evidence handling. Existing same-boundary protected-evidence and
global mixed-store feedback denials pass.

MCP now obtains the active engine configuration from request lifespan context.
Supplemental evidence lookup uses only the actual primary node owner/scope;
it does not authorize a protected object. Missing primary objects still fail
closed. New PostgreSQL component cases use disposable private schemas with no
public-table fallback and remove them at teardown. The real grounding warning
is captured through a local sink without depending on other tests' logging
configuration. The production global feedback guard is unchanged.

The affected selection passed 33 tests in 15.02 seconds, exit 0
(`privacy320-final-repair-2.log`). The PostgreSQL admission/reopen cohort passed
8 tests, with 27 unrelated cases deselected, in 2.25 seconds, exit 0
(`privacy320-final-pg-isolation.log`). Post-test inspection finds zero public
protected records and zero remaining component schemas. The task-owned test
store reset was limited to public test tables, without CASCADE; its 37 original
protected records/owners and database identity are recorded before cleanup.

Ruff passes. The first contract check reported three moved MCP source locators;
refreshing only those line numbers makes the contract validator pass with all
frozen definitions unchanged. The failed locator check is retained. No durable
field, schema, cryptographic representation or capture decision changed.

S2 and object enrollment have completed their second bounded repair cycles.
Both targeted behaviors pass; no third repair is authorized. Logging and test
isolation are resolved in cycle 1. Required affected reviews precede one final
full suite from a verified identical plain-clone validation checkout, using
the same locked environment and task database. No checkout test is changed,
skipped or overridden.


## Final affected review and base integration

All four final compatibility-repair reports approved snapshot `a03a8cff`.
The exact old-base full suite then passed with exit 0: 6965 passed and 104
skipped in 1362.18 seconds (`privacy320-full-clone-final.log`). It ran in a
plain clone with verified source hashes, import paths and locked dependencies;
no checkout override or test skip was added. Earlier base/current timing and
checkout failures remain historical evidence and grouped follow-up work.

Main subsequently added reingestion and version 0.16.0a5. The bounded integration
adds configured or persisted owner/scope protection refusal before reingest
source selection, including dry-run, zero limit and cursor calls. Shared batch,
correction context, retry, planning, queued save and native publication paths
recheck their actual participating identities. Normal protected processing
retains its existing authority and transactional payload consumption. No new
protected reingest feature, durable field or historical checksum format is added.
These per-effect checks do not provide a global cross-process legacy-enrollment
lease.

The unchanged transport validation selects fixed errors from configured or
explicit request policy. A valid typed call on a reopened legacy-configured
engine consults persisted protection. Malformed reingest arguments on that
legacy-configured transport retain legacy validation formatting; they accept
no new source passage and do not load or disclose protected store content.
Security and adversarial reviews independently accepted this documented limit.

The integration's pre-fix reproduction remains exit 1 with seven failures and
28 deselected cases (`privacy320-reingest-prefixed.log`). Its first finite
validation passed with exit 0: 290 passed and six expected backend-specific
skips in 112.41 seconds (`privacy320-reingest-integration-1.log`). This selection
retains successful protected admissions and memory publication alongside refusal
and legacy controls. The existing S2 and object-enrollment cycle-2 limits remain
unchanged; no third repair was attempted.

Core, security, data and adversarial affected reports are COMPLETE and APPROVED
at `b0b1d49b`. Each independently verified the same provisional base and source
snapshot. The reports are retained as `privacy320-review-<role>-reingest.md`.
Unchanged field inventory and eight adversarial scenario dispositions carry
forward; all eight are Accept. There is no new unresolved finding.

| Final-source check | Result | Log |
| --- | --- | --- |
| Ruff | Exit 0 | `privacy320-reingest-ruff-1.log` |
| Strict public typing | Exit 0 | `privacy320-reingest-mypy-1.log` |
| Frozen contract structure and source anchors | Exit 0; 42 copy classes, 145 anchors, 17 surfaces | `privacy320-reingest-contract-1.log` |
| Offline wheel and source build | Exit 0; 0.16.0a5 | `privacy320-reingest-build-1.log` |
| Locked installed-wheel check | Exit 0; site-packages import and dependency versions verified | `privacy320-reingest-wheel-1.log` |
| Exact plain-clone setup | Exit 0; 35 source hashes and helper metadata match, 141 installed distributions match the lock | `privacy320-reingest-clone-provenance.json` |
| Final full suite | Exit 0; 7040 passed, 104 skipped, 1586.17 seconds | `privacy320-full-reingest-final.log` |
| Final current-default simulations | Exit 0; 74/74 checkpoints, zero errors | `privacy320-simulations-current-final.log` and `.json` |
| Final previous-default simulations | Exit 0; 74/74 checkpoints, zero errors | `privacy320-simulations-previous-final.log` and `.json` |

The final suite uses the same isolated task database, no root override, and no
live model credentials. The frozen product scenarios remain `not_run`: component
capability doubles do not certify production sanitizer, crypto or the later
protected-product gate. Actual merged-base source equality is verified. Normal pull-request checks
remain part of delivery.


## Final validation provenance

The final suite used `python -m pytest tests/ -q -rs` with the locked Python 3.11
and all required installed extras, only the isolated task database, and clone
imports verified before execution. It ran once from 16:17:07 UTC on
2026-10-20. It returned exit 0 with 7040 passed and 104 skipped in 1586.17
seconds. The 64 skip-reason groups are unchanged backend-specific, local
benchmark-fixture, optional external-checkout and opt-in stress cases; none
was added for this change. Framework integration tests are included in the
full `tests/` run and do not appear among the skips.

The final log SHA256 is
`cdf8137b888ad21109cad9396f5b7d1d7b52b3cd837382e590bd5c1abc2324fa`.
Both current and previous-default simulation commands then passed sequentially
on the exact final source. Their earlier pre-repair results remain separate.
No tests, assertions, root override or default setting was altered to obtain
these passes.

Actual main `976821c6` and provisional base `01a39705` both have tree
`5d35d9022f8e61732daf74a15c8f74975f53f01b`. Rebase preserved feature tree
`b23457b358463e2efb1a9eff5c173e49f9b0bb0b`; only this excluded report is updated
afterward. All 35 source hashes in the running clone and implementation checkout
matched the reviewed manifest again after pytest completed. This establishes
final-source coverage without a duplicate suite or an assertion that a changed
base was reviewed.


## Optional framework dependency CI repair

PR head `de520084` failed DuckDB Python 3.13 CI run `39461268703` with one
failure, 5898 passed and 1176 skipped in 1143.83 seconds. The combined AP01
framework test in `test_privacy_interfaces.py` imported LangChain unconditionally.
Core jobs intentionally install dev/API/MCP dependencies without framework
extras. The required integration jobs installed both frameworks but selected
the existing integration modules, so they did not run that new combined test.
This is an introduced test-placement regression, separate from the resolved S2
and object-enrollment behaviors.

The repair moves the LangChain and LlamaIndex halves into their existing
integration test modules. Each keeps the real client and adapter call,
`PrivacyDenied` assertion and no-event assertion. Existing positive adapter
tests remain unchanged. No skip statement, optional dependency, runtime code or
CI workflow was added or changed. Both relocated tests execute in the already
required integration selection.

The original failure was reproduced before edits in an isolated locked core
environment with both framework packages absent: exit 1, one failure in 0.11
seconds (`privacy320-ci-framework-prefixed.log`). After the move, the core
selection passes 15 tests with the two existing optional module skips in 3.83
seconds, exit 0 (`privacy320-ci-framework-core-repair-1.log`). The exact required
integration selection passes 48 tests, including both relocated AP01 tests and
all existing positive cases, with no skips in 9.72 seconds, exit 0
(`privacy320-ci-framework-integrations-repair-1.log`). Affected Ruff and
`git diff --check` pass. The dependency/import provenance and complete failed CI
log are retained with hashes in the ignored repair evidence.

This distinct behavior is resolved by its first local repair validation, subject
to affected review and complete required CI on the final pushed head. No unchanged
local full runtime suite, simulations or artifact checks were repeated for this
test-only relocation. The prior 7040-test result remains explicitly prior-layout
evidence, and the original S2/object-enrollment cycle-2 limits are unchanged.


## Current-main group boundary reconciliation

The same initial PR CI run also failed PostgreSQL Python 3.13 with the sole
optional LangChain import failure: 1 failed, 6996 passed, 110 skipped in
1557.70 seconds. The whole run finished with four successful, two failed and
four cancelled jobs. Both backend logs and their actual outcomes remain saved.
Core and adversarial affected reviews approved the relocated mandatory AP01
assertions at snapshot `85c15e90`; no runtime change was needed for that defect.

The published branch then checkpointed that reviewed relocation and normally
merged main `4f23c01e` in commit `779d667b`. The merge preserves both parents and
upstream defaults. Main's new group lookup can select a person after the saved
plan, parse prior source turns into a text-keyed LRU and publish `named_in`
provenance. A pre-fix public-admission reproduction created a protected person
after legacy preparation and observed the forbidden group edge. Its one failed
case, exit 1, is retained in `privacy320-group-prefixed.log`; it was not a setup
or missing-dependency failure.

The repair keeps protected group processing unavailable. Real alpha defaults
with group links enabled return `privacy_configuration_invalid` before store
creation; explicitly disabling links retains admitted memory, immutable policy
and no completed evidence payload. Legacy group reads check the initial event,
fixed owner/scope, authoritative protection and actual returned events/nodes
before caching or name use, with checks after awaits. Native legacy edge writes
validate actual endpoints, primary provenance and any naming event on the held
backend connection, then reuse the existing insert primitive. Accepted queued
writes retain cancellation ownership. No new schema, fields, edge format or
historical checksum is introduced. Optional legacy links remain best effort;
a skipped link does not roll back an already committed legacy derivation. This
is not protected group support or a global cross-process enrollment lease.

The 40 new cases were fixed before execution: one product-default case, twelve
late-member boundaries, twenty-one native-edge cases and six other-owner/scope
legacy controls. Together with thirty existing AP01, CORE-2 and reingest cases,
they pass: 70 passed, no skips, exit 0 in 19.00 seconds. Core-without-frameworks
passes 15 tests with the two existing missing-extra module skips, exit 0 in
5.77 seconds. The exact framework integration selection passes 48 tests,
including both mandatory AP01 adapter refusals and the unchanged positive
controls, no skips, exit 0 in 13.51 seconds. Ruff, strict public typing and diff
checks pass. The frozen contract validator passes with 42 copy classes,
151 exact source anchors and 17 surfaces; original scenarios remain unchanged.
The C06/C09/C32 inventory additions classify upstream group provenance,
repeated-claim references and transient source-cache keys without claiming
production protected support. Existing maintenance warnings in the pre-fix
reproduction remain separate from the demonstrated forbidden edge.

CORE-2's first repair passed its original validation. This new-main closure is
conservatively its second attempt, and the targeted repair passed. S2 and
object-enrollment remain resolved at cycle 2 with no counter reset or code
change. The optional dependency placement repair passed cycle 1. No new full
local suite, artifact build, simulation run or quality benchmark is planned;
required exact-head CI supplies those final checks after affected review.


### Reingest request callback correction

A focused review found that the new group call had bypassed reingest's existing
checked submit. That lost the original whole-request scope selection and saved
plan-reference check after a group read or queue wait. The correction passes
that same request check through scoped reads before and after awaits and uses
the existing checked submit for the native edge operation. Fixed event-scope
native identity checks and cancellation ownership remain. No new native format,
field, schema or cross-process lease is introduced.

Two exact directory-backend regression nodes cover the group read and queued
write boundaries. Their first two fixture preparations failed before activation:
the scripted batch used a legacy unknown polarity that unchecked model-response
validation rejects. Both logs remain setup failures, not runtime defect proof.
Using the existing upstream positive claim contract then reproduced two actual
forbidden group edges after another-scope protected admission, exit 1, two failed
in 2.84 seconds (`privacy320-group-request-prefixed-working-contract-cycle2.log`).

The first corrected selection remains FAILED overall: 2 failed, 23 passed in
17.19 seconds, exit 1 (`privacy320-group-request-repair-cycle1.log`). Both
whole-request refusal and zero-edge assertions passed; the extra reference
assertion failed because the direct-store-only fixture produced no planned
references. The second validation changes only that fixture: it publicly ingests
the same We claim with groups off, closes/reopens with groups on and reingests it,
so the real superseded earlier claim is a nonempty dependency. Both exact nodes
pass in 1.89 seconds, exit 0, and the same 23 affected controls pass in 15.12
seconds, exit 0. No assertion was removed and runtime was unchanged between
these two validations. Changed-test Ruff and diff checks pass; the already
passing runtime Ruff, public typing and contract checks carry unchanged.

The earlier 70 boundary and 60 legacy cases, 15 core cases/two expected framework
module skips and 48 real framework integration cases remain evidence at
`e6bf288b`. They are not relabeled as runs of the callback correction. The two
new cases and 23 affected controls provide its changed-scope evidence. No
unrelated passing cohort was repeated. The exact callback behavior had zero
previous unsuccessful cycles before its first correction; its first complete
selection failed and its second validation passed. Original CORE-2, S2,
object-enrollment and test-placement histories remain unchanged. Source remains
frozen at `95f4b45d`; all four affected reviews approve it. The final required
full suite, build and simulations remain pending in exact-head CI after push.


### Affected review completion

Core, security, data and adversarial reviews are COMPLETE/APPROVE at snapshot
`95f4b45d492286a61348adcc241d3f0aea74d09c27d4ee442f8ce7d2fe8b070b`,
base `4f23c01e53cc534c78f72421c0f62867ca1f8e98`, 37 hashed paths and
4908 changed lines. Each independently matched the source snapshot and covered
the affected group-read, native identity and reingest request/reference paths.
The data review carries the unchanged field/schema inventory; all eight
adversarial scenario dispositions remain Accept. No new blocking finding,
follow-up or additional test cohort was requested.

The complete reports are retained as `privacy320-review-<role>-group.md` with
SHA256s in the delivery provenance. Only this review document changed after
those approvals. The two final regression cases, 23 affected controls and
same-runtime static checks remain the current bounded evidence. Earlier
70/60/core/framework cohorts and the 7040-test full run retain their stated
source stages. Required full backend, integration, build, installed-wheel and
simulation CI on the updated PR head is pending; review approval does not
substitute for that result. All failed evidence and repair-cycle history remain
unchanged.
