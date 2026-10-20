# Engine epic: Sanitized memory, private evidence, and reliable erasure

Designed 2026-10-19; updated for automatic sanitization and separate repository ownership.

**Destination: `dwamianm/prism`, the open-source PRME engine.** This epic owns nine
engine issues: PV-01–PV-07, PV-11 and PV-13. PV IDs remain stable planning references;
the published GitHub issues are linked below. Policies and numerical defaults remain proposed.

- [Engine issue specifications](issues.md)
- [Engine architecture](architecture.md)
- [Frozen PV-01 contract](../../RFC-0019-Evidence-Privacy.md),
  [content-copy inventory](content-copy-inventory.json),
  [sanitizer registry](sanitizer-contract.json) and
  [40 plus 12 scenario manifest](privacy-scenarios.json). These specify future
  behavior; no runtime privacy test has passed merely because the contract exists.
- [Hosted epic](https://github.com/dwamianm/prism-monorepo/blob/main/memory-bank/plans/2026-10-evidence-privacy/README.md): five separate hosted issues and project settings

## Published GitHub tracking

Published 2026-10-19. [This epic](https://github.com/dwamianm/prism/issues/318) owns only this repository's issues.
[Companion hosted epic](https://github.com/dwamianm/prism-monorepo/issues/279) tracks the other repository.
Native GitHub parent and blocking relationships match the approved dependency graph.

| Planning ID | Published issue                                                                                                                      |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| PV-01       | [dwamianm/prism#319: Specify privacy boundaries and inventory every content copy](https://github.com/dwamianm/prism/issues/319)      |
| PV-02       | [dwamianm/prism#320: Add evidence capture, retention and admission policies](https://github.com/dwamianm/prism/issues/320)           |
| PV-03       | [dwamianm/prism#321: Encrypt evidence with separate keys and fail-closed storage](https://github.com/dwamianm/prism/issues/321)      |
| PV-13       | [dwamianm/prism#322: Sanitize source passages before every admission path](https://github.com/dwamianm/prism/issues/322)             |
| PV-04       | [dwamianm/prism#323: Separate usable memory from sealed source evidence](https://github.com/dwamianm/prism/issues/323)               |
| PV-05       | [dwamianm/prism#324: Protect journals, receipts and asynchronous extraction work](https://github.com/dwamianm/prism/issues/324)      |
| PV-06       | [dwamianm/prism#325: Implement evidence expiry and dependency-aware memory erasure](https://github.com/dwamianm/prism/issues/325)    |
| PV-07       | [dwamianm/prism#326: Migrate and export protected packs with explicit recovery limits](https://github.com/dwamianm/prism/issues/326) |
| PV-11       | [dwamianm/prism#327: Verify privacy contracts and measure sealed retrieval behavior](https://github.com/dwamianm/prism/issues/327)   |

## Request and definition of done

The owner requested private source evidence, deliberate retention/deletion,
troubleshooting-only evidence access, automatic sanitization and tunable hosted
privacy per project. They clarified that engine work belongs in this repository,
with a separate hosted epic consuming released engine functionality.

This design revision is complete when engine and hosted issue bodies live only in
their owning repositories, sanitization and project controls have testable criteria,
all cross-repository dependencies resolve, and the Markdown/link/dependency checks
pass. The design update and subsequent user-authorized issue publication are complete.
Implementation, live model evaluation and deployment remain separate work.

## Recommendation and scope

Sanitize before storing, queueing, embedding, extracting or logging content in both
standard and sealed profiles. Combine supported credential detection, bounded
category rules and a maintained local entity recognizer. Standard mode retrieves
sanitized passages; sealed mode uses admitted memory and separately encrypted
supporting evidence. Direct store, batch/fast ingestion and adapters share the
same boundary. Configured sensitive-category rules also apply to generated text.

Sanitization cannot guarantee that every secret or confidential fact is detected.
Preserve conditions, negations and relationships or reject ambiguous admissions.
Strict applications can admit only approved structured fields and validated values.
Pseudonyms are scoped identities, not a guarantee of anonymity.

In v1, pre-sanitization originals are discarded after transient processing.
Evidence capture `full` means the full sanitized source. The optional sealed vault
also stores sanitized evidence. Unsanitized-original retention is deferred; it
must never appear as an accidental debug or retry copy.

Encrypt retained evidence by default for new protected stores, with separate
write and diagnostic-read capabilities. Missing keys fail closed. The engine
provides backend-neutral policy/key interfaces and local unlock support. It does
not implement hosted organizations, Cognito, project settings tables, or AWS IAM.
Local trusted operator access is not isolation against malicious code in the same
process or an administrator controlling the machine.

A key cannot recognize the intent to troubleshoot. The access boundary comes from
who can decrypt and how capabilities are checked. Hosted key custody differs from
a password manager with client-held end-to-end keys; no such guarantee is claimed.

## Policy contract and proposed defaults

| Control            | Engine contract / starting recommendation                                                                       |
| ------------------ | --------------------------------------------------------------------------------------------------------------- |
| Sanitization       | Versioned balanced/strict/custom policy; supported category/action and language matrix; failures deny admission |
| Credentials        | Mandatory supported credential-blocking baseline for hosted protected use; no claim of universal detection      |
| Evidence capture   | None/minimal/full sanitized text; standard initially full, sealed initially minimal                             |
| Evidence use       | Runtime or diagnostics-only; sealed remains an explicit option pending its quality gate                         |
| Evidence lifetime  | Proposed 30 days; configurable finite duration or explicit unlimited when deployment policy permits             |
| Memory lifetime    | Separate policy, including explicit `until_deleted`; lifecycle archival is not erasure                          |
| Processing work    | Sanitized and encrypted, proposed maximum 24-hour retry deadline, bounded by source policy                      |
| Diagnostic access  | Scoped capabilities and expiry; hosted ceilings/reauthentication are set by the consuming service               |
| Policy integration | Immutable per-operation scoped snapshot/revision and activation fence; no shared config mutation                |

All getters, receipts and adapters identify the effective policy and source
availability without leaking removed text. The sanitized representation has its
own identity, checksum and transformation version. It never masquerades as the
original quotation. Existing stores require migration and copy cleanup.

Evidence expiry can retain deliberately admitted facts. A request to forget a
memory also resolves its affected derivatives, journals, indexes and pending work.
Privacy-authorized erasure amends the append-only contract explicitly; retain only
minimal non-content erasure records and declare unavailable historical payloads.
Backups and exported copies have separate limits; deleting a live wrapped key
alone is not proof of cryptographic erasure from backup copies.

## Acceptance contract

- **E1 — Encrypted storage:** every retained evidence payload and sanitized-input work
  payload in a protected store is encrypted before durable write. Protected
  source-only test markers are absent from unprotected records and artifacts.
- **E2 — Enforced separation:** ordinary Python/HTTP/MCP, export, receipt, graph,
  history and retrieval paths cannot disclose sealed source-only content.
  They may return explicitly admitted active facts, which remain sensitive.
- **E3 — Controlled disclosure:** protected evidence reads require an explicit,
  scoped, unexpired capability and content-free audit support; missing capability,
  audit storage or keys denies disclosure. Hosted human authentication and grant
  issuance are implemented by the hosted epic, not by the standalone engine.
- **E4 — Deliberate retention:** effective capture, processing, evidence retention
  and memory-erasure policies are inspectable. Expired evidence is inaccessible
  even before the asynchronous purge completes.
- **E5 — Complete scoped erasure:** acknowledged erasure fences new writes from
  old jobs, covers the inventoried live copies and derivatives, survives restart,
  and prevents resurrection during supported restore/rebuild operations.
  Status distinguishes live purge, backup expiry and external copies.
- **E6 — Honest migration and quality:** old stores are not relabeled protected;
  migrations are resumable and verified. Storage-only encryption parity holds
  admitted input and policy constant; sanitization-induced changes are measured
  separately and never relabeled encryption-parity successes. Sealed-profile quality is
  measured separately, with no automatic global default change.
- **E7 — Usable controls:** a local operator can inspect
  policy, inspect deletion status and perform an authorized diagnostic workflow;
  interfaces explain permanent key loss and residual backup copies.
- **E8 — Sanitized sources:** all supported admission paths run the versioned
  sanitizer before durable content storage or external processing. Required test
  fixtures leak no prohibited values through sources, claims, embeddings, logs,
  previews, queues or diagnostics. Measure supported detector precision/recall
  and benign-text damage; do not claim universal discovery or anonymity.
- **E9 — Policy integration contract:** the engine accepts immutable scoped
  policy/revision snapshots and enforcement fences without shared global mutation.
  Hosted policy editing, roles, persistence and UI are owned by the hosted epic.

Security pass counts are engineering release evidence, not proof against every
attack. No adoption, revenue or competitive win targets are invented here.

## Engine issues and sequence

| Stage             | Engine issues       | Exit                                                                                           |
| ----------------- | ------------------- | ---------------------------------------------------------------------------------------------- |
| Contract          | PV-01, PV-02        | Copy inventory, supported policy/language/action matrix and public integration contract frozen |
| Admission/storage | PV-13, PV-03        | Shared sanitizer and separate evidence-key interfaces implemented                              |
| Memory lifecycle  | PV-04, PV-05, PV-06 | Retrieval separation, protected recovery and dependency-aware erasure implemented              |
| Portability       | PV-07               | Legacy migration, local controls and export/restore limits verified                            |
| Verification      | PV-11               | Required engine checks and separately measured sanitation/sealed behavior pass                 |

Release a tested engine version before the hosted service updates its package pin.
The engine epic can complete with its local capabilities and integration contract
verified; it does not depend on hosted UI or a cloud deployment. The hosted epic
has its own release gate and cannot claim completion from engine tests alone.

## Non-goals

No universal PII detection, leak-proof memory, custom cryptography, recovery of
removed text, arbitrary plugin execution or end-to-end hosted inference. Raw
original retention, reversible identity recovery and visual inputs in protected
v1 profiles are deferred. Provider-specific key adapters and hosted accounts,
policy persistence, billing, scheduling and UI belong in the hosted repository.

## Bounded validation and delivery

Preserve the original 40 engine privacy scenarios and add exactly 12 sanitization
scenarios: 52 per backend, 104 backend-case executions. Keep the 24-query storage
parity check (same admitted input/policy) and the separate 24-case sealed behavior
set. Retain the earlier unsanitized baseline and report sanitation-induced losses
separately. No passing condition is substituted for a failed original requirement.

Use deterministic local/fake-provider fixtures, no new live model calls. Apply the
engine's required default-change evaluation rule only with a separately approved
budget before changing universal retrieval defaults. Required engine CI runs on
the final revision. One implementation agent is the default; additional reviewers,
cloud work and evaluations require the applicable authorization/budget.

After two unsuccessful repair-and-validation cycles for one behavior, stop that
strategy and report the evidence and a bounded alternative. Findings outside the
criteria become follow-ups. Engine documentation is authored here and synced by
the hosted docs site only after the engine change is committed/released.

## Contract and implementation handoff

PV-01 freezes the library/provider boundary and the English-only versioned
recognizer/action registry in RFC-0019 and its linked JSON artifacts. It assigns
stable case IDs to the original 40 category slots and the 12 sanitation additions;
the earlier plan did not contain a named executable manifest or passing baseline.
PV-02 implements immutable policy snapshots and public enforcement hooks, and
PV-13 implements and validates the selected sanitizer. Neither is implemented by
the contract ticket. Numerical retention recommendations remain proposals;
registry thresholds and bounds are explicit v1 contract choices, not measured
accuracy, latency or security guarantees.

Run the bounded contract check with
`python3 docs/plans/2026-10-evidence-privacy/validate-contract.py` from the repository
root. It checks source anchors, inventory structure, registry closure, original
counts, E1-E9 coverage, dependency consistency and local document links. It does
not execute the future 104 backend-case gate or certify privacy behavior.
