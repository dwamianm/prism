# PV-11 privacy gate report (#327)

Date: 2026-10-29. Engine `main` after #378, #379 and #380, plus the fourth slice. Every planned #327 item is done except EM07, which stays an expected failure by the owner's decision. This report makes no universal privacy or leak-proof claim.

## What was run

- `pv11-gate.py` maps each of the 52 frozen scenarios in `privacy-scenarios.json` to pytest results on DuckDB and PostgreSQL, checks every adapter subcheck against `pv11-case-map.json`, and reports the two quality sets and the detector measures. A case maps to tests named after its ID, plus explicit entries in the case map (AC02, AC05, CL01, ER07).
- Baseline: `tests/test_privacy_*.py` on DuckDB (directory and single file) and a local pgvector pg16, on `8756a540`. 527 passed, 36 skipped, 7 xfailed, 0 failed. `PV-11-baseline.json`.
- First slice: 534 passed, 36 skipped, 7 xfailed, 0 failed. `PV-11-slice1.json`.
- Final run, the same files on the same backends: 554 passed, 36 skipped, 3 xfailed, 0 failed. `PV-11-final.json`.
- Providers were deterministic local fakes. No live or paid model call was made.

## Scenario results, 104 backend-case executions

| Status | Baseline | First slice | Final |
| --- | --- | --- | --- |
| passed | 91 | 95 | 99 |
| gap (no test on that backend) | 7 | 0 | 0 |
| backend independent (stated reason) | 0 | 3 | 3 |
| expected failure (open item) | 6 | 6 | 2 |
| failed | 0 | 0 | 0 |

- EM01, EM06 and AC05 are backend independent: their tests exercise crypto, custody and audit logic and open no storage backend. The PostgreSQL stored-evidence read path is covered by AC02. They are not counted as PostgreSQL passes.
- The two expected failures are EM07 on both backends.

## Adapter subchecks, 121

| Status | Final |
| --- | --- |
| proven | 102 |
| proven by refusal | 18 |
| missing | 1 |

- `async_python` is proven by a case's own engine tests. Every other adapter has explicit tests in the case map, and its subcheck counts only when those tests ran and passed.
- "Proven by refusal" marks an adapter that does not support the protected operation and refuses it before any side effect with a fixed code. The LangChain and LlamaIndex chat-history adapters refuse every protected write; their retrieval adapters are positive in AC08. The ordinary CLI cannot supply a privacy runtime, so its reads refuse to open a protected store.
- The one missing subcheck is EM07's `async_python`, because EM07's own tests fail.

## Quality sets

### Storage parity, 24 fixed queries: passed

- Queries: the 17 questions of `benchmarks/fixtures/fast-cycle/store-v1.json`, then the first 7 of `store-heldout-v1.json`, over their shared 75 memories. The fixture hashes are written beside the test before the baseline capture.
- Edges, each within one backend: a protected DuckDB directory exported and restored into an encrypted single-file pack, and a protected PostgreSQL schema restored into another PostgreSQL namespace. Same policy, keys, runtime grant and fixed clock before expiry.
- Selected record IDs and order, packed sections and context bytes match exactly on both edges.
- The plain-store baseline and the sanitization-induced differences are written beside the test. They are not asserted and do not count toward parity.
- The run failed twice before it passed. Both failures are kept here:
  1. The shared fake embedding gives every text the same vector. So every vector score tied, and the order came from index internals. The quality sets now use a deterministic word-hash embedding.
  2. A real engine bug: a protected store dropped the keyword document of a directly stored entity on rebuild, restore and every single-file pack open. #380 fixed it, and both edges then passed.
- A DuckDB-to-PostgreSQL edge is not part of the set. The two backends use different search engines by design, so moving between them is not a byte-preserving transformation.

### Sealed behavior, 24 frozen scenarios: passed

- Fixture: `tests/fixtures/privacy/sealed-behavior-v1.json`. Six simple facts, six conditions or corrections (the frozen qualifiers Q01 to Q06), six multi-source cases and six source-only or quotation requests.
- Every required admitted fact and qualifier is in the sealed context. No source-only value appears in any response. Every quotation request reports the source unavailable.
- Standard-profile answers to the same scenarios, under a runtime grant, are written beside the test with their differences. No benchmark equivalence is claimed.
- The fixture's first run missed the three conditional qualifiers. Default retrieval excludes a conditional fact whose condition is unresolved, in every store (`prme.retrieval.filtering`). The fixture now asks those three in explicit mode and records their default-mode result. Nothing had been committed before that change.

## Detector measures

The 69 frozen synthetic rows of `tests/fixtures/privacy/sanitizer-v1.json` through `LocalSanitizer.admit`, with the fixture's own custom rules configured. Full results: `PV-11-detector-measures.json`. Analyzer-only precision and recall per category are in `PV-13-results.json` (#322).

| Measure | Balanced preset | Contacts pseudonymized |
| --- | --- | --- |
| Supported values removed or refused, where the policy does not allow the category | all | all |
| Benign rows changed | 0 of 21 | 0 of 21 |
| Unsupported forms removed | 0 of 14 | 0 of 14 |
| Rows refused (credentials and the configured blocked topic) | 15 | 15 |
| Key-provider calls | 0 | 10 |
| Retained bytes of admitted rows | 1,573 of 1,594 | 2,500 of 1,594 |
| Latency per admission, median and p95 | 5.8 ms, 5.9 ms | 5.9 ms, 5.9 ms |

- Person, location and IP address are allowed by the balanced preset, so their supported values are kept by policy, not missed.
- Pseudonym tokens are longer than the values they replace, so retained bytes grow.
- Latency is wall time on the measuring machine and is not portable.

Limits:

- English only. The caller declares the language; no detection runs.
- The recognizers are the pinned local Presidio and spaCy `en_core_web_sm` models plus the bounded rule registry.
- Contextual sensitivity, a value that is private only in its context, is not detected.
- The fixture's unsupported formats are missed, as recorded. That is expected and is not a pass.

## Open items

1. **EM07, standard migration parity** (both backends). Still open. Moved here from #326 by the owner. The owner approved one more repair try on 2026-10-29, and it failed. The test stays an expected failure with its assertions unchanged, and #326 and #327 stay open for EM07 only. The measured differences, all in evidence projection and context framing:
   - The legacy side replaces the consolidated excerpt with its first member, because `evidence_projection_max_sources` is 1. It does not check that the replacement covers the other member.
   - The protected side resolves only event passages, never admitted member nodes, and refuses a replacement that does not cover every dependency of the group. So it keeps the excerpt. The failed try resolved admitted member nodes and counted events covered by a selected member. The cap of one source still left the second member uncovered, so nothing changed.
   - Every protected context opens with a privacy notice line ("Only admitted memory is included..."), so a byte-equal context cannot hold even when the records match. With no grant, each fixture query packs the same records, but the kept excerpt changes the order of two equal-score facts in one of the three queries.
   - With the runtime grant, a note is projected to its sanitized source passage, which has a different record ID. A legacy store has no source passages to project.

   A bounded alternative, not started: count a dependency as covered when it stays in the results as its own candidate, and compare protected contexts after the exact notice line. Both change parity rules, so they need the owner's approval.
2. **Known intermittent native DuckDB abort.** It was seen in CI inside DuckDB's native close (`test_EM07_mixed_saved_family_identity_closure_interruptions`, #374) and inside a privacy repository query (#380's first run). Reruns passed. The likely cause is two threads using one DuckDB connection at once, which DuckDB 2 refuses with "connection is busy"; #377 tracks it.

## Resolved in this ticket

- **SN11 and SN12** (both backends), from #322, in the second slice (#378). Both cases pass with no engine change:
  - SN11 covers the fast batch and the raw-record import refusing a whole batch on a later failing item, a removed contact in a display name, and the query gate. A credential query is refused before any provider send. A removed contact is replaced before transmission and in the receipt.
  - SN12 covers an unissued placeholder and an undeclared field in extraction output, and a closed-schema extra field in generated output. It also covers generated summary and profile text that carries a removed value: that text is refused before any plan, vector or node, and the same paths then publish clean output.
- **Explicit-mapping gaps**, in the third slice (#379).
  - AC05 has `test_AC05_failed_audit_append_fails_closed`. A failed append at the authorized stage never reaches the private key. A failed append at the finished stage returns no plaintext. The refusal carries a fixed code with no provider text.
  - CL01 has `test_CL01_metadata_keys_and_display_fields` on both backends.
  - ER07 needed no new test. Its snapshot-only case is the missing and foreign ledger refusals already in `test_same_lineage_restore_applies_current_ledger_before_recovery`.
- **Adapter subchecks, quality sets and detector measures**, in the fourth slice.
- **Direct-entity keyword documents in protected stores** (#380), found by the storage parity set.

## Limits

Sanitization detection is English only and can miss values. A protected store refuses plain writes and refuses to open without its runtime; that is the owner's protected-only decision, not a measured result. PostgreSQL WAL, base and temporary files and unmanaged backups were not inspected. The quality sets use synthetic fixtures and deterministic fake embeddings; they show storage and privacy behavior, not answer quality.
