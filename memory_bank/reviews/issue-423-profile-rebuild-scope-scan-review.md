# Issue #423 review: a one-person profile rebuild reads only that person's records and honors its deadline

## Review Scope

- Base: `main` at `52d99de1be90477abcfa7256088fd8359fea2eb5`.
- Branch: `issue-423-profile-rebuild-scope-scan`.
- Reviewed snapshot: `b352281961d1caf228350041f22588d6b0773c7fa775a93ad18535362fc1c596`.
- Post-fix snapshot re-verified with the static checks, the focused pytest files and the full suite: `9c9ad791d1eb1671163f8bc979aa61c11fed4d65d776e3681117d0e32c69c56b`, against `origin/main` at `d2bc51b3b75f92645f44305bb9955ca37a738ba6` after the rebase.
- Tier: L. Predicates: SECURITY (privacy read path) and NEW_SYMBOLS (new exception class and filter parameters). DATA, INFRA and PERSISTED_FIELD were classified not applicable: the change adds read-only SQL filters, with no schema, index, migration, backfill or write change, and no new durable field. Merged checklists: 3 and 4 into core, and 5 into core for the new symbols.
- Required reviewers: core (1, 3, 4, 5), security (2), adversarial (8). All ran independently on the snapshot above. Reports: `issue-423-profile-rebuild-scope-core-review.md`, `issue-423-profile-rebuild-scope-security-review.md`, `issue-423-profile-rebuild-scope-adversarial-review.md`.
- Evidence supplied: `uv run ruff check src/ tests/` (pass); strict mypy on `tests/typing/public_api.py` (pass); focused profile, organizer, privacy and enumeration pytest files (pass); PostgreSQL tests skip locally without `PRME_TEST_DATABASE_URL`; `uv run pytest -q` before the review fixes (7214 passed, 1542 skipped, 2 xfailed), on the post-fix tree (7219 passed, 1549 skipped, 2 xfailed), and on the rebased final tree (7226 passed, 1550 skipped, 2 xfailed).

## Files Changed

- `src/prme/storage/engine.py`
- `src/prme/organizer/owner_profiles.py`
- `src/prme/storage/duckpgq_graph.py`
- `src/prme/storage/pg/graph_store.py`
- `src/prme/storage/graph_store.py`
- `src/prme/storage/event_store.py`
- `src/prme/storage/pg/event_store.py`
- `src/prme/privacy/read_graph.py`
- `src/prme/__init__.py`
- `tests/test_profile_graph_sources.py`
- `tests/test_node_enumeration.py`
- `tests/test_pg_graph_store.py`
- `tests/test_pg_event_store.py`
- `docs/ENTITY-PROFILES.md`
- `CHANGELOG.md`

## Approach Summary

`consolidate_knowledge(entity_names=...)` used to page every active node in the scope and filter it in Python. It now reads filtered, immutable-ID-ordered pages: records whose content names the person, records attributed to the person (the reserved speaker key or a claim's `speaker_reference`), claims whose evidence cites one of the person's turns (turn ids read from the event log, so an unmaterialized or archived note does not hide the claim), and claims the graph binds to the person's entity through HAS_FACT. The auto-detect path (`entity_names=None`) and any non-ASCII name still page the whole scope, because the database's case folding need not match the Unicode-aware matcher.

`consolidate_knowledge()` gained an optional `deadline` (`time.monotonic()`). It is checked between source pages and before preparing an entity, and raises the new `ProfileBuildDeadlineExceeded` before publishing, so the previous profile stays in place. Both organizer rebuild paths pass their own deadline. `GraphStore.scan_nodes()` gained optional `content_contains_any`, `speaker` and `evidence_events`; `EventStore.get_by_user()` gained optional `speaker`; `AdmittedGraphView` forwards the scan filters. No default changed.

## Must Fix

None. Reviewers found no Must Fix item.

## Should Fix

1. Protected graph-bound sources skipped the `source_availability` mask (core 1, security 1, adversarial S6). **Fixed.** The mask moved from `scan_pages()` into `absorb()`, the single funnel for all four reads, using the existing `without_availability()` helper. `engine.py`.

## Consider

1. Duplicate HAS_FACT read between the bound-claim phase and the per-name loop (core 2). **Fixed.** `subjects_of()` caches the edge targets per name, so the narrow path reads the edges once and the per-name loop reuses the result.
2. Deadline coverage was uneven around the edge and bound reads (core 3, adversarial S2). **Partly fixed, partly follow-up.** A deadline check now runs before each name's edge read and after the unbounded `get_nodes` call. The remaining risk is one database statement that runs past the deadline; that is follow-up #S2 below.
3. The `evidence_events` list is re-sent on every page of the evidence scan (core 4, adversarial S4). **Follow-up.** Grouped with the per-name event walk; see follow-up #S4 below.
4. The speaker scan asked for every lifecycle state and then discarded the retired ones (core 5, adversarial S7). **Fixed.** The attributed scan now uses the two source states; the archived-turn case is covered by the event-log turn lookup instead.
5. `entity_names=[]` ran two unfiltered whole-scope reads before returning 0 (core 6). **Fixed.** An empty list returns 0 immediately after normalization.
6. `ProfileBuildDeadlineExceeded` was not exported, and a protected caller sees `PrivacyDenied` from the disclosure wrapper (core 7). **Fixed.** The exception is exported from `prme` and the docstring states the protected behavior.
7. Sync-client parity for the new parameters (core 8). **Accepted.** The new filters and `deadline` are internal to the engine and the organizer; the synchronous client is unchanged on purpose.
8. Missing coverage: no test compared a narrowed build against a whole-scope build (core coverage). **Fixed.** `test_a_named_rebuild_matches_a_whole_scope_read` drops the narrowing filters and compares profile text, source ids and path counts. The other gaps raised are covered by the new non-ASCII, event-log filter, organizer deadline and LIKE-escaping tests. A protected build whose only claim path is HAS_FACT still has no dedicated test; the mask now lives in the single `absorb()` funnel, which every read uses.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Tenant isolation: scan filters | PASS | Every new filter is ANDed after `user_id` and `scope` on both backends and cannot widen a read. |
| Tenant isolation: `get_by_user(speaker=...)` | PASS | `user_id = ?`/`$1` stays first and unconditional. |
| Scope confinement in the rebuild | PASS | `scope=` on every read; `absorb()` re-checks `node.scope`. |
| `AdmittedGraphView` containment | PASS | Owner check and `project_node` still run; filters are forwarded, not used as authority. |
| SQL parameterization | PASS | DuckDB `?`, PostgreSQL `$n`; UUIDs normalized; no value interpolated. |
| LIKE escaping | PASS | `_escape_like` reuses the existing `query_nodes` behavior on both backends. |
| Speaker filter vs projection | PASS | Filters read stored metadata, every returned node is projected, and protected writes store nodes equal to their projection. |
| Protected source mask | PASS after fix | `absorb()` strips `source_availability` for protected reads. |
| Deadline no partial publication | PASS | Checked between pages and before preparation; publication happens after the last check. |
| Logging / PII | PASS | The deadline log line carries no name or content. |

Residual and accepted: the admitted view now pushes the content/speaker filters into SQL before `project_node`. Today this is not exploitable because protected write paths store nodes equal to their projection and every returned node is still projected. The invariant is documented in `AdmittedGraphView.scan_nodes` and left as a latent concern for a future write path that skips validation.

## Pattern Consistency Assessment

- The new `scan_nodes` filters mirror `query_nodes(content_contains_any=...)` semantics and share `_escape_like`.
- Engine, protocol, DuckDB, PostgreSQL and `AdmittedGraphView` carry the three new keyword-only parameters in the same order; `get_by_user` carries `speaker` last in both stores.
- The protected mask now uses the existing `without_availability()` helper, matching `summarization.py` and `consolidation.py`.
- The sync client was deliberately not extended (internal parameters).

## Redundancy Check

- No new types beyond `ProfileBuildDeadlineExceeded`, now exported.
- The duplicated HAS_FACT read was removed with the per-name cache.
- No duplicate filter helpers; `_escape_like` and `without_availability` are reused.

## Wiring Findings

- Both organizer call sites pass real `time.monotonic()` deadlines: `maintenance.py` and `end_session()`.
- `_rebuild()` catches `ProfileBuildDeadlineExceeded` before the generic handler and logs the stop at info level.
- The new protocol parameters are accepted by both backends; a custom backend that does not accept them raises `TypeError` on a named build, which the CHANGELOG states.
- No new environment or config value, no registration or migration step.

## Data Safety and Read/Write Proof

Not applicable: no new durable field, schema, index, migration or write. The change alters read queries only. Stored `prme_speaker_v1` and `speaker_reference` values are read as before; the SQL prefilter now trims surrounding spaces to match `normalize_speaker()`.

## Break Scenarios (adversarial)

| # | Scenario | Kind | Likelihood | Impact | Verdict | Resolution |
|---|---|---|---|---|---|---|
| S1 | A non-ASCII name folds differently in SQL `LOWER()` than in the Unicode matcher, so sources are missed and a live profile is retired | newly introduced | low | high | Fix now | Fixed: non-ASCII names keep the whole-scope read; test added |
| S2 | One filtered page (or the unpaged edge/bound read) can still run past the deadline | newly introduced gap | medium on a 100x store | medium | Follow-up | Follow-up issue; per-name and post-bound checks added |
| S3 | An un-normalized legacy speaker value stops matching the SQL prefilter | newly introduced read over old data | low | medium | Follow-up | Fixed now: `TRIM()` on both sides in all four predicates; event-log test added |
| S4 | One event-log walk per name and a re-sent evidence id list for multi-name calls | newly introduced | medium at benchmark scale | medium | Follow-up | Follow-up issue |
| S5 | A timed-out rebuild is indistinguishable from a healthy no-op | newly introduced | medium, coupled to S2 | medium | Fix now | Fixed: info log line on the deadline stop |
| S6 | The graph-bound read keeps `source_availability`, so protected profiles re-prepare or fail validation | newly introduced | low to medium on a protected store | medium | Fix now | Fixed: mask moved into `absorb()` |
| S7 | The speaker scan reads every lifecycle state and discards retired rows | newly introduced | low to medium | low to medium | Fix now | Fixed: the scan uses the two source states |

Pre-mortem headline: "A Greek-named customer's knowledge profile vanished after an upgrade because the new SQL prefilter lowercased names differently from the matcher it feeds, and nobody noticed because the rebuild that dropped it also gave up quietly on the next pass." Both halves are addressed: the non-ASCII fallback keeps the sources, and the deadline stop now logs at info level. The per-statement overrun and the multi-name event walk remain follow-ups.

## Follow-ups Raised

- #505 (Follow-up A): a single filtered page can still run past a rebuild's deadline. Add a per-statement timeout or bounded id windows. Found while working #423.
- #506 (Follow-up B): a multi-name rebuild walks the event log once per name and re-sends every turn id on each evidence page. Use one event-log pass and windowed evidence ids. Found while working #423.

## Resolution Status

| Finding | Severity | Status |
|---|---|---|
| Protected `source_availability` mask skipped on the bound-claim read | Should Fix / Fix now | Resolved |
| Non-ASCII name folding drops sources | Fix now | Resolved (whole-scope fallback) |
| Un-normalized legacy speaker values | Follow-up | Resolved now (`TRIM` in all four predicates) |
| Speaker scan reads every lifecycle state | Fix now | Resolved |
| Timed-out rebuild is silent | Fix now | Resolved (info log) |
| Empty `entity_names` does two full scans | Consider | Resolved (early return) |
| Duplicate HAS_FACT read | Consider | Resolved (cached `subjects_of`) |
| Exception not exported / protected wrapper behavior | Consider | Resolved |
| Deadline around edge and bound reads | Consider | Resolved for the loop checks; statement-level timeout is Follow-up A |
| Evidence id list re-sent per page / per-name event walk | Consider | Follow-up B |
| Sync-client parity | Consider | Accepted, parameters are internal |
| Admitted-view prefilter before projection | Consider (security) | Accepted, invariant documented |
| Missing equivalence test | Coverage | Resolved |
| No dedicated protected HAS_FACT-only test | Coverage | Open coverage gap, mitigated by the single `absorb()` funnel |
