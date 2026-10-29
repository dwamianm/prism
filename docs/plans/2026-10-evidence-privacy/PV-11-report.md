# PV-11 privacy gate report, first slice (#327)

Date: 2026-10-28. Engine `main` at `8756a540` plus this branch. This is a partial report. The owner chose to close the backend gaps first, publish an alpha that names every open item, and finish #327 in later slices. It makes no universal privacy or leak-proof claim.

## What was run

- `pv11-gate.py` maps each of the 52 frozen scenarios in `privacy-scenarios.json` to pytest results on DuckDB and PostgreSQL. A case maps to tests named after its ID, plus explicit entries in `pv11-case-map.json` (AC02, AC05, CL01, ER07).
- Baseline capture: `tests/test_privacy_*.py` on DuckDB (directory and single file) and a local pgvector pg16, on `8756a540`. 527 passed, 36 skipped, 7 xfailed, 0 failed. `PV-11-baseline.json`.
- Slice validation after the backend gap work: 534 passed, 36 skipped, 7 xfailed, 0 failed. `PV-11-slice1.json`.
- Providers were deterministic local fakes. No live or paid model call was made.

## Scenario results, 104 backend-case executions

| Status | Baseline | This slice |
| --- | --- | --- |
| passed | 91 | 95 |
| gap (no test on that backend) | 7 | 0 |
| backend independent (stated reason) | 0 | 3 |
| expected failure (open item) | 6 | 6 |
| failed | 0 | 0 |

- AP03, AP05, AP07 and AP08 now run on PostgreSQL too. AP03 uses the PostgreSQL workspace.
- EM01, EM06 and AC05 are recorded as backend independent: their tests exercise crypto, custody and audit logic and open no storage backend. The PostgreSQL stored-evidence read path is covered by AC02. These are not counted as PostgreSQL passes.

## Open items

1. **EM07, standard migration parity** (both backends). Still open. Moved here from #326 by the owner. The owner approved one more repair try on 2026-10-29, and it failed; the test stays an expected failure with its assertions unchanged. The measured differences, all in evidence projection and context framing:
   - The legacy side replaces the consolidated excerpt with its first member, because `evidence_projection_max_sources` is 1. It does not check that the replacement covers the other member.
   - The protected side resolves only event passages, never admitted member nodes, and refuses a replacement that does not cover every dependency of the group. So it keeps the excerpt. The failed try resolved admitted member nodes and counted events covered by a selected member; the cap of one source still left the second member uncovered, so nothing changed.
   - Every protected context opens with a privacy notice line ("Only admitted memory is included..."), so a byte-equal context cannot hold even when the records match. With no grant, each fixture query packs the same records, but the kept excerpt changes the order of two equal-score facts in one of the three queries.
   - With the runtime grant, a note is projected to its sanitized source passage, which has a different record ID. A legacy store has no source passages to project.

   A bounded alternative, not started: count a dependency as covered when it stays in the results as its own candidate, and compare protected contexts after the exact notice line. Both change parity rules, so they need the owner's approval.
2. **SN11 and SN12** (both backends), from #322. Resolved in the second slice: both cases now pass on DuckDB and PostgreSQL with no engine change. SN11 covers the fast batch and the raw-record import refusing a whole batch on a later failing item, a removed contact in a display name, and the query gate (a credential query is refused before any provider send; a removed contact is replaced before transmission and in the receipt). SN12 covers an unissued placeholder and an undeclared field in extraction output, a closed-schema extra field in generated output, and generated summary and profile text that carries a removed value, which is refused before any plan, vector or node; the same paths then publish clean output.
3. **Explicit-mapping gaps.** Resolved in the third slice. AC05 now has `test_AC05_failed_audit_append_fails_closed`: a failed append at the authorized stage never reaches the private key, a failed append at the finished stage returns no plaintext, and the refusal carries a fixed code with no provider text. CL01 now has `test_CL01_metadata_keys_and_display_fields` on both backends. ER07 needed no new test: its snapshot-only case is the missing and foreign ledger refusals already in `test_same_lineage_restore_applies_current_ledger_before_recovery`, and the case map now says so.
4. **Adapter subchecks.** A name match is candidate coverage. The gate does not yet prove each case through every listed adapter (HTTP, MCP, LangChain, LlamaIndex, explorer, CLI and the rest).
5. **Quality sets not built:** the 24 fixed storage parity queries and the 24 sealed behavior scenarios.
6. **Detector measures not reported here:** precision and recall on labeled fixtures, benign-text damage, key-provider calls, retained bytes and latency.
7. **Known flaky native crash:** one CI run aborted inside DuckDB's native close in `test_EM07_mixed_saved_family_identity_closure_interruptions`; the rerun and 15 local runs passed.

## Limits

Sanitization detection is English only and can miss values. A protected store refuses plain writes and refuses to open without its runtime; that is the owner's protected-only decision, not a measured result. PostgreSQL WAL, base and temporary files and unmanaged backups were not inspected.
