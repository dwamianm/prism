# Review: issue #462, retrieval returns a profile that quotes a retired memory

## Review Scope

- Base: `main` at `c8b817eed4cff81679ed43e3c376264d8438c2b7`
- Final reviewed snapshot: `8d1f2bccf856e2f049ccc253e7f024b7a32961eb4cf24ea278cb8df0daf34704`
- Prior reviewed snapshots: `a54553016d1eb504918a5ee4f49c76d055f89b2da050f6a58f4274e305cc1438` (first), `569387d0a9c57c0e5ddc32c3dfe0823f7aca32338f55fb36ed3859b38ea1c3e8` (after the first fix round)
- Tier: M (`review-plan`; `reviewTier` unset, default auto). Roster: core (checklists 1, 3, 4, 5; Sonnet) and adversarial (checklist 8; Opus). Security, infrastructure and data reviewers not applicable: no auth, tenant-isolation, schema, migration or persistent-field change; the new read is owner scoped through the existing `get_nodes(user_id=...)` contract.
- Reports: `memory_bank/reviews/issue-462-retire-profile-when-quoted-memory-retired-core.md`, `memory_bank/reviews/issue-462-retire-profile-when-quoted-memory-retired-adversarial.md`
- Evidence: `uv run pytest -q tests/test_retired_profile_sources.py -p no:randomly` 12 passed, 11 skipped; profile-related modules 110 passed, 76 skipped; `uv run ruff check` on the three Python files passed; mypy src files reports the same six pre-existing `pipeline.py` errors and none in new lines; CI mypy runs only on `tests/typing/public_api.py`.
- Offline evidence gate: a scan of all 501 saved gate-pack `memory.duckdb` files (10 LoCoMo and the LongMemEval-S baseline) found zero `entity_profile` nodes, so this filter makes no read and changes no result or receipt there. The gate replay confirms it. Base side: LoCoMo all evidence packed 1290/1540 (0.8398), all evidence returned 1525/1540 (0.9928), projected accuracy 0.7765; LongMemEval-S 445/500 (0.9468), 470/500 (1.0000), projected 0.9139. Change side: identical on every reported measure (LoCoMo 1290/1540, 1525/1540, 0.7765; LongMemEval-S 445/500, 470/500, 0.9139).

## Files Changed

- `src/prme/retrieval/profile_sources.py` (new): `suppress_retired_profiles`, `exclude_unverified_profiles`, source-list parsing.
- `src/prme/retrieval/pipeline.py`: import; Stage 5.5h filter after the owner-profile insertion; cross-scope hint filter; `_suppress_retired_profiles` wrapper; receipt bookkeeping for a suppressed owner profile.
- `tests/test_retired_profile_sources.py` (new): 11 tests.
- `docs/ENTITY-PROFILES.md`, `CHANGELOG.md`.

## Approach Summary

A profile is a SUMMARY node with `metadata.entity_profile` and `source_node_ids`; `build_profile` writes the selected sources' content into it. Retrieval filtered candidates only by their own lifecycle state, so an archived, superseded or deleted source left the profile quoting retired text. The change is the issue's option 2: a read-path filter. It reads every quoted record once with `get_nodes(include_superseded=True, user_id=...)` and drops a profile whose quoted record is missing, or, outside `EXPLICIT`, retired. Profiles with no parsable source list are unverifiable and dropped. A failed source read fails closed. Nothing is written, so profile publication generation and `StaleProfileError` are unchanged. It runs after every stage that can add a candidate, including the owner-profile insertion at Stage 5.5g, and on cross-scope hints.

## Must Fix

None.

## Should Fix

1. Owner-profile receipt recorded the feature after the profile was suppressed. Fixed: after Stage 5.5h the feature and `OWNER_PROFILE` count are popped when the placed profile is no longer in `scored`. Covered by `test_a_suppressed_owner_profile_is_not_recorded_in_the_receipt`.

## Consider

1. Fail-open on a non-`PrivacyDenied` read failure returned profiles unchecked. Fixed by `exclude_unverified_profiles`; reason `profile_sources_unverified`.
2. Legacy profiles with no `source_node_ids` bypassed the check. Fixed: dropped in every mode with `profile_sources_unknown`.
3. Protected reads project each quoted node one at a time (`AdmittedGraphView.get_nodes`), so a profile quoting N sources costs N privacy record reads on that path. Rare, unchanged by this diff's semantics, accepted.
4. Cross-scope hint call had no test. Added `test_cross_scope_hints_leave_out_a_profile_with_a_retired_source`.
5. The retirement test now asserts the retired text was in the profile content before retirement, so the absence assertion is non-vacuous.

## Security Audit Results

- Read-only: no writes, lifecycle transitions, index eviction, `DERIVATION_VERSION` change or schema change.
- The source read is owner scoped; another user's node cannot keep a profile alive.
- `PrivacyDenied` is re-raised at both call sites; a policy failure is never swallowed.
- Erasure: a deleted or erased source is absent from `get_nodes`, so the profile is dropped in every mode. Fail-closed.
- Exclusion reasons embed the quoted source UUID, which the owner already reads in the profile's own metadata and which `RetrievalResponse.excluded` exposes only in the Python API, not the HTTP response.

## Pattern Consistency Assessment

Mirrors `src/prme/retrieval/replaced_sources.py` (same signature shape, batch owner-scoped read, `ExcludedCandidate` reason form, pipeline wrapper with `PrivacyDenied` re-raised). Reuses `DEFAULT_EXCLUDED_LIFECYCLE` so the criterion matches `filter_epistemic`. `EXPLICIT` handling matches that module. The entity-profile predicate is a third copy of an existing check with no canonical helper; consistent rather than a DRY regression.

## Redundancy Check

All new symbols are used: `suppress_retired_profiles` twice, `exclude_unverified_profiles` in the wrapper, `_is_profile` and `_quoted_sources` internally. No new dependency, no dead code, no duplicated constant.

## Wiring Findings

`MemoryEngine.retrieve()` is the only caller of `RetrievalPipeline.retrieve`. Every stage that can add a candidate precedes Stage 5.5h; nothing after it adds candidates. Cross-scope hints get their own call. `MemoryEngine.search()` returns raw index hits without filtering and is deprecated; pre-existing and outside the acceptance criteria. New test file is picked up by the existing `pytest tests/` CI step.

## Data Safety and Read/Write Proof

Not applicable for new durable fields: none added. Existing data: profiles written by current releases carry `source_node_ids`; profiles written before profile format version 2 do not and are left out rather than trusted. Erasure and deletion are handled by absence. No migration or backfill.

## Break Scenarios (adversarial) and adjudication

| # | Scenario | Label | Verdict | Reasoning |
|---|---|---|---|---|
| 1 | A profile disappears from retrieval permanently when a quoted record retires, because the organizer's stale-profile rebuild runs only with `enable_owner_profile_context`; the pre-mortem headline | Newly introduced behavior, intended contract | Follow-up | The issue explicitly chose "leave it out until it is rebuilt", and the hosted service rebuilds its profiles. Making automatic rebuilds run for every store with profiles is a product/default decision with write and embedding cost. Docs and CHANGELOG now name the gate so the contract is accurate. Issue opened for the rebuild policy. |
| 2 | A non-privacy read failure made the stage fail open and return retired text | Newly introduced control flow | Fix now | It lets an ordinary storage error bypass the rule the issue exists to enforce. Contained fix: `exclude_unverified_profiles`. Implemented and tested. |
| 3 | Profiles written before profile format version 2 have no source list and bypass the check | Pre-existing leak, incomplete fix | Fix now | Dropping unverifiable profiles is contained and closes the same leak. Implemented and tested. |
| 4 | Consolidation and hierarchical summaries quote source text and are not checked | Pre-existing, outside this issue | Follow-up | Same defect in a different representation, with a different quoted-subset field. Separate issue. |
| 5 | Owner-profile receipt records the feature for a suppressed profile | Newly introduced | Fix now | Audit-surface inconsistency; contained bookkeeping fix. Implemented and tested. |
| 6 | `DEPRECATED` lifecycle is not treated as retired | Pre-existing divergence | Accept | The issue names archived, superseded and deleted. The source node itself is still returned under current retrieval rules, so the profile is no more exposed than its source. Recorded here for a future lifecycle-consistency change. |
| 7 | Receipt pop ran before result selection, so `limit`/floor removal left the feature in the receipt (raised in re-review) | Pre-existing class, narrowed by the first fix | Fix now | The pop moved below `select_candidates`; the `limit=1` test now covers it. Packing omission remains the separate `in_context` signal, which re-review accepted. |

Pre-mortem headline (Agent 8, verbatim): "Incident: knowledge profiles silently vanish from retrieval after one archived source, because the default configuration never runs the documented rebuild."

## Follow-ups Raised

1. #466: a retired source hides an entity profile because no automatic rebuild runs when `enable_owner_profile_context` is off (scenario 1).
2. #467: a summary still quotes a memory after that memory is retired (scenario 4).

## Resolution Status

| Item | Status |
|---|---|
| Core Should Fix 1 (owner-profile receipt) | Resolved |
| Adversarial 2 (fail open) | Resolved |
| Adversarial 3 (unverifiable legacy profiles) | Resolved |
| Adversarial 1 (rebuild policy) | Follow-up issue; docs corrected in this PR |
| Adversarial 4 (summary representations) | Follow-up issue |
| Consider 3 (protected-read cost) | Accepted |
| Consider 4, 5 (hint coverage, non-vacuous test) | Resolved |
| Re-review Considers A-D (pinned premise, hint positive control, wrapper failure test, EXPLICIT unknown) | Resolved: owner test no longer claims a ranking it does not force and is proven by the feature assertion; hint test has a positive control; monkeypatched `get_nodes(include_superseded=True)` failure test added; unknown-source test runs in DEFAULT and EXPLICIT; docs state every mode |
| Re-review of affected scope | Core re-review approved snapshot `569387d0` with no Must/Should; final delta review requested on `8d1f2bcc` |

## Re-review record

- Core re-review of `569387d0`: original Should Fix resolved, no new Must/Should, four non-blocking Considers (A-D). Adversarial re-review of `569387d0`: scenarios 2 and 3 fixed and verified, scenario 1 reduced to an accurate documented product decision, scenario 4 unchanged as a follow-up; one new scenario (receipt pop before selection) with a contained move recommended.
- Final delta on `8d1f2bcc`: the receipt pop moved below `select_candidates`, the `limit=1` receipt test added, the hint test gained a positive control, a monkeypatched fail-closed integration test was added, the owner test premise was corrected, and the unknown-source test and docs now cover `EXPLICIT`. Core delta confirmation is recorded in the core report.
