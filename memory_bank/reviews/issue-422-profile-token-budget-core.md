# Review: issue #422 profile token budget (core: checks 1, 3, 4, 5)

Status: COMPLETE

- Repository: /Users/dwamianm/Sites/prism
- Base commit: 404993f528a4e2987b00f15436b583b721b296cb (branch main)
- Reviewed snapshot: 84d62853cb28868fa2a295fa7d9adc5f8754803b858e6b4d313064d33e0fde33
- Diff reviewed: /tmp/issue-422-review.diff (matches working tree: 8 files, +111/-15)
- Scope: correctness (1), code quality/tests (3), pattern consistency/DRY (4), redundancy (5)
- Not reviewed here: security/data, adversarial checklist

## Assigned checklist

1. Correctness and logic (edge cases, None handling, boundaries, error states, data flow, type safety, ignored sentinels).
3. Code quality (complexity, naming, dead code, error handling, test coverage, performance).
4. Pattern consistency and DRY against 2-3 repo analogues.
5. Redundancy and unnecessary code (new functions/constants, existing equivalents).

## Acceptance criteria used

1. A rebuilt profile keeps the token budget it was built with.
2. A profile written before the budget was recorded does not lose sources it already quotes.
3. No change to profile ordering, the option default, or retrieval receipts.
4. Tests cover the rebuild behavior.

## Findings

### F1 (Low, check 5 + DRY) `DEFAULT_PROFILE_TOKENS` duplicates a public default with no link to it, and the test that claims to pin them does not

- New code: `src/prme/organizer/owner_profiles.py:65-68` adds `DEFAULT_PROFILE_TOKENS = 500` with the comment "It matches the default of `consolidate_knowledge()`".
- Existing copies of the same value: `src/prme/storage/engine.py:4552` (`max_profile_tokens: int = 500` on the async API) and `src/prme/client.py:1110` (`max_profile_tokens: int = 500` on the sync client). No pre-existing shared constant exists (checked: `rg max_profile_tokens` has no constant; config has no such setting).
- The guard test does not keep them in step: `tests/test_owner_profile_context.py:331` asserts `_profile_budget(None) == DEFAULT_PROFILE_TOKENS == 500`. That compares the new constant with a literal, so if someone changes the engine default to 1000 and forgets this constant, the test still passes and rebuilds silently fall back to 500 again, which is exactly the defect in issue #422.
- Repo pattern for the same problem: `PROFILE_HEADER`/`PROFILE_PREFIX` are defined once in `src/prme/retrieval/owner_profile.py:44-45` and imported by `src/prme/organizer/profiles.py:13`, so a value that both a producer and a consumer depend on lives in one module. `src/prme/organizer/owner_profiles.py` cannot import `prme.storage.engine` at module scope (it deliberately imports engine symbols inside functions, e.g. `:106`, `:174`, because of the import cycle), so the constant cannot simply be imported from engine.
- Suggested fix: define `DEFAULT_MAX_PROFILE_TOKENS = 500` beside the engine default in a module both can import without a cycle (e.g. `src/prme/organizer/profiles.py`, which engine already imports at function scope and owner_profiles imports at module scope), use it as the engine and client parameter default, and have the test assert the helper's fallback against `inspect.signature(MemoryEngine.consolidate_knowledge).parameters["max_profile_tokens"].default` instead of the literal 500. Alternatively keep the local constant but add that signature assertion so drift fails.

### F2 (Low, check 3 + 5) `_rebuild`'s new `profile` parameter defaults to `None`, which re-opens the silent fallback the PR closes

- `src/prme/organizer/owner_profiles.py:227-234`: `profile: MemoryNode | None = None`, and `_rebuild` computes `max_profile_tokens=_profile_budget(profile)`. With the default, any future call site that omits `profile` silently rebuilds at 500, the behavior issue #422 exists to stop, and no caller needs that.
- Both current call sites pass it (`:276-278`, `:309-321`).
- Suggested fix: make it a required keyword-only parameter (`profile: MemoryNode | None`, no default). `None` stays meaningful for the "no existing profile, this is a first build" case in `refresh_owner_profiles`.

### F3 (Low, check 3) No test exercises the budget through the `refresh_owner_profiles` (end_session) caller

- The change touches two `_rebuild` call sites. `tests/test_owner_profile_context.py:294-320` covers the organizer path (`refresh_stale_profiles` via `MaintenanceRunner`, `_rebuild` at `owner_profiles.py:276-278`). Nothing covers the end_session path (`owner_profiles.py:309-321`, reached through `MemoryEngine.end_session`, `engine.py:5059-5069`). The existing end_session tests (`tests/test_owner_profile_context.py:351-386`) build profiles with the default budget, so a regression that dropped the `profile=profile` argument at `:319-321` would not fail any test.
- Suggested fix: add one end_session case in the same style as `test_a_rebuild_keeps_the_token_budget_its_profile_recorded`, i.e. build the owner's profile explicitly at a larger budget, archive a quoted source, call `engine.end_session(...)`, and assert the replacement kept `max_profile_tokens` and its sources. (A build in `refresh_owner_profiles` requires the owner-name marker, used by `mark_owner` at `tests/test_owner_profile_context.py:94-99`.)

### F4 (Low, check 3) AC2's guarantee overclaims for a backdated record and a changed tokenizer

- `src/prme/organizer/owner_profiles.py:81-83` states the `tokens_used` fallback "so a rebuild keeps every source it quotes", and three docs repeat a version of it (`docs/PACKING.md:508-511`, `docs/ENTITY-PROFILES.md:58-61`, `CHANGELOG.md` Fixed entry).
- The argument that holds: `build_profile` orders sources by `(_date(event_time or created_at), id)` and only ever appends (`src/prme/organizer/profiles.py:98-111`), and `tokens_used` is the exact token count of the previous text (`engine.py:4867` sets it from `build_profile`'s return at `:4833`), so re-adding the same sources in the same order ends at exactly that count and every one of them fits under a budget of `tokens_used` (the fit test is `> token_budget`, so equality fits).
- Two cases where it does not hold: (a) a new record with an *older* `event_time` than the quoted sources sorts before them (callers may pass `event_time`, e.g. `store(event_time=...)`, `engine.py:1124`), can consume budget ahead of them, and can displace the last quoted source when the profile was full; (b) `metadata["tokenizer"]` is not consulted, so if `packing.tokenizer` changed since the profile was built, `tokens_used` was counted with a different tokenizer and can under-count. Neither is reachable in this PR's tests, and (a) is a consequence of the oldest-first ordering that the PR deliberately leaves open.
- Suggested fix: soften the unconditional claim in the helper docstring and the three docs to "keeps the sources it quotes when the source set only grows, as the current ordering produces", or leave the wording and record the two cases as known limits. No code change strictly needed; this is a comment/doc accuracy issue, not a behavioral defect.

## Check results

### 1. Correctness and logic — clean apart from F4

- Recording site: `src/prme/storage/engine.py:4865-4870` adds `"max_profile_tokens": max_profile_tokens` beside the existing `tokens_used`/`tokenizer` keys; the value was validated at `:4596-4597` (`type(...) is int`, `>= 0`), so a stored value is always a nonnegative int, and the same value that bounded `build_profile` at `:4830-4832` is the one recorded.
- Boundary/None handling in `_profile_budget` (`owner_profiles.py:75-92`): `None` profile -> `{}` -> 500; `metadata is None` -> `{}`; `bool` rejected by `type(budget) is int`; negative rejected; `max(500, used)`; `0` accepted, and a profile can never store `0` because `consolidate_knowledge(max_profile_tokens=0)` publishes nothing (asserted at `tests/test_knowledge_profile_scopes.py:181-183`).
- Error state: `_profile_budget` is evaluated inside `_rebuild`'s `try` (`owner_profiles.py:230-234`), so a malformed metadata value (non-dict) surfaces as the existing "Profile rebuild failed; the previous profile stays" path, leaving the old profile in place.
- Both rebuild callers pass the profile they replace and use the same name for the build (`:276-277`, `:311-320`), so the budget read is the budget of the profile that is actually replaced.
- No ignored sentinel: `consolidate_knowledge` returns an int and `_rebuild` already maps it to `published > 0` (pre-existing, unchanged); `_profile_budget` returns a plain int used directly.
- Protected owners: `project_generated_node` (`src/prme/privacy/journals.py:108-119`) is a closed allowlist for generated profiles and drops `max_profile_tokens` (as it already drops `tokenizer` and `source_paths`). That is harmless because both refresh paths return early for an owner under a policy (`owner_profiles.py:253-254`, `:297-298`); protected profiles are rebuilt only by explicit `consolidate_knowledge()`, which supplies its own budget.
- Interaction with the plan cache: `profile_request_hash` hashes `node.model_dump()` including metadata (`src/prme/models/profile.py:41-56`), so the new key changes the hash. Effect is limited to one extra prepare/embedding for a plan prepared before the upgrade, and to two different budgets hashing separately; no correctness impact. Not a finding.
- `DERIVATION_VERSION` (`src/prme/ingestion/reingest.py:89`) needs no bump: `reingest()` does not touch profiles (`rg` in that module finds none) and `tests/fixtures/derivation/trip_derived.json` contains no profile record.
- Option default unchanged: no change to `src/prme/config.py`; `enable_owner_profile_context` stays `False` (`documentation/configuration.md:148` description unchanged in substance).
- Profile ordering unchanged: `build_profile` untouched; `_is_stale` untouched.

### 3. Code quality — F2, F3, F4

- Naming and placement of `_profile_budget`/`DEFAULT_PROFILE_TOKENS` read well and the docstring explains both branches.
- Complexity is minimal; no dead code; no new I/O, env or config reads.
- Performance: removing the accidental 500-token cap means a background rebuild (`_run_maintenance` -> `refresh_stale_profiles`, `owner_profiles.py:241-281`, "a rebuild that has started runs to its end") now costs up to whatever budget an application recorded, in token counting plus one embedding. This is the intended behavior per AC1 and is bounded by the app's own choice, but the PR's docs do not mention the cost shift. Optional: note it in `docs/PACKING.md`.
- Test coverage: F3 and F4 above. The new integration test does discriminate 4000 from 500 (each of the three sources is ~200 words, so a 500-token build keeps one, and the test asserts `source_count_included == 2`), and the unit test covers `None`, a recorded budget, `tokens_used` above and below the default, a bool masquerading as an int, and a negative budget.

### 4. Pattern consistency and DRY — F1

- `src/prme/storage/engine.py`: the one-line metadata addition matches the adjacent `tokens_used`/`tokenizer` entries at `:4866-4867` and the docstring update at `:4571-4573`. Consistent.
- `src/prme/organizer/owner_profiles.py`: the defensive metadata reader matches `_quoted_sources` (`src/prme/retrieval/profile_sources.py:49-62`) and `profile_entity_name` (`src/prme/retrieval/owner_profile.py:62-73`) in shape, and its strict `type(value) is int` matches the validated-int convention in `src/prme/privacy/journals.py:116-118` and `src/prme/storage/engine.py:4596`. Consistent. (Side note, pre-existing and not introduced here: `_is_stale` at `owner_profiles.py:212-213` uses the looser `isinstance(..., int)`, which would accept `True` as a count. Out of scope.)
- Constant duplication: F1.
- `tests/test_owner_profile_context.py`: the new integration test reuses the module helpers (`config`, `profiles`) like its neighbours. Nit: the unit test imports `DEFAULT_PROFILE_TOKENS` and `_profile_budget` inside the function body (`:323`) while the rest of the file imports at module scope; harmless, ruff-clean.
- Docs: `docs/ENTITY-PROFILES.md:54-61`, `docs/PACKING.md:504-511`, `docs/RFC-0015-Self-Organizing-Memory.md:364-371`, `documentation/configuration.md:148` and the CHANGELOG entry all state the same rule as the code, and the option is not described as flipping. Nit: the RFC edit leaves an awkward short line, `docs/RFC-0015-Self-Organizing-Memory.md:370` ("published. Names and token" / "limits are validated ..."), splitting "token limits" across lines. Markdown renders the same; re-wrap for readability.

### 5. Redundancy and unnecessary code — F1, F2

- New symbols: one constant (`DEFAULT_PROFILE_TOKENS`, duplicate — F1) and one function (`_profile_budget`). No existing helper computes this; `rg max_profile_tokens` finds only the engine/client parameter, the new key, and tests, so the function is not a duplicate.
- Unnecessary default: F2.
- No dead code, no unreachable branch (`max(DEFAULT_PROFILE_TOKENS, used)`'s two arms are both covered by the unit test).

## Verified against acceptance criteria

- AC1: recorded at `engine.py:4868`, reused at `owner_profiles.py:231-234` from both rebuild paths; covered by `tests/test_owner_profile_context.py:294-320`. Met.
- AC2: `tokens_used` fallback at `owner_profiles.py:89-91`; sound under unchanged ordering and tokenizer (F4 names the two limits); covered at the helper level by `:322-337`, not end to end. Met for the intended case.
- AC3: no change to `build_profile`, `_is_stale`, `config.py` or retrieval; the metadata key is additive and absent from receipts; the existing receipt-equality tests (`tests/test_owner_profile_context.py`, `receipt_bytes` comparisons) still pass per the provided evidence, which this review did not re-run.
- AC4: covered for the organizer path and the helper; not covered for the end_session path (F3).

No blocking finding: no acceptance criterion is violated by a normal path, no regression is introduced relative to main, and no security issue was seen in scope.
