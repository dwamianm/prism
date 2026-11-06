# Review: issue #448 a named user's profile takes the claims bound to her entity

## Review Scope

- Base branch: `main`, base commit `859b58f634d64894573a5841aa38c54d164a1afc` when the reviews ran. The
  branch was rebased onto `57ec400f5f83488439c30dd84a93fb0e1c822322` before pushing; that commit only
  changes `BENCHMARKS.md`, which none of the changed files or their dependencies read, so no reviewed
  scope was affected and no revalidation was needed.
- Reviewed snapshot 1: `dacc38661fcb205cf214208e964c73a81fbc7f39cf83044751dd0a0f130f55d2` (2 files).
- Reviewed snapshot 2: `c528fe098500e2526a06e3ffe4f135af7d1c68d0dc0162b7525be4182faf312e` (6 files).
- Tier M, resolved by `python3 ~/.claude/loop-tools/housekeeping.py review-plan --base main`:
  core (checklists 1, 3, 4, 5 and the basic security/data screen) and adversarial (8).
  Security (2), infrastructure (6) and data/storage (7) were not applicable: no schema, migration,
  network, deployment or credential surface changes.
- Roster run with `ticket-reviewer`, core on Sonnet and adversarial on Opus. Both wrote their
  reports to `memory_bank/reviews/issue-448-named-user-profile-claims-bound-to-entity-core-review.md`
  and `-adversarial-review.md`, and both re-reviewed the revised snapshot at the worker's request.
- Final tree snapshot after the reviewer-requested R2 items: `36535eefa478c836531ee21bd8ce946dcbf9c990164d9b281c8a5240a99e1aef`
  (6 files, 487 changed lines). The post-revision changes were: dedupe of `_mentioning`, a
  content-filtered entity read in `_bound_claims`, a claim-type and scope filter on its targets, a
  `.strip()` on the entity-type comparison, and docstring wording and reflow. No logic changed
  beyond those items; the checks were rerun on that tree.

## Files Changed

- `src/prme/storage/engine.py`: `consolidate_knowledge()` unions text mentions, speaker-stored
  records, entity-subject claims and turn-spoken claims; `source_paths` in profile metadata; owner
  and scope checks on the attribution read.
- `src/prme/organizer/owner_profiles.py`: `_bound_claims()` and a `_mentioning()` that also sees
  graph-bound and turn-spoken claims, so the rebuild triggers react to them.
- `tests/test_profile_graph_sources.py` (new): subject binding, turn binding, staleness read,
  person-versus-organisation, cross-user isolation, cross-scope isolation, no-entity gate.
- `docs/ENTITY-PROFILES.md`, `docs/PACKING.md`, `CHANGELOG.md`.

## Approach Summary

A named person's profile previously took only records whose text names them (plus, on request,
records stored with them as speaker). Extracted claims keep no speaker key and often do not name
the person, so first-person claims and claims from their turns never qualified. The graph already
binds them: a `HAS_FACT` edge from the person's entity (written by `enable_speaker_references`) and
the source event's `prme_speaker_v1`. The build now unions those claims, preferring a person entity
when a person and an organisation share a name, and records per-path counts. Selection order and
token budget are unchanged, no claim text or extraction request changes, and `DERIVATION_VERSION`
does not move.

## Must Fix

None outstanding. Both rounds found no blocking correctness or security defect.

## Should Fix

None outstanding. The revision-2 items were applied (R2-1 to R2-5 below).

## Consider

- F3 / R2-1 / scenario 2 (Follow-up): the build still retains every active claim node in the scope
  and reads one event per unbound claim. `_bound_claims` now uses a content-filtered entity read
  instead of a full scan, which removes the per-profile entity sweep the revision introduced.
  The remaining bound is filed as a follow-up.
- U3: `profile_request_hash` covers metadata, so adding `source_paths` re-prepares and re-embeds
  each rebuilt profile once after the upgrade. Accepted.
- U2: an auto-detected name that exactly matches an entity node now also uses the graph paths.
  Intended for a named person; noted in the pull request.
- R2-6 (accepted, follow-up): the claim-attribution rule is written twice, in `consolidate_knowledge`
  and in `_mentioning`. The two copies differ only where `speaker_reference.speaker` is present but not
  a non-empty string, which the writer's `normalize_speaker` prevents, so they cannot drift on engine
  data. A shared helper is filed as a follow-up rather than widening this change.
- `_bound_claims` reads at most `MENTION_SCAN` entity nodes whose content contains the name; a burst of
  newer same-substring entities could hide the exact entity from the staleness read. It can only miss
  a rebuild, never include a wrong source.

## Security Audit Results

| Area | Result | Details |
| --- | --- | --- |
| Cross-user reads | PASS | Claim candidates come only from the owner- and scope-filtered node scan; the `HAS_FACT` read filters `edge.user_id`; the event read requires the same user and scope; `_bound_claims` reads with `user_id` and filters `node.scope`. |
| Cross-scope reads | PASS | Every source set comes from one scoped scan; the bound-claim read now also filters the target's scope. |
| Protected stores | PASS | The organizer refuses a policy store before any raw read; the engine build uses the admitted view, skips the event read, and uses admitted claim metadata only. |
| Secrets/PII in logs | N/A | No new logging. |
| Injection | N/A | No new query text; existing parameterized reads. |

## Pattern Consistency Assessment

- The owner-scoped edge filter now matches `MemoryEngine.get_edges` (7960dc60).
- The event owner/scope check matches the sibling event read at `engine.py:3432-3434`.
- Entity-type comparison matches ingestion (`strip().casefold()`).
- `PROFILE_CLAIM_TYPES` lives in the engine and is imported lazily where the organizer needs it.

## Redundancy Check

`naming`/`said_by` repeat the two clauses of `mentioning`, kept apart to attribute per-path counts
(accepted). `_bound_claims` mirrors the engine's entity selection rather than importing the whole
build, which would couple the organizer to the engine's private scan loop.

## Wiring Findings

- No configuration option is added. Automatic callers are already gated by
  `enable_owner_profile_context` (default False); explicit `consolidate_knowledge()` callers get the
  new membership by design.
- No new module, route or CLI entry is added.

## Data Safety and Read/Write Proof

- No schema or key change. `source_paths` is an additive metadata key on generated profile SUMMARY
  nodes; it is written at publication and read only by tests. A protected publication's journal
  projection keeps the source counts and drops the new key (documented in code).
- No event or node is rewritten; only the new profile node and its indexes are written, and the
  profile it replaces is archived as before.
- A `StaleProfileError` still resolves through the existing generation fence.

## Break Scenarios (adversarial)

| # | Scenario | Label | Verdict | Resolution |
| --- | --- | --- | --- | --- |
| 1 | The rebuild trigger cannot see the new membership, so the live profile is silently stale | newly introduced | Fix now (narrowed) | `_bound_claims` and the `speaker_reference` / source-event match in `_mentioning` were added; the event fallback now reads the last evidence ref with the owner and scope checked. |
| 2 | One event round trip per claim event, per person, on the event loop, and every active claim retained | newly introduced | Fix now for the early skip and owner/scope check; Follow-up for the bound and the batched read | Early `continue` when `speaker_reference` names someone else; event checked for owner and scope; the entity sweep in `_bound_claims` replaced by a content-filtered read. Filed as a follow-up. |
| 3 | The entity is chosen by name and lowest UUID, ignoring type | pre-existing shape, newly relied on | Fix now | Person entities are preferred and all person entities are unioned, in the engine and in the organizer; test added. |
| 4 | Silent omission everywhere and the acceptance is not asserted | newly introduced / pre-existing | Fix now for the acceptance check; Follow-up for logging | The member-v1 acceptance was run manually and is reported in the pull request; the CI tests cannot replay it because the extraction cache is untracked. Logging filed as a follow-up. |
| A | The acceptance is not met at the default 500-token budget | pre-existing design, newly exposed | Report, do not widen | The issue requires keeping both the token budget and the oldest-first selection order, which cannot fit all five items of a 14-source profile. At 500 tokens the profile names Theo, Nico and the trip; all five appear at a complete-profile budget (2000). Reported plainly in the pull request and the issue stays open. |

Adversarial pre-mortem headline: "The profile still answered 'do you remember my diagnosis?' without
it: the claim was finally in the source set, but the 500-token budget kept the oldest records and the
staleness check then declared the profile fresh forever."

## Follow-ups Raised

1. #459: bound a named profile build's claim scan and batch its event reads (F3, R2-1, scenario 2).
2. #460: make a profile build say when the graph path found nothing, and surface `source_paths` in the
   rebuild results (scenario 4).
3. #461: share the claim-attribution rule between the profile build and the staleness read, and move
   `PROFILE_CLAIM_TYPES` to a module both import directly (R2-6).

## Resolution Status

| Item | Status |
| --- | --- |
| F1 owner-scoped edge read | Fixed |
| F2 event owner/scope check | Fixed |
| F3 claim retention and per-claim event read | Follow-up |
| F4 duplicate entity nodes | Fixed |
| F5 per-path counts are not a partition | Documented |
| F6 protected profiles drop `source_paths` | Documented |
| F7 docs described the old source set | Fixed |
| F8 `naming`/`said_by` duplication | Accepted |
| F9 test coverage | Fixed (staleness, person/organisation, scope isolation, turn binding) |
| R2-1 per-profile entity sweep | Fixed (content-filtered read); remaining bound is the follow-up |
| R2-2 duplicate node in `_mentioning` | Fixed (deduped) |
| R2-3 bound claims not claim-typed | Fixed |
| R2-4 entity-type whitespace | Fixed |
| R2-5 `_bound_claims` privacy note and docstrings | Fixed |
| Scenario 1 staleness | Fixed for subject and turn-bound claims |
| Scenario 2 cost | Follow-up |
| Scenario 3 entity selection | Fixed |
| Scenario 4 observability | Follow-up |
| Residual A default-budget acceptance | Reported, issue left open |
| U1 LoCoMo profile surface | Reported in the pull request |
