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

1. **EM07, standard migration parity** (both backends). Moved here from #326 by the owner. Without a source grant, results differ only where the legacy side folds a consolidated excerpt into the fact it repeats and the protected side does not. With the runtime grant, standard mode substitutes sanitized source passages by design.
2. **SN11 and SN12** (both backends), pending from #322: pre-write side effects of the query gate, and reintroduction of removed values through generated output. Their tests are expected failures.
3. **Explicit-mapping gaps.** AC05 has no test of a failed audit append. CL01 does not cover metadata keys or speaker and display fields. ER07 does not cover a restore that has only the snapshot ledger.
4. **Adapter subchecks.** A name match is candidate coverage. The gate does not yet prove each case through every listed adapter (HTTP, MCP, LangChain, LlamaIndex, explorer, CLI and the rest).
5. **Quality sets not built:** the 24 fixed storage parity queries and the 24 sealed behavior scenarios.
6. **Detector measures not reported here:** precision and recall on labeled fixtures, benign-text damage, key-provider calls, retained bytes and latency.
7. **Known flaky native crash:** one CI run aborted inside DuckDB's native close in `test_EM07_mixed_saved_family_identity_closure_interruptions`; the rerun and 15 local runs passed.

## Limits

Sanitization detection is English only and can miss values. A protected store refuses plain writes and refuses to open without its runtime; that is the owner's protected-only decision, not a measured result. PostgreSQL WAL, base and temporary files and unmanaged backups were not inspected.
