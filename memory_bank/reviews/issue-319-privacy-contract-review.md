# Issue #319: privacy contract review and evidence

Status: COMPLETE

Scope is the six original criteria in
[PV-01-plan.md](../../docs/plans/2026-10-evidence-privacy/PV-01-plan.md).
Source baseline is `72360483`; no runtime source, dependencies or stored data change.
The contract freezes future behavior, not a passing security implementation.

## Source-to-inventory traceability review

I traced the architecture's seven original copy surfaces and its required API,
adapter, metadata, log, temporary-file, export and backup additions to 42 copy
classes with exact source anchors. The machine-readable inventory records concrete
fields/response paths, direct holders, current retention, required treatment and
erasure owners. Ordinary response readers are identified separately from direct
database/process holders. Backend placement is explicit, including both DuckDB
layouts and PostgreSQL. Planned vault/key/ledger fields are marked not implemented.

| Architecture surface | Inventory | Source evidence and implication |
| --- | --- | --- |
| Source events and metadata | C01-C03 | DuckDB/PostgreSQL `events` DDL and `Event.compute_content_hash`: body, nested metadata and predictable hashes need separate treatment |
| Claims, notes and graph | C04-C06 | `MemoryNode`, grounding metadata and `MemoryEdge`: quotes, value lookups, names and structured fields are content |
| Derivation, processing and recovery | C07-C10, C22, C37 | Direct-store/extraction/batch/derivation models, work rows and reconstruction registries: prepared text and vectors are durable copies; work rows currently point to source/journal bodies |
| Receipts and queries | C15, C23 | `RetrievalReceipt`, citation/relevance/ranking repositories: query text, execution data, hashes and metadata are not anonymous |
| Lifecycle and organizer | C16-C21, C40 | Lifecycle/TTL/reinforcement/penalty/merge/profile/consolidation/alias records contain complete snapshots; entity snapshot generation itself is currently ephemeral |
| Indexes and staging | C11-C14 | `vector_payloads`, `vector_staging`, USearch file/table, Tantivy and PostgreSQL search sources: deleting only graph/evidence rows is insufficient |
| Encryption and temporary artifacts | C33-C34, C41 | Existing directory encryption has an open plaintext window; native single-file encryption includes WAL/spill; proposed evidence encryption is before storage and uses separate keys |
| Public read/write adapters | C24-C30, C32 | Python facade, HTTP routes, MCP tools/resources, chat history, framework retrievers and explorer all expose sibling paths; generated/provider text needs admission/output checks |
| Operational and external copies | C31, C35-C36, C39, C42 | Exception tracebacks, CLI JSON output, workspace labels, original packs and operator backups require explicit ownership; an engine API cannot certify external deletion |
| Unsupported visual legacy | C38 | `blobs.data`, thumbnail/frame hashes and descriptions exist in local packs; protected v1 rejects new visual input and refuses or explicitly removes unsupported legacy copies during migration |

The premise that encrypting one event column is sufficient is falsified by these
existing copies. The RFC deliberately does not broaden this ticket into fixing
them. Their implementation owners remain the published dependent issues.

## Acceptance coverage

| Original criterion | Contract evidence |
| --- | --- |
| AC1 copy inventory | All 42 records have fields, exact source references, principal, current/proposed retention and erasure ownership; every record maps to a future scenario |
| AC2 profiles, protection and sanitizer | RFC sections 2-5; closed English category/action matrix and 14 versioned recognizers; admitted facts remain sensitive |
| AC3 availability and separate lifecycle states | RFC sections 6 and 8 define source-availability-v1 and erasure-status-v1 with distinct expiry, archival, memory erasure, live purge and backup completion |
| AC4 history/rebuild amendment | RFC section 8 plus RFC-0002/RFC-0014 amendments preserve intact bytes and opaque identity; erased payload/checksum availability is versioned, never forged |
| AC5 maintained crypto/key provider | RFC section 7 selects PyCA AESGCM/RSA-OAEP and a public writer/separate private native-keyring or external provider boundary; same-process/admin and key-loss limits are explicit |
| AC6 original 40 plus 12 | Five original groups of eight and 12 exact named PV-13 cases, each with setup, requirement, outcome, copy/owner and adapter mapping; runtime status is `not_run` |

The original plan supplied category counts, not executable case IDs or a passing
baseline. No case was replaced by a sanitizer case. The separate 24-query parity
and 24-case sealed sets remain assigned to PV-11; this contract does not invent
their measured results.

## Primary dependency evidence

Read on 2026-10-19: PyCA's AESGCM/RSA documentation, Presidio 2.2.364 release and
analyzer/language documentation, package metadata for Presidio 2.2.364 and spaCy
3.8.11, the spaCy en_core_web_sm 3.8.0 model metadata and keyring platform/backend
documentation. The RFC and registry link these primary sources. The selected
model declares spaCy `>=3.8.0,<3.9.0`; selected analyzer/package metadata supports
the repository's Python 3.11-3.13 range. This is source compatibility inspection,
not installation or detector quality evidence. PV-13 locks artifacts/transitives
and verifies compatibility/fixtures before claiming support. No dependency was
installed, no model was run, and no live extraction or benchmark was called.

The pinned upstream PhoneRecognizer source confirms `supported_regions` and
`leniency` parameters. Local evidence stores fetched source hashes/URLs in
`.claude/worktrees/issue-319-evidence/upstream-inspection.json`.

## Validation and review status

`python3 docs/plans/2026-10-evidence-privacy/validate-contract.py`: exit 0.
Verified 42 copy classes, 121 exact source anchors, 17 architecture surfaces,
10 categories, 14 recognizers, original 40 plus 12 named cases, E1-E9 and copy
coverage, 9 published issues, 26 unchanged dependency edges and 17 local links.
Log: `.claude/worktrees/issue-319-evidence/contract-check.log`.

`git diff --check`: exit 0.
Log: `.claude/worktrees/issue-319-evidence/diff-check.log`.
`git diff --cached --check`: exit 0, including the new files.
Log: `.claude/worktrees/issue-319-evidence/staged-diff-check.log`.
Machine-readable command/exit records:
`.claude/worktrees/issue-319-evidence/validation.json`.

Independent review requested with
`housekeeping.py review-plan --base main --risk SECURITY --risk DATA`:

- Base: `72360483cd837d1639f96d153d66c5ab16bce2fe`.
- Snapshot: `e92183f08b5e983d0d8eb49dd5a3fc32ccfdfd3c0d41ecca726f05be1df817f5`.
- Tier L, 11 substantive files, 2,700 changed lines; this review report is
  excluded by the helper's standard review-artifact handling, without a custom
  exclusion. The delivery has 12 files including this report.
- Core: GPT-6 Astra, checklists 1/3/4/5 merged; security: GPT-6 Astra,
  checklist 2; data: GPT-6 Sol, checklist 7; adversarial: GPT-6 Astra,
  checklist 8 with eight scenarios. Infrastructure checklist 6 is not applicable.
- SECURITY and DATA were raised explicitly because this specification governs
  sensitive data, key custody and erasure/migration. NEW_SYMBOLS and
  PERSISTED_FIELD were also detected. The proposed durable fields have no runtime
  writers/readers in this documentation ticket; implementations remain pending,
  not waived or mislabeled complete. The full detected roster is retained.

All four independent reports are COMPLETE at the exact base and snapshot above.
I read their source evidence, assigned checklist coverage and dispositions. There
are no must-fix, should-fix or Fix-now findings. The final review plan retains the
same snapshot, tier, roster and applicability decisions.

| Review | Checklist coverage | Result and evidence |
| --- | --- | --- |
| Core | 1, 3, 4, 5 | PASS; independent source checks substantiate the inventory, all six criteria, configuration comparison and necessity of the contract artifacts. Structural checks are accepted as structural evidence only. |
| Security | 2 | PASS; profile and authorization boundaries, fail-closed sanitation, sensitive copies, purpose-separated keys, restore fencing and history amendments are explicit. No unresolved contract security issue. |
| Data | 7 | PASS; copy placement, stored identity, retention, pending work, recovery and migration are covered. One Low follow-up is handed to the existing purge/migration owners below. |
| Adversarial | 8 | PASS; exactly eight retained scenarios, all Accept, no Fix now, Follow-up or Dismissed dispositions. |

The adversarial report retains these eight scenarios and their source evidence:

1. Metadata, query and generated-output bypass: Accept; AC1/AC2/AC6.
2. Legacy history deletion and retired explorer reads: Accept; AC1/AC2/AC6.
3. Stale policy, retry and reconstruction race: Accept; AC2/AC3/AC6.
4. Erasure, restore and original checksums: Accept; AC3/AC4/AC6.
5. Copied text, hashes and embeddings: Accept; AC1/AC3/AC4/AC6.
6. Key custody, loss and false isolation claims: Accept; AC2/AC5/AC6.
7. Sanitizer failure and semantic damage: Accept; AC2/AC6.
8. Source availability transitions and existence oracle: Accept; AC3/AC4/AC6.

Low follow-up, AC1 implementation handoff: inventory C12 groups DuckDB-only
`vector_staging` with registries that also cover PostgreSQL. C11 already records
PostgreSQL `nodes.embedding`, so no copy is missing and AC1 is satisfied. PV-06
and PV-07 ([#325](https://github.com/dwamianm/prism/issues/325),
[#326](https://github.com/dwamianm/prism/issues/326)) should split or annotate
per-layout fields when using the inventory as the executable purge checklist.
This does not add a criterion to #319 or require a duplicate issue.

Full independent reports are preserved under
`.claude/worktrees/issue-319-evidence/`; their SHA-256 digests identify the
reviewed evidence exactly:

- `review-core.md`: `5b9cda7e108de5273b2f768793e1a00c18e3b9cadb926cdae38fb698ab4c62e7`.
- `review-security.md`: `a52f02cd039a2f0ed922ebe86f8f943e2fd9736d61f4f022a3835e28b182442b`.
- `review-data.md`: `345ede5db23f90f9ee1f982b092cd8232edd583947bfd7ad0ac59cc5f9118b7c`.
- `review-adversarial.md`: `2a1f8ca66d0050ea156166009522d62c7d4615f713e21ed0eaa5326c7a60a8b0`.

Existing PR CI remains required before merge. The unrelated local runtime suite
was not run for this documentation-only ticket. No substantive files changed
during review, so no repeated runtime or contract check is warranted.

One construction-time anchor lookup initially named the wrong alias model file.
Source inspection found `AliasProposalEvidence` in `storage/alias_proposal.py`;
the reference was corrected before the acceptance check. An additional pool
anchor was tightened to `create_pool`. Neither result was represented as a
runtime privacy test. There are no runtime implementation repair cycles here.

## Remaining boundaries

The implementation, migration, detector measurements and 104 backend-case runs
remain pending in their existing tickets. Encryption does not hide admitted
facts. Same-process/local administrator access, misdeclared languages, unsupported
secret formats and external/backup copies remain explicit limits. No new
out-of-scope follow-up issue is required; the sole Low review handoff uses the
existing #325/#326 scope.
