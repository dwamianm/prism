# Core review: issue #462, leave out a profile that quotes a retired memory

Status: COMPLETE

- Base commit: c8b817eed4cff81679ed43e3c376264d8438c2b7
- Reviewed snapshot: a54553016d1eb504918a5ee4f49c76d055f89b2da050f6a58f4274e305cc1438 (uncommitted work; HEAD equals base)
- Re-reviewed snapshot: 569387d0a9c57c0e5ddc32c3dfe0823f7aca32338f55fb36ed3859b38ea1c3e8 (uncommitted work; HEAD equals base)
- Final delta snapshot: 8d1f2bccf856e2f049ccc253e7f024b7a32961eb4cf24ea278cb8df0daf34704 (uncommitted work; HEAD equals base)
- Checklist: 1 Correctness & Logic; 3 Code Quality; 4 Pattern Consistency & DRY; 5 Redundancy & Unnecessary Code; plus security and runtime-wiring screen.

## Scope

A read-path filter (`src/prme/retrieval/profile_sources.py`) that drops a
knowledge-profile candidate when any record named in its
`metadata.source_node_ids` is archived/superseded (DEFAULT mode) or gone
(any mode), wired at Stage 5.5h and on cross-scope hints, with a CHANGELOG
entry, a docs paragraph, and a new test module.

## Files Reviewed

- src/prme/retrieval/profile_sources.py (new)
- src/prme/retrieval/pipeline.py (import; Stage 5.5h; hints call; wrapper;
  Stage 5.5g and the receipt assembly)
- tests/test_retired_profile_sources.py (new)
- CHANGELOG.md; docs/ENTITY-PROFILES.md
- Support read for verification: src/prme/retrieval/filtering.py:27-131;
  src/prme/retrieval/replaced_sources.py:73-152;
  src/prme/retrieval/owner_profile.py:62-174;
  src/prme/retrieval/selection.py:90-151;
  src/prme/organizer/owner_profiles.py:90-191; src/prme/organizer/profiles.py:91-114;
  src/prme/models/profile.py:127-181; src/prme/models/relevance.py:523-530;
  src/prme/storage/duckpgq_graph.py:173-208, 1086-1100;
  src/prme/storage/pg/graph_store.py:210-244, 516-532;
  src/prme/storage/graph_rows.py:37-87;
  src/prme/privacy/read_graph.py:35-45; src/prme/privacy/reads.py:99-270;
  src/prme/privacy/journals.py:76-182, 224-328; src/prme/storage/engine.py:4833-4908,
  2883-2920; src/prme/types.py:63-78, 210-218.

## Findings (first review, snapshot a5455301)

### Must Fix

None.

### Should Fix

1. **A suppressed owner profile still marks the receipt as having placed it.**
   `pipeline.py:1121-1128` set `execution_features["owner_profile_context"]` and
   `candidate_counts["OWNER_PROFILE"]` when `add_owner_profile()` reported
   `profile_placed`, and Stage 5.5h could then drop that candidate. The receipt
   is built later from those values (`pipeline.py:1373-1424`, `1434-1445`,
   `1515`), so it recorded the feature while no result carried an
   `owner_profile` adjustment, contradicting the invariant at
   `pipeline.py:1118-1120` and `relevance.py:523-530` (issue #398).

### Consider

2. **Fail-open on a non-`PrivacyDenied` failure returned the retired text.**
   The wrapper caught every other exception and returned the candidates
   unchanged; only `PrivacyDenied` was re-raised.
3. **Protected-read cost and metadata.** The batch read is one query on DuckDB
   (`duckpgq_graph.py:1086-1100`) and PostgreSQL (`pg/graph_store.py:226-244`),
   and none is made without a profile candidate. On a protected read, however,
   `AdmittedGraphView.get_nodes` calls `project_node` per node
   (`privacy/read_graph.py:38-45`), which queries the privacy records one node
   at a time (`privacy/reads.py:220-222`): a profile quoting N sources costs N
   record reads per retrieval (and again for the cross-scope hint pass). Those
   rows are added to `private_read.records`, so `metadata.source_availability`
   (`pipeline.py:1505-1508`) can list records not otherwise read.
4. **Legacy profiles with no `source_node_ids` were never filtered.**
5. **The retirement test did not assert the profile actually quoted the retired
   text**, so it could have passed vacuously.
6. **No test for the cross-scope hint call.**

## Security screen (first review)

- Read-only: no writes, no lifecycle/generation/`DERIVATION_VERSION` change.
- The source read is owner-scoped (`get_nodes(..., user_id=...)`), so another
  user's node cannot keep a profile alive; `PrivacyDenied` is re-raised.
- Erasure is handled fail-closed: an erased source projects to `None`
  (`privacy/reads.py:228-236`), which reads as missing and drops the profile
  even in `EXPLICIT`; `erasure.py:672` deletes node rows outright.
- Exclusion reasons embed the source UUID; the same id is already in the
  profile's own `source_node_ids` metadata and the HTTP `RetrieveResponse` does
  not expose `excluded` (`api/models.py:436-449`), so not a new leak.
- A source record that fails projection with `PrivacyDenied` makes the whole
  retrieval raise; fail-closed and consistent with the wrapper contract.

## Pattern Consistency (first review)

- Mirrors `replaced_sources.py`: same signature shape, same
  `ExcludedCandidate(reason=f"...:{id}")` form (`replaced_sources.py:151`),
  same batch read (`replaced_sources.py:111-118`), same wrapper shape and
  `exc_info` convention (`pipeline.py:1652-1667`).
- Reuses `DEFAULT_EXCLUDED_LIFECYCLE` (`filtering.py:27-30`), so the profile
  criterion is exactly the lifecycle criterion `filter_epistemic` uses
  (`filtering.py:82`); `EXPLICIT` handling matches `filtering.py:66-67`.
- The entity-profile predicate is a copy of the check in `owner_profile.py:62-73`
  and `models/profile.py:132-134`; no canonical helper exists, so consistent.
- Module not exported from `prme/retrieval/__init__.py`, matching
  `replaced_sources`.

## Redundancy / NEW_SYMBOLS (first review)

- All new symbols were used; no dead code, no unused imports.

## Wiring (first review)

Complete for `MemoryEngine.retrieve()`: every stage that can add a candidate
runs before Stage 5.5h, and Stage 5.6, hint scoring and selection only remove
candidates. Cross-scope hints got their own call before scoring.
`MemoryEngine.retrieve` is the only caller of `RetrievalPipeline.retrieve`
(`engine.py:2848`). Pre-existing, not introduced here: `MemoryEngine.search()`
returns raw vector/lexical hits with no filtering at all
(`engine.py:2883-2920`).

## Test coverage (first review)

Acceptance criteria were covered; the owner-profile path, the hint call, the
protected store and the source-less profile case were not.

## Worker evidence checked independently (first review)

- `get_nodes(include_superseded=True)` really returns archived and superseded
  rows and omits missing ids (`duckpgq_graph.py:183, 1091-1100`;
  `pg/graph_store.py:220-244`).
- Every selected profile source's text is written into the profile
  (`organizer/profiles.py:99-111`; `engine.py:4833, 4860`), which is the
  premise of the filter.
- `EXPLICIT` keeps retired candidates (`filtering.py:66-67`).
- The organizer's stale pass rebuilds a profile whose source retired
  (`owner_profiles.py:168-191`, gated on `enable_owner_profile_context` at
  `maintenance.py:189`) and retires it instead when fewer than two sources
  remain (`engine.py:4822-4825`).

## Re-review (snapshot 569387d0a9c57c0e5ddc32c3dfe0823f7aca32338f55fb36ed3859b38ea1c3e8)

Re-read the changed scope: `profile_sources.py` (rewritten), `pipeline.py`
(Stage 5.5h, hints call, wrapper), `CHANGELOG.md:16`,
`docs/ENTITY-PROFILES.md:72-86`, and `tests/test_retired_profile_sources.py`.
Worker-supplied run: 11 passed / 10 skipped for the new module, 110 passed /
76 skipped across the profile-related modules, ruff clean, mypy unchanged.

### Original Should Fix 1 (owner-profile receipt): RESOLVED

The receipt pop was added after Stage 5.5h. Checked: the condition cannot
over-fire, because `_owner_profile()` already applies the same
`filter_candidate_types`/`filter_epistemic` filters
(`pipeline.py:1637-1650, 1577-1586`), and `execution_features()` returns a fresh
dict (`pipeline.py:331-345`). The new owner-receipt test exercises place, then
suppress, and asserts the feature and `OWNER_PROFILE` count are gone.

### Original Consider findings

- **2 (fail-open on a non-PrivacyDenied error): RESOLVED.** The wrapper returns
  `exclude_unverified_profiles(candidates)` (`pipeline.py:1698-1706`,
  `profile_sources.py:80-91`); `PrivacyDenied` is still re-raised. The rule is
  documented (`profile_sources.py:20-22`, `docs/ENTITY-PROFILES.md:78-80`,
  `CHANGELOG.md:16`).
- **3 (protected-read cost / `source_availability`): OPEN, unchanged.** One
  record read per quoted source on the admitted view
  (`privacy/read_graph.py:38-45` calling `privacy/reads.py:220-222`). No hot
  loop; note only.
- **4 (legacy profiles with no source list): RESOLVED as a deliberate,
  documented fail-closed rule.** `profile_sources.py:126-130` drops them in
  every mode with `profile_sources_unknown` before the `EXPLICIT` short-circuit.
  I verified the docs' reason: `source_node_ids` and `profile_format_version`
  were introduced in the same commit (88cc5e24), so a profile without a source
  list really is pre-version-2; protected migration also rewrites the journal
  projection with `source_node_ids` (`privacy/journals.py:90-93, 316-328`).
- **5 (test strength on the main path): RESOLVED.** `assert AURORA[0] in
  aurora.content` (`tests/test_retired_profile_sources.py:82`).
- **6 (cross-scope hint test): RESOLVED in the final delta** (positive control
  added, see below).

## Final delta (snapshot 8d1f2bccf856e2f049ccc253e7f024b7a32961eb4cf24ea278cb8df0daf34704)

Changes re-read: the receipt pop moved from just after Stage 5.5h to just after
`select_candidates` (`pipeline.py:1272-1283`); the Stage 5.5h comment now names
`enable_owner_profile_context` as the gate for the organizer rebuild
(`pipeline.py:1137-1148`); tests updated (12 tests, new failure-injection
integration test, positive control for hints, DEFAULT+EXPLICIT unknown-source
loop, no pinned-record premise); docs/CHANGELOG now say unverifiable profiles
are left out in every retrieval mode. Snapshot confirmed with
`housekeeping.py review-plan --base main`.

**Pop move: correct.** The only readers of `execution_features` and
`candidate_counts` after 5.5g are the receipt and metadata construction at
`pipeline.py:1390, 1451, 1462, 1532, 1549`, all after the pop; nothing between
5.5g and `select_candidates` reads either. `select_candidates` only removes
candidates and preserves score order, so `ranking_policy="score_id"` (which
`add_owner_profile()` may have set at `pipeline.py:1126-1127`) stays valid after
the profile is dropped, and the dropped candidate's `owner_profile` adjustment
is not in the receipt, so `relevance.py:523-530` still validates. The pop now
also fires for a profile removed by `min_score`, `limit`, `max_per_source` or
`max_per_evidence`, which matches the new comment (`pipeline.py:1279-1281`) and
is stricter than #398 in the safe direction (the feature is now recorded only
while the placed profile remains in the results).

**`limit=1` behavior matches `select_candidates`.** `select_candidates` fills
the cap from the ranked order with no pin/task exemption
(`selection.py:114-146`), and `add_owner_profile()` scores the profile at the
top result's inherited score times `1 - 5e-7` (`owner_profile.py:202-226`), so
the profile is rank 2 whenever it was inserted or score-raised; the first
`ask()` in the test asserts the feature, which proves exactly that placement
happened (placement away from the top is the only way the feature is set), so
`limit=1` necessarily drops the profile with `result_limit` and the pop fires.
The new test (`tests/test_retired_profile_sources.py:204-238`) is therefore
non-vacuous for both the suppression and the selection paths.

**New tests are non-vacuous.**
- `test_a_failed_source_read_leaves_profiles_out`
  (`tests/test_retired_profile_sources.py:241-260`) makes
  `get_nodes(include_superseded=True)` raise; in that retrieval no other stage
  uses that keyword (replaced-source suppression is off, no contested nodes,
  hints off), and the asserted `profile_sources_unverified` reason exists only
  on the fail-closed path. The profile's sources are active, and the first test
  in the module shows such a profile is otherwise returned.
- `test_cross_scope_hints_leave_out_a_profile_with_a_retired_source`
  (`:263-283`) now asserts the profile is a hint while active before archiving
  and asserting it gone, with `cross_scope_top_n=10` so the profile is inside
  the hint cap. Non-vacuous.
- The unknown-source test now loops DEFAULT and EXPLICIT with the reason
  assertion (`:161-178`), matching the docs' "in every retrieval mode".
- The owner test no longer claims a pinned record and asserts the actual
  placement evidence (`:222-232`), addressing Consider A.

**Docs/CHANGELOG:** "A profile with no parsable `source_node_ids` ... is left
out in every retrieval mode" (`docs/ENTITY-PROFILES.md:76-78`, `CHANGELOG.md:16`)
matches `profile_sources.py:128-135`. The docs' EXPLICIT sentence now sits after
that clause, and the organizer gate is named (`:83-86`).

**Residual notes (no Must/Should):**
- Consider 3 (protected-read per-source projection cost) remains; unchanged.
- Consider E remains: a `source_node_ids` list that is partly malformed still
  verifies only its parsable entries (`profile_sources.py:54-62`); engine-written
  metadata is canonical, so this needs a corrupt direct write.
- Micro-nit: the Stage 5.5g comment (`pipeline.py:1121-1123`) still says only
  that the receipt records the feature "when the profile was placed"; the pop
  below makes the full rule "placed and still in the results", which the pop's
  own comment states. No code impact.
- I did not rerun the suite; the full run and the offline gate are reported as
  in progress for the PR.

### Verdict (final delta)

The pop move is correct, the `limit=1` expectation matches `select_candidates`,
and every new test has a positive control or an effect that only the new code
can produce. No new must-fix or should-fix findings. Approve.

Status: COMPLETE
