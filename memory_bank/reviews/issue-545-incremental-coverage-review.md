# Review: issue #545, incremental organizer runs that leave memories unexamined

## Review Scope

- Repository: `/Users/dwamianm/Sites/prism`
- Base branch and commit: `main` at `323ec74917cb3bd894873c6c9b9dfc5f44f307f7`
- Reviewed snapshots: `afc704f76409817df410ca16ae275688e318465b2aad051d72d7b6d67862cb02` (first review), `3abff926b8e193580d5d31abd820337b5eef053800dce1ab5dbea4f3b0a9050e` (post-fix delta), and `84434ba67ed929fe5e6be128148b259360f09cbce708a61e6c8989e3ef07c045` (final: the delta's four Consider findings fixed, the anchor test added, one comment reflow). The first two came from `python3 ~/.claude/loop-tools/housekeeping.py review-plan --base main` over the uncommitted working tree; the final one differs from the second only by those fixes, the anchor test and comments, which the affected test files cover.
- Tier: L. The helper classified `DATA`, `SECURITY`, `PERSISTED_FIELD` and `NEW_SYMBOLS`; `INFRA` is false. `reviewTier` is unset in `.claude/github-ticket-loop.config.json`, so the default applied.
- Required roster: core (checklists 1, 3, 4, 5), security (2), data (7), adversarial (8). Checklist 6 (infrastructure and operations) was not applicable: no new service, route, CLI command, environment variable or deployment step is added; the two new columns are created by the existing schema bootstrap on both backends.
- Reviewer reports (working files outside the tree so the reviewed snapshot stays exact): `/tmp/pr545-review/core.md`, `security.md`, `data.md`, `adversarial.md`.
- Acceptance criteria (issue #545, "Done when"): (1) each of the four cases has a test that fails before the fix and passes after it on DuckDB and on PostgreSQL; (2) an incremental run and a full run still end with the same store on the existing equivalence tests; (3) the changelog says whether an install that already ran incremental mode should clear its watermarks once after upgrading.

## Files Changed

- `src/prme/storage/engine.py`
- `src/prme/privacy/admission.py` (post-fix delta)
- `src/prme/organizer/watermarks.py`
- `src/prme/storage/schema.py`
- `src/prme/storage/pg/schema.py`
- `src/prme/storage/graph_store.py`
- `src/prme/storage/duckpgq_graph.py`
- `src/prme/storage/pg/graph_store.py`
- `src/prme/organizer/jobs.py`
- `src/prme/organizer/deduplication.py`
- `src/prme/storage/erasure.py`
- `docs/RFC-0015-Self-Organizing-Memory.md`
- `docs/ORGANIZER-JOBS.md`
- `CHANGELOG.md`
- `tests/test_organizer_incremental.py`
- `tests/test_organizer_incremental_pg.py`
- `tests/test_privacy_admission.py` (post-fix delta)
- `tests/test_rank_fusion_recency.py` (post-fix delta: one anchor test)
- `tests/test_batched_materialization.py`
- `tests/test_privacy_erasure_retirement.py`

## Approach Summary

- A note materialized from a queued event keeps its save time as `created_at` and takes the time it is made as `updated_at`. The raw NOTE path stamps it directly; the privacy-admitted path (`materialize_direct`) stamps the published copy when it has to build a derivation plan, leaving the accepted direct record unchanged.
- The watermark row gains `stalled_id` and `stalled_attempts`. A failed page counts an attempt for the first node the next run would retry; a page that only ran out of budget does not. At three attempts the job records the node in `details["skipped_nodes"]` and a warning, advances the cursor past it and continues the window.
- `merge_duplicates` reports per-pair failures through `failure_count`, and the incremental deduplicate job does not advance a page whose merge failed, matching `resolve_aliases`.
- `scope_key` keeps the exact user id, matching node ownership; `owner_scope_keys` lets the erasure purge and verification delete the owner's rows under any UUID text form the standard parser accepts, and the writer guard still refuses an erased owner under any spelling.
- Schema: both backends create the columns; DuckDB migrates an older pack with `PRAGMA table_info`, `ALTER TABLE` and `CHECKPOINT`; PostgreSQL adds them with `ADD COLUMN IF NOT EXISTS` and they join the runtime schema fingerprint.

## Must Fix

None. All four reviewers reported no must-fix defect in the first pass.

## Should Fix

| ID | Finding | Resolution |
| --- | --- | --- |
| Core F1 / security O4 | `_canonical_owner` was dead code with a docstring claiming a use, and `owner_scope_keys` parsed the UUID itself. | Fixed in the post-fix delta: the helper is deleted, so the module has one authoritative comparison. |
| Data A | The changelog said the default rank fusion "reads `created_at` and is unchanged", but the rank fusion current-update multiplier's reference time uses `_memory_time`, which falls back to `updated_at`. | Fixed: the changelog and the pull request now name the rank-fusion current-update multiplier and the weighted formula as the two paths that can date a no-`event_time` note by its materialization time, and say the current-state recency boost and rendered contexts are unchanged. Verified in `src/prme/retrieval/scoring.py` (`time_of = _weighted_time_source(effective_weights)`, the update-multiplier block below it). |
| Adversarial S1 (protected store) | `_materialize_event` returns into `materialize_direct` before the raw-NOTE branch, and `materialize_direct` published the node built at acceptance, so case 1 was unfixed on a privacy-admitted store. | Fixed in the post-fix delta: the published copy takes the publication time as `updated_at` when a plan must be built. New test on the directory, single-file pack and PostgreSQL layouts fails without the fix and passes with it. The stronger reason the protected path is safe is that the publish records the derivation plan, so a replay reproduces the re-stamped node (the delta review verified this). The non-protected direct-store variant is a follow-up (see below): it has no plan record, and `tests/test_durable_ingestion.py:261` compares the stored node to the receipt node, so a re-stamp there would break a replay invariant rather than a test. |
| Security O1 / adversarial S2 | The owner-wide watermark delete read every scope key on every 128-target purge step, then twice more in verification. | Fixed in part: the delete now runs on the purge's first step only (`first_step` from the saved progress before it advances) and verification still re-applies and checks it. The remaining cost is one scan in the purge plus two in verification, recorded as an Accept below. |
| Core F6 / data D / security O3 | The erasure clears `stalled_id` but left `stalled_attempts` behind. | Fixed: both the purge and the verification set `stalled_attempts=0` with the id. |

## Consider (adjudicated)

- **Core F2 (duplicated stall/skip block).** Accept. The two handlers differ in their log labels and in which locals the block mutates (`skipped`, `processed`, `last_cursor`, the scan cursor, `failed`, `stalled`); a helper would take eight parameters and hide the page loop. The shared rule itself is already one function (`_stalled_attempt_count`). A third incremental job should extract the whole page-failure step.
- **Core F3 / adversarial S3 (a failed merge is attributed to the page head).** Follow-up #546. The ticket's bound is met and the scope is no longer pinned, but naming the failing pair needs a signature change to `merge_duplicates` and `resolve_aliases`, and a poisoned pair near the end of a 500-node page costs up to 500 skips at three runs each.
- **Core F4 (redundant `else: stalled = None`).** Fixed: removed from both handlers.
- **Core F5 (a stopped run that does not reach the stalled node clears the count).** Accept. The count means "failed runs that reached this node"; a budget-stopped run advances the cursor toward it, and every run that reaches the node fails, so the bound is still reached. The behaviour is documented in the module comment and the RFC.
- **Core F7 (nits).** Fixed: the helper's `head` parameter is annotated `"MemoryNode"` through the existing TYPE_CHECKING import and the new tests read `jobs_module._MAX_STALLED_ATTEMPTS` directly. The blank-line nit did not apply on re-reading the file.
- **Adversarial S4 (a skipped node leaves no durable trace).** Follow-up #547. The issue's criterion is the result details and a warning log, which are implemented; persisting the skip in the row and surfacing it in a field the protected projection keeps is a small, separate change.
- **Adversarial S5 (two failing nodes reset each other's count).** Accept. A node that fails every time is always the head of the retry, so its count grows and the bound still fires; a reset requires the head to succeed on some run, which is progress. A rotating failure is a different, non-pinning case.
- **Adversarial S6 (last-writer-wins stall fields).** Accept. The two fields describe the latest writer, are documented in the store, the engine, the RFC and the job comment, and the worst case is one delayed skip.
- **Adversarial S7 (PostgreSQL `initialize=False` refuses to open).** Accept. It is the established #517 pattern for a runtime schema change, it fails loudly, and the changelog requires the bootstrap.
- **Adversarial S8 (wall-clock `updated_at`, the widened determinism test, and the current-update anchor).** Accept, after re-checking the alternative. The change makes an `ingest_fast` note's `updated_at` the time its row was made, which is already what a `store()` node's `updated_at` means, so the default rank fusion's current-update anchor now treats both paths alike; the anchor test `tests/test_rank_fusion_recency.py::test_a_note_made_long_after_it_was_saved_can_be_the_newest_update` pins it, and the changelog states that a pool holding such a note can order it as the newest update and that the time is wall-clock. The alternative that avoids the retrieval input, a separate materialization timestamp column, was rejected in the plan: it needs a new column, a new window predicate and cursor expression on both backends, and a migration, which is a larger change than the issue proposes. The test's eight events are all materialized raw notes, so normalizing `updated_at` covers exactly the field the change moves, and every other field and the rendered context are still compared strictly. The offline evidence gate cannot show the effect because its packs hold no `ingest_fast` notes; that is recorded in the changelog and the pull request.
- **Data B (no PostgreSQL upgrade test against a pre-column table).** Accept. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is the established idempotent form used by three existing columns, inside the same transactional bootstrap as the fingerprint write; a test would have to drop and recreate a shared table in the test database, which is riskier than the gap.
- **Data C (last-writer-wins stall fields can drop a count).** Accept, same reasoning as adversarial S6.
- **Security O2 (`stalled_id` widens the #542 post-verification window).** Follow-up #542. The window already exists for `resume_id` and is tracked there; the change adds a second carrier of an opaque node id and does not close it. No new class of data is exposed.
- **Security O5 (the "any spelling" wording overstates coverage).** Fixed: the changelog now says "any text form of the UUID the standard parser accepts", and the RFC says the same. Whitespace-padded text is a separate exact owner on both the watermark and the node-purge side, which is the #525 property.

## Security Audit Results

| Area | Result | Details |
| --- | --- | --- |
| Plaintext identities in logs or details | PASS | The new warning and `skipped_nodes` entry hold a node id and an attempt count only; `_resume`/`_stall` are popped before return; protected stores redact the log and drop details. |
| Owner and tenant isolation | PASS | Stall state lives in the scope's own row, compares only within that window, and scoped scans stay owner-filtered. |
| Erasure purge and verification | PASS | The owner's rows are removed under any parsable spelling and the check fails on a survivor; the per-node clear covers cursor and stall; a non-UUID owner makes the helper return no keys rather than raise. |
| Exact-text scope key | PASS | The key matches exact ownership; the write guard still canonicalises before the erasure lookup, so every parsable spelling is refused after an erasure. |
| SQL construction | PASS | Every value is bound; the only interpolated SQL is fixed `ALTER TABLE` text and a constant `PRAGMA table_info` identifier. |
| Input validation | PASS | `stalled_id` is cast by the database, `stalled_attempts` comes from a trusted writer, and the helper ignores unparsable keys. |
| Disclosure through the new details or logs | PASS | Node id and count only, never persisted; protected results drop details. |

## Pattern Consistency Assessment

- `merge_duplicates(failure_count=...)` matches `resolve_aliases(failure_count=...)` in position, type, increment site and warning.
- The `_stall` side channel mirrors the existing `_resume` contract; the reader and writer pairs are identical between DuckDB and PostgreSQL.
- The DuckDB and PostgreSQL migrations match the existing `event_extractions`/`nodes` migration patterns.
- One new duplication is recorded under Core F2 and accepted.

## Redundancy Check

The only redundant symbol was `_canonical_owner`, removed in the post-fix delta. The two dataclasses have exactly two construction sites, both updated; no unused imports were introduced.

## Wiring Findings

No new configuration key, environment variable, route, CLI command or deployment step. The two columns are created by the existing DuckDB `initialize_database` (every open, including single-file packs) and the PostgreSQL bootstrap; the PostgreSQL runtime fingerprint covers the change, so an `initialize=False` deployment refuses to open until the privileged bootstrap runs, as the changelog states. CI collects the new tests through `pytest tests/`.

## Data Safety and Read/Write Proof

| Field | Writer | Reader | Result |
| --- | --- | --- | --- |
| `organizer_watermarks.stalled_id` | The upsert in both graph stores, reached through `MemoryEngine.set_organizer_watermark` from `_run_incremental` | The watermark readers in both stores, normalised into `OrganizerWatermark.stalled_id` and read by `_stalled_attempt_count` | PASS |
| `organizer_watermarks.stalled_attempts` | Same | Same, with `NULL` read as 0 | PASS |

Migration and backfill: DuckDB adds both columns to an existing pack and checkpoints; a test opens a pre-column pack and keeps its row. PostgreSQL adds the columns with the idempotent `ADD COLUMN IF NOT EXISTS` inside the advisory-locked bootstrap and records the fingerprint in the same transaction. Existing rows are preserved; the upsert keeps `completed_through` monotonic and the newer resume cursor, and the new fields are last-writer-wins by design.

## Break Scenarios (adversarial) and Adjudication

| # | Scenario | Label | Likelihood | Impact | Verdict |
| --- | --- | --- | --- | --- | --- |
| S1 | A protected store (or a deferred direct store) publishes a note with its acceptance time, so the next window misses it | Newly introduced by omission (protected path fixed) | Medium | Case 1 unfixed for a supported layout | **Fix now** (protected path fixed and tested); direct-store variant **Follow-up #548** |
| S2 | The owner-wide purge scans the whole watermark table once per 128-target step | Newly introduced | High on a large multi-tenant store | Slow or failed erasures, no data loss | **Fix now** for the repetition (first step only); residual single scan **Accept** (20k-row scan measured at 10 ms) |
| S3 | A page-level failure blames the page head, so the bound advances one node per three runs | Newly introduced | Medium | Liveness, not correctness | **Follow-up #546** |
| S4 | A skipped node leaves no durable record and is not revisited until it changes | Newly introduced | Medium | A memory stays unexamined; only a log line and `details` record it | **Follow-up #547** |
| S5 | Two failing nodes reset each other's attempt count | Newly introduced, withdrawn by the reviewer on re-trace | Low-Medium | Delay, not a pin | **Accept** |
| S6 | Concurrent runs overwrite the stall fields | Newly introduced, documented | Low | A delayed skip, no data loss | **Accept** |
| S7 | PostgreSQL `initialize=False` refuses to open until the bootstrap | Newly introduced, backward compatibility | Certain for that shape | Coordinated migration | **Accept** (documented, established pattern) |
| S8 | `updated_at` is wall-clock, and the determinism test normalizes it | Newly introduced | Certain | A narrow current-state ordering change under the default rank fusion | **Accept**, with a test that pins the anchor and the disclosure in the changelog; the offline gate cannot see it because its packs are `store()`-built |

Pre-mortem headline from the adversarial pass: "The protected-store copy of case (1) ships unfixed, so a privacy-admitted pack keeps silently missing exactly the memories this ticket was filed to catch, while a full scan of the watermark table turns an erasure on a large tenant store into an OOM." The first half was fixed in the post-fix delta; the second half was reduced to one scan per purge and is recorded below.

## Follow-ups Raised

- #546: a failed merge or alias resolution is attributed to the page head, so the retry bound advances one node per three runs.
- #547: a node skipped after three failed runs leaves no durable record and is not revisited until it changes.
- #548: a deferred direct-store node keeps its save time, so an organizer window that completes in between can miss it.
- Named, not opened: #542, whose post-verification write window now also carries `stalled_id`.

## Resolution Status

| Item | Status |
| --- | --- |
| Acceptance criterion 1 (four cases, tests on DuckDB and PostgreSQL) | Met; the protected-store copy of case 1 also has a test on all three layouts |
| Acceptance criterion 2 (incremental and full runs agree) | Met; the equivalence tests are unchanged |
| Acceptance criterion 3 (changelog upgrade note) | Met |
| Full test suite on the final revision | `uv run pytest -q` with `PRME_TEST_DATABASE_URL` on a temporary PostgreSQL server: 8820 passed, 157 skipped, 3 xfailed |
| Core F1, F4, F6, F7 | Fixed |
| Core F2, F5 | Accepted with reasoning |
| Core F3 | Follow-up |
| Data A, D | Fixed |
| Data B, C | Accepted with reasoning |
| Security O1 | Fixed in part; residual accepted |
| Security O2 | Existing #542 |
| Security O3, O4, O5 | Fixed |
| Adversarial S1 | Fixed for the protected path; direct-store variant is a follow-up |
| Adversarial S2 | Fixed in part; residual accepted |
| Adversarial S3, S4 | Follow-ups |
| Adversarial S5-S8 | Accepted with reasoning |

Post-fix delta re-review: recorded in the reviewer reports under "Post-fix delta". Security reported PASS with no new finding, data reported clean with each durable proof re-verified, the adversarial pass closed S1 on the protected path and withdrew S5, and core reported four new Consider findings, all fixed here:

- F8: the changelog's retrieval paragraph contradicted itself about rendered contexts. Fixed: it now scopes the unchanged claim to the recency boost and states the narrow ordering effect once.
- F9: three places still said "any spelling". Fixed: `watermarks.py`, `erasure.py` and the RFC verification paragraph now use "any UUID text form the standard parser accepts".
- F10: nothing asserted `stalled_attempts == 0` after a purge. Fixed: the erasure test now asserts it beside `stalled_id`.
- F11: `first_step` re-derived the progress sentinel. Fixed: `_PURGE_PROGRESS_START` is shared by the default and the comparison.

Two adversarial delta objections were checked and not sustained:

- It said the rank fusion current-update multiplier's reference time is not reachable. The multiplier block at `src/prme/retrieval/scoring.py:1015-1045` sits outside the fused and non-fused branches, rank fusion requires `recency_time` to be unset (`src/prme/retrieval/config.py:471-472`), so `_weighted_time_source` returns `_memory_time` and a no-`event_time` note can become the reference. The changelog clause stands.
- It said the direct-store equality constraint is `tests/test_direct_store_recovery.py:22`. That assertion compares the saved record to the accepted node; the constraint that a re-stamp would break is `tests/test_durable_ingestion.py:261`, which compares the stored node to the receipt node. The follow-up records both, with the replay invariant as the real reason.
