# Consolidated review — issue #497, deterministic profile tie-break

## Review Scope

- Base branch and commit: `main` @ `52d99de1be90477abcfa7256088fd8359fea2eb5`
- Reviewed snapshot: `9767aaea7609dc5ad909fa3980277d438fa8f1217395da1289c37f678dbda1ff`
- Tier: M (reviewed snapshot: 4 files, 145 changed lines; after the reviewer-requested doc/test edits: 6 files, 176 changed lines; `NEW_SYMBOLS`; `DATA`, `INFRA`, `SECURITY` and `PERSISTED_FIELD` false)
- Roster from `housekeeping.py review-plan --base main`: core (checklists 1, 3, 4, 5) on Sonnet; adversarial (checklist 8) on Opus. Security, infrastructure and data reviewers not applicable.
- Completed required checklists: core (1, 3, 4, 5) and adversarial (8). No checklist was skipped; 3, 4 and 5 were merged into the core report per policy.
- Reports: `issue-497-profile-tie-break-deterministic-core.md`, `issue-497-profile-tie-break-deterministic-adversarial.md`.
- Post-review delta: one docs sentence, two docstring corrections, one CHANGELOG clause, removal of a redundant regex flag, a renamed test, two added tests and one added assertion. All were requested by the reviewers; no production behavior changed after the reviewed snapshot.
- Final tree snapshot: `e379b7942c571736bb5099d6821e70f5b4d03e1d575a6f35154eb2cdda314f9b` (tier M, same roster).
- Verification on the final tree: `uv run --no-sync python -m pytest tests/ -q -n auto --dist worksteal --ignore=tests/test_lexical_shutdown.py` => 7083 passed, 1535 skipped, 2 xfailed in 717 s; `uv run --no-sync python -m pytest tests/test_lexical_shutdown.py -q` => 10 passed; `uv run --no-sync pytest -q tests/test_profile_excerpts.py tests/test_profile_graph_sources.py` => 25 passed, 9 skipped; `uv run --no-sync ruff check src/ tests/` and the CI mypy public API check pass.

## Files Changed

- `src/prme/organizer/profiles.py`: equal-event-time ordering in `build_profile` (`_profile_order`, `_distinct_terms`, `_TURN_TYPES`).
- `src/prme/organizer/owner_profiles.py`: `_is_stale` docstring now states that the ordering change alone does not rebuild an old profile.
- `tests/test_profile_excerpts.py`: unit regression with random ids, turn-versus-claim rank, term count, text order and the id fallback.
- `tests/test_profile_graph_sources.py`: store-level regression for one budget slot; `make_node` accepts an id and event time.
- `docs/PACKING.md`, `CHANGELOG.md`: the ordering rule and the fact that existing profiles are not rewritten.

## Approach Summary

`build_profile` sorts sources oldest first and then greedily adds every complete source that fits the token budget. Its old tie-break for equal event times was the node UUID, so a turn and a claim extracted from it (same event time) swapped places per store. The new key keeps the event time first, then puts a turn (NOTE/EVENT) before claims derived from it, then orders by the number of distinct terms (more coverage first), then the content text, and only then the id for records that state the same text. Stores whose sources never tied on event time produce the same bytes.

## Must Fix

None.

## Should Fix

None.

## Consider

1. `_is_stale` and `docs/PACKING.md` justified the rebuild skip with "the text would not change", which the tie-break change makes false for a profile built under the old order. Resolved: both now say the profile is left alone and only improves when an application replaces it.
2. The content and id levels of the sort key had no direct test, and `test_equal_event_times_order_by_content_not_by_id` actually exercised the type rank. Resolved: the test is renamed to `test_equal_event_times_put_a_turn_before_its_claim`, and two tests now cover equal-term-count text order and the id fallback. The store-level test also asserts `source_count_available == 3`.
3. The rule applies only at an exact event-time tie; a claim with a resolved date can still precede its turn (`src/prme/ingestion/pipeline.py:1858-1877`). Out of scope under the "tie-break only" constraint; raised as follow-up #501.
4. `re.UNICODE` on the word regex was redundant. Resolved: removed.

## Security Audit Results

Not applicable: no auth, authorization, tenant isolation, protected data, input parsing or output surface changed. The helper reported `SECURITY` false, and the core reviewer screened basic security in the merged checklist. No new logging, env/config reads, network or DB calls.

## Pattern Consistency Assessment

`_profile_order` follows the local pattern of a private pure helper and a bare tuple key; `_stable_source_key` in `organizer/consolidation.py` has a deliberately different contract and was not reused. `_TURN_TYPES` uses `NodeType` members rather than raw strings. The public `build_profile` signature is unchanged, and its single production caller is `MemoryEngine.consolidate_knowledge`.

## Redundancy Check

No existing utility counts distinct terms or orders profile sources. `_WORD` in `integrations/product_candidates.py` is an ASCII product tokenizer in another domain. No new dependency, type or config surface.

## Wiring Findings

The new helpers are used at `src/prme/organizer/profiles.py:134`. `build_profile` has one production caller (`src/prme/storage/engine.py:4831`), and both test files are discovered by pytest. The regression tests fail on the base code: the claim forced to `UUID(int=0)` sorts first under the old key, and `source(..., 1)` sorts before `source(..., 2)`.

## Data Safety and Read/Write Proof

Not applicable: no new durable field, schema, index, key or migration. The profile node's format and `profile_format_version` 2 are unchanged; only the order of quoted sources within the existing text can change. A profile built before the change is not rewritten by the upgrade.

## Break Scenarios (adversarial)

| ID | Scenario | Mechanism | Likelihood | Impact | Detection | Verdict | Class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | Existing bad profiles are never repaired | `_is_stale` returns False when `included < available` (`owner_profiles.py:208-210`), so the automatic refresh never rebuilds a profile that already dropped a source; no CLI forces one | High for the population that reported the issue | Medium: the reported symptom persists for that profile | Quiet; compare profile content with its active sources | Follow-up #500 | Pre-existing, newly load-bearing |
| S2 | A claim resolved to an older date beats its turn | A resolved `temporal_ref` sets the claim's event time (`pipeline.py:1858-1877`), so the first key differs and the type rank never runs | Low to medium | Low to medium: the turn's context can be dropped at a tight budget | Quiet; compare resolved claims with their evidence turns | Follow-up #501 | Pre-existing |
| S3 | Byte-identical records still flip on random id | The last key is `str(node.id)` (`profiles.py:123`); a re-ingest mints new UUIDs | Low | Low: rendered body is identical, only the `source=` provenance line differs | Quiet; diff two builds for differing `source=` values with identical bodies | Accept | Pre-existing |
| S4 | Per-source word sets raise peak memory at very large budgets | `_distinct_terms` builds a transient word set per source and keeps the full content reference in each key | Low | Low to medium on a pathological store | Profile-build wall time and process RSS at a large budget | Accept | Newly introduced |

Pre-mortem headline: "We shipped the deterministic tie-break, but the owners who reported #497 still see a profile that names only Nico, because `_is_stale` never rebuilds a profile that already dropped sources and no CLI can force one."

## Adjudication

- **S1 — Follow-up (#500).** The issue's acceptance criterion is about how a build selects sources, and a new build is deterministic. Repairing stored profiles needs a one-time signal (`profile_format_version` plus the privacy journal and migration, or an operator command) and one embedding per profile, which is a product/contract decision this change should not absorb. The false justification in `_is_stale` and the docs was corrected here.
- **S2 — Follow-up (#501).** The reported case has no temporal reference and is fixed. Grouping a claim with its evidence turn would change ordering where event times differ, which the issue and the plan exclude; it needs a decision about which time a resolved claim sorts by.
- **S3 — Accept.** The same memories (same stored nodes) already build the same profile exactly. The residual only affects a re-ingest of byte-identical records, where the quoted text is identical and only the embedded source id differs. A fix would add a rendered-line tie-break for no user-visible gain.
- **S4 — Accept.** The word set is transient per key computation and is not retained in the sort key (the key holds an int); the full content strings are already held by the loaded nodes, and `count_tokens` remains the heavier per-source cost. Sources are bounded by the records that mention the person. Worth measuring only if very-large-budget profiles become a production shape.

Tally: Fix now 0, Follow-up 2, Accept 2, Dismissed 0.

## Follow-ups Raised

- #500: Knowledge profiles that dropped a source are never rebuilt automatically.
- #501: A claim with a resolved date can still displace its turn in a knowledge profile.

## Resolution Status

| Item | Severity | Status |
| --- | --- | --- |
| Rebuild-skip justification in `_is_stale` and `docs/PACKING.md` | Consider | Resolved |
| Missing content/id tie-break tests, misleading test name | Consider | Resolved |
| Resolved-date claim ordering (S2) | Consider / break scenario | Follow-up #501 |
| Redundant `re.UNICODE` | Consider | Resolved |
| Existing profiles never repaired (S1) | Break scenario | Follow-up #500 |
| Byte-identical records fall to id (S3) | Break scenario | Accepted |
| Word-set memory at very large budgets (S4) | Break scenario | Accepted |
| Must Fix / Should Fix | — | None raised |
