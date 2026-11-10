# Agent 8, independent adversarial review: alias_resolve incremental (#523)

Status: COMPLETE

- Base: origin/issue-517-organize-incremental-converges at 7f8b489820a007addd29fd79e76c4322618d6a38
- Reviewed snapshot: 8126027abc080b61ab7ff363ecd545559759909cc14d0d4678188134b1626c1b
- Scope read: `src/prme/organizer/{alias_resolution.py,jobs.py,watermarks.py}`,
  `src/prme/storage/{duckpgq_graph.py,graph_store.py,pg/graph_store.py}`,
  `src/prme/privacy/read_graph.py`, `src/prme/storage/{organizer_merge.py,lifecycle.py,alias_proposal.py}`,
  `src/prme/ingestion/entity_merge.py`, `src/prme/models/{base.py,nodes.py}`,
  `src/prme/storage/{engine.py,maintenance.py}`, `src/prme/cli.py`, docs.
- Method: read the diff, then the full job, the watermark wrapper, both SQL stores,
  the admitted view, the merge/proposal idempotency keys, and the node timestamp
  rules. No source or test file was modified.

This review assumes the change shipped and a postmortem is being written two
weeks later. It does not restate what the change does and does not re-own
correctness, security, quality, pattern, redundancy or wiring items.

---

## Ranked scenarios

### 1. "Incremental equals full" is false, and the documented full-run escape hatch only covers the newest 100 entities

- **Label: newly introduced** (the alias half; the same bounded full run already existed for `deduplicate` under #517).
- **Trigger.** Any store with more than 100 ENTITY nodes in one owner scope where a name-variant pair sits outside the newest 100 by `created_at`. Example: store `"JavaScript"` and `"js"`, then 100 unrelated filler entities in the same scope; enable `incremental_enabled` and call `organize(jobs=["alias_resolve"])` once. The incremental run merges the pair; the full run does not.
- **Mechanism.**
  - Full: `src/prme/organizer/jobs.py:765-767` calls `find_aliases(...)` with no `batch_size`, so `src/prme/organizer/alias_resolution.py:252-257` issues `query_nodes(node_type=ENTITY, limit=batch_size=100)`; `src/prme/storage/duckpgq_graph.py:1399-1406` orders `created_at DESC LIMIT 100`. Phase 1 (string pairs) therefore only ever sees pairs whose two endpoints are both in the newest 100; phase 2 queries only those 100 contents. The two oldest entities are never a pairing source.
  - Incremental, first run: no watermark, so `src/prme/organizer/jobs.py:838-846` scans `updated_after=None`, i.e. the whole scope; `src/prme/organizer/alias_resolution.py:376-383` finds the twin by name anywhere in the scope (`content_equals_any`), producing a 0.95 abbreviation candidate that `resolve_aliases` merges (>= `_MERGE_CONFIDENCE_THRESHOLD`).
  - The end states differ by one superseded node. No embedding score is involved: phase 1 is string-only.
  - The same bound breaks the documented mitigation in `docs/RFC-0015-Self-Organizing-Memory.md:507-516` and `src/prme/organizer/jobs.py:751-753`: "Run with the option off, or clear that scope's row, to re-evaluate existing data under the new setting, index or alias table." The full run re-evaluates only the newest 100 entities, so a lowered `alias_similarity_threshold`, a rebuilt or switched embedding model, or a new `KNOWN_ALIASES` entry silently does nothing for every older record, and the incremental watermark never revisits them either. That makes threshold/alias-table changes effectively unrecoverable without deleting data.
- **Likelihood: High.** Every non-trivial store has >100 entities; the change's headline feature (find an unchanged twin outside the page) is exactly the asymmetry.
- **Impact: Medium.** Two directions: (a) enabling the flag runs extra merges on old pairs the full job never reached (name-variant merges only; semantic pairs only link, so the destructive scope is bounded); (b) the documented "re-evaluate" procedure reports success while old data is untouched. (b) is the incident: an operator changes a tuning knob, follows the docs, sees `nodes_modified: 0`, and concludes the knob is inert.
- **Detection: silent.** Both runs report `status: completed`.
- **Cheapest mitigation.** Either page the full alias scan through the whole scope inside `_job_alias_resolve` (mirror the incremental pager without a watermark), or correct the RFC/CHANGELOG text to state the 100-entity bound and tell operators the full job cannot re-evaluate old data. Doc-only: ~10 lines. Paging: ~40-60 lines and it changes the full job's cost profile.
- **Recommended verdict: Fix now.** The acceptance criterion names "incremental equals full" as a proven property; the equivalence test (`tests/test_organizer_incremental.py:580-607`) uses five entities and cannot see this.

### 2. A budget smaller than one entity's work makes the alias job make zero progress forever

- **Label: newly introduced for `alias_resolve`** (the same shape exists in `_deduplicate_incremental`, but alias adds a per-entity database lookup, making the threshold much easier to hit).
- **Trigger.** Any `organize()` call where the alias job's remaining budget is smaller than the cost of `scan_changed_nodes` plus one entity's name lookup and semantic search. Realistic: `DEFAULT_JOBS` shares one budget, `alias_resolve` is fifth (`promote`, `decay_sweep`, `archive`, `deduplicate`, `alias_resolve`), and `docs/ORGANIZER-JOBS.md:101-107` records that at 10,000 nodes the registry's measured work is ~17.8 s against a 5,000 ms default, so later jobs are "partial or skipped". A cron calling `organize(budget_ms=...)` or `prme organize --budget-ms` small enough reproduces it deterministically.
- **Mechanism.**
  - `src/prme/organizer/jobs.py:824` halves the remaining budget; `:856-861` checks it *before* appending the first entity, so a slow `scan_changed_nodes` leaves `examined` empty.
  - Even with one entity examined, `src/prme/organizer/alias_resolution.py:409-425` counts `completed` only when the semantic search for that entity finishes; `remaining_ms` can be ~1 ms, so `completed` stays 0.
  - `src/prme/organizer/jobs.py:890-896`: `done = examined[:0]` is empty, so `last_cursor` stays `None`.
  - `src/prme/organizer/jobs.py:106-120`: with no `_resume` cursor the wrapper writes nothing, so the previous/absent watermark stands; the next run starts at exactly the same place and fails identically.
  - Result: the job never advances past entity 1, so neither its string twins nor any later entity are ever resolved on that schedule.
- **Likelihood: Medium.** Requires a shared/small budget; the documented default at 10k nodes already starves the later jobs, and the measured ~8 ms/entity (ORGANIZER-JOBS.md:89, incremental 510 processed in 4,005 ms) makes the threshold reachable.
- **Impact: High for affected schedules.** Alias resolution silently stops converging.
- **Detection: quiet.** `status: "budget_exhausted"`, `nodes_processed: 0`, no warning log; `src/prme/cli.py:624` only prints "the next run resumes here" when `nodes_processed` is non-zero, so the CLI hides exactly this state. Exit code is 0.
- **Cheapest mitigation.** Guarantee at least one entity per page: examine `entities[:1]` unconditionally (or run phase 2 for the first name-checked entity regardless of the clock). ~5-10 lines. Optionally report a distinct `no_progress` status when a stop yields `processed == 0`.
- **Recommended verdict: Fix now.** It is cheap, and it protects the "resume" property the acceptance criteria require.

### 3. On a privacy-admitted store the name lookup is one full-scope scan per changed entity, so an incremental page is O(entities x scope) and typically stops at zero progress

- **Label: newly introduced.**
- **Trigger.** `incremental_enabled` on a store with privacy journals (the hosted/protected path) and a non-empty window.
- **Mechanism.** `src/prme/organizer/alias_resolution.py:376-383` calls `engine.query_nodes(...)` once per entity in the page. For a protected store `src/prme/storage/engine.py:3193-3196` routes this to `AdmittedGraphView.query_nodes`, which at `src/prme/privacy/read_graph.py:132-156` consumes `_all_nodes` (`:105-115`) — i.e. it pages the *entire admitted scope* and sorts it in memory for every single lookup. A 500-entity page therefore performs 500 whole-scope scans plus Python projection per node. The RFC's "known limitation" (`docs/RFC-0015-Self-Organizing-Memory.md:519-522`) describes one whole-scope scan per *page*; this is up to `_INCREMENTAL_PAGE_SIZE` (500) times worse, and it is new with alias_resolve (deduplicate never calls `query_nodes` per entity). A single lookup easily exceeds `scan_budget_ms`, which then lands in scenario 2 (no cursor, no progress).
- **Likelihood: Medium** (protected stores only, but that is the hosted/multi-tenant target this option is for).
- **Impact: High.** The alias job makes no progress while burning the budget each pass; on large scopes it may not resolve anything after the upgrade.
- **Detection: quiet.** Same signature as scenario 2: `budget_exhausted`, `nodes_processed` 0 or tiny, no log.
- **Cheapest mitigation.** Build a per-run normalized-name index for the scope once (one `scan_nodes` pass, dict content.casefold() -> ids, reused for all entities), or push `content_equals_any` down into `AdmittedGraphView.scan_nodes` so the admitted view filters during its single pass. ~30-60 lines. A stopgap: cap `_ALIAS_NAME_LOOKUP_LIMIT` work or skip the name phase on `protected_reads` backends.
- **Recommended verdict: Fix now if the hosted service runs protected stores with the flag on; otherwise Follow-up plus a doc note.** In either case the doc's "known limitation" sentence should be corrected to say the name lookup repeats the whole-scope read per entity.

### 4. A transient failure inside `resolve_aliases` is swallowed, the run reports `completed`, the watermark advances, and the pair is never retried

- **Label: newly introduced** (the swallow is pre-existing; its permanence under a watermark is new).
- **Trigger.** An old, unchanged entity is one endpoint of a pair discovered in a run; `merge_nodes`/`propose_alias` raises once (PostgreSQL `FOR UPDATE` lock timeout or deadlock, journal deadline, DuckDB transaction conflict, a privacy `PrivacyDenied`).
- **Mechanism.** `src/prme/organizer/alias_resolution.py:534-540` catches every `Exception`, logs a warning and continues, returning only a success count. `src/prme/organizer/jobs.py:877-880` adds that count and sets no error flag; `:918` therefore reports `completed`; `src/prme/organizer/jobs.py:121-126` advances `completed_through` to `ceiling - 60 s`. The pair was added to `seen_pairs`, and neither endpoint's `updated_at` changes when the resolution fails, so no later window contains it. The full-scope job retries the same newest-100 pairs every pass; the incremental job does not.
- **Likelihood: Medium-Low.** Needs one transient backend error on an old pair.
- **Impact: Medium.** A legitimate merge or alias proposal is dropped permanently; `nodes_modified` is silently short by one; on a multi-tenant store the same failure can recur across tenants.
- **Detection: quiet.** One `logger.warning` (`Failed to resolve alias pair`), `errors: 0`, `status: completed`.
- **Cheapest mitigation.** Have `resolve_aliases` return (applied, failed) and have the incremental job treat a non-zero failure count as a page failure (do not advance the cursor past it). ~15-20 lines.
- **Recommended verdict: Follow-up** (bounded blast radius; not on the acceptance path), but record it in the known-limitations list.

### 5. Clock skew larger than the 60-second overlap permanently skips newly written entities

- **Label: pre-existing mechanism (#517), newly applies to `alias_resolve`.**
- **Trigger.** A shared PostgreSQL store written by hosts whose clocks differ by more than 60 s from the host running the organizer (misconfigured VM, no NTP).
- **Mechanism.** `src/prme/organizer/jobs.py:82` takes the ceiling from `datetime.now(timezone.utc)` on the application host and `:125` writes `ceiling - _WATERMARK_OVERLAP` (60 s, `:61`). A writer whose clock is behind stamps a freshly written entity with `updated_at` below that watermark; `src/prme/storage/duckpgq_graph.py:470-479` (and the PG equivalent `:538-549`) then never returns it, on this or any later run. Both endpoints of a future alias pair can be lost this way; the entity may even have been written moments before the run.
- **Likelihood: Low** (requires >60 s skew; NTP normally keeps well under 1 s).
- **Impact: Medium** and permanent when it happens: the entity is invisible to alias resolution and, for deduplicate, to merging.
- **Detection: silent.** Nothing compares the local clock with the store's.
- **Cheapest mitigation.** Derive the ceiling from the store clock (`SELECT now()` / `current_timestamp`) instead of the application clock, or raise the overlap and record the skew. ~10-20 lines.
- **Recommended verdict: Follow-up** (shared with #517; do not expand this ticket).

### 6. The single flag changes `alias_resolve` on upgrade for installs that set it for deduplicate

- **Label: newly introduced.**
- **Trigger.** An existing deployment already has `PRME_ORGANIZER__INCREMENTAL_ENABLED=true` (introduced in #517 for deduplicate only) and upgrades to this version.
- **Mechanism.** `src/prme/organizer/watermarks.py:39` now puts `alias_resolve` in `INCREMENTAL_JOBS`, and `src/prme/organizer/jobs.py:181-186` routes it to the incremental handler. There is no per-job switch and no watermark row yet, so the first post-upgrade alias run scans the entire scope (every entity, one lookup each) and merges old name-variant pairs the previous full job never reached, then writes `organizer_watermarks` rows. `src/prme/config.py:630-639` changed the option's description but not its name, so no explicit opt-in exists for the second job.
- **Likelihood: Medium-High** for anyone with the flag on.
- **Impact: Low-Medium.** A slow first pass and unexpected merges of long-standing entities; on a large multi-tenant store, a long catch-up across tenants.
- **Detection: quiet** until someone reads `nodes_modified` or the pack.
- **Cheapest mitigation.** State it plainly in the CHANGELOG (partly done) and in `docs/RFC-0015`; if the team wants opt-in granularity, key the flag per job (`deduplicate_incremental`, `alias_resolve_incremental`). Per-job flags: ~20 lines.
- **Recommended verdict: Accept** (intended design) with an explicit upgrade note; consider per-job flags later.

### 7. The 500-name lookup bound can hide the only twin, and nothing retries it

- **Label: newly introduced.**
- **Trigger.** More than 500 lifecycle-active entities in one owner/scope whose normalized content is in the alias-equivalent form set, with the real twin among the oldest.
- **Mechanism.** `src/prme/organizer/alias_resolution.py:309` caps the lookup at 500, and `:382` passes it to `query_nodes`, which orders `created_at DESC LIMIT 500` (`src/prme/storage/duckpgq_graph.py:1399-1406`; `src/prme/privacy/read_graph.py:157-158`). If the 500 newest matching rows are siblings that do not satisfy `_is_abbreviation_match`/`_is_case_variation` (for example, the changed name is a canonical form and the returned rows are case variants of a different abbreviation group), the twin is not returned; the pair is never formed, and because neither endpoint is written again, no later run retries it. The inline comment at `:307-309` calls this a "page bound, not a completeness claim", which is honest but contradicts the CHANGELOG's "an older name twin ... is still found" without qualification.
- **Likelihood: Low** (requires >500 alias-equivalent entities in one scope; ingest reuses case-insensitive names, so this needs direct `store()` usage or many `entity_type` variants).
- **Impact: Medium** when it fires: a silent permanent miss.
- **Detection: silent.**
- **Cheapest mitigation.** Page the lookup when `len(partners) == _ALIAS_NAME_LOOKUP_LIMIT` (keyset by `id`), or lower the limit and document the bound in the CHANGELOG. Paging: ~25 lines; doc: ~3 lines.
- **Recommended verdict: Follow-up.**

### 8. `content_equals_any=[]` fails open on DuckDB/PostgreSQL and closed on the admitted view

- **Label: newly introduced.**
- **Trigger.** A future caller passes an empty list (e.g. a computed name set that came out empty).
- **Mechanism.** `src/prme/storage/duckpgq_graph.py:1381-1386` and `src/prme/storage/pg/graph_store.py:428-431` guard with `is not None and content_equals_any`, so `[]` adds no predicate: the query returns up to `limit` arbitrary nodes. `src/prme/privacy/read_graph.py:128-131,154` builds `equals_set = set()` and then `not in equals_set` always continues, returning nothing. Same API, opposite semantics, depending on the backend. Not reachable from `find_aliases_in_page` (its form set is never empty), but it is now a public `GraphStore` argument.
- **Likelihood: Low** (no current caller).
- **Impact: Low** (fail-open returns rows, not wrong merges: `_string_alias_candidate` still rejects non-matching names; a future caller could be surprised).
- **Detection: silent.**
- **Cheapest mitigation.** Treat an empty list as "match nothing" on both SQL stores (`if content_equals_any is not None:`). ~2 lines plus a test.
- **Recommended verdict: Follow-up.**

---

## Attacked hardest and why each failed

1. **"The alias candidate set is write-determined."** I tried to make a new pair eligible with neither endpoint written. The two structural premises hold: `content` is not in `_UPDATE_ALLOWED_FIELDS` (`src/prme/storage/duckpgq_graph.py:1183-1199`), so an entity cannot be renamed without a new row; `MemoryNode` stamps `updated_at` at construction (`src/prme/models/base.py:31-38`), lifecycle transitions write it (`src/prme/storage/lifecycle.py:157`), and `merge_nodes` rewrites it on both endpoints (`src/prme/storage/organizer_merge.py:169-181`). I also checked that the string rule's partner set is complete: I enumerated every name in and around `KNOWN_ALIASES` and verified `_alias_name_forms(a)` contains `b.strip().lower()` for every pair where `_is_abbreviation_match(a,b)` or `_is_case_variation(a,b)` is true (0 misses). The only remaining holes are the documented ones (threshold, alias table, index/model) — which are exactly the ones whose documented escape hatch is broken by scenario 1.
2. **"A budget-stopped run resumes without skipping."** I walked every branch of the cursor update. `done = examined[:completed]` (`jobs.py:890`) is always a prefix of the page in `(updated_at, id)` order; a stop inside the examine loop, a `completed < len(examined)` stop, a `lookup_failed` error, and an empty `examined` all break before `updated_after = entities[-1]...` (`:907-910`), so the cursor only ever advances over the fully examined prefix. A merge rewriting an endpoint's `updated_at` to `now > ceiling` can only push it into the *next* window, never past the cursor. I could not construct a skip. The failure mode is the opposite: scenario 2, where the cursor does not advance at all.
3. **"A second run over a completed scope changes nothing."** I traced merges (deterministic `uuid5` operation id, replay returns `applied=False`), proposals (`alias_proposal.py:274`, id derived only from the sorted pair, not the score), the 60-second overlap re-examining merge-touched canonical nodes, and three- and four-way name groups across page boundaries (`merged_ids` retires the whole compatible group in one `resolve_aliases` call per page, and retired members leave the scan filter). I found no second-run write. The nearest miss is the >500-group case folded into scenario 7.

## Checklist coverage (one line each)

- Premise challenge: fails only at the documented tuning/index/alias-table seam, whose mitigation is broken by scenario 1; otherwise holds.
- Guard loopholes: scenario 1 (full-run escape hatch bounded to 100) and scenario 2 (no-progress stop writes no cursor).
- Existing data and installs: scenario 6 (flag silently covers a second job); first incremental run is an unbounded catch-up.
- State and lifecycle: `ARCHIVED` is terminal (`src/prme/types.py:104`), `CONTESTED -> STABLE` stamps `updated_at`, merges stamp both endpoints, so lifecycle writes enter the window; no hole found.
- Idempotency and retries: merge/proposal operation ids are deterministic; scenario 4 is the retry hole.
- Ordering, timing, consistency: keyset is `(COALESCE(updated_at, created_at), id)` on both stores and the admitted view; scenario 5 is the clock-skew hole; concurrent runs can regress the watermark but only cause repeated work, not loss.
- Scale: scenario 3 (protected stores) and the O(scope) name lookup on plain stores; 100x entities make a single lookup the whole scan budget. Candidate resolution per page is not budget-checked, so one page can overrun `budget_ms` badly when a name group is large.
- Partial failure: scenario 4; `generation_search` failure returns `status: error` correctly; a failed page does not move the cursor.
- Backward compatibility: the new keyword on the `GraphStore` Protocol (`src/prme/storage/graph_store.py:206`) breaks any third-party backend that predates it; the resulting `TypeError` is caught at `alias_resolution.py:384-388` and surfaces only as `status: error` every run. All in-repo implementations (DuckDB, PostgreSQL, admitted view) are updated.
- Blast radius: scenario 6; the new SQL predicate is additive and has no other caller.
- Detection: scenarios 1 and 4 are silent; 2 and 3 are quiet because the CLI suppresses the resume line when `nodes_processed == 0`; nothing reports "watermark not advancing".
- New SQL filter: wildcard/`LIKE` mismatch is absent (equality, bound parameters); whitespace-only normalizes to `""` and matches empty-content rows but cannot form a candidate; very long content is parameterized; scope is filtered in SQL and `compatible_provenance` independently requires equal `(user_id, scope)`; the admitted path is implemented but compares `casefold()` where SQL uses `LOWER()`, a divergence only for exotic Unicode; scenario 8 is the empty-list split.
- Privacy-projected result: `src/prme/privacy/diagnostics.py:165-190` keeps the typed status and drops `details`, so `aliases_found`/`pairs_not_applied` are not visible on a protected store (same as deduplicate); not an incident.

## One-line pre-mortem

An operator enables incremental, changes the alias table, follows the documented full-run re-evaluation, sees a clean `completed`, and old name variants stay unmerged forever because the "full" alias job never reads past the newest 100 entities.
