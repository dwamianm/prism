# Issue #517 Incremental Organizer: Core Review (checklists 1, 3, 4, 5)

Status: COMPLETE

- Base commit: 52a76189ae1ed146236e8cde0776b158ca7d8ced (main)
- Reviewed snapshot: f6eb75eda452e53eec40538cd10278656f4fe0ef72e2125069d158918d75495d (uncommitted work tree)
- Reviewer: core (correctness/logic, code quality and test coverage, pattern consistency/DRY with the full-scope comparison, redundancy)
- No file was edited except this report.
- Verified source: the new and changed files in full, plus `run_job`, `MemoryEngine.organize`, `engine.scan_changed_nodes`, `engine.promote`, `duckpgq_graph.promote/_create_edge_sync/_row_to_node`, `pg/schema._RUNTIME_TABLES`, `privacy/reads.ReadContext.project_node/disclose`, `retrieval/snapshots.generate_entity_snapshot`, `ingestion/entity_merge.py`, `organizer/deduplication.py`, `storage/erasure.py`.

## Assigned checklist

1. Correctness and logic of the incremental path.
3. Code quality and test coverage.
4. Pattern consistency and DRY against the existing full-scope jobs, with a full-configuration comparison.
5. Redundancy and unnecessary code.

Every item was addressed; results are below.

## Summary

The watermark store, the protocol additions, the schema changes on both backends and the option-off path are sound in shape. The correctness of the windowing is not: the age-window disjunct is not part of the keyset, the protected read view treats "no lower bound" as "nothing matches", and a deduplicate page error advances the cursor past unprocessed rows. The tests cover only single-page scopes, so none of these show up in the 13 passing tests. Acceptance criteria 1, 2 and 5 are not met for the cases below.

## Blocking findings

### B1. The protected read view returns nothing when both lower bounds are absent, and `_run_incremental` then advances past the whole backlog

`AdmittedGraphView.scan_changed_nodes` starts `in_window = False` and only sets it when `updated_after is not None` or `created_after is not None` (`src/prme/privacy/read_graph.py:170-181`). The SQL backends add no window condition at all when both are `None` and therefore return every matching row (`src/prme/storage/duckpgq_graph.py:474-492`, `src/prme/storage/pg/graph_store.py:539-557`).

`_run_incremental` passes `updated_after=None` and, for `deduplicate` and `snapshot_generation` always, a `created_after` of `None` (`src/prme/organizer/jobs.py:79-93`); the first `promote` run also has `created_after=None` because `age_floor` is derived from `completed_through` (`src/prme/organizer/jobs.py:268-272`). `MemoryEngine.scan_changed_nodes` routes a protected owner through that view (`src/prme/storage/engine.py:3147-3155`, `src/prme/storage/engine.py:3186-3196`).

Consequence: on a protected store, the first incremental run of all three jobs examines zero nodes, reports `completed`, and `_run_incremental` writes `completed_through = ceiling` (`src/prme/organizer/jobs.py:115-120`). Every pre-existing tentative node older than `ceiling - promotion_age_days`, and every pre-existing duplicate and entity, is then below both the update window and the next run's created band, so it is never examined again. That breaks criterion 1 and the "incremental == full" part of criterion 5 for exactly the deployments that use the privacy boundary.

Fix: an absent lower bound must mean "no lower bound", as in the SQL stores. `in_window` should default to `True` when both bounds are `None`, or the view should be tested against the same window semantics as the SQL.

### B2. `promote`'s age-window disjunct is not keyset-bounded, so the cursor cannot advance past the age band

The scan builds `((stamp, id) > cursor) OR (created_at > age_floor)` and orders by `COALESCE(updated_at, created_at), id` (`src/prme/storage/duckpgq_graph.py:474-497`; the same at `src/prme/storage/pg/graph_store.py:539-562` and `src/prme/privacy/read_graph.py:170-181`). The `created_at` disjunct is fixed for the whole run and is not bounded by `after_id`, so rows already returned stay eligible on every later page. `_promote_incremental` advances `updated_after`/`after_id` from `nodes[-1]` (`src/prme/organizer/jobs.py:317-322`) but the age disjunct ignores that.

Two consequences, both reachable with the shipped defaults:

1. Duplicate re-examination and an inflated `nodes_processed`. Any row in the age band sorts by its own stamp, which is older than the run window, so it is returned again on each page that intersects that band. `processed` increments once per examination (`src/prme/organizer/jobs.py:304-308`), so criterion 4's "how many nodes it examined" is wrong.
2. No progress and no convergence when the age band holds at least `_INCREMENTAL_PAGE_SIZE` (500) rows that stay eligible. The page then repeats the same first 500 rows forever: the `while True` loop is bounded only by the budget (`src/prme/organizer/jobs.py:304-307`), so a run budget-stops and records a resume cursor inside that set (`src/prme/organizer/jobs.py:333-334`). The next run recomputes `age_floor` from `completed_through`, which never advanced, and the cursor does not exclude the age disjunct, so it sees the same first 500 again. This is directly reachable: entity nodes are created TENTATIVE with `evidence_refs=[]` when there is no evidence event (`src/prme/ingestion/entity_merge.py:129-155`), the default `promotion_evidence_count` is 1, and those nodes are never promoted, so they stay in the filter. The age band's width equals the gap between runs, so an infrequent `organize` schedule widens it.

Consequence: criterion 2 ("the next run continues from it") fails for this window and the job can never reach `completed`, so criterion 5's convergence is broken. Mitigation: put the candidate set under the keyset, for example `(stamp, id) > cursor AND ((stamp > run_start) OR created_at > age_floor)`, in all three stores. An independent reviewer (adversarial, scenario 1) reached the same mechanism; this is my independent confirmation, with the additional finding that the same defect is present in the protected view.

### B3. A deduplicate page error advances the cursor past the unprocessed page

In `_deduplicate_incremental`, a page-level exception sets `failed = True` but does not break (`src/prme/organizer/jobs.py:774-779`). The code then falls through to `if stopped: break` (false for an error) and advances `updated_after`/`after_id` to `nodes[-1]` (`src/prme/organizer/jobs.py:781-786`). If a later page succeeds, `last_cursor` points after the failed page (`src/prme/organizer/jobs.py:772-773`), the result carries `_resume` (`src/prme/organizer/jobs.py:803-804`), and `_run_incremental` writes it with the old `completed_through` (`src/prme/organizer/jobs.py:100-114`). The next run starts after the failed page, so its nodes are never examined, which is worse than not resuming: work is silently skipped. Break on the failure, or leave the cursor at the end of the last fully processed page.

## Should fix

### S1. The `snapshot_generation` justification is false, and a future snapshot cache would be stale

The docstring says only entities created or changed since the last run are visited because "an unchanged entity's snapshot is the same view built from the same graph" (`src/prme/organizer/jobs.py:866-868`, `src/prme/organizer/jobs.py:926-929`). A snapshot is built from the 1-hop neighborhood and edges, not from the entity row (`src/prme/retrieval/snapshots.py:142-159`), and creating an edge does not touch either endpoint's `updated_at` (the insert is `src/prme/storage/duckpgq_graph.py:1448-1476`, with no `UPDATE nodes`). An unchanged entity that gains a mention therefore gets no new snapshot while the full job regenerates it. The store is unaffected today because snapshots are discarded, so this does not break criterion 5, but the stated invariant is wrong and a cached snapshot (explicitly planned at `src/prme/organizer/jobs.py:864`) would be stale. Either drop `snapshot_generation` from `INCREMENTAL_JOBS` or document the narrower meaning.

### S2. `promote` completes and advances past a node whose promotion raised

A `ValueError` from `engine.promote` increments `errors` but leaves `status="completed"` and `failed=False` (`src/prme/organizer/jobs.py:310-315`, `src/prme/organizer/jobs.py:330`), so `_run_incremental` sets `completed_through=ceiling` (`src/prme/organizer/jobs.py:115-120`). The node is not retried until something else writes it. The full-scope job retries every run because it re-queries the oldest tentative nodes (`src/prme/organizer/jobs.py:214-239`). Most `ValueError`s are the benign already-promoted race, but the incremental mode now treats any of them as done. Consider treating an error count above zero as a stop, or re-examining error rows on the next run.

### S3. Test coverage does not prove the stated properties

`tests/test_organizer_incremental.py` uses at most 20 nodes (`:206-232`) and `tests/test_organizer_incremental_pg.py` uses 4 (`:90-124`), so no test exercises a second page. Specifically missing:

- A page boundary above `_INCREMENTAL_PAGE_SIZE`, which is the only way B2 surfaces.
- A key-tie case on `updated_at`. The PG test uses distinct minutes (`tests/test_organizer_incremental_pg.py:94-124`); DuckDB has no keyset test at all.
- The `created_after` disjunct. The one age-window test has a single node (`tests/test_organizer_incremental.py:267-295`), so the disjunct never interacts with the cursor.
- The protected `AdmittedGraphView.scan_changed_nodes` path (B1 has no test).
- The erasure change: no test references `organizer_watermarks` outside the incremental test, so `src/prme/storage/erasure.py:682-697` is unexercised, and its `DELETE` fires on every purge step (see M5).
- `snapshot_generation` incremental behavior; it runs in the 10k test only, with nothing to find.

Criterion 5 asks the tests to prove convergence, resume and incremental == full. They currently prove those on single-page scopes only.

### S4. Protected-store cost is not bounded by the page and is not measured

`AdmittedGraphView.scan_changed_nodes` iterates `_all_nodes` over the whole scope, materializes and projects every node (`project_node` performs a repository lookup per node, `src/prme/privacy/reads.py:217-247`), sorts, and only then slices to `limit` (`src/prme/privacy/read_graph.py:162-183`, `src/prme/privacy/read_graph.py:105-115`). The 10,000-node criterion 6 test uses an unprotected engine, so the protected path is not measured; at 10k nodes with a per-node admission lookup it will not meet the one-second bound. It also contradicts the purpose of the option, which is to avoid a full scan.

## Minor findings

- M1. The RFC says "A failed run writes neither, so the window re-runs" (`docs/RFC-0015-Self-Organizing-Memory.md:489`), but `_run_incremental` writes the processed prefix on `status="error"` (`src/prme/organizer/jobs.py:100-114`), and its own docstring says so (`src/prme/organizer/jobs.py:70-73`). Fix the RFC sentence.
- M2. The CHANGELOG says "A second run over an unchanged scope writes nothing" (`CHANGELOG.md:12`). Every completed incremental run rewrites its watermark row with a fresh `updated_at` (`src/prme/storage/duckpgq_graph.py:534-558`). The intended claim is that the memory store is unchanged; say that.
- M3. The CLI prints "Stopped on the budget; the next run resumes here" whenever `stopped_on_budget` is true (`src/prme/cli.py:624-625`), including stops with no cursor, where `_run_incremental` writes no resume point (`src/prme/organizer/jobs.py:104`). The message is then false.
- M4. `updated_at` is `COALESCE`d in SQL with a comment about legacy NULL rows (`src/prme/storage/duckpgq_graph.py:466-467`) but the cursor is built from `node.updated_at` in all three loops (`src/prme/organizer/jobs.py:309`, `:319`, `:334`, `:773`, `:783`, `:804`, `:967`, `:976`, `:992`). A NULL would restart the page and raise `AttributeError` on `.isoformat()` when a resume is written. `MemoryNode.updated_at` is currently non-optional (`src/prme/models/base.py:35-37`), so this is latent rather than live, but the SQL and the Python disagree about whether NULL exists.
- M5. `src/prme/storage/erasure.py:691-697` deletes the owner's watermark row on every purge step, before all targets in the batch are gone. It is idempotent, but if the job resumes, the final steps no longer have the row. It is also outside the per-node loop while the resume-id clear at `:682-690` is inside it, which reads as accidental; the scope delete can be hoisted next to `progress["done"]`.
- M6. Redundant code: `except (ValueError, Exception)` is just `except Exception` (`src/prme/organizer/jobs.py:971`; copied from the existing full path at `:904`). `isinstance(resume_updated_at, str)` is always true because `_resume` is built with `isoformat()` (`src/prme/organizer/jobs.py:106`, `:334`, `:804`, `:992`).
- M7. `scope_key(None)` and `scope_key("")` both return `""` (`src/prme/organizer/watermarks.py:35-37`). A caller that passes an empty user id shares the unscoped operator watermark, so one can advance it past the other's work. `scan_nodes` rejects `user_id == ""` (`src/prme/storage/duckpgq_graph.py:729-734`); the watermark layer does not reject it.
- M8. Concurrent runs of the same job in one scope race on the single upsert (`src/prme/organizer/jobs.py:77-120`, `src/prme/storage/duckpgq_graph.py:540-558`). Last writer wins; the worst observed outcome is re-work, not a skip, but nothing serializes the two runs.
- M9. Reporting semantics diverge between modes: the full `promote` counts only evidence-eligible nodes as processed (`src/prme/organizer/jobs.py:232-238`) while the incremental one counts every examined node (`:304-308`), and the full `deduplicate` reports `len(duplicates)` while the incremental one reports nodes searched (`:668` vs `:790`). Criterion 4 says examined, so the incremental numbers are the right ones; the two modes should agree or the difference should be documented.
- M10. The three incremental loops repeat the same page, stop and cursor scaffolding (about 40 lines each, `src/prme/organizer/jobs.py:283-335`, `:723-805`, `:942-993`). A shared page iterator would have made B2 and B3 a single fix instead of three. Not required for this ticket.
- M11. Option-off behavior shift: the full `deduplicate` path now starts its budget clock after `generation_search` and `query_nodes` (`src/prme/organizer/deduplication.py:86-97`, clock at `:141`) where it used to start before them, so it gets slightly more effective budget than before. Small, but it is a change on the default path.
- M12. `MemoryEngine.scan_changed_nodes` is declared `async def ... (self, **kwargs)` (`src/prme/storage/engine.py:3147-3155`) while the rest of the surface is typed. Style only.

## Criterion-by-criterion result

| # | Criterion | Result |
|---|---|---|
| 1 | Per-scope, per-job watermark written after the job's writes | Store, schema and ordering are correct. Fails the "changed since the last completed run" part on protected stores (B1) and re-examines rows in the age band (B2). |
| 2 | Budget stop records a resumable point; the next run continues | Works for a single-page promote or dedup stop (tests). Fails for the age band (B2) and after a dedup page error followed by a successful page (B3). |
| 3 | A second run with nothing new changes nothing and finishes quickly | Holds in the tests for an unchanged scope. The watermark row is rewritten each run (M2). A scope with a full age band of unpromotable nodes never finishes and never converges (B2). |
| 4 | organize() reports examined, changed, finished/stopped per job | `status`, `stopped_on_budget` and the counts are reported and the CLI prints them. The counts are inflated by B2 and use different meanings in the two modes (M9); the CLI resume message can be false (M3); full-scope jobs still report `completed` when they break on budget. |
| 5 | Tests prove convergence, resume, incremental == full | Not met: single-page scopes only, no protected path, no error path, no multi-page, no erasure (S3). |
| 6 | Under 1 second at 10,000 nodes with nothing new, DuckDB and PostgreSQL | Passes on the committed DuckDB test; the PG test exercises the same path but needs a live server. Not measured for protected stores (S4). |
| 7 | Defaults and a single explicit organize() call unchanged | Met: the option is off by default (`src/prme/config.py:630-640`), `run_job` skips the wrapper (`src/prme/organizer/jobs.py:175-179`), handlers default `window=None`, and a default run writes no watermark. Only M11 is a residual default-path change. |

## Unresolved risks

- Whether `updated_at` can be NULL in a supported pre-migration pack. If yes, M4 is a live crash on a budget stop.
- Whether a protected store is expected to run the organizer with `incremental_enabled=True`. If it is out of scope, B1 should be documented as such and the option should refuse protected stores rather than silently skip their backlog.
- The full test suite was still running at review time. The committed suites cited by the worker (13 incremental tests, targeted organizer and schema suites, PG integration, ruff, mypy) were treated as passed; I did not re-run them.

## Clean results (one line each)

- Watermark DDL and upsert on both backends: keys, types and `ON CONFLICT` are correct (`src/prme/storage/schema.py:231-246`, `src/prme/storage/pg/schema.py:235-245`).
- `organizer_watermarks` is added to the PG runtime table set and created by `initialize_pg_database` (`src/prme/storage/pg/schema.py:29`, `:377`); a runtime re-attestation is required after this schema addition, which is the intended behavior of `validate_pg_runtime`.
- Protocol additions are structural only; no `isinstance(..., GraphStore)` call exists to break on the new methods.
- PG placeholder numbering, `LIMIT $idx` and `::uuid` casts in `scan_changed_nodes` are correct (`src/prme/storage/pg/graph_store.py:509-564`).
- DuckDB parameter order matches the `?` placeholders in the window clause (`src/prme/storage/duckpgq_graph.py:474-499`).
- `(COALESCE(updated_at, created_at) = ? AND id > ?::UUID)` gives a correct exclusive tie-break on non-NULL rows, matching the `ORDER BY ... , id ASC`.
- `_run_incremental` captures the ceiling before any work and does not advance `completed_through` on a stop or error, so records written during a run land in the next window (`src/prme/organizer/jobs.py:76-120`).
- A completed run clears `resume_updated_at`/`resume_id` through the upsert defaults, covered by the round-trip test (`tests/test_organizer_incremental.py:141-148`).
- `_resume` is removed from the exposed `details` before the result is returned (`src/prme/organizer/jobs.py:98`).
- The `deduplicate` prefix accounting only moves the cursor over `completed` nodes (`src/prme/organizer/jobs.py:768-773`), and `merge_duplicates` revalidates and is idempotent (`src/prme/organizer/deduplication.py:287-334`).
- The `find_duplicates` refactor preserves the full-scope semantics, including the partial return on budget exhaustion (`src/prme/organizer/deduplication.py:141-167`, `:227`).
- Erasure clears an unscoped row's resume id rather than retaining the erased node id, and deletes the owner's scoped rows (`src/prme/storage/erasure.py:682-697`); `str(intent.owner_id)` matches the `nodes.user_id` convention used elsewhere in the same purge.
- CLI, CHANGELOG, configuration.md and the RFC document the new option; no cross-reference to the renumbered RFC sections is broken.
- Job names in `INCREMENTAL_JOBS` all exist in `ALL_JOBS` and in the dispatch table (`src/prme/organizer/watermarks.py:30-32`, `src/prme/organizer/jobs.py:33-45`, `:158-169`).
- No import cycle between `watermarks.py`, `jobs.py` and the graph stores (the store imports `OrganizerWatermark` lazily or under `TYPE_CHECKING`).
- `JobResult`'s new fields are additive with defaults, so existing constructors and `model_dump()` callers keep working (`src/prme/organizer/models.py:17-31`).
