# Engine evidence privacy: implementation issues

Destination: **`dwamianm/prism`**. These 9 published issue specifications belong to
[this repository's epic](README.md). Live issue links appear with each specification.
PV IDs are stable planning references, not GitHub issue numbers.
Read [the architecture](architecture.md) before implementation.

The [other repository's epic](https://github.com/dwamianm/prism-monorepo/blob/main/memory-bank/plans/2026-10-evidence-privacy/README.md) owns its own issue bodies.
Cross-repository links express dependencies between independent epics, not children
filed in the wrong repository. Preserve others' work in both shared repositories.

## Issue index

| ID                                                                                | Title                                                            | Depends on                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Epic criteria          |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------- |
| [PV-01](#pv-01--specify-privacy-boundaries-and-inventory-every-content-copy)      | Specify privacy boundaries and inventory every content copy      | None                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | E1–E9                  |
| [PV-02](#pv-02--add-evidence-capture-retention-and-admission-policies)            | Add evidence capture, retention and admission policies           | [PV-01](#pv-01--specify-privacy-boundaries-and-inventory-every-content-copy)                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | E1, E4, E7, E8, E9     |
| [PV-03](#pv-03--encrypt-evidence-with-separate-keys-and-fail-closed-storage)      | Encrypt evidence with separate keys and fail-closed storage      | [PV-01](#pv-01--specify-privacy-boundaries-and-inventory-every-content-copy), [PV-02](#pv-02--add-evidence-capture-retention-and-admission-policies)                                                                                                                                                                                                                                                                                                                                                                                             | E1, E2, E3             |
| [PV-04](#pv-04--separate-usable-memory-from-sealed-source-evidence)               | Separate usable memory from sealed source evidence               | [PV-02](#pv-02--add-evidence-capture-retention-and-admission-policies), [PV-03](#pv-03--encrypt-evidence-with-separate-keys-and-fail-closed-storage), [PV-13](#pv-13--sanitize-source-passages-before-every-admission-path)                                                                                                                                                                                                                                                                                                                      | E2, E3, E6, E8         |
| [PV-05](#pv-05--protect-journals-receipts-and-asynchronous-extraction-work)       | Protect journals, receipts and asynchronous extraction work      | [PV-03](#pv-03--encrypt-evidence-with-separate-keys-and-fail-closed-storage), [PV-04](#pv-04--separate-usable-memory-from-sealed-source-evidence), [PV-13](#pv-13--sanitize-source-passages-before-every-admission-path)                                                                                                                                                                                                                                                                                                                         | E1, E2, E4, E8         |
| [PV-06](#pv-06--implement-evidence-expiry-and-dependency-aware-memory-erasure)    | Implement evidence expiry and dependency-aware memory erasure    | [PV-02](#pv-02--add-evidence-capture-retention-and-admission-policies), [PV-03](#pv-03--encrypt-evidence-with-separate-keys-and-fail-closed-storage), [PV-04](#pv-04--separate-usable-memory-from-sealed-source-evidence), [PV-05](#pv-05--protect-journals-receipts-and-asynchronous-extraction-work)                                                                                                                                                                                                                                           | E4, E5, E9             |
| [PV-07](#pv-07--migrate-and-export-protected-packs-with-explicit-recovery-limits) | Migrate and export protected packs with explicit recovery limits | [PV-03](#pv-03--encrypt-evidence-with-separate-keys-and-fail-closed-storage), [PV-04](#pv-04--separate-usable-memory-from-sealed-source-evidence), [PV-05](#pv-05--protect-journals-receipts-and-asynchronous-extraction-work), [PV-06](#pv-06--implement-evidence-expiry-and-dependency-aware-memory-erasure)                                                                                                                                                                                                                                   | E1, E3, E5, E6, E7, E8 |
| [PV-11](#pv-11--verify-privacy-contracts-and-measure-sealed-retrieval-behavior)   | Verify privacy contracts and measure sealed retrieval behavior   | [PV-02](#pv-02--add-evidence-capture-retention-and-admission-policies), [PV-03](#pv-03--encrypt-evidence-with-separate-keys-and-fail-closed-storage), [PV-04](#pv-04--separate-usable-memory-from-sealed-source-evidence), [PV-05](#pv-05--protect-journals-receipts-and-asynchronous-extraction-work), [PV-06](#pv-06--implement-evidence-expiry-and-dependency-aware-memory-erasure), [PV-07](#pv-07--migrate-and-export-protected-packs-with-explicit-recovery-limits), [PV-13](#pv-13--sanitize-source-passages-before-every-admission-path) | E1–E9                  |
| [PV-13](#pv-13--sanitize-source-passages-before-every-admission-path)             | Sanitize source passages before every admission path             | [PV-01](#pv-01--specify-privacy-boundaries-and-inventory-every-content-copy), [PV-02](#pv-02--add-evidence-capture-retention-and-admission-policies)                                                                                                                                                                                                                                                                                                                                                                                             | E2, E6, E8, E9         |

## Validation and delivery rules

- Freeze each ticket's criteria before implementation. A blocker must violate a
  criterion, introduce a regression or identify a concrete security issue; other
  findings are follow-ups. Preserve failed evidence and the original test manifest.
- Use one implementation agent by default. This document does not authorize
  additional agents, paid model calls, cloud resources or deployment.
- Use targeted checks while editing and required full CI on the final relevant
  revision. Share verified evidence across tickets and repeat passing checks only
  for a relevant change or unresolved concern.
- After two failed repair/validation cycles for one behavior, stop that strategy
  and report the evidence and a bounded alternative. Do not waive privacy failures
  to finish inside a budget; a third cycle requires owner approval.
- Final engine CI includes its DuckDB and PostgreSQL suites, lint, public API
  typing, simulations and integration checks defined by its workflows.
- PV-11 owns the finite offline matrix: the original 40 scenarios plus 12
  sanitization scenarios per backend, 24 fixed storage-parity queries and 24 sealed
  scenarios. Preserve the pre-sanitization baseline and report sanitizer-induced
  differences separately. No live model calls; universal default changes require
  their separately approved mandatory answer evaluations.
- Release the reusable engine contract for hosted consumers. Hosted UI, accounts,
  IAM services and cloud release checks are outside this epic's completion gate.

## PV-01 — Specify privacy boundaries and inventory every content copy

Published issue: [dwamianm/prism#319](https://github.com/dwamianm/prism/issues/319)

Repository: `dwamianm/prism` · Stage: contract · Depends on: none.

User story: As an application owner, I need a precise account of what PRME retains
and which components can read it, so I can choose a policy based on actual behavior.

Scope: write an engine RFC and a machine-readable content-copy inventory. Cover
DuckDB directory/single-file and PostgreSQL backends, public Python/HTTP/MCP APIs,
framework/chat-history adapters, explorer, ingestion, storage, retrieval,
organizer, recovery, export and backup boundaries. Record a cross-repository
handoff for hosted IAM, control-plane roles and snapshot handling.

Acceptance criteria:

- [ ] Every source/raw-input copy in the architecture inventory maps to a concrete
      storage field or response path, access principal, retention policy and
      erasure owner. Include queries, metadata, prepared embeddings and hashes.
- [ ] Define standard, sealed and no-evidence contracts; admitted facts remain
      sensitive. Explicitly distinguish at-rest encryption, service isolation
      and end-to-end encryption. Freeze the supported category/action/language
      matrix and versioned recognizer registry for PV-02/PV-13.
- [ ] Define versioned source-availability states and separate expiry, archival,
      memory erasure, live purge and backup expiry.
- [ ] Amend append-only/rebuild promises for authorized erasure; no claim of
      replaying erased content. Existing history/checksums keep their identity.
- [ ] Select a maintained crypto library and local key-provider boundary; document
      same-process/local-admin limitations and key-loss behavior.
- [ ] Preserve the original 40 privacy-contract scenarios for PV-11: eight each for
      admission/policy, access, copied-content leakage, erasure/recovery and
      encryption/migration. Add the 12 sanitization scenarios in PV-13 without
      replacing the original cases. Every case maps to a requirement and expected result.

Validation: one source-to-inventory traceability review and a dependency/coverage
check against E1–E9. No live model calls, benchmarks or implementation spike is
required. If a concrete unknown cannot be resolved by source inspection, record
one bounded spike separately instead of expanding this ticket.

## PV-02 — Add evidence capture, retention and admission policies

Published issue: [dwamianm/prism#320](https://github.com/dwamianm/prism/issues/320)

Repository: `dwamianm/prism` · Stage: engine · Depends on: PV-01.

User story: As an application owner, I want to choose what is stored and for how
long before input is persisted or sent to a provider.

Scope: policy models/configuration, policy inspection, sync/async client, HTTP/MCP
admission and deterministic pre-processing. Keep lifecycle TTL distinct from
content-erasure deadlines. Define policy composition and caller exclusion hooks; PV-13 implements the
shared sanitizer. Expose immutable policy snapshots for hosted consumers without
requiring a hosted account, database schema or global configuration mutation.

Acceptance criteria:

- [ ] Support `none/minimal/full` evidence capture, runtime/diagnostic evidence
      use, finite evidence expiry and explicit memory-retention policy.
      Invalid combinations fail before any durable write/provider call.
- [ ] Persist the effective policy/version with each accepted record; a stricter
      caller policy cannot be weakened by a project default or retry.
- [ ] Define shared pre-side-effect admission hooks before logging, storage,
      embeddings or provider calls; PV-13 supplies their sanitizer implementation.
      Expose immutable scoped policy/revision snapshots and authoritative fence
      hooks that deny stale work before publication, transmission or disclosure.
      Test the contract with a fake denying policy; no hosted account is required.
- [ ] Minimal evidence preserves conditions, negation and attribution. Oversized
      or ambiguous excerpts do not silently become incomplete facts.
- [ ] Separate no diagnostic retention from no durable processing input. The latter
      rejects asynchronous ingestion and explains caller resubmission on failure.
- [ ] Protected profiles reject unsupported visual input before persistence.
      Policy inspection exposes provider use and unlimited retention explicitly.

Validation: the eight admission/policy fixtures from PV-01 across sync/async and
public adapters using fake providers; include rejection before side effects and
old-policy replay. Unit checks of deadlines use a fake clock, not wall-clock sleeps.

## PV-03 — Encrypt evidence with separate keys and fail-closed storage

Published issue: [dwamianm/prism#321](https://github.com/dwamianm/prism/issues/321)

Repository: `dwamianm/prism` · Stage: engine · Depends on: PV-01, PV-02.

User story: As a store owner, I want retained evidence unreadable without its
separate decryption capability, even if someone obtains the memory files.

Scope: versioned evidence envelopes, ciphertext storage for both backends, key
provider interfaces, local provisioning/unlock CLI and missing-key behavior.
Provide an adapter interface for hosted KMS, implemented in PV-08. Protect active
memory through ordinary store encryption separately; it is not anonymized.

Acceptance criteria:

- [ ] Each evidence object uses authenticated encryption and a randomly generated
      data key. Record identity, owner/scope, schema and purpose are authenticated.
      Wrong key/context, swapped ciphertext or corruption fails closed.
- [ ] Only ciphertext and wrapped keys reach evidence storage. No plaintext/key
      appears in manifests, logs, errors, temp files or ordinary export paths.
- [ ] New protected stores require key provisioning; unavailable keys never cause
      plaintext fallback. Recovery copies are separate, deliberate and documented.
- [ ] Local sealed operation separates write/public capability from diagnostic
      private capability using the selected maintained library/provider. The
      normal retrieval process cannot obtain the diagnostic key automatically.
- [ ] Standard and sealed access modes use explicit capabilities. A configuration
      with a permanently loaded decrypting key cannot claim process isolation.
- [ ] Rotation/re-wrapping is resumable; reads identify the exact key version.
      Document that rotation and deletion of a live wrapped key are not proof of
      erasing older backups.

Validation: fixed library-compatible vectors and eight encryption/migration
scenario slots shared with PV-07 on both backends, including interruption and
key loss; synthetic marker scans after normal and failed writes. No custom crypto
or cloud key creation in this ticket.

## PV-04 — Separate usable memory from sealed source evidence

Published issue: [dwamianm/prism#323](https://github.com/dwamianm/prism/issues/323)

Repository: `dwamianm/prism` · Stage: engine · Depends on: PV-02, PV-03, PV-13.

User story: As a developer, I want agents to use admitted facts while source-only
details remain unavailable to ordinary credentials.

Scope: ingestion output, source references, graph/note representations, indexes,
retrieval/packing, exact claim-state APIs and adapter read surfaces. Standard mode
keeps its intentional source-use semantics. Sealed mode is explicit.

Acceptance criteria:

- [ ] Sealed mode indexes/returns only admitted memory and permitted metadata.
      Raw notes, `evidence_quote` and longer supporting passages are not copied
      into ordinary claims, graph responses or packed context.
- [ ] Statement rendering retains necessary conditions, negation, effective time
      and provenance links; a diagnostic source is never promoted into truth.
- [ ] Ordinary event/history, receipt, graph, aggregate, Python/HTTP/MCP and
      framework reads return an availability status or denied response for sealed
      evidence, not a bypass around the retriever.
- [ ] Queries needing source-only text disclose that limitation. No fallback
      decrypt, raw-text search, model-assisted vault access or broad source cache.
- [ ] Direct `store()` applies the same sanitizer before admitting caller text.
      No promise that sealing sources hides an intentionally permitted sensitive
      fact. Evidence checks use the labeled sanitized source representation.
- [ ] Unsupported framework/raw-history features fail explicitly in sealed mode
      rather than silently switching the store back to standard mode.

Validation: the applicable access/leakage fixtures, a mock key provider that fails
the test if ordinary sealed retrieval attempts any evidence decrypt, and fixed
qualifier/correction fixtures. Share saved results with PV-11; no live answer model.

## PV-05 — Protect journals, receipts and asynchronous extraction work

Published issue: [dwamianm/prism#324](https://github.com/dwamianm/prism/issues/324)

Repository: `dwamianm/prism` · Stage: engine · Depends on: PV-03, PV-04, PV-13.

User story: As a store owner, I want failures and restarts handled without creating
an unprotected second archive of my conversations.

Scope: sanitized-input work queue, extraction records, durable derivation plans,
operation snapshots, retrieval queries/receipts, organizer/profile journals,
failure/log paths and restart recovery.

Acceptance criteria:

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

## PV-06 — Implement evidence expiry and dependency-aware memory erasure

Published issue: [dwamianm/prism#325](https://github.com/dwamianm/prism/issues/325)

Repository: `dwamianm/prism` · Stage: engine · Depends on: PV-02–PV-05.

User story: As an end user, I want a forgotten memory removed from the retained
copies the product controls and prevented from reappearing later.

Scope: source-only expiry, source/memory/owner-scoped erasure requests, durable
fences, dependency traversal, purge jobs/status, local maintenance and engine APIs.

Acceptance criteria:

- [ ] Evidence expiry immediately denies evidence reads at the deadline even if
      purge has not run. A source-only purge explicitly reports that admitted
      facts remain; a memory-erasure request includes their affected derivatives.
- [ ] An acknowledged erasure establishes a transactional read/write fence.
      In-flight reveals revalidate; old extraction/organizer jobs cannot publish
      affected content after the fence, including after restart.
- [ ] Purge covers every live copy in PV-01, including graph/history, summaries,
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

Validation: all eight erasure/recovery scenarios on both backends with fake clocks,
concurrent stale workers and shared-provenance fixtures. Verify public responses
and all inventoried logical stores after purge/rebuild. Filesystem residue and
backup limits must be reported rather than hidden behind a passing API test.

## PV-07 — Migrate and export protected packs with explicit recovery limits

Published issue: [dwamianm/prism#326](https://github.com/dwamianm/prism/issues/326)

Repository: `dwamianm/prism` · Stage: engine · Depends on: PV-03–PV-06.

User story: As an existing user, I want to adopt the new policy without losing
usable memory or mistakenly believing old copies have become protected.

Scope: directory/single-file/PostgreSQL schema migrations, protected export/import,
key provisioning/rotation recovery, local CLI policy inspection and restore rules.

Acceptance criteria:

- [ ] Migration dry-run reports plaintext copies, policy changes, key requirements,
      retained backups and unavailable features. No store is labeled sealed until
      all required copy transformations and checks have completed.
- [ ] Migration journals are resumable and respect erasure fences. Legacy records
      keep their original version/checksum identity; unsupported readers refuse
      new protected schemas rather than misinterpreting encrypted fields.
- [ ] Default exports contain encrypted evidence and metadata, never recovery
      secrets. Active-memory-only exports clearly describe that content; plaintext
      diagnostic evidence export is not silently enabled by this ticket.
- [ ] Old source packs and backups are explicitly listed as remaining copies.
      Migration leaves originals intact until an operator deliberately disposes
      of them; no false automatic-erasure claim.
- [ ] Restore/import accepts the current erasure ledger and enforces it before
      serving. A snapshot without a newer ledger cannot claim deletion continuity.
      Document the limits of arbitrary offline portable copies.
- [ ] Local CLI can inspect policy/status, request erasure and perform an explicit
      scoped diagnostic unlock; key loss and unsupported mode errors are usable.

Validation: share the eight encryption/migration scenarios with PV-03, including
legacy fixtures, interruption, wrong keys, rotation and restore of a pre-erasure
snapshot. Compare standard-mode saved retrieval before/after migration at a fixed
clock before expiry. No new benchmark track or cloud resources.

## PV-11 — Verify privacy contracts and measure sealed retrieval behavior

Published issue: [dwamianm/prism#327](https://github.com/dwamianm/prism/issues/327)

Repository: `dwamianm/prism` · Stage: verification · Depends on: PV-02–PV-07, PV-13.

User story: As a developer considering a protected profile, I want evidence about
its actual boundaries and limitations rather than a blanket security claim.

Scope: execute the frozen manifest, standard-mode parity fixtures, a small sealed
behavior set and a reproducible release report. This ticket does not optimize the
retriever or initiate a competitive benchmark campaign.

Acceptance criteria:

- [ ] All original 40 privacy scenarios and 12 new sanitization scenarios pass
      on DuckDB and PostgreSQL: 104 backend-case executions. Add protocol adapters within those cases where applicable;
      document the exact tested storage layouts and versions.
- [ ] Source-only synthetic markers are absent from all prohibited outputs and
      inventoried copies after success/failure/restart. Deliberately admitted facts
      are tested separately and are not mislabeled evidence leaks or anonymity.
- [ ] For 24 fixed existing retrieval queries, storage encryption and migration
      preserve standard-profile selected records and packed text at a fixed clock
      before expiry, holding admitted sanitized input and policy constant. Record
      sanitization-induced differences separately; retain the pre-sanitization
      baseline and never relabel an encryption-parity failure as intentional.
- [ ] Run 24 frozen sealed-profile scenarios: six simple facts, six conditions or
      corrections, six multi-source cases, six source-only/quotation requests.
      All required facts/qualifiers and expected source-unavailable outcomes pass.
      Report standard/sealed differences without asserting benchmark equivalence.
- [ ] Record detector precision/recall on supported labeled fixtures, benign-text
      damage, key-provider calls, retained bytes and latency. Enumerate language,
      recognizer and contextual-sensitivity limits; no universal leak-proof claim.
- [ ] Final engine CI is green. Report test failures and unresolved risks; no
      universal retrieval-default change, new provider calls or quality tuning loop.

Validation budget: one baseline capture and one final execution of the specified
sets, plus at most two repair/targeted-validation cycles for a failing behavior.
Use deterministic fake extraction or fixed existing extraction artifacts; zero
new paid/live model calls. A privacy-mode default change would require a separate
proposal and the engine's mandatory, explicitly budgeted answer evaluations.

## PV-13 — Sanitize source passages before every admission path

Published issue: [dwamianm/prism#322](https://github.com/dwamianm/prism/issues/322)

Repository: `dwamianm/prism` · Stage: engine · Depends on: PV-01, PV-02.

User story: As an application developer, I want automatic privacy processing in
the open-source engine so standard retrieval can use source passages without
retaining values prohibited by my policy.

Scope: shared sanitizer for supported ingestion/import/query and generated-text
paths, a maintained local recognizer adapter, bounded rule registry, sanitized
source provenance, scoped placeholders and strict structured-memory admission.
This feature works without the hosted service. No custom classifier training.

Acceptance criteria:

- [ ] Run the effective policy before content logging, storage, queueing,
      embedding and provider calls for sync/async store/ingest, fast/batch,
      HTTP/MCP and framework/import adapters. Check content metadata and generated
      claims/summaries too. Pre-sanitization originals are never persisted in v1.
- [ ] Implement supported credential blocking and category-specific allow/redact/
      pseudonymize/block rules, versioned presets, supported languages and bounded
      safe custom patterns. Errors/timeouts/unsupported configurations fail closed;
      mandatory hosted restrictions are expressed as reusable engine policy.
- [ ] Optional placeholders preserve within-owner relationships without global
      identity hashes or plaintext reverse maps. Scope tokens by project/owner/
      scope, prevent token spoofing and include mapping state in erasure coverage.
      Do not claim that pseudonymized facts are anonymous.
- [ ] Extract and validate verbatim evidence against the sanitized source's own
      identity/checksum. Retain policy/detector version and non-content action
      counts. Preserve conditions/negation or reject ambiguous claims; never keep
      removed values or raw-value hashes in transformation reports.
- [ ] Strict admission permits only approved fields and validated values. Policy
      inspection reports detector/language coverage and may expose only sanitized
      preview output; no inference that a clean scan proves absence of secrets.
- [ ] Add twelve fixed scenarios: recognized credential, contact identifier,
      placeholder continuity, cross-owner/project isolation, configured sensitive
      topic, condition/negation preservation, unsupported language, supported
      obfuscation, detector failure, timeout, pre-write side effects, and generated
      output reintroduction. Include benign controls and known unsupported cases.

Validation: these twelve cases on each storage backend (24 additional executions),
with relevant adapter assertions within cases. Measure detector misses/false
positives against labeled fixtures, inspect every inventoried copy, and report
supported coverage. Use fake extraction and local fixtures; no new model calls.
PV-11 reuses this evidence and preserves the original 40 privacy cases.
