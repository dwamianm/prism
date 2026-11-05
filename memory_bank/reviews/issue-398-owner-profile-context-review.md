# Review: issue #398, the owner's profile for a first-person question

## Review Scope

- Base: `main` at `658e08830983262e8a8ce14b9db6e201a2d92e04`.
- The first review ran on snapshot `3d32e8a08f0afc6d9299037d0a3014987590c09e1601fcf6dc4e4abfebf7fd5f` (housekeeping
  `review-plan`, default flipped on). The fixes below were
  re-reviewed on `8ca5b827c37b391886cd3d60e431d51c2b8eab93a6bd20208f6e56c5f65fcbfc` (default off).
- Tier L (predicates SECURITY, DATA, NEW_SYMBOLS). Roster run under the owner's light review budget: two reviewers.
  - Core reviewer (Opus): checklists 1 (correctness), 3 (quality), 4 (patterns and full-configuration comparison) and
    5 (redundancy), with 2 (security) and 7 (data safety) merged into it. Runtime wiring screened by the same reviewer
    (checklist 6 not applicable: no infrastructure).
  - Adversarial reviewer (Opus): checklist 8, up to 8 scenarios.
- Merged coverage: 2 and 7 were covered inside the core report, not skipped.

## Files Changed

Source: `src/prme/retrieval/owner_profile.py` (new), `src/prme/organizer/owner_profiles.py` (new),
`src/prme/retrieval/pipeline.py`, `src/prme/retrieval/query_analysis.py`, `src/prme/retrieval/models.py`,
`src/prme/retrieval/packing.py`, `src/prme/models/relevance.py`, `src/prme/storage/engine.py`,
`src/prme/organizer/maintenance.py`, `src/prme/organizer/models.py`, `src/prme/organizer/profiles.py`,
`src/prme/ingestion/owner_name.py`, `src/prme/privacy/diagnostics.py`, `src/prme/config.py`, `src/prme/config_audit.py`,
`src/prme/client.py`, `src/prme/api/models.py`, `src/prme/api/routes.py`, `src/prme/mcp/server.py`.
Harness: `benchmarks/diagnostics/chat_probe.py`. Tests: `tests/test_owner_profile_context.py` (new),
`tests/test_config_audit.py`, `tests/test_privacy_journal_records.py`. Docs: `docs/PACKING.md`,
`documentation/configuration.md`, `CHANGELOG.md`.

## Approach Summary

`enable_owner_profile_context` (off): a first-person question from a named owner (the `retrieve(speaker=...)` argument,
or the single `owner_name` marker of the scope) gets the owner's newest active knowledge profile right after the top
result, through a replayable `owner_profile` score adjustment and a reserved `OWNER_PROFILE` packing path, modeled on
the current-state observations of #387. The receipt records `execution.features.owner_profile_context` only when the
profile was placed. `end_session()` (1 s budget) and the opportunistic organizer pass rebuild stale profiles, with the
person's own speaker turns as sources (`consolidate_knowledge(include_speaker_turns=True)`). The chat probe calls
`end_session()` after the last turn.

## Must Fix

- F1 (core): the offline evidence gate had not been run. Resolved: off against on, every row unchanged (0 wins,
  0 losses on LoCoMo and LongMemEval-S).

## Should Fix

- F2: speaker turns changed every `consolidate_knowledge()` caller, including the LoCoMo build. Resolved: keyword-only
  `include_speaker_turns=False`; only the refresh path sets it.
- F3: rebuilding a full profile repeats its text but writes a record and an embedding. Resolved for the churn: a profile
  that already left sources out is not stale. The content problem behind it is a follow-up (below).
- F4: the `end_session()` refresh had no error guard and no budget. Resolved: try/except (counted in `errors`) and a
  1 s deadline before each build.
- F5: the unnamed lookup scanned every entity in every scope. Resolved: only scopes holding one of the owner's 100
  newest profiles are read.
- F6: the CHANGELOG said no stored memory is rewritten. Resolved: the docs say the profile record is replaced (the old
  one archived) and source records are never changed.

## Consider

- F7 (case mismatch leaves two active profiles): fixed, the rebuild uses the stored profile name.
- F8 (DRY): the request filter closure is shared (`_request_filter`), `build_profile` uses `PROFILE_HEADER`,
  `profiles_named` is reused. `MIN_PROFILE_SOURCES` still repeats the `< 2` in `consolidate_knowledge`; left as is.
- F9: "World War I" and "I/O" read as first person (only an extra profile; accepted); the opportunistic result is not
  logged (accepted, same as the other maintenance counters); the speaker is not in the receipt parameters (the feature
  and the adjustment are; accepted).

## Delta re-review (snapshot 8ca5b827)

The core reviewer re-checked every fix: F1 to F8 resolved, the `carried_owner_entity` refactor keeps its behavior, the
new import has no cycle, and nothing changes with the option off.

- D1 (Should Fix): a full profile was never rebuilt, even when a correction retired one of its sources, so the replaced
  fact stayed in the profile placed after the top result. Resolved: a profile is stale when any of its sources is no
  longer tentative or stable; test added.
- D2 (Consider): `end_session()` builds the newest marked owner's profile, while retrieval without a speaker needs one
  marked name. Accepted: building a real person's profile is harmless, and placement is what the guard protects.
- D3 (Consider): a failed rebuild inside `_rebuild` is logged but not counted in the job's `errors`. Accepted.
- D4 (Consider): an explicit `consolidate_knowledge()` replaces a refresh-built profile with a mention-only one until
  the owner's next record. Accepted and documented by the keyword.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Tenant isolation | PASS | every query passes `user_id`; LIKE wildcards only widen a prefilter that exact name equality then narrows |
| Protected reads | PASS | the copied pipeline reads through `AdmittedGraphView`; `PrivacyDenied` is re-raised |
| Input validation | PASS | `speaker` is validated by `normalize_speaker` on HTTP, MCP and in the pipeline |
| Logs and results | PASS | no speaker or content logged; organizer results name the person only to the same owner |
| Privacy projection | FIXED | `end_session()` added an unknown job id that the protected projection rejected (adversarial 1) |

## Pattern Consistency Assessment

`add_owner_profile` follows `add_current_state_observations` (top choice, provenance copy, path tagging, sort rule);
the receipt validation follows the `current_observation` precedent; the option follows
`enable_superseded_source_suppression` (constructor argument, receipt record only when it acted).

## Redundancy Check

No duplicate helper left beyond `MIN_PROFILE_SOURCES` (Consider). `marked_owner_entities` is the shared scan behind
`carried_owner_entity`, not a copy.

## Wiring Findings

Both `RetrievalPipeline` constructions get the option; `speaker` reaches the pipeline from the engine, the client, the
HTTP route and the MCP tool; the maintenance pass and `end_session()` read the engine configuration. No CLI flag for
`speaker` (not required).

## Data Safety and Read/Write Proof

No new durable field or schema. Writes: profile SUMMARY records through the existing `consolidate_knowledge()`
publication (old profile archived, indexes evicted). Receipts: new adjustment kind, recorded only when placed; older
readers cannot read such a receipt, as with `current_observation`. `MaintenanceResult.profiles_rebuilt` is an
additive in-memory field.

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "0.17.0a2 upgrade: end_session() starts raising PrivacyDenied for every
privacy-configured owner, and first-person questions everywhere get the user's first-day snapshot reserved at rank 2."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | `end_session()` raises `PrivacyDenied` for protected owners | new | High (with policy) | Fix now | skipped owners get no job; the job id is allowed in the projection; test added |
| 2 | The owner's profile is a snapshot of their first records | new as default; build order pre-existing | High | Follow-up, and the default stays off | needs a profile content decision (newest or current sources); the churn part fixed (F3) |
| 3 | `end_session()` and the background pass overrun their budgets | new | Medium | Fix now for `end_session()` (deadline); Follow-up for the full-scope scan inside a rebuild | the scan is inside `consolidate_knowledge()` |
| 4 | Shared `user_id`: one member gets another's profile | new | Low to medium | Fix now | an owner is named only when the scope has one marked name |
| 5 | Background rebuilds rewrite existing profiles at 500 tokens | new | Low to medium | Follow-up (with 2) | the budget is not stored on the profile; option off |
| 6 | Per-retrieve lookup cost | new | Medium | Fix now (only profile scopes); Follow-up for protected full scans (with 3) | |
| 7 | The stable audit and offline gate cannot see the option | structural | High | Follow-up | the full benchmark builds no profiles |
| 8 | Persistent rebuild failure repeats work each pass | new | Low | Fix now for the `end_session()` guard; Accept the lack of backoff | the option is off and failures are logged |

## Follow-ups Raised

- #422: a knowledge profile keeps a person's oldest records, so the owner's profile goes stale (adversarial 2 and 5).
- #423: a profile rebuild scans the whole scope and can overrun the organizer's time budget (adversarial 3 and 6).
- #424: the full benchmark and the offline gate never build profiles, so they cannot measure the option (adversarial 7).

## Resolution Status

| Item | Status |
|---|---|
| F1 to F7 | resolved |
| F8, F9 | partly resolved, rest accepted |
| Adversarial 1, 4 | fixed |
| Adversarial 3, 6, 8 | fixed in part, rest follow-up or accepted |
| Adversarial 2, 5, 7 | follow-up (#422, #424); default kept off |
| Delta D1 | fixed |
| Delta D2 to D4 | accepted |

Adjudication tally: Fix now 5 (1, 3 in part, 4, 6 in part, 8 in part), Follow-up 5 (2, 3 rest, 5, 6 rest, 7) in three
issues, Accept 1 (8, no backoff), Dismissed 0.
