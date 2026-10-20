# PV-01 delivery plan

Issue: [#319](https://github.com/dwamianm/prism/issues/319). Base: `main`,
`72360483` (explicit assignment). Scope approved through the parent epic.
Recorded before edits on 2026-10-19. This is a contract ticket, not implementation.

## Original acceptance criteria

1. Every source/raw-input copy in the architecture inventory maps to a concrete
   storage field or response path, access principal, retention policy and
   erasure owner. Include queries, metadata, prepared embeddings and hashes.
2. Define standard, sealed and no-evidence contracts; admitted facts remain
   sensitive. Explicitly distinguish at-rest encryption, service isolation
   and end-to-end encryption. Freeze the supported category/action/language
   matrix and versioned recognizer registry for PV-02/PV-13.
3. Define versioned source-availability states and separate expiry, archival,
   memory erasure, live purge and backup expiry.
4. Amend append-only/rebuild promises for authorized erasure; no claim of
   replaying erased content. Existing history/checksums keep their identity.
5. Select a maintained crypto library and local key-provider boundary; document
   same-process/local-admin limitations and key-loss behavior.
6. Preserve the original 40 privacy-contract scenarios for PV-11: eight each for
   admission/policy, access, copied-content leakage, erasure/recovery and
   encryption/migration. Add the 12 sanitization scenarios in PV-13 without
   replacing the original cases. Every case maps to a requirement and expected result.

Done means the RFC and machine-readable contracts satisfy these six criteria,
the finite traceability/coverage checks pass, required independent reviews are
resolved, and the change is committed, pushed and submitted as a PR to `main`.
It does not mean the proposed privacy behavior exists or its 104 backend-case
executions have passed. The earlier plan allocated 40 category slots, without a
named executable manifest or passing baseline; this ticket names those slots.

## Investigation and approach

The root assumption is that encrypting `events.content` alone cannot isolate
evidence. Source inspection confirms copies in `operations.payload`, graph
content/metadata, `vector_staging.content`, prepared embeddings, receipts,
framework message metadata, explorer responses, and pack/export artifacts.
The inventory records both current behavior and required treatment so proposed
guarantees cannot be mistaken for current implementation.

Sibling readers include `get_event`, `get_extraction`, graph/provenance/state and
aggregate APIs, receipt inspection, Python clients, HTTP/MCP, framework history,
the explorer, local CLI and low-level trusted operator access. Recovery readers
include raw-note materialization, derivation/profile registries and index rebuild.
The immutable snapshot/checksum rules in existing RFCs must remain identifiable.

Use the existing PyCA `cryptography` dependency for authenticated encryption and
public-key wrapping; verify its primary documentation. Freeze a local Presidio
recognizer adapter and English model registry using primary upstream sources.
Do not add either implementation or dependency changes here. A single encrypted
column, whole-pack encryption alone, remote classification of unsanitized input,
and global unkeyed identity hashes are rejected because they do not cover the
observed copies or the approved threat model.

## Exact planned files

- `docs/RFC-0019-Evidence-Privacy.md`: profiles, availability, crypto/provider,
  erasure/history and hosted handoff contract.
- `docs/plans/2026-10-evidence-privacy/content-copy-inventory.json`: source-backed
  fields, responses, principals, retention and erasure ownership.
- `docs/plans/2026-10-evidence-privacy/sanitizer-contract.json`: closed category,
  action, language and versioned recognizer registry.
- `docs/plans/2026-10-evidence-privacy/privacy-scenarios.json`: 40 plus 12 slots,
  E1-E9 coverage, issue ownership and dependencies.
- `docs/plans/2026-10-evidence-privacy/validate-contract.py`: bounded structural,
  source-anchor, dependency, coverage and local-link validation.
- `docs/plans/2026-10-evidence-privacy/PV-01-plan.md`: this plan.
- `docs/RFC-0002-Event-Store.md` and
  `docs/RFC-0014-Portability-Sync-and-Federation.md`: explicit proposed amendment.
- `docs/INDEX.md`, `docs/plans/2026-10-evidence-privacy/README.md` and
  `docs/plans/2026-10-evidence-privacy/architecture.md`: discovery links and
  distinguish frozen choices from implementation.
- `memory_bank/reviews/issue-319-privacy-contract-review.md`: source traceability,
  check evidence, review resolution and known limits.

## Finite validation plan

1. One source-to-inventory traceability review against the pinned base, including
   DuckDB directory/single-file, PostgreSQL, public/adaptor reads and external copies.
2. Run `python3 docs/plans/2026-10-evidence-privacy/validate-contract.py` for exact
   8-by-5 plus 12 counts, requirement mapping, source anchors, registry closure,
   dependencies and local Markdown links. Record real exit status and full output.
3. Run `git diff --check`; inspect the final diff for scope, accidental sensitive
   data, fabricated current functionality and altered historical identities.
4. Request the installed review policy's exact roster using a stable snapshot.
   Resolve only confirmed acceptance/security/regression findings and rerun the
   affected checks. Preserve failed logs and stop a strategy after two failed
   repair/validation cycles for the same behavior.
5. The existing PR CI remains required. This documentation-only ticket does not
   run the unrelated local application suites, backend fixtures, benchmarks or
   new model calls. The parent delivery process verifies final CI before merge.

Security risks are specification gaps: forgotten copied content, accidentally
weakened admission, mistaken key custody guarantees, resurrecting erased history,
and confusing metadata with anonymous data. No runtime/hosted security claim is
made by this change. Runtime implementation and hosted IAM, roles, snapshot
services and deployment remain with their published dependent tickets.
