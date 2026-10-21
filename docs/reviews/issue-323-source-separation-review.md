# Issue #323 source separation review

Status: COMPLETE. Verdict: APPROVE the reviewed prerequisite. Original integration acceptance remains pending.

## Reviewed source and scope

- Reviewed HEAD: `c4531a0e7ad8c3a558eadbe3ed18278ea1f346c9`
- Source tree before this review document: `aa6180ae52aa51edee457b4f4bae3d9b364438e9`
- Base: `53c8b70a8dddb92ec11cfc2582efa00e020479dc`
- Review snapshot: `c9ee661c9c370a0e9cdd96a0972b761d5a30237483e1422c705050f5cff10087`
- Scope: 40 files, 4,374 changed lines; security, data, persisted-field and new-symbol risks.

This document is review transport only. The final snapshot calculation excludes
`docs/reviews/issue-323-source-separation-review.md` explicitly, and excludes no
implementation, tests or relevant dependency. Its addition does not change the
reviewed source. Four independent reviews supply tier-L coverage: CORE covers
checklists 1/3/4/5, SECURITY covers 2, DATA covers 7, and adversarial covers eight
frozen cases under 8. Infrastructure checklist 6 is not applicable.

The six original criteria are retained in PV-04-plan.md: admitted-only sealed
memory; qualifier/time/provenance preservation; ordinary public availability or
denial; source limitations with no automatic vault fallback; the common direct
store sanitizer and labeled sanitized source; explicit unsupported framework
refusal. The fixture and scenario definitions were not weakened.

## Findings and disposition

| Finding | Original obligation | Disposition |
| --- | --- | --- |
| CORE-323-01: default snapshot text lost the future-effective date | AC2 | Resolved using the existing reader-time formatter in summary, generated input and rendered text. Legacy snapshot bytes remain unchanged. |
| SEC-323-1: an earlier contributor expired during a later awaited authorization | AC1/AC3 | Resolved with completed-result disclosure guards checking all contributing lifetimes after awaited guard release, including each iterator yield. |
| DATA-323-01: orphan protected markers could select an owner-wide raw route | AC3/AC6 | Resolved with the existing marker-aware range classifier, including unscoped reads; unrelated legacy owners remain usable. |

Each behavior received one successful bounded source correction and affected
independent approval. Original failures remain historical failures. Nine actual
regression failures were preserved separately from fixture/setup errors. The
completed repair checks passed on directory DuckDB, single-file DuckDB and
PostgreSQL: nine focused regressions, nine ordinary/selected-source compatibility
checks and 23 legacy snapshot checks. No remaining implementation-review blocker
was established. A stale internal admission docstring was also corrected.

The eight original adversarial cases are AP04, AC01, AC04, AC06, AC07, AC08, CL02
and CL07. The review accepted the inspected component behavior, with no new
Fix-now finding. Their shared whole-case states remain not_run until real
dependent integrations execute; a review approval does not promote a test result.

## Upstream composition

The already reviewed source at `eb4576463e5f00f1e8849c539ec627c9ed4bf484` normally
merged upstream #352 at `b5fa5be5294dbf28e63536fce4836dad8644561e` without conflicts
or runtime repair, producing `49b98aeb6de5f6ef77372d37ca11795473ab48f5`.
A subsequent normal merge of the now-merged encryption prerequisite produces the
reviewed HEAD above with exactly the same source tree.

Sixteen upstream files are byte-identical to upstream. The two composed runtime
files, ingestion/pipeline.py and storage/engine.py, and additive configuration
documentation received affected CORE and SECURITY review, both APPROVE. Protected
ingestion remains on the coordinator path; optional legacy extraction windows
and reingest retain their protected-input refusals. Q04 grounding, all three
review repairs, source projections, crypto and sanitization remain unchanged.
DATA and adversarial coverage is retained for unchanged owned source.

The composed source passed 103 window/reingest/privacy-fence checks and eight
protected qualifier/staging/provider executions. Strict public types, scoped
Ruff and structural-contract checks passed. The initial unsupported --no-cov
argument failed before collection; the corrected invocation provides the pass.
Shifted inventory anchors changed only line numbers, not copy classification.

## Evidence and limits

The original `.test` email-removal assertion remains failed evidence outside the
pinned recognizer's supported suffix grammar. The approved admitted-byte oracle
and existing supported-email control do not erase that result or claim universal
sensitive-data detection. Intentionally permitted facts can remain sensitive.

Earlier full suites were interrupted for concrete review defects and then an
upstream source change; neither is a completed pass. Final full Ruff, build and
installed-wheel verification passed at the reviewed HEAD. The full suite and two
required deterministic simulation configurations are delivery checks whose
final results are recorded in the PR. No live answer model or long benchmark is
part of this change.

Real storage, crypto, sanitization and routing are exercised, with named policy,
OS and external-provider fixtures. Actual current-ledger/scoped-revocation/erasure
belongs to #325; durable work, receipt, profile/summary and recovery integration
belongs to #324. These remain original acceptance obligations. CL07 staging and
reopen checks do not establish complete erasure. The PR references #323 without
closing it. No deployment or release is included.

## Immutable review records

The complete original, affected-repair and composition reports remain in the
local delivery evidence directory. These hashes identify the reviewed records:

| Report | SHA-256 |
| --- | --- |
| `review-323-core.md` | `16e88b4dd4663678c2b9447cade5de6f70135e967563e1fe1da9b9cde7c33c76` |
| `review-323-security.md` | `84987aaf4e585836595e019b2575bbd0f398dc0c9596a907e1439382b183bd8c` |
| `review-323-data.md` | `ffd895ea467c848f81a3ea04c97295456dbc4ecdf91a35943c6d951b06d5e36c` |
| `review-323-repair-core.md` | `36fd70b4ce1f809d1e904ab16998d45b028def89657af88b7a91233c40ccae94` |
| `review-323-repair-security.md` | `73b016cebb4fd9f09a6dbec04f22ccf1697b8b7c6cffac21b4668ae4ff9c5507` |
| `review-323-repair-data.md` | `2d5bbbfca89be69010296c819abb8ca9c9bf57325acdf586557bc10976132062` |
| `review-323-adversarial.md` | `da74ff382a2119db2a15c5feb5acbd83c82f18088cbf708ba2e698d5564ce01f` |
| `review-323-upstream352-core.md` | `a1f87da728e125ae7451f827ee71ed13739becb61d0815f7142162b32fbfeab5` |
| `review-323-upstream352-security.md` | `d7ecc4291710e3365311a2b0e4aed579550acb4804ad898f24355a77e9fd02dd` |

## CI fixture correction

The initial PR integration run on Python 3.13 failed three legacy pagination tests
(45 passed): their `_PagedEvents` double lacked the now-required
`MemoryClient.require_legacy_history` method. The original failure remains
preserved. The subsequent local full suite was intentionally interrupted at
1,966 passes and 46 skips because it contained that obsolete fixture; it is not
a completed pass.

At `54aa261ce37e81d74b7ab326bdc24fde882e27d6` the double explicitly authorizes only
its fixture owner, records that call, and asserts authorization before every page.
All original pagination, latest-clear-marker and unsupported-version assertions
remain. Production guards and all runtime source are unchanged. The three
existing integration files pass all 48 tests on local Python 3.11.6; touched Ruff
and diff whitespace pass. The final full suite and required CI remain delivery
checks, with Python 3.13 not inferred from the local version.

Affected CORE review: COMPLETE, APPROVE. This is one successful bounded fixture
correction with zero unsuccessful repair cycles. Earlier source reviews remain
valid for unchanged runtime. Updated source snapshot:
`7f2fb7a340a320c3dcbc9a0d0c2fb1671b9ae20a14bb868d89abf5f75ffde411`
against the same dependency base, 41 source files / 4,466 changed lines, with only
this review transport document excluded. No original acceptance gate changed.

Complete report: `review-323-ci-fixture-core.md`, SHA-256 `9ce8fdb793909961bf621e6a3921b06f1a20778c0fb747345d08f93bf5db0650`.

## Exact-contributor and candidate-settlement repairs, 2026-10-21

Affected review: COMPLETE, APPROVE. One independent affected review covers the
three changed runtime paths and two test paths in repair commit `da8f8694`. No
blocker was found. Prior reviews remain evidence for unchanged scopes.

Exact object/source contexts use authoritative contributors; broad classification,
orphan refusal and final contributor deadlines remain intact. The deadline probe
now exercises A's actual final authorization for exact provenance while preserving
the original A/B broad-read checks. Its initial three setup failures remain saved;
the unchanged production correction has passing evidence for all 24 planned
cases after that probe correction. Nine source-access compatibility cases pass.

A separate deterministic reproduction confirmed that the nested pinned/task
query gather could return a privacy error before its other query finished. The
first correction settles both children and prioritizes privacy denial; its two
focused regressions pass. This does not establish the original allocator-crash
cause. Final complete validation remains required.

Current main `1dcf6abb` merged normally. The reviewed engine AST is unchanged
except docstrings; the other four reviewed files are byte-identical. The upstream
reingest/version changes retain main's implementation and will be covered by the
final suite. No derivation-version bump is needed for these read/lifetime changes.

Affected report: `review-323-cycle3-affected.md`, SHA-256
`6d74613b9cfef0ef3daf2ff2b8bbfb9855a256dc415a79c142c7d5c5340b557f`.
Original #324/#325 integrated acceptance remains pending.
