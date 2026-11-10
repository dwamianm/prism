# Review: issue #525, harden the organizer watermark, its erasure interaction, and concurrent upserts

## Review Scope

- Repository: `/Users/dwamianm/Sites/prism`
- Base branch and commit: `main` at `56a7d6a86e07d4060d6e0121622488106880552a` (the branch was rebased onto this commit after review; the rebase re-parented the same source and tests)
- Reviewed snapshot: `32f2016f899098f56ff820b8f76ca141860cdc6da50087a5573c374ef44e7a12` (initial), re-reviewed at `9cd7c6d3460a1f34637fc4baf905573132937d1e40ec312820403ca8f926a326` (post-fix delta); the rebased final tree is `7eca42f8dcd8f14e6ed8b73d17e8f1d8c0c154d27adc6051b0da01d8764ded52`. The final tree adds the self-healing verification described under Resolution Status.
- Tier: L (helper classified `NEW_SYMBOLS`, `SECURITY` and `DATA` risks; `INFRA` and `PERSISTED_FIELD` false). `reviewTier` is unset in `.claude/github-ticket-loop.config.json`, so the default applied.
- Required roster: core (checklists 1, 3, 4, 5), security (2), data (7), adversarial (8). Checklist 6 (infrastructure) was not applicable.
- Acceptance criteria: (1) a post-erasure organize cannot re-create a plaintext owner watermark key; (2) erasure verification covers the resume cursor; (3) concurrent runs converge on the furthest completed position and the newer resume cursor.

## Files Changed

- `src/prme/organizer/watermarks.py`
- `src/prme/organizer/jobs.py`
- `src/prme/storage/duckpgq_graph.py`
- `src/prme/storage/pg/graph_store.py`
- `src/prme/storage/engine.py`
- `src/prme/storage/erasure.py`
- `src/prme/storage/graph_store.py`
- `src/prme/storage/schema.py` (unchanged; no schema change)
- `docs/RFC-0015-Self-Organizing-Memory.md`
- `CHANGELOG.md`
- `tests/test_organizer_incremental.py`
- `tests/test_organizer_incremental_pg.py`
- `tests/test_privacy_erasure_retirement.py`

## Approach Summary

- A watermark write for a scope whose owner has an owner-wide memory erasure on record is refused. The owner id is parsed as a UUID first, so a non-UUID or empty id writes normally. The writer re-checks after the upsert and deletes the row it just wrote if an erasure appeared in the check-to-write window.
- `scope_key` stores a UUID owner id in canonical text, the same form the purge deletes. `scope_owner` raises for a non-empty key with no `u:` prefix.
- The erasure verification stage re-applies the purge's scope delete for an owner-wide memory intent and its cursor clear for an erased node, then fails if either survives.
- The DuckDB and PostgreSQL upserts keep the furthest `completed_through` and the lexicographically newer `(resume_updated_at, resume_id)` cursor; a completed run clears a cursor only when it does not point past its completed position.
- The engine method returns a bool and `_run_incremental` records `details["incremental"]["watermark"] = "not_written_erased_scope"` when the write is refused.

## Must Fix

| ID | Finding | Status |
| --- | --- | --- |
| Core F1 / adversarial 3 | `owner_wide_erasure_requested` sent a non-UUID scope owner to the UUID `owner_id` column, raising on DuckDB and PostgreSQL and logging the plaintext id. | Fixed. The id is parsed as a UUID and a non-UUID id returns False. Covered by a test on a store with real privacy tables. |

## Should Fix

| ID | Finding | Status |
| --- | --- | --- |
| Core F2 / security D1 / data G1 | The first delta regressed a verify failure to `stage="sql"`, but `_purge_sql_step` returns early when its saved progress is `done`, so the purge never re-ran and the erasure could cycle at verify forever. | Fixed by choosing the reviewers' alternative: the verify stage re-applies the scope delete and the cursor clear itself, then confirms absence. The stage regression was removed. Covered by the owner-scope and resume-cursor tests. |
| Core F3 / security F1 / data F1 | The guard is a read followed by a separate write, so a purge could fit between them and the write could re-create the plaintext row. | Fixed by the post-write re-check and `delete_organizer_watermark`, plus the verification scope check. Every interleaving now ends with the row removed: if the intent commits before the post-check, the post-check deletes; if after, the purge runs later and deletes the already-written row. |
| Core F6 | Test gaps around `scope_owner`, the `not_written_erased_scope` detail, the non-UUID path, and the post-write delete. | Fixed with the tests listed below. |
| Security F2 / adversarial 3 | The non-UUID guard crash and the plaintext id in the exception log. | Fixed with the UUID validation (same change as Must Fix). |
| Security F4 | `scope_owner` treated any key without the `u:` prefix as the operator scope. | Fixed: a non-empty key with an unknown prefix raises `ValueError`. |

## Consider (adjudicated)

- Core F4: an incoming cursor is not compared with the existing completed position, so a stopped run that writes second can leave a cursor behind the completed position. Accept. The next run re-scans a range it already processed; nothing is skipped. Recorded in RFC-0015 section 5.5.
- Core F5: no index on `organizer_watermarks.resume_id`. Accept for this change. The table holds one row per scope and job, the erasure path is rare, and the purge already filtered on this column before #525. A new index would need a privileged PostgreSQL schema re-run.
- Security F3: the store-level `set_organizer_watermark` is callable without the erasure refusal. Accept with documentation: the `GraphStore` protocol now says a production writer must call the `MemoryEngine` method; no in-tree caller bypasses it.
- Data F2: stale cursor when a stopped run writes second. Accept; safe, documented in the RFC.
- Data F3 / adversarial 7: an owner-wide erasure permanently disables scoped incremental watermarks for that owner. Accept as the intended conservative rule and documented in the module docstring, the engine docstring, the RFC and the CHANGELOG.
- Adversarial 8: every scoped watermark write parses the owner's erasure intents. Accept. Incremental is opt-in and the intent table is small; the two parses per organize are negligible.
- Security/Data residual: a crash between the upsert and the post-write delete can leave a row. Accept. Narrow; the verification scope check covers an owner-wide intent that is still in flight, and a later organize refuses without writing.
- Security D2 / Core F3 residual / adversarial 2 (legacy part): a row written before this release under a non-canonical UUID text, or one written for an owner already erased, is not migrated. Follow-up #540.

## Security Audit Results

| Area | Result | Details |
| --- | --- | --- |
| Tenant isolation and owner filter | PASS | The guard selects `WHERE owner_id=$1`; an intent from another owner cannot match. |
| Targeted vs owner-wide erasure | PASS | Only `mode == "memory" and owner_all` refuses a write, matching what the purge deletes. Targeted erasures leave a live owner writable. |
| SQL injection | PASS | Every value is a bound parameter on both backends. |
| Scope-key confusion | PASS | `""` (operator) and `"u:"` (empty user id) remain distinct; a malformed key now raises. |
| Secrets or PII in tests | PASS | Synthetic content and random ids only. |
| Refusal logging | PASS after fix | The guard no longer raises on a non-UUID id, so no plaintext id reaches a traceback. |
| Post-write delete authorization | PASS | Deletes only the caller's `(scope_key, job)` row that the same call wrote. |

## Pattern Consistency Assessment

- The DuckDB and PostgreSQL upsert CASE bodies are textually identical apart from placeholders and the timestamp function.
- `delete_organizer_watermark` follows the existing protocol/store pattern and is implemented on both backends.
- `owner_wide_erasure_requested` follows the neighboring repository method pattern (`tables()` gate, `fetch`, Pydantic parse). Reusing `intents()` was rejected because it raises `privacy_migration_required` for old intents and would change watermark-write behavior.
- `scope_key`/`scope_owner` are the single encoding point, as the purge comment requires. The canonical-owner rule now appears in `_canonical_owner`, the guard's UUID parse, and the purge's `str(intent.owner_id)`; they agree today. Centralizing them further was left as a note.

## Redundancy Check

- No new duplicate utility was added. `scope_owner` has no existing equivalent; `_canonical_owner` is new and used by `scope_key`.
- `delete_organizer_watermark` has no existing equivalent.
- No new dependency, table, column or index.

## Wiring Findings

- `OrganizerConfig.incremental_enabled` is the only new config read (`src/prme/organizer/jobs.py`).
- Both organizer job writers consume the new bool return.
- The new store method is on the `GraphStore` protocol and both implementations.
- No schema change, so no migration or grant change is required. PostgreSQL deployments already need INSERT, UPDATE and DELETE on `organizer_watermarks` from #517; the new DELETE uses the same grant.
- `docs/RFC-0015-Self-Organizing-Memory.md` and `CHANGELOG.md` are updated.

## Data Safety and Read/Write Proof

- No new field. Existing `organizer_watermarks` rows are read and written with the same columns.
- The data reviewer exercised the exact upsert, delete and verification statements on DuckDB 1.4.1 and PostgreSQL 16.11 and found: monotonic cases correct on both, delete removes only the targeted `(scope_key, job)` row, verification returns a row while one exists, and a repeated write is a no-op.
- Existing last-writer-wins rows read correctly; no migration. The first write under the new code reconciles them monotonically.
- The 60-second overlap and keyset resume semantics are unchanged.

## Break Scenarios (adversarial) and Adjudication

| # | Scenario | Label | Likelihood x impact | Verdict |
| --- | --- | --- | --- | --- |
| 1 | Rows resurrected before this release are never removed | pre-existing | medium x medium | Follow-up #540 (needs a one-time scrub) |
| 2 | Non-canonical key written vs canonical purge key; verification missed the scope row | pre-existing key handling | low-medium x medium | Fix now (canonical writer key, scope-row verification); legacy rows Follow-up #540 |
| 3 | Non-UUID owner raises in the guard | newly-introduced | low-medium x medium | Fix now |
| 4 | Time-of-check/time-of-use between the guard read and the INSERT | newly-introduced | low x high | Fix now (post-write re-check and delete) |
| 5 | A future-stamped watermark silently stalls incremental runs | newly-introduced | low x medium | Follow-up #541 |
| 6 | A resume cursor written after verification names an erased node | pre-existing channel | low x medium | Follow-up #542 |
| 7 | Owner erased then re-admitted never gets a watermark again | newly-introduced (by design) | low x low | Accept, documented |
| 8 | Every write parses every erasure intent | newly-introduced | low x low | Accept |

Pre-mortem headline (adversarial): "We stopped the writer but never checked the artifact, so a surviving plaintext owner key and a non-UUID crash shipped together." Both parts are closed in this change.

## Follow-ups Raised

- #540: watermark rows written before this release for an already-erased owner, or under a non-canonical UUID text, are never scrubbed.
- #541: a future-stamped watermark silently stalls incremental organizer runs.
- #542: a resume cursor written after erasure verification can still name an erased node.

## Resolution Status

| Item | Status |
| --- | --- |
| Core F1, F2, F3, F6 | Resolved |
| Security F1, F2, F4 | Resolved |
| Security F3, data F2/F3, core F4/F5, adversarial 7/8 | Accepted with rationale |
| Data G1, security D1 | Resolved by self-healing verification |
| Adversarial 1, 5, 6 | Follow-ups |
| Adversarial 2 (new rows) | Resolved; legacy rows in follow-up |
| Adversarial 3, 4 | Resolved |
| Adversarial 7, 8 | Accepted |

Evidence on the final tree:

- `uv run ruff check` on all changed Python files: pass.
- `uv run pytest tests/test_organizer_incremental.py -q`: 43 passed.
- `uv run pytest tests/test_privacy_erasure_retirement.py -q`: 8 passed, 4 skipped (PostgreSQL env unset).
- Temporary PostgreSQL on port 55432, removed afterwards: `tests/test_organizer_incremental_pg.py` 9 passed; the two new privacy tests across all three layouts 6 passed.
- Targeted suites after the rebase onto 56a7d6a8: `tests/test_organizer_incremental.py` and `tests/test_privacy_erasure_retirement.py` together, 55 passed and 4 skipped.
- `uv run pytest -q` with `.env` moved aside on the rebased final tree: 7350 passed, 1611 skipped, 2 xfailed in 2371.18 s, exit 0.
