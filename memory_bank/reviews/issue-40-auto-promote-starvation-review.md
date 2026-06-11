# Code Review — issue-40-auto-promote-starvation

## Files Changed

- `src/prme/storage/graph_store.py` — protocol: added `created_before` / `oldest_first` params to `query_nodes`.
- `src/prme/storage/duckpgq_graph.py` — DuckDB `query_nodes` + `_query_nodes_sync`: SQL `created_at <= ?` predicate + ASC/DESC toggle.
- `src/prme/storage/pg/graph_store.py` — PostgreSQL `query_nodes`: mirrored `$idx` predicate + toggle.
- `src/prme/organizer/maintenance.py` — `_auto_promote`: push age cutoff into SQL, query oldest-first, drop Python age guard.
- `src/prme/organizer/jobs.py` — `_job_promote`: same fix on the scheduled-organizer twin.
- `tests/test_organizer.py` — starvation regression tests (maintenance + job), inclusive-cutoff boundary test, negative assertions.

## Approach Summary

Issue #40 had two halves. The pinned-candidate cap (H8) was already resolved by #38 (predicates pushed into SQL with `limit=500` applied after filtering). The remaining half — auto-promote starvation (M5) — is fixed here: `_auto_promote`/`_job_promote` queried tentative nodes ordered `created_at DESC` with a LIMIT and applied the age cutoff in Python, so older eligible nodes behind a full window of newer nodes were never examined. The fix pushes the age cutoff into SQL (`created_before=cutoff`) and orders oldest-first (`oldest_first=True`); since promotion moves a node out of TENTATIVE, each bounded pass self-advances through the backlog without a persisted cursor.

## Must Fix

None.

## Should Fix

1. Inclusive-cutoff boundary was untested — **resolved**: added `test_auto_promotion_includes_node_at_exact_cutoff`.
2. Job-level test did not assert that sub-cutoff nodes stay tentative — **resolved**: added negative assertions to both new tests.

## Consider

- ORDER BY has no secondary tie-break on equal `created_at`; drainage of exact ties is non-deterministic. Pre-existing property of the DESC path too; not changed here.
- `_auto_promote` and `_job_promote` remain near-duplicate promotion logic; this fix kept them in lockstep but did not deduplicate them (out of scope for a bug fix).
- PG `created_before`/`oldest_first` SQL is structurally verified but only the DuckDB path is exercised by the new tests (PG tests are skipped without a database).

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| SQL injection — `oldest_first` ORDER toggle | PASS | `"ASC" if oldest_first else "DESC"` — bool-gated literal, no user input. DuckDB + PG. |
| SQL injection — `created_before` | PASS | Bound parameter (`?` / `$idx`), never interpolated. |
| user_id / scope scoping | PASS | Unchanged; new conditions narrow, never widen, results. |
| Determinism / append-only | PASS | Read-path only; cutoff derived from `now - promotion_age_days`. |
| External exposure of new params | PASS | HTTP routes use a fixed kwargs allowlist; new params reachable only by internal organizer callers. |

## Pattern Consistency Assessment

Three `query_nodes` signatures are in lockstep (same names, order, defaults, types). Each store's new clause follows its own placeholder idiom — DuckDB `?`/no-counter, PostgreSQL `$idx` with `idx += 1` present. Both promotion call sites updated identically. Matches the #38 "filter-then-limit in SQL" precedent.

## Redundancy Check

No redundancy. `valid_at` filters `valid_from`/`valid_to` (a different concern), so `created_before` fills a genuine gap. `oldest_first` as a bool is minimal (only ASC/DESC needed; an enum would be over-engineering). Old Python age guards fully removed, no dead code. The two new tests cover two distinct production functions.

## Wiring Findings

Wired end-to-end: protocol → both concrete stores apply the params in SQL → `engine.query_nodes` `**kwargs` passthrough forwards them → both organizer call sites pass them. DuckDB async→sync positional order verified consistent. No mock/fake graph store breaks (all use `**kwargs`). No new config/env values.

- Out-of-scope finding (flagged for a human, not fixed here): `.github/workflows/ci.yml` triggers on `master`, but the integration branch is `main`, so CI does not run on `main`-targeted PRs. Pre-existing; unrelated to issue #40.

## Resolution Status

| Finding | Severity | Status |
|---|---|---|
| Inclusive-cutoff boundary untested | Should Fix | Resolved (boundary test added) |
| Sub-cutoff nodes not asserted tentative | Should Fix | Resolved (negative assertions added) |
| ORDER BY tie-break | Consider | Acknowledged, not changed (pre-existing) |
| `_auto_promote`/`_job_promote` duplication | Consider | Acknowledged, out of scope |
| PG path lacks direct test | Consider | Acknowledged (PG tests skipped locally) |
| CI triggers on `master` not `main` | Should Fix (out of scope) | Flagged for human in PR |
