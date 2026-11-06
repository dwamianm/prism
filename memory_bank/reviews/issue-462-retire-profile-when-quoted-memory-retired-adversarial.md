# Adversarial review: issue #462 (retire profile when quoted memory retired)

Status: COMPLETE

Base: `main` at `c8b817eed4cff81679ed43e3c376264d8438c2b7`
Reviewed snapshot: `a54553016d1eb504918a5ee4f49c76d055f89b2da050f6a58f4274e305cc1438` (uncommitted work; HEAD == base)
Re-review snapshot: `569387d0a9c57c0e5ddc32c3dfe0823f7aca32338f55fb36ed3859b38ea1c3e8` (see the Re-review section at the end)

## Verdict

The read-path filter is placed correctly for the main retrieval pipeline (all candidate-producing
stages run before it, nothing after it adds candidates), and the ordinary case (active sources kept,
archived/superseded/missing source drops the profile) works. The change nevertheless leaves two ways
for the #462 leak to survive (fail-open control flow, legacy profiles without `source_node_ids`), and
it makes the profile's visibility depend on a rebuild path that is gated by an unrelated default-off
flag, so in the default configuration a profile that loses one source disappears from retrieval
permanently. Four scenarios below, ranked by likelihood x impact.

## Scenarios

### 1. A profile disappears from retrieval permanently because the documented rebuild never runs (newly introduced)

- **Trigger.** A store has entity profiles (built by an explicit `MemoryEngine.consolidate_knowledge()`,
  or earlier by `end_session()` with the flag on). `enable_owner_profile_context` is at its default
  `False` (`src/prme/config.py:1301-1302`). A record a profile quotes is archived by the `archive` or
  `decay_sweep` organizer job (both in `DEFAULT_JOBS`, `src/prme/organizer/jobs.py:28-44`), superseded
  by a correction, or deleted. Every later `retrieve()` drops the profile.
- **Mechanism.** `src/prme/retrieval/pipeline.py:1134-1143` removes the profile on every request and
  writes nothing. The claimed rebuild path is gated: `src/prme/organizer/maintenance.py:188-190`
  runs `refresh_stale_profiles` only when `enable_owner_profile_context` is true, and
  `src/prme/storage/engine.py:5052` runs `refresh_owner_profiles` from `end_session()` under the same
  gate. Privacy-governed owners never get an organizer refresh at all
  (`src/prme/organizer/owner_profiles.py:222-223`, `:264`). There is no scheduled or CLI path to
  `consolidate_knowledge()`; it must be called explicitly. So the "until it is rebuilt" state is
  permanent for any deployment that does not run that one flag.
  The profiles are otherwise ordinary retrievable SUMMARY memories: the new test
  `tests/test_retired_profile_sources.py:65-72` builds profiles under the default test configuration
  (no `enable_owner_profile_context`) and gets the profile back by ordinary ranking, so this population
  is real.
- **Likelihood.** Medium-High: once profiles exist, any retention/decay/archive sweep or correction
  triggers it, and the flag is off by default.
- **Impact.** Medium: silent loss of the user's profile context (the reason `consolidate_knowledge`
  exists) for every subsequent question, not only questions about the retired record. No data is lost
  in the store, but retrieval never surfaces it again without operator action.
- **Detection.** Low. Nothing is logged; the persisted receipt payload does not include exclusions
  (`src/prme/retrieval/pipeline.py:1426-1466`), only the `candidates_filtered` count. Only
  `response.excluded` carries a `profile_source_retired:<uuid>` reason, and callers rarely read it.
- **Cheapest mitigation.** Run the stale-profile refresh whenever profiles exist (remove or invert the
  gate at `src/prme/organizer/maintenance.py:188-190`), and correct the claim in
  `docs/ENTITY-PROFILES.md:80` and the CHANGELOG that the organizer pass rebuilds it. Size S for the
  gate and docs; size M if a receipt feature/count for suppressed profiles is also wanted.
- **Recommended verdict.** Fix now (docs plus the gate) - the change's contract says the profile is
  suppressed "until rebuilt", but the rebuild is unreachable in the default configuration.

### 2. Non-privacy read failure silently re-enables the leak (newly introduced control flow, mirrors a pre-existing pattern)

- **Trigger.** A retrieval has at least one profile candidate and `graph_store.get_nodes` raises
  anything other than `PrivacyDenied`: a DuckDB/PG connection or lock error, a malformed row hit by
  `_owned_view_sync`, an oversized `IN` list, or a graph store that does not accept the keyword.
- **Mechanism.** `src/prme/retrieval/pipeline.py:1684-1689` catches every exception and
  `return candidates, []`, so the retired profile is served with its retired text. Under
  `_privacy_context` the warning is emitted with `exc_info=False` (`:1687`), so not even a traceback
  is available. The module function itself never fails open; the wrapper choice does.
- **Likelihood.** Low-Medium. The read is one owner-scoped `IN` query on the hot path
  (`src/prme/retrieval/profile_sources.py:84-90`), but it now runs on every retrieval that happens to
  surface a profile, so a single pack/connection/schema anomaly disables the whole filter for that
  request.
- **Impact.** High: the exact acceptance criterion ("retrieval must not return that memory's text
  through the profile") is violated, including for archived/superseded and deleted records, with no
  error surfaced to the caller.
- **Detection.** A `WARNING` log only; no receipt flag, no response field, no metric.
- **Cheapest mitigation.** Fail closed for this stage: on a non-privacy exception, drop every
  candidate whose node is a profile (or keep them and add
  `ExcludedCandidate(reason="profile_sources_unverified")`) instead of returning all candidates.
  Size S (a few lines at `src/prme/retrieval/pipeline.py:1684-1689`).
- **Recommended verdict.** Fix now - it is the one path where the new safety check can be turned off
  by an ordinary storage error, and the fix is a few lines.

### 3. Profiles written before v0.12.0 have no source list and are never checked (pre-existing leak, incomplete fix)

- **Trigger.** A store upgraded from v0.11.0 or earlier that ran `consolidate_knowledge()` while
  entity profiles carried only `{"entity_profile": True, "entity_name": ...}` and no
  `source_node_ids`. That shape was written until commit `88cc5e24` (2026-09-12) added
  `source_node_ids` and `profile_format_version: 2`; `v0.11.0` was tagged 2026-09-11. The v1 profile is
  still active, was never rebuilt, and one of the records it quoted is later archived, superseded or
  deleted.
- **Mechanism.** `src/prme/retrieval/profile_sources.py:40-41` returns `[]` when `source_node_ids` is
  absent or not a list, and `:72-73` omits such candidates from `quoted`, so nothing is verified. The
  profile's content (which the old builder wrote from the source records) is still an indexed SUMMARY
  and is returned by candidate generation and by the owner-profile lookup. This is deliberate:
  `src/prme/retrieval/profile_sources.py:65-66` says an unparsable profile is "left in place", while
  `docs/ENTITY-PROFILES.md:72-80` states the rule without an exception for legacy shapes.
- **Likelihood.** Low: only one release wrote v1 profiles and any later `consolidate_knowledge()` run
  archives them, but no migration or backfill exists outside the privacy path
  (`src/prme/storage/privacy_migration.py:683` is privacy-only).
- **Impact.** High: the same retired/deleted text leak the issue is about, with no limit to how old
  the retired record is.
- **Detection.** None: no log, no exclusion, no count; the profile simply never enters `quoted`.
- **Cheapest mitigation.** Treat a profile with no parsable source list as unverifiable and leave it
  out of retrieval (reason `profile_sources_unknown`), or add a one-time rebuild/retire pass for v1
  profiles. Size S for the retrieval drop; M if a rebuild is preferred.
- **Recommended verdict.** Fix now (drop unverifiable profiles) or record an explicit owner decision
  to accept the residual leak for v0.11 stores.

### 4. The same rule does not hold for consolidation/hierarchical summaries (pre-existing, outside this ticket)

- **Trigger.** A consolidation summary (`src/prme/organizer/consolidation.py:438`) or hierarchical
  summary (`src/prme/organizer/summarization.py:358`) quotes source text and records the quoted
  identities; one of those records is later archived/superseded/deleted; a query retrieves the
  summary.
- **Mechanism.** The filter matches only `node.metadata.entity_profile is True`
  (`src/prme/retrieval/profile_sources.py:37`). Summaries set `consolidation_summary`/summary
  metadata and also carry `source_node_ids`/`selected_source_node_ids`; they are never checked, so
  their content still contains the retired text. The `consolidate` and `summarize` jobs run in
  `DEFAULT_JOBS` (`src/prme/organizer/jobs.py:28-44`), so this representation is far more common than
  entity profiles.
- **Likelihood.** High, but it is pre-existing and outside issue #462's stated scope.
- **Impact.** Medium-High: the same "retired memory text returned through a derived view" defect
  survives in a sibling representation; this is likely a separate follow-up issue.
- **Detection.** None.
- **Cheapest mitigation.** Generalize the read-path filter to derived SUMMARY nodes that declare
  sources, using `selected_source_node_ids` for summaries (their quoted subset), or open a follow-up
  issue with the same acceptance rule. Size M because summary quoting uses a different field and
  `selected_source_node_ids` is absent on some older shapes.
- **Recommended verdict.** Follow-up (separate issue) - do not expand this PR, but record it so the
  rule is not assumed to be representation-wide.

## Clean checklist results (one line each)

- Pipeline placement: correct; Stage 5.5h is after projection/augmentation/folding/observations/owner
  profile and before replaced-source suppression, selection and packing, and no later stage adds
  candidates (`src/prme/retrieval/pipeline.py:1121-1143`, `:1259`, `:1279-1284`).
- Cross-scope hints and owner-profile insertion: both covered (`:1209-1211`, `:1121-1143`).
- Metadata guard (`entity_profile is True`): matches the JSON round-trip (DuckDB `json.loads` at
  `src/prme/storage/duckpgq_graph.py:2130-2135`) and privacy projection
  (`src/prme/privacy/journals.py:100-115`); no bypass found for engine-written profiles.
- Backends: DuckDB and PostgreSQL `get_nodes(include_superseded=True, user_id=...)` both return all
  lifecycle states and filter by owner (`src/prme/storage/duckpgq_graph.py:173-210`,
  `src/prme/storage/pg/graph_store.py:210-244`); single-file packs use the same graph store.
- Read semantics: archived/superseded/missing handled, contested kept (consistent with
  `ACTIVE_LIFECYCLE_STATES`), EXPLICIT keeps retired-but-existing sources as documented.
- Determinism/receipts: no writes, no reordering of kept candidates, one extra read only when a
  profile candidate exists; the receipt schema validator is unaffected because a dropped profile
  takes its `owner_profile` score adjustment with it (`src/prme/models/relevance.py:523-529`).
- Blast radius: `suppress_retired_profiles` is used only by the pipeline wrapper; `source_node_ids`
  is also read by `_is_stale` (`src/prme/organizer/owner_profiles.py:177-181`) and erasure/privacy
  journals, all of which are unchanged by a read-only filter.
- Tests: the new file covers active-only, archive, supersede, delete, EXPLICIT archive, EXPLICIT
  delete, and rebuild; it does not cover the owner-profile stage, cross-scope hints, legacy/malformed
  source lists, or the fail-open wrapper.

## Attacks that failed (why each did not break the change)

1. **Re-insertion after the filter.** No stage after 5.5h adds candidates: `select_candidates` and
   `pack_context` only remove/format (`src/prme/retrieval/pipeline.py:1259-1284`), the temporal
   enricher works from packed records only (`src/prme/retrieval/temporal_relations.py:593-602`), and
   packing has no graph reads.
2. **Owner-profile and hint bypass.** The owner profile is inserted at 5.5g immediately before 5.5h,
   and hints are filtered inside their own pass; both use the same `retrieval_mode`.
3. **False positives from metadata/backends/privacy.** `source_node_ids` is canonicalized before the
   key lookup, owner scoping is the request owner, and the admitted view preserves the profile's
   source list, so active sources are found and the profile is kept; only genuinely unverifiable
   source lists escape the check (scenario 3).

## Additional observations

- **Owner-profile receipt inconsistency (newly introduced).** `execution_features["owner_profile_context"]`
  and `candidate_counts["OWNER_PROFILE"]` are set at `src/prme/retrieval/pipeline.py:1126-1128`
  before 5.5h may drop the profile at `:1140-1143`, so the receipt records the feature and a path
  count for a request whose results contain no profile. `src/prme/models/relevance.py:523-529` only
  checks the reverse direction, so nothing fails. Add a test and unset/record on drop. Size S.
  (Addressed in the re-review; see New scenario 1.)
- **DEPRECATED lifecycle is not treated as retired (pre-existing divergence).** `DEFAULT_EXCLUDED_LIFECYCLE`
  is `{SUPERSEDED, ARCHIVED}` (`src/prme/retrieval/filtering.py:27-30`), while `DEPRECATED` is a
  reachable lifecycle state (`src/prme/storage/lifecycle.py:19-24`) produced by the organizer
  (`src/prme/organizer/maintenance.py:317-321`) and excluded from `ACTIVE_LIFECYCLE_STATES`
  (`src/prme/types.py:284-288`). A profile quoting a deprecated source therefore stays. The source
  node itself may also still leak because `deprecate()` bypasses index eviction, so this is a
  consistency gap to resolve separately, not a regression of this change.
- **No `source_node_ids` length bound.** A pathological profile with a very large source list turns
  into one large `IN` query per retrieval; profiles are built within a token budget
  (`src/prme/storage/engine.py:4857-4865`), so this is theoretical.

## Re-review (snapshot 569387d0a9c57c0e5ddc32c3dfe0823f7aca32338f55fb36ed3859b38ea1c3e8)

Re-read: `src/prme/retrieval/profile_sources.py` (rewritten), the Stage 5.5h block and
`_suppress_retired_profiles` in `src/prme/retrieval/pipeline.py`, `docs/ENTITY-PROFILES.md`,
`CHANGELOG.md`, and `tests/test_retired_profile_sources.py` (11 tests). Base unchanged at
`c8b817ee`; the worktree is still the uncommitted change.

### Adjudication of the four original scenarios

- **Scenario 1 (default config never rebuilds): resolved as an explicit product decision.** The
  docs now name the gate (`docs/ENTITY-PROFILES.md:83-86`) and the CHANGELOG says "until an explicit
  `consolidate_knowledge()` rebuild (or the organizer's stale-profile pass, when
  `enable_owner_profile_context` is on)". The behavior is described accurately now, so the
  suppression is no longer a contract violation; an automatic rebuild remains a follow-up product
  decision. Nit: the in-code Stage 5.5h comment still says "the organizer's stale-profile pass
  rebuilds it from the same list" with no gate (`src/prme/retrieval/pipeline.py:1142`).
- **Scenario 2 (fail-open): fixed and verified.** The wrapper now returns
  `exclude_unverified_profiles(candidates)` on any non-`PrivacyDenied` exception
  (`src/prme/retrieval/pipeline.py:1698-1706`), and the helper drops every entity-profile SUMMARY
  (`src/prme/retrieval/profile_sources.py:44-46`, `:79-90`). Break attempts failed: the helper
  cannot miss an engine-written profile (every writer stores a bool `entity_profile` on a SUMMARY);
  `PrivacyDenied` still aborts the request; `BaseException` subclasses are not swallowed; the hints
  call uses the same wrapper. The only residual is a profile whose `entity_profile` is truthy but
  not `True` (`1`, `"true"`), which neither the normal nor the fail-closed path recognises; all
  engine writers store JSON `true`, and existing retrieval helpers already use `is True`
  (`src/prme/retrieval/owner_profile.py:68`), so this is a consistency nit, not a reachable leak.
- **Scenario 3 (legacy profiles with no source list): fixed and verified.** An unparsable source
  list now drops the profile in every mode with `profile_sources_unknown`
  (`src/prme/retrieval/profile_sources.py:128-130`), and that branch runs before the EXPLICIT check,
  so EXPLICIT drops unverifiable profiles too. Break attempts failed: a partially malformed list
  still checks every parsable id (the only verifiable part); a JSON-string or dict
  `source_node_ids` drops the profile; a list of UUID objects is parsed. The only nuance is that
  EXPLICIT no longer surfaces a legacy profile for an operator audit; the node is still readable via
  `get_node`/`query_nodes` and the docs state the rule, so this is acceptable.
- **Scenario 4 (consolidation/hierarchical summaries): accepted as a separate follow-up.** The
  guard is still `entity_profile is True` (`src/prme/retrieval/profile_sources.py:44-46`), so this
  PR leaves the sibling representation untouched, as agreed. No change to that verdict.

### New scenario 1 (receipt pop runs before selection; pre-existing class, narrowed but not closed)

- **Trigger.** A request with `enable_owner_profile_context` on and a named owner, where the owner
  profile survives Stage 5.5h but selection then removes it from the returned results: `limit=1`
  (the direct case), a `min_score` floor between the top result's score and the profile's inherited
  score, or a `max_per_source`/`max_per_evidence` collision. Packing can also leave it out for
  budget.
- **Mechanism.** The pop block runs at `src/prme/retrieval/pipeline.py:1147-1157`, but
  `select_candidates` runs at `:1273` and `pack_context` at `:1293-1294`. With `limit=1` the profile
  is excluded with `result_limit` after the pop check, so the receipt keeps
  `execution.features["owner_profile_context"]` and `candidates_generated["OWNER_PROFILE"]` while
  `response.results` contains no profile. The receipt validator only checks the reverse direction
  (a score adjustment without the feature, `src/prme/models/relevance.py:523-529`), so nothing
  fails. Label: pre-existing for the selection stages; the new block narrows the inconsistency to
  them instead of closing it.
- **Likelihood.** Medium: `limit=1` is a natural caller choice, and the new test only exercises the
  default limit.
- **Impact.** Low: receipt/audit mis-attribution, no leak and no wrong result; `in_context` remains
  the separate packing signal.
- **Detection.** Only a consumer that cross-checks the feature against `response.results` (the new
  test does, but only for the 5.5h drop).
- **Cheapest mitigation.** Move the 8-line pop block below `select_candidates` (`:1273`) so "still
  in the results" matches the comment; keep packing separate because `in_context` already records
  it. Size S.
- **Recommended verdict.** Follow-up (or Fix now, since it is a move of an existing block).

No second new scenario: the other candidate attacks (post-filter re-insertion, metadata/backend
mismatch, owner-profile/hint bypass) still fail for the reasons recorded above.

### Clean re-review results (one line each)

- Fail-closed helper: drops only entity-profile SUMMARY candidates, preserves order and
  non-profiles, and returns `profile_sources_unverified` exclusions.
- Unverifiable path: no engine writer emits an empty or non-list source list for an active profile
  (publication requires non-empty sources; privacy projection always writes a list), so the drop
  cannot regress engine-built profiles.
- Owner receipt pop: removes both the feature and the `OWNER_PROFILE` count, and no
  `owner_profile` score adjustment can survive because the adjustment lives on the dropped
  candidate's own provenance.
- Existing profile tests: real profiles built by `consolidate_knowledge()` carry source lists, so
  the unknown-source drop does not regress them (110 passed/76 skipped evidence).
- Test coverage gaps: the wrapper's exception path is only unit-tested through the helper (no
  integration test that a raised source read drops profiles), no EXPLICIT-unknown test, and no
  `limit`/`min_score` receipt test.

Status: COMPLETE

## Pre-mortem headline

Incident: knowledge profiles silently vanish from retrieval after one archived source, because the default configuration never runs the documented rebuild.
