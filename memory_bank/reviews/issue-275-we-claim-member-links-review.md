# Review: issue-275-we-claim-member-links (prism-monorepo #275)

## Review Scope

- Base: origin/main `13b25058` (0.16.0a4). Reviewed head: `e6b4db7e` (3 commits). Review snapshot `5d937584fdd9641a86db260fa29e3871e6e83b2ebfb3774b41234e7d573b997c` (housekeeping `review-plan --base origin/main --risk DATA --risk PERSISTED_FIELD`).
- Tier L. Predicates: DATA (added by hand: graph edge writes, event store reads, claim merge supersedes), PERSISTED_FIELD (added by hand: edge `metadata.named_in`), NEW_SYMBOLS (detected). The helper alone proposed tier M with no risk flags.
- Roster limit: the user set a budget of two reviewers per issue. Core (Opus) carried checklists 1, 3, 4, 5 and the merged security (2) and data safety (7) checklists; adversarial (Opus) carried 8 with a cap of 8 scenarios. Infrastructure (6) was not applicable. No checklist was skipped.
- Reports: core and adversarial, both `Status: COMPLETE` on the snapshot above.
- The fixes (`326f3263`, `cdf8b354`) were checked against each finding by reading the diff, and by the implementer's targeted tests on both backends; the full suite ran again on the final head.

## Files Changed

`src/prme/ingestion/group_links.py`, `claim_merge.py`, `pipeline.py`, `planning.py`, `src/prme/storage/event_store.py`, `src/prme/storage/pg/event_store.py`, `src/prme/storage/engine.py`, `src/prme/models/entity_identity.py`, `src/prme/config.py`, `benchmarks/conversations/defects-v1.json`, `benchmarks/diagnostics/chat_probe.py`, `benchmarks/fixtures/fast-cycle/baseline-main.json`, `tests/test_group_members.py` (new), `tests/conftest.py`, `tests/test_config_audit.py`, `tests/test_default_structure_options.py`, `tests/test_graph_fusion.py`, `BENCHMARKS.md`, `CHANGELOG.md`, `CLAUDE.md`, `docs/ENTITY-IDENTITY.md`, `docs/INTEGRATION.md`, `docs/RFC-0003-Epistemic-State-Model.md`, `documentation/configuration.md`.

## Approach Summary

`enable_owner_group_links` (from PR #315, unreleased) is extended and turned on in the alpha channel. A "we" claim of the owner's user turn keeps its literal "We" and its message-local subject, and gets `MENTIONS` links marked `group_member`: always to the owner's named entity, and to each member when the owner's turns of the session, up to the claim's turn, name exactly one group with the owner and each member matches exactly one stored person. Member links record `named_in`. The same "we" claim repeated in two user turns of one session merges through the owner's own group links. A new `until` argument on `get_by_user` and `get_events` keeps per-turn, batched and rebuilt ingestion in agreement.

## Must Fix

None.

## Should Fix

| # | Finding | Source | Resolution |
|---|---|---|---|
| S1 | The phrase reader takes quadratic time on a long list | core | Fixed: lists append and keep counts, at most 16 members, 4,096 tokens per turn, a bounded LRU cache of turn readings; long-input test |
| S2 | Matching the last words of a name links a person outside the group ("Jordan Lee" to a stored "Lee") | core | Fixed: full name first; a leading weekday, month or title is the only word dropped; tests |
| S3 | The merge lookup reads the owner's whole history for every fact of a "we" turn | core, adversarial 3 | Fixed in part: early return before any read for a claim not about "we" or without a session, and only claims linked by the session's turns are loaded. The edge read into the owner is still unbounded: follow-up F2 |
| S4 | The fast-cycle baseline records a commit no branch contains | core | Fixed: retaken at `326f3263` (clean tree); all 168 measures equal |

## Consider

| # | Finding | Verdict |
|---|---|---|
| C1 | One failed edge stops later claims' owner links; a comment promises a repair that does not exist | Fixed: owner links first, per-edge handling, comment corrected |
| C2 | `until` relies on unique microsecond timestamps | Accept: each event gets its own `now()`, batched turns are appended one at a time, ties break by ID |
| C3 | `LIKE` person lookup capped at 1,000 with no tie-break | Follow-up F2 (#334) |
| C4 | The event read is not scoped to the user | Fixed: an event of another user gets no link |
| C5 | A text link to the owner stops the group link, so that claim cannot merge | Fixed (one line), test |
| C6 | The same "we" claim merges while two different groups are named in the session | Accept: documented as "same session" |
| C7 | A swallowed plan-time lookup failure skips the merge for that saved plan | Accept: the existing `carried_owner_name` pattern |
| C8 | `until` undocumented; a naive datetime | Fixed: docstring, `docs/INTEGRATION.md`, naive read as UTC on both stores; not added to `MemoryClient` |

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or personal data in logs | PASS | Only event IDs and error types are logged |
| Tenant isolation, reads | PASS | Session turns, people and owner read by user and scope; the event read now checks the user (C4) |
| Tenant isolation, edge writes | PASS | Edge `user_id` is the plan's user; provenance is the event |
| Cross-user entity match | PASS | Person lookup filters user and scope |
| Regex backtracking | PASS | Token pattern has no nested quantifiers |
| CPU on crafted chat text | PASS after S1 | Was quadratic |
| Unsafe deserialization | N/A | None added |

## Pattern Consistency Assessment

The option matches its siblings (`enable_owner_name_carry`, `enable_mention_links`) on every configuration point: field, environment variable, `config_audit`, `documentation/configuration.md`, CHANGELOG, the CLAUDE.md alpha list, engine wiring in both pipeline constructors, the conftest pin and the default test. `group_links.py` follows `mention_links.py` (post-commit, outside the plan, fixed edge IDs, best effort).

## Redundancy Check

Consolidated after review: one `is_group_reference` predicate now serves `group_fields`, `_speaks_for_a_group` and `_claim_statement`; the pronoun list reuses `entity_identity.PERSONAL_REFERENCES`; the 1,000 against 500 lookup limit is explained in a comment. No dead code.

## Wiring Findings

Not a separate checklist at this tier (infrastructure not applicable). The core reviewer confirmed the option reaches both pipeline constructors and the default test pins it.

## Data Safety and Read/Write Proof

- Append-only: no event is written or changed; merges retire copies through the plan's replacement path (#209).
- Option off: plans and records keep their bytes (`group_owner` stays None; the post-commit hook is skipped).
- Option on: plans are recorded before publication and replayed; links have fixed uuid5 IDs, so a second publication makes none twice.
- `named_in`: written at `group_links.py` member link creation; no runtime reader (provenance, read by tests). Accepted as write-only provenance.
- Partial failure: links lost on a crash between commit and linking are not repaired (follow-up F4).

## Break Scenarios (adversarial)

Pre-mortem headline: "Alpha memory credits a partner with the owner's work decisions."

| # | Scenario | Introduced | Likelihood | Verdict | Reason |
|---|---|---|---|---|---|
| 1 | A partner named once in a long session is linked to later unrelated "we" claims | new, within the spec | Medium | Follow-up F1 (#333) | The issue defines the evidence as the session; a window is a product decision. Documented in `docs/ENTITY-IDENTITY.md` |
| 2 | Another speaker's identical "we" claim retires the owner's copy | new | Low | Fix now: fixed | The merge reads only owner links into the turn's own owner; test |
| 3 | Unbounded reads on the owner hub for every "we" turn | new, older pattern | Medium | Fixed in part, Follow-up F2 (#334) | Early return and session-bounded loads now; the edge read stays |
| 4 | Member links pull the owner's neighborhood into graph search for a member; the 150-candidate cut is by confidence, not distance | older, made worse | Medium | Follow-up F3 (#335) | Fix changes default retrieval and needs the gates |
| 5 | Links lost on a crash between commit and linking; nothing repairs them | older pattern | Low | Comment fixed, Follow-up F4 (#336) | A repair sweep is new work |

## Follow-ups Raised

- F1: #333 (a partner named once in a long session is linked to every later "we" claim of that session)
- F2: #334 (group link lookups scan every entity and cap name matches at 1,000)
- F3: #335 (graph search keeps its 150 candidates by confidence and recency, not distance)
- F4: #336 (links made after a commit are not repaired after a crash)

## Resolution Status

| Item | Status |
|---|---|
| S1 to S4 | Resolved (S3 in part, rest in F2) |
| C1, C4, C5, C8 | Resolved |
| C2, C6, C7 | Accepted |
| C3 | Follow-up F2 (#334) |
| Adversarial 2 | Resolved |
| Adversarial 1, 3, 4, 5 | Follow-ups F1 to F4 (3 and 5 partly fixed) |
