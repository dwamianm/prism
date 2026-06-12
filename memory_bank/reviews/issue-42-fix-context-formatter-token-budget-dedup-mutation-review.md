# Review — issue-42-fix-context-formatter-token-budget-dedup-mutation

## Files Changed

- `src/prme/retrieval/context_formatter.py` — the fix.
- `tests/test_context_formatter.py` — new tests for dedup, profile-exclusion, token budget, no-mutation, sanitization-survives-dedup.
- `tests/test_prime_enhancements.py` — updated 6 callers of `_build_profile_preamble` for its new tuple return.

## Approach Summary

Issue #42 reported three correctness defects in `context_formatter.py`:

1. **Token budget bypassed** — `format_for_llm` had no budget mechanism; callers
   passed raw `results[:N]` and the configured packing budget never bounded the
   formatted output.
2. **20-30% duplicate content** — only `_format_aggregation` deduplicated; the
   default/temporal/knowledge_update formats did not, and profile-preamble nodes
   were repeated verbatim in the retrieved-memory body.
3. **In-place mutation** — `_format_temporal/_format_knowledge_update/_format_aggregation`
   called `results.sort()`, mutating the caller's list.

Fix:

- Added a shared `_content_key()` (consolidates the old inline aggregation
  heuristic) and `_select_entries()` (dedup + profile-exclusion + budget-aware
  trim by relevance rank). All four format variants now route through it.
- `_build_profile_preamble` returns `(section, consumed_keys)` so the body can
  exclude nodes already rendered in the profile.
- Added an opt-in `token_budget: int | None = None` param to `format_for_llm`.
  When set, lowest-ranked body entries are dropped until the body fits; the
  always-kept profile/conflict/reasoning scaffolding is reserved against the
  budget. Default `None` preserves exhaustive aggregation (no silent regression).
- `sorted()` everywhere instead of `.sort()` — the caller's list is never mutated.
- PR #36's `_sanitize_content` remains applied at every interpolation site
  (verified by review and a regression test).

## Must Fix

| # | Finding | Resolution |
|---|---------|------------|
| 1 | `_build_profile_preamble` tuple return broke 6 tests in `test_prime_enhancements.py` (lines 349/375/391/419/436/456) | Fixed — unpacked the tuple at all 6 sites |

## Should Fix

| # | Finding | Resolution |
|---|---------|------------|
| 2 | Default `token_budget=4096` silently capped benchmark context, undermining exhaustive aggregation (longmemeval requests 100 results "to catch all items") | Fixed — default changed to `None` (opt-in); budget mechanism stays available |
| 3 | Hardcoded `4096` duplicated `PackingConfig.token_budget` (two sources of truth) | Resolved by #2 — no literal default; callers/config thread the budget |
| 4 | Docstring claimed the budget bounds the entire string; per-format headers are not reserved | Fixed — docstring softened to "approximate ceiling on the body" with the header-margin caveat |

## Consider

| # | Finding | Resolution |
|---|---------|------------|
| 5 | `+8` per-entry magic number | Fixed — promoted to named constant `_PER_ENTRY_PREFIX_TOKENS` |
| 6 | `_select_entries` could be mistaken for / consolidated with `pack_context` | Fixed — added cross-reference note in the docstring explaining the distinction |
| 7 | `_content_key` is `_noun_noun` not `_verb_noun` | Left as-is — names a derived value, parallel to `_get_event_dt`; cosmetic |

## Security Audit Results (Agent 2)

PASS, no regression of PR #36's prompt-injection defense.

| Area | Result | Details |
|------|--------|---------|
| `_sanitize_content` at every interpolation site | PASS | 9/9 sites match `main`; none lost the wrapper |
| `_DATA_NOTICE` trust banner | PASS | Still emitted on both body paths |
| `_content_key` uses raw content | PASS | Internal set-membership only; never emitted |
| Dedup suppressing legitimate entries | PASS/benign | Only collapses near-identical text; survivor still sanitized |
| Profile-exclusion hiding conflicts | PASS | Conflicts built from full results, not the deduped body |
| New logging / PII | PASS | No logging added |
| Budget dropping entries | PASS/benign | Drops lowest-ranked only; one entry always kept |

## Pattern Consistency Assessment (Agent 4)

- `estimate_token_cost` correctly reused (imported, not reimplemented).
- `sorted()` over `.sort()` is the correct, intentional divergence — the variants
  were the outlier sorting a parameter that aliased caller state.
- `_select_entries` is a justified separate path from `pack_context` (flat
  rendered-list trim vs sectioned bundle bin-packing with STR re-ranking).

## Redundancy Check (Agent 5)

No redundant/dead code added. `_content_key` consolidates the removed inline
aggregation dedup; `_select_entries` is shared by all 4 variants; the old
`seen_content`/inline `content_key` loop is fully removed.

## Wiring Findings (Agent 6)

- All `format_for_llm` callers use keyword args; the new keyword-only `token_budget`
  cannot collide positionally. With default `None`, existing benchmark callers are
  unaffected (no silent trim).
- `_build_profile_preamble` tuple return propagated to the one production caller and
  all 6 test callers.
- `estimate_token_cost` import is one-directional (no circular import with packing).

## Resolution Status

| Severity | Count | Resolved |
|----------|-------|----------|
| Must Fix | 1 | 1 |
| Should Fix | 3 | 3 |
| Consider | 3 | 2 applied, 1 declined (cosmetic) |

All blocking findings resolved. Full suite: 1178 passed, 25 skipped (PostgreSQL).
