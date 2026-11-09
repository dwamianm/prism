# Core review: issue dwamianm/prism#423

Status: COMPLETE

Snapshot: b352281961d1caf228350041f22588d6b0773c7fa775a93ad18535362fc1c596
(the branch's changes are uncommitted working-tree edits; `git rev-parse HEAD`
is `52d99de1be90477abcfa7256088fd8359fea2eb5`, so the diff reviewed is
`git diff main -- . ':!.env.issue423-tmp'`)
Base: main @ 52d99de1be90477abcfa7256088fd8359fea2eb5
Branch: issue-423-profile-rebuild-scope-scan

## Files/scope reviewed

- `src/prme/storage/engine.py` (the whole `consolidate_knowledge()` body,
  `scan_nodes()`, the new exception, and `end_session()`'s refresh call)
- `src/prme/organizer/owner_profiles.py` (`_rebuild`, both refresh loops, and
  the existing `_mentioning`/`_bound_claims` analogues)
- `src/prme/storage/duckpgq_graph.py`, `src/prme/storage/pg/graph_store.py`
  (filter SQL, `_escape_like`, `get_nodes`), `src/prme/storage/graph_store.py`
  (protocol), `src/prme/privacy/read_graph.py` (`AdmittedGraphView`)
- `src/prme/storage/event_store.py`, `src/prme/storage/pg/event_store.py`
  (speaker filter, `before` cursor), `src/prme/models/speaker.py`
- `src/prme/organizer/profiles.py` (`mentions_entity`, `build_profile` order),
  `src/prme/models/profile.py`, `src/prme/models/derivation.py`,
  `src/prme/privacy/journals.py`, `src/prme/privacy/admission.py`,
  `src/prme/retrieval/owner_profile.py`
- Analogues for the masking pattern: `src/prme/organizer/summarization.py:225`,
  `src/prme/organizer/consolidation.py:275`; for the bound-claim read:
  `src/prme/organizer/owner_profiles.py:110-133`
- All four changed test files, plus existing profile/privacy tests that call
  `consolidate_knowledge()`.
- Docs/CHANGELOG statements checked against the code. No config or default was
  touched (confirmed: no `config.py`/`config_audit.py` in the diff).

## Findings by severity

### Must Fix

None. The narrowing is complete for the non-protected paths: I traced every
source path the old whole-scope filter produced (`named`, `said`,
`subject_claims`, `spoken_claims`) into the four new reads and the sets are the
same, so `build_profile` (which re-sorts by event time then id,
`src/prme/organizer/profiles.py:102`) yields the same text. Details and the
narrow residual caveats are under Checklist coverage item 1.

### Should Fix (non-blocking)

1. `src/prme/storage/engine.py:4807` — the protected-context metadata mask is
   not applied to the bound-claim read. `scan_pages()` strips
   `source_availability` for protected reads (`engine.py:4696-4701`), but
   `absorb(await graph.get_nodes(sorted(bound), user_id=user_id))` is called
   outside `scan_pages()`, so under `AdmittedGraphView.get_nodes()`
   (`src/prme/privacy/read_graph.py:35-45`, which projects through
   `ReadContext.project_node`, adding `source_availability` at
   `src/prme/privacy/reads.py:267`) the bound claims keep that key. The old
   code put every node through the mask because everything came from the page
   loop, so this is a regression for privacy-protected owners. Profile text is
   unaffected (the mask only touches metadata), but
   `profile_request_hash()` -> `node_checksum()`
   (`src/prme/models/profile.py:41-56`, `src/prme/models/derivation.py:34-40`)
   hashes source metadata, so the request identity, the prepared-plan payload
   and checksum, and the `reusable()` hit for an otherwise identical rebuild
   now depend on transient source availability. Fix: move the mask into
   `absorb()` (the single funnel for all four reads), which also removes the
   duplicate of the existing helper `without_availability`
   (`src/prme/privacy/journals.py:706`, used exactly this way in
   `src/prme/organizer/summarization.py:225` and
   `src/prme/organizer/consolidation.py:275`). This is not a blocker for the
   ticket: profile content and the non-protected paths are unchanged.

### Consider

2. `src/prme/storage/engine.py:4795-4807` duplicates the HAS_FACT read and the
   "person entity wins over same-text organisation" selection that the
   per-name loop repeats at `engine.py:4898-4917` (and that
   `owner_profiles._bound_claims`, `src/prme/organizer/owner_profiles.py:110-133`,
   implements a third time). For a one-person rebuild (every organizer caller)
   the person's `HAS_FACT` edges are now read twice per build. The ticket is
   about reducing rebuild work, so computing `bound` once and reusing the ids
   for `subjects` would remove a round trip without changing behavior.

3. Deadline coverage is uneven in the new read. `src/prme/storage/engine.py:4792`
   checks once before the `for name in entity_names` edge loop, so a call with
   many names can run all edge reads past the deadline; and
   `engine.py:4807` issues one unpaged `get_nodes(sorted(bound))` (one array
   parameter on PostgreSQL, one `IN (?,...)` statement with one parameter per
   id on DuckDB) with no check after it. Both are bounded by one person's claim
   count, but they are the only reads in the new path without a between-page
   check.

4. `src/prme/storage/engine.py:4770-4780`: `turn_events()` materializes the
   person's whole turn list and the `evidence_events` scan re-serializes it as
   a JSON parameter on every page of that scan (`json_each(?::JSON)` on DuckDB,
   `?| $n::text[]` on PostgreSQL). Cost per page is O(turns); for a person with
   many turns this grows rather than staying page-sized. Chunking the event ids
   (or filtering on the event page) would keep each page bounded.

5. `src/prme/storage/engine.py:4759`: the speaker scan passes
   `lifecycle_states=list(LifecycleState)` and `absorb()` then drops everything
   outside `TENTATIVE`/`STABLE` (`engine.py:4720-4723`), so archived and
   superseded rows are read and discarded. `source_states` would express the
   same result with less work.

6. `src/prme/storage/engine.py:4746-4759` + `engine.py:4847`: with
   `entity_names=[]`, the empty list is falsy in the storage filters, so both
   reads run *unfiltered* whole-scope scans before `if not entity_names:
   return 0`. The old code scanned the whole scope once in this case; the new
   code scans it twice. Not a regression in behavior, but an empty filter list
   silently meaning "no filter" is a footgun in a new API surface.

7. Public-surface consistency for the new exception:
   `ProfileBuildDeadlineExceeded` is defined at
   `src/prme/storage/engine.py:182` and is not exported from
   `src/prme/__init__.py`, while the sibling `StaleProfileError`
   (`src/prme/models/profile.py:59`) is exported at `src/prme/__init__.py:90`
   and `:322`. A caller of the public `MemoryEngine.consolidate_knowledge()`
   that wants to catch the documented raise must import from a storage module.
   Related: under a privacy policy the `@privacy_disclosure` wrapper routes the
   coroutine through `capability_call`
   (`src/prme/privacy/admission.py:38-45`), which rewrites any non-`PrivacyDenied`
   exception to `PrivacyDenied("privacy_processing_unavailable")`, so a
   protected caller that passes `deadline` never sees
   `ProfileBuildDeadlineExceeded` even though the docstring
   (`engine.py:4611-4613`) says it is raised.

8. Sync-surface parity: `MemoryClient.scan_nodes()` (`src/prme/client.py:970`)
   and `MemoryClient.consolidate_knowledge()` (`client.py:1104`, docstring
   "matching the async API") were not extended with the new optional filters /
   `deadline`. Acceptable if the new parameters are deliberately internal, but
   it is a deliberate choice worth recording.

## Security and runtime wiring screen

- All three new `scan_nodes` filters and the new `get_by_user` filter are
  parameterized; `content_contains_any` goes through `_escape_like` in both
  backends and the speaker `IN` lists are placeholders, so no injection. The
  DuckDB `evidence_events` parameter is `json.dumps` of UUID-normalized ids.
- Tenant scoping is preserved: `scan_pages` passes the caller's `user_id` and
  `scope`; `get_by_user` is owner- and scope-scoped; `bound` drops edges whose
  `user_id` differs; `absorb` re-filters `scope`. `AdmittedGraphView` raises on
  `operator_unscoped` and keeps its owner check for the new filters.
- No new environment/config reads, no schema/index/migration or write change,
  no default change. `DERIVATION_VERSION` is correctly not bumped (derived
  profile text is unchanged).
- Runtime wiring: both organizer call sites pass real `time.monotonic()`
  deadlines (`maintenance.py:195`, `engine.py:5192`); `_rebuild` catches the
  new exception before the generic handler so no failure is logged
  (`owner_profiles.py:238-248`). Verified the DuckDB JSON semantics the new
  DuckDB filters rely on (`json_each(...).value::VARCHAR` keeps string scalars
  quoted on both sides of the join, so equality still holds).

## Checklist coverage

1. **Correctness and logic — checked.** Equivalence traced per path:
   - `named`: old = every in-scope active node whose content `mentions_entity`
     matches; new = `absorb` of a LIKE-substring superset (`engine.py:4748-4749`)
     then the same `naming` filter, so identical.
   - `said`: new SQL speaker scan (`engine.py:4757-4759`) plus `said_by`'s
     `metadata_speaker` filter reproduces the old "old page scan with
     `own_turns`" set; the scan runs even when `include_speaker_turns=False`,
     which only affects `claim_nodes` (needed for #448) and is filtered out of
     `said`/`named`.
   - `subject_claims`: `get_nodes(sorted(bound))` (`engine.py:4807`) plus
     `absorb`'s state/scope/type filter reproduces the old
     `claim_nodes ∩ subjects`.
   - `spoken_claims`: every old case (speaker_reference match, cited turn
     speaker) is absorbed through the speaker scan or the event-id scan
     (`engine.py:4786-4788`), and the per-name filter at `engine.py:4917-4937`
     is unchanged.
   - `existing_profiles`: an old profile's content always contains its name
     (`PROFILE_PREFIX`/`PROFILE_HEADER`, required as well by
     `project_generated_node`), so the content scan still finds it and the
     `previous`/retire behavior is preserved.
   - `entity_names=None` is still the whole-scope scan.
   - Residual narrow risks (not findings): the prefilter uses DB `LOWER`+LIKE
     while the Python filters use `casefold`/`re.IGNORECASE`, so a
     full-case-folding difference (for example `ß`/`SS`) or a legacy
     unnormalized `prme_speaker_v1` value could drop a source that the old
     Python-only filter kept. Not reachable through the current write paths.
   - Boundary handling: `deadline=None` and `deadline` passed are both covered;
     the empty-`entity_names` and unpaged-`bound` cases are noted above.
   - Concurrency: no shared mutable state; `absorb`/`absorbed` are call-local;
     `event_speakers` cache is unchanged. `_rebuild` maps the deadline
     exception to `None` before the generic handler, so no warning is logged.
   - Type safety: the protocol and both backends stay in sync; mypy strict on
     the public surface, as reported by the sweep.

3. **Code quality — checked.** The `scan_pages` helper is a good deduplication
   of the old inline loop; naming (`absorb`, `turn_events`, `bound`) is clear.
   No dead code left from the removed `mentioning` helper (it had only the one
   use). Findings 2-6 are the quality/performance items.

4. **Pattern consistency and DRY — checked.** Filters mirror
   `query_nodes(content_contains_any=...)` semantics and documentation
   (`graph_store.py:202-235`, `duckpgq_graph.py:1211-1218`); the PG filter is
   documented by reference to the DuckDB one. The protected mask is the one
   place the pattern is not followed (finding 1); `without_availability`
   already exists and is used the same way in summarization/consolidation.
   Parameter-level comparison for `scan_nodes`: engine, protocol, DuckDB, PG and
   `AdmittedGraphView` all carry the three new keyword-only parameters in the
   same order; `get_by_user` carries `speaker` last in both stores (keyword-only,
   so positional callers are unaffected) and `_get_by_user_sync`'s positional
   forward matches. Sync-client parity is finding 8.

5. **Redundancy — checked.** No new types beyond
   `ProfileBuildDeadlineExceeded`; no duplicate filter helpers
   (`_escape_like` is reused). The duplicated HAS_FACT read (finding 2) is the
   one real redundancy; `list(LifecycleState)` (finding 5) is a second.

## Missing test coverage

- No test asserts the acceptance criterion directly: that a named rebuild's
  profile text and `source_paths` equal those of a whole-scope build of the
  same store. The new tests assert presence/absence and one hand-counted
  four-path case, which would not catch a future second narrowing that drops a
  path.
- No privacy-protected named rebuild that exercises a claim reachable *only*
  through `HAS_FACT`; such a test would have caught finding 1 (the unmasked
  `source_availability` on that source).
- No test of `content_contains_any` escaping for `%`, `_` or `\` on either
  backend, although the pattern builder is shared with `query_nodes`.
- The `deadline` behavior is tested through `consolidate_knowledge()` and
  `_rebuild()` only, not through `refresh_stale_profiles()` /
  `refresh_owner_profiles()`, so the "not rebuilt, previous profile stays"
  contract of the organizer loops is not pinned.
- The DuckDB `EventStore.get_by_user(speaker=...)` filter has no direct test
  (it is only covered indirectly by the archived-turn profile test); the
  PostgreSQL counterpart has one and skips locally.
