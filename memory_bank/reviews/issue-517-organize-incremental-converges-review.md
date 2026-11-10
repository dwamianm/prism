# Issue #517 Consolidated Review

Status: COMPLETE

## Review Scope

- Base: `main` at `52a76189ae1ed146236e8cde0776b158ca7d8ced`.
- First-round reviewed snapshot: `f6eb75eda452e53eec40538cd10278656f4fe0ef72e2125069d158918d75495d`.
- Re-review snapshot: `68700cdab0cf41f480e2d7bc753fd9c09be29aa52914d73f2c2bde0a61a74847`.
- Final ship snapshot: `b493ae275a48b6e80a3863aa15573449b28dc2a09694c79491283c412cb563e0` (review report files excluded as transport artifacts). The only delta from the re-reviewed tree is a CHANGELOG heading wording fix; code and tests are unchanged.
- Full suite: `uv run pytest -q` returned 7273 passed, 1560 skipped, 2 xfailed, exit 0, in 37m39s.
- Tier: L. Predicates: DATA, INFRA, NEW_SYMBOLS, PERSISTED_FIELD, SECURITY.
- Required checklists: core (1, 3, 4, 5), security (2), infrastructure (6), data (7), adversarial (8). All five ran in both rounds.
- Evidence: `uv run ruff check src/ tests/` passed; `uv run mypy --strict tests/typing/public_api.py` passed; targeted organizer, deduplication, schema, privacy erasure, and PostgreSQL suites passed; the full `uv run pytest -q` result is recorded in the pull request.

## Files Changed

CHANGELOG.md; docs/RFC-0015-Self-Organizing-Memory.md; documentation/configuration.md; src/prme/cli.py; src/prme/config.py; src/prme/organizer/deduplication.py; src/prme/organizer/jobs.py; src/prme/organizer/models.py; src/prme/organizer/watermarks.py (new); src/prme/privacy/diagnostics.py; src/prme/privacy/read_graph.py; src/prme/storage/duckpgq_graph.py; src/prme/storage/engine.py; src/prme/storage/erasure.py; src/prme/storage/graph_store.py; src/prme/storage/pg/graph_store.py; src/prme/storage/pg/schema.py; src/prme/storage/schema.py; tests/test_organizer_incremental.py (new); tests/test_organizer_incremental_pg.py (new); tests/test_privacy_erasure_retirement.py.

## Approach Summary

Add a per-scope, per-job watermark (`organizer_watermarks`) on DuckDB and PostgreSQL, written after a job's own writes. An opt-in `OrganizerConfig.incremental_enabled` (default false) routes `deduplicate` through a keyset-paged window over nodes changed since the last completed run, records a resume cursor when the budget stops, and reports examined, changed, and finished/stopped counts. Other jobs stay full-scope with a documented reason. A completed run keeps a 60-second overlap so a concurrent write visible just after its timestamp is re-examined. The erasure purge removes an owner's watermark row and clears a matching resume cursor.

## Must Fix

All first-round blocking findings are resolved.

| Finding | Resolution |
| --- | --- |
| Protected facade returned an empty first window (core B1, data F1, security F3) | Fixed: an absent lower bound now means everything up to the ceiling; tested on the facade and SQL paths. |
| Promote age-window union was not keyset-bounded and could livelock (core B2, data F4/F5, adversarial 1) | Fixed by keeping `promote` full-scope and removing its incremental handler; the dead `created_after` scaffold was removed from the protocol and all stores. |
| A failed deduplicate page advanced the cursor (core B3, data F2) | Fixed: the loop breaks on stop or failure before advancing; a regression test now fails page 2 after page 1 succeeds and asserts the cursor stops at page 1. |
| A vector-search failure was counted as examined (data F3) | Fixed: the incremental prefix stops at the first failure and reports `error`; the full-scope path keeps its earlier skip behavior. |
| Snapshot generation's incremental justification was false (core S1) | Fixed by keeping `snapshot_generation` full-scope. |
| PostgreSQL scan exposed a foreign `superseded_by` (security F2) | Fixed: the new scan applies `_owned_view`. |
| Empty user id collided with the unscoped key (security F1, core M7, adversarial 3) | Fixed: scoped keys are `u:` + user id; the empty string is reserved for unscoped. |
| Protected projection dropped `status`/`stopped_on_budget` (data F8, infra 1) | Fixed and tested. |
| Erasure verification treated a re-creatable watermark row as a failed purge (data F-RR1) | Fixed by removing the unsound predicate. The purge still deletes the row and the test seeds it and asserts removal; a later run re-creating it is benign operational metadata, tracked as a follow-up. |
| A vector-search exception produced a silent permanent stall (adversarial re-review 2) | Fixed: incremental failure is reported as `error`, retried from the prefix, and the option-off skip behavior is restored. |

## Should Fix

All applied: docs corrected (failed-run prefix, "changes no memory", 60-second visibility bound, protected-store limitation, index-change blindness); CLI resume message only when a prefix was processed; full-scope deduplicate budget clock restored; `scope_key` used by the erasure delete; schema comments corrected; `created_after` dead scaffold removed.

## Consider (not required)

- A monotonic watermark upsert (`GREATEST`) and a per-scope organize lock. Assessed as follow-up: concurrency produces redundant work and a possible backwards watermark, but no skip.
- A page iterator shared by the incremental jobs. Moot with one incremental job.

## Security Audit Results

No disclosure or injection finding remains. New statements bind all values. The protected facade projects and discloses. The `u:` key keeps scopes apart. The watermark API is metadata-only, in-process, and its resume id never leaves the engine. The erasure purge removes the owner row and clears a resume id naming an erased node.

## Pattern Consistency Assessment

The scan mirrors `query_nodes`/`scan_nodes` naming and returns `MemoryNode` lists; the stores keep their existing lock/pool and `_owned_view` patterns; `JobResult` additions are additive with defaults; the privacy projector allow-lists the new status literals. No duplicate utility was added.

## Redundancy Check

The incremental and full-scope deduplicate paths share `_find_duplicates_in_nodes`, so similarity, provenance, and admission rules cannot drift. The watermark key encoding has one definition. The removed `created_after` scaffold left no dead branch.

## Wiring Findings

DuckDB creates the table on every open; PostgreSQL creates it in the bootstrap transaction and includes it in `_RUNTIME_TABLES`, so a pre-existing deployment re-runs the privileged bootstrap and grants the runtime login INSERT/UPDATE/DELETE. The option is documented in `documentation/configuration.md`. New tests are collected and the PostgreSQL file skips without `PRME_TEST_DATABASE_URL`. CLI and HTTP serialize the new fields without a schema break.

## Data Safety and Read/Write Proof

- `scope_key`, `job`, `completed_through`, `resume_updated_at`, `resume_id` each have a writer and reader on both backends (`duckpgq_graph.py`, `pg/graph_store.py`), exercised by the round-trip and integration tests.
- The table starts empty; absent means a first run with no lower bound. No backfill is needed and no stored memory is rewritten.
- The watermark is written after the job's writes. A stop or error keeps `completed_through` and records only the searched prefix. A completed run writes the ceiling less 60 seconds.
- Retry is idempotent: already-promoted nodes leave the tentative filter, merged duplicates leave the active filter, and merges revalidate inside the write transaction.

## Break Scenarios (adversarial) and Adjudication

| Scenario | Verdict | Reasoning |
| --- | --- | --- |
| Promote age-window defeats the page keyset (first round 1) | Fix now, applied | Removed promote from the incremental set; it stays full-scope. |
| A concurrent write below the ceiling is skipped (first round 2) | Fix now, applied | 60-second overlap re-examines the tail; a write visible later than that is a documented follow-up. |
| Empty scope key poisons an unscoped run (first round 3) | Fix now, applied | `u:` prefix separates scoped keys. |
| Erasure watermark re-created by a later organize (first round 4) | Follow-up | The owner id is already retained by the erasure ledger; hashing or fencing the key is a separate hardening change. The false-failure predicate that made it block erasure was removed. |
| Config/index change does not re-examine old records (first round 5, re-review 1) | Follow-up | Documented in RFC 5.5; clearing the row or running with the option off re-evaluates. |
| Concurrent watermark read-modify-write (first round 6, re-review 4) | Follow-up | No skip found; duplicated work only. A monotonic upsert is a separate hardening change. |
| Protected disclosure-empty page and whole-scope cost (first round 7) | Follow-up | The empty-window bug is fixed; the per-page whole-scope read and cost bound are documented limitations. |
| NULL `updated_at` (first round 8) | Follow-up | The cursor falls back to `created_at`; `MemoryNode` still rejects a NULL row loudly. No supported writer produces one. |
| Search exception stalls the scope (re-review 2) | Fix now, applied | Reported as `error` and retried from the prefix; option-off path unchanged. |

Pre-mortem headline (adversarial re-review): incremental deduplicate silently stops converging the moment its inputs move outside the node table (a re-embedded index or a poison node), while an erased owner's watermark row quietly returns the next time the tenant loop runs. The poison-node case is now loud and retried; the index-change and erasure re-creation cases are recorded as follow-ups.

## Follow-ups Raised

1. Make the remaining organizer jobs incremental where their candidate set is provably write-determined; record the rest as permanently full-scope.
2. Reset or fingerprint the incremental deduplicate watermark when the vector index is rebuilt or the embedding model changes, and cover records materialized with an old event timestamp.
3. Harden the erasure-watermark interaction: key the scope by a keyed hash or fence the write against an advanced erasure epoch, verify the resume-cursor clear, and make the watermark upsert monotonic.

## Resolution Status

| Item | Status |
| --- | --- |
| Core blocking findings | Resolved and re-reviewed |
| Data blocking findings | Resolved; F-RR1 resolved by removing the unsound predicate |
| Security findings | Resolved and re-reviewed; no new exposure |
| Infrastructure finding | Resolved and tested |
| Adversarial fix-now scenarios | Resolved and re-reviewed |
| Adversarial follow-up scenarios | Recorded as follow-ups 1-3 |
| Static checks | Passed |
| Full suite | Recorded in the pull request |
