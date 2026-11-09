# Review: issue #422 profile token budget (core: checks 1, 3, 4, 5) — round 2

Status: COMPLETE

- Repository: /Users/dwamianm/Sites/prism
- Base commit: 404993f528a4e2987b00f15436b583b721b296cb (main)
- Reviewed snapshot: 1e383082b45f76d898b9cf9f76e1626ced6cb8bbeca920f7bad4158fc8bdf499
- Diff reviewed: /tmp/issue-422-review-round2.diff (10 files, +162/-22; verified byte-identical to `git diff` on the working tree)
- Scope: correctness (1), code quality/tests (3), pattern consistency/DRY (4), redundancy (5)
- Prior reviews: core round 1 (F1-F4), adversarial round 1 (S1-S6)
- Evidence used: worker's `ruff` (pass), `pytest` on the three profile suites (61 passed, 28 skipped), and public-API `mypy` (success). Not re-run here; source verified independently.

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

## Prior findings — resolution check

- **F1 (constant duplicate / test did not pin it) — RESOLVED.** `DEFAULT_MAX_PROFILE_TOKENS = 500` is now defined once at `src/prme/config.py:32-34`, imported by `src/prme/storage/engine.py:53`, `src/prme/client.py:43` and `src/prme/organizer/owner_profiles.py:36`, and used as both method defaults (`engine.py:4552`, `client.py:1110`) and the organizer fallback (`owner_profiles.py:87-88`). The test at `tests/test_owner_profile_context.py:357-361` asserts both `consolidate_knowledge` signatures carry that constant, so changing an API default without the constant fails. No remaining literal `500` default for this parameter anywhere (`src` grep).
- **F2 (optional `profile` parameter re-opened the silent fallback) — RESOLVED.** `_rebuild` now takes `profile: MemoryNode | None` with no default, keyword-only (`owner_profiles.py:223-225`). Both call sites pass it (`:278-280` organizer, `:321-323` end_session), and no other caller or test invokes `_rebuild` (grep). `None` remains the meaningful first-build case.
- **F3 (no end_session coverage) — RESOLVED.** `tests/test_owner_profile_context.py:322-341` builds at 4000, stores a newer mention, calls `engine.end_session()`, and asserts the replaced profile kept `max_profile_tokens == 4000` with all sources included. The organizer path stays covered at `:294-319`.
- **F4 (docstring/docs overclaimed "keeps every source it quotes") — RESOLVED for the helper docstring.** `_profile_budget`'s docstring (`owner_profiles.py:70-80`) now says the fallback "starts from the tokens it used rather than the default" instead of claiming every quoted source survives. Docs state the same fallback (`docs/ENTITY-PROFILES.md:57-64`, `docs/PACKING.md:507-514`, `CHANGELOG.md:12`, `docs/RFC-0015-Self-Organizing-Memory.md:367-372`). The RFC re-wrap nit from round 1 is also gone.
- **Adversarial S6 (a zero recorded budget retried forever) — RESOLVED.** `owner_profiles.py:83` now requires `budget > 0`; `0`, `-1` and `True` fall through to `tokens_used` or the default, and `tests/test_owner_profile_context.py:367-369` pins those three cases.

## Findings

### N1 (Nit, check 4 — documentation consistency) `docs/PACKING.md:527-528` still generalizes "within 500 tokens"

The changed paragraph at `docs/PACKING.md:505-514` now says a rebuild keeps the budget the profile recorded, but the unchanged rationale at `:527-528` still reads "A profile keeps its oldest sources within 500 tokens". After this change a profile an application built with a larger budget keeps the larger budget, so this sentence is only true for the default build the fast gate used. Suggested wording: "within its token budget (500 by default)". Non-blocking; the ordering rationale it supports is unchanged.

## Check results

### 1. Correctness and logic — clean

- Recording (`engine.py:4868`) writes the same validated `max_profile_tokens` that bounded `build_profile` (`:4831`), beside `tokens_used`/`tokenizer`; the `scope is None` recursion forwards it (`:4623`), so no scope loses it.
- Reading (`owner_profiles.py:81-88`): `None` profile and `metadata is None` -> default; `type(...) is int` rejects `bool`, floats and strings; `budget > 0` rejects zero/negative; the `tokens_used` branch requires `>= 0`; the result is a plain `int`. `metadata` is JSON-round-tripped on read (`duckpgq_graph.py:2130-2133`), so the stored value is a Python int, matching the strict type test.
- Both rebuild call sites pass the profile they replace, so the budget read is the replaced profile's budget; `refresh_owner_profiles` passes `None` only for a first build (`:311-323`), which correctly takes the default.
- `_profile_budget` runs inside `_rebuild`'s `try` (`:232-236`), so a malformed metadata value follows the existing "previous profile stays" path.
- The recorded value is bounded upstream: `build_profile` publishes only when header plus selected sources fit the budget (`organizer/profiles.py:107-114`), so a stored `tokens_used <= max_profile_tokens`; `consolidate_knowledge` rejects negative/non-int (`engine.py:4596-4597`) and a zero budget publishes nothing.
- Protected stores: `project_generated_node` drops the new key (`privacy/journals.py:90-119`), and both refresh paths return early for an owner under a policy (`owner_profiles.py:255-256`, `:299-300`); docs now state this explicitly. The migration test's hand-built profile metadata without the key still projects.
- No sentinel is bypassed: `_rebuild` keeps the pre-existing `published > 0` mapping; `_profile_budget` returns a real int.
- `DERIVATION_VERSION` needs no bump: `reingest()` does not touch profiles and the derivation fixture holds no profile record (unchanged from round 1).
- Ordering (`build_profile`), `_is_stale`, the `enable_owner_profile_context` default and retrieval receipts are untouched; the only durable delta is one additive metadata key.

### 3. Code quality — resolved F2/F3/F4; two non-blocking notes

- `_rebuild`'s docstring now explains why `profile` is required, matching the fix's intent. Complexity is minimal; no dead code; imports are ruff-clean.
- Tests cover both refresh paths, the helper's branches (recorded budget, `tokens_used` above/below the default, `bool`, zero, negative, `None`) and the shared-constant invariant. The new integration tests discriminate 4000 from 500 through the persisted `max_profile_tokens`, not only through source counts.
- Performance (non-blocking, follow-up): reusing a recorded large budget means a background rebuild's token counting (superlinear in the number of sources) and one embedding now run at the application's chosen size instead of 500. That is the intended consequence of AC1, but nothing caps it and no doc notes the cost shift; adversarial round 1 scenario 2 records it as follow-up.
- Nits: the new unit test imports `inspect`, `MemoryClient`, `DEFAULT_MAX_PROFILE_TOKENS` and `_profile_budget` inside the function body (`tests/test_owner_profile_context.py:345-349`) while the rest of the file imports at module scope; harmless and ruff-clean. `assert DEFAULT_MAX_PROFILE_TOKENS == 500` pins the literal, which is the desired drift guard here.

### 4. Pattern consistency and DRY — clean apart from N1

- The single constant near the defaults is the pattern used by `src/prme/retrieval/config.py:28-69` (`DEFAULT_RRF_K`, `DEFAULT_RERANKER_PRIOR_WEIGHT`, etc.), and `PROFILE_HEADER`/`PROFILE_PREFIX` are likewise defined once (`retrieval/owner_profile.py:43-45`). Import direction is safe: `prme.config` imports only `prme.privacy.policy`, `prme.retrieval.config`, `prme.retrieval.temporal_relations`; none imports `prme.storage.engine`, `prme.client` or `prme.organizer`, so `owner_profiles -> config` adds no cycle (confirmed by the passing suite, which imports all three modules).
- `_profile_budget`'s defensive dict read matches `_quoted_sources` (`retrieval/profile_sources.py:49-62`) and `profile_entity_name` (`retrieval/owner_profile.py:62-73`); its strict `type(...) is int` matches the engine's own `max_profile_tokens` validation (`engine.py:4596`) and the privacy allowlist (`privacy/journals.py:116-118`).
- The engine/client default change is a pure de-duplication; the metadata addition sits beside the existing `tokens_used`/`tokenizer` keys. Consistent.

### 5. Redundancy and unnecessary code — clean

- One new constant (now the sole source of the 500 default, replacing two literals plus the helper's) and one new helper (`_profile_budget`). No existing equivalent computes this (`rg max_profile_tokens` finds only the API parameter, the recorded key, and tests).
- No unnecessary parameter default remains (F2), and both `max(DEFAULT, used)` arms are exercised by the unit test.

## Verified against acceptance criteria

- **AC1 — Met.** Recorded at `engine.py:4868`, read at `owner_profiles.py:81-88`, passed at `:235` from both refresh callers; covered end to end for both (`tests/test_owner_profile_context.py:294-341`).
- **AC2 — Met for the documented case.** Legacy profiles fall back to `max(500, tokens_used)`; rebuilding at `tokens_used` reproduces the deterministic selection, so no quoted source is dropped by the default. The narrow tokenizer-change and backdated-`event_time` corners remain (adversarial round 1 scenarios 1 and 3) and are outside this bounded ticket.
- **AC3 — Met.** `build_profile`, `_is_stale`, `enable_owner_profile_context` and receipts are untouched; the metadata key is additive and absent from receipts.
- **AC4 — Met.** Organizer path, end_session path and helper branches are all covered.

## Verdict

No blocking finding: every prior core finding (F1-F4) and adversarial S6 is resolved, the `> 0` guard, the single constant's home/import direction and the required-parameter change at both call sites are all correct, and no regression is introduced relative to `main`. N1 is a documentation nit; the budget-size performance note is a documented follow-up.
