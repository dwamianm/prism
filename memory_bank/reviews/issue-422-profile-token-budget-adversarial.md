# Adversarial review: issue #422 (a knowledge profile keeps its token budget)

Status: COMPLETE

Base: `main` at `404993f528a4e2987b00f15436b583b721b296cb`
Reviewed snapshot: `84d62853cb28868fa2a295fa7d9adc5f8754803b858e6b4d313064d33e0fde33` (uncommitted work; HEAD == base)
Checklist: 8 (adversarial)

## Checklist worked

- Premise challenge (is "the profile forgot its budget" the whole defect, and does the fallback restate the issue's own bug?): checked.
- Sibling rebuild entry points (`refresh_owner_profiles` from `end_session`, `refresh_stale_profiles` from the organizer, explicit `consolidate_knowledge`, `resume_profile` / prepared-plan replay, privacy migration, protected stores): checked.
- Guard loopholes (`_is_stale` truncation short-circuit, `_owner_has_policy`, type checks in `_profile_budget`, `consolidate_knowledge` validation): checked.
- Existing data written under old rules (profiles without the key, with a stale `tokens_used`, budget 0, v1 profiles, tokenizer changes): checked.
- State and lifecycle (replaced profiles, archived predecessors, retirement on <2 sources, protected projection): checked.
- Idempotency and retries (request hash, prepared-plan reuse, repeated failed rebuilds): checked.
- Ordering/timing/consistency (opportunistic 200 ms pass, `end_session` 1 s budget, run-to-completion rebuild): checked.
- Scale (recorded budget size, quadratic token counting in `build_profile`, embedding cost): checked.
- Partial failure (`_rebuild` swallow, previous profile survives, no publication): checked.
- Backward compatibility (no config/env change, metadata key additive, option stays off, receipts): checked.
- Blast radius and side effects (indexing, embedding, privacy journals/snapshots, erasure, determinism): checked.
- Detection (logs, organizer entry shape, metadata counters): checked.

## Verdict

The bounded fix is correct for its stated acceptance criteria. The budget is written (`src/prme/storage/engine.py:4868`), read
(`src/prme/organizer/owner_profiles.py:75-92`), and passed by both refresh callers (`:233`, `:276-278`, `:319-321`);
ordering (`src/prme/organizer/profiles.py:98`), the option default and receipts are untouched. The residual risk is in
the *fallback*: `tokens_used` is a lower bound on a lost budget, and the code stores it as if it were the budget, then
the `_is_stale` truncation guard makes any consequent truncation permanent. That is the same class of silent staleness
#422 exists to stop, one level down. It is behind the default-off `enable_owner_profile_context`, so nothing in a
default deployment changes. Six scenarios below, ranked by likelihood x impact.

### 1. A legacy profile's budget ratchets down to `tokens_used` and the profile then freezes, still stale (newly introduced)

- **Trigger.** A store already holds a profile built before this release with `max_profile_tokens=4000` (any existing
  profile is "legacy": the key is new). The build had spare room (`tokens_used` ~2100 for three 700-token sources), so
  `source_count_included == source_count_available`. The app turns on `enable_owner_profile_context`. A new source
  arrives and the organizer rebuilds.
- **Mechanism.** `_profile_budget` returns `max(500, tokens_used)` (`src/prme/organizer/owner_profiles.py:89-91`), so the
  rebuild runs at ~2100, not 4000, and the replacement records that value (`src/prme/storage/engine.py:4868`). With
  the fourth 700-token source the oldest-first loop in `build_profile` (`src/prme/organizer/profiles.py:98-110`) skips
  it, the new profile has `source_count_included < source_count_available`, and `_is_stale` then returns `False`
  unconditionally (`src/prme/organizer/owner_profiles.py:212-214`). The original 4000 budget is never seen again, and
  every later pass skips the profile as "already truncated, its text would not change".
- **Likelihood.** Medium: every profile in the field today is legacy, and `tokens_used < budget` is the normal case
  (the loop stops at the first source that does not fit, so a profile typically leaves up to one source of slack).
- **Impact.** The owner's profile permanently omits sources the application's own budget would have included, silently,
  which is the outcome the ticket is meant to prevent. Retrieval answers lose the omitted facts, not the profile.
- **Detection.** Quiet. Only `source_count_available` > `source_count_included` in the profile metadata shows it, and
  nothing logs or reports the budget used.
- **Cheapest mitigation.** Treat the fallback as a lower bound with headroom instead of a budget: `max(DEFAULT, used + used // 4)`
  or a config default ceiling for legacy rebuilds; do not copy a usage-derived number into `max_profile_tokens`.
  Size: S.
- **Verdict.** Follow-up.

### 2. A recorded large budget is replayed on the request path without any ceiling (newly introduced)

- **Trigger.** An embedded application called `consolidate_knowledge(max_profile_tokens=4000)` (or much larger; the
  client exposes the parameter, `src/prme/client.py:1110`) and the profile records it. The profile goes stale and a
  `retrieve()`/`ingest()` triggers the opportunistic pass.
- **Mechanism.** `_profile_budget` returns the stored integer unchanged (`src/prme/organizer/owner_profiles.py:86-88`)
  and `_rebuild` passes it to `consolidate_knowledge` (`:233`). `build_profile` re-tokenizes the whole accumulated text
  for every candidate (`src/prme/organizer/profiles.py:106-107`), so the tokenizer work grows with the square of the
  number of sources that fit; the result is then embedded in one call (`src/prme/storage/engine.py:4895-4896`). The
  opportunistic pass has a 200 ms deadline (`src/prme/config.py:614-616`, `src/prme/organizer/maintenance.py:146`) and
  `end_session`'s refresh has 1 s (`src/prme/storage/engine.py:174`, `:5067`), and a started rebuild runs to its end.
  Before this change the organizer always rebuilt at 500 tokens; a 4000-token budget is roughly 64x the counting work.
- **Likelihood.** Low-Medium: needs an application that chose a large budget and enabled the option; no bound exists
  anywhere in the code, and `consolidate_knowledge` validates only "nonnegative integer" (`src/prme/storage/engine.py:4596-4597`).
- **Impact.** Multi-hundred-millisecond stalls inside `retrieve()`/`ingest()`/`end_session()` on every stale profile,
  plus an embedding call over a much larger text (FastEmbed defaults truncate near 512 tokens, so the profile's own
  vector covers a prefix either way).
- **Detection.** Quiet: `profiles_rebuilt` carries only `entity_name`, `scope`, `published` (`src/prme/organizer/owner_profiles.py:238`),
  and the budget is not logged; the only symptom is latency.
- **Cheapest mitigation.** Clamp the reused budget to a configured ceiling (a new `organizer`/profile field, or the
  existing packing budget) and log the budget chosen for each rebuild. Size: S-M.
- **Verdict.** Follow-up.

### 3. A changed `packing.tokenizer` invalidates the legacy fallback and drops a quoted source (newly introduced)

- **Trigger.** A legacy profile records `tokens_used` counted with `cl100k_base`
  (`metadata["tokenizer"]`, `src/prme/storage/engine.py:4867`). The operator later changes `packing.tokenizer`
  (`src/prme/retrieval/config.py:619-621`) to an encoding that counts more tokens for the same text, then a rebuild runs.
- **Mechanism.** `_profile_budget` returns `max(500, tokens_used)` (`src/prme/organizer/owner_profiles.py:89-91`) and
  ignores the recorded `tokenizer`; `build_profile` re-counts under the *configured* tokenizer
  (`src/prme/organizer/profiles.py:107`, `src/prme/retrieval/tokenization.py:16-18`). If the new count of the same
  selection exceeds `tokens_used`, the tail source is skipped, and the replacement then records that too-small budget
  and freezes on the truncation guard (`src/prme/organizer/owner_profiles.py:212-214`).
- **Likelihood.** Low: changing the packing tokenizer is unusual, and both encodings often differ by only a few percent.
- **Impact.** One or more quoted sources vanish from the profile and cannot come back through the refresh path; this is
  exactly the "a legacy profile does not lose quoted sources" criterion, broken in a corner.
- **Detection.** Quiet, as in scenario 1.
- **Cheapest mitigation.** When `metadata["tokenizer"]` differs from `engine._config.packing.tokenizer`, use a generous
  budget (or treat the budget as unknown and skip the rebuild), not `tokens_used`. Size: S.
- **Verdict.** Follow-up.

### 4. A deliberately small budget is inflated to 500 on the first refresh (newly introduced)

- **Trigger.** An application built profiles with `max_profile_tokens=250` (the API allows it and the suite uses it,
  `tests/test_knowledge_profile_scopes.py:149`, `:185`) and enables `enable_owner_profile_context`.
- **Mechanism.** `max(500, tokens_used)` (`src/prme/organizer/owner_profiles.py:91`) rebuilds the 250-token profile at
  500 and the replacement records 500 (`src/prme/storage/engine.py:4868`), so the caller's explicit cap is gone.
  `tokens_used` alone would already preserve every quoted source.
- **Likelihood.** Low: a sub-500 budget is unusual, and it is documented in `CHANGELOG.md:12` and
  `docs/ENTITY-PROFILES.md:58-61`.
- **Impact.** The owner's profile can double in size on a store that asked for a tighter one; it is the reserved
  `OWNER_PROFILE` slot in packing, so it can crowd out ordinary records in a tight context budget.
- **Detection.** Quiet; `tokens_used` on the rebuilt profile is the only trace.
- **Cheapest mitigation.** Use `used` when present and `DEFAULT_PROFILE_TOKENS` only when `tokens_used` is missing.
  Size: XS.
- **Verdict.** Follow-up.

### 5. Protected profiles never record the budget, contradicting the docs and the changelog (newly introduced claim; pre-existing projection)

- **Trigger.** A store with a privacy policy calls `consolidate_knowledge(max_profile_tokens=4000)`.
- **Mechanism.** `admit_generated_node` -> `project_generated_node` rebuilds metadata from a closed allowlist
  (`src/prme/privacy/journals.py:90-119`; the profile keys are only `source_count_available`, `source_count_included`,
  `tokens_used`), so `max_profile_tokens` is dropped before the profile is stored. The claim in `CHANGELOG.md:12`,
  `docs/ENTITY-PROFILES.md:57-61`, `docs/PACKING.md:504-511` and `docs/RFC-0015-Self-Organizing-Memory.md:364-369` that
  "a profile records the budget it was built with" is false for every protected profile.
- **Likelihood.** Low as a runtime fault: both refresh paths refuse an owner under a policy
  (`src/prme/organizer/owner_profiles.py:253`, `:297`), so no protected rebuild happens today.
- **Impact.** No behavior change now; a future protected rebuild path (or a store opened without its privacy ledger)
  silently falls back to `max(500, tokens_used)`, and an operator inspecting metadata finds the key missing.
- **Detection.** Silent.
- **Cheapest mitigation.** Qualify the docs now (one line: the budget is recorded for unprotected profiles; protected
  stores are not refreshed). A real fix needs a `profile_format_version` bump plus a conditional projection, because the
  allowlist is also the re-validation path for already-stored nodes (`src/prme/privacy/journals.py:224-247`): adding a
  required key would make every existing protected profile fail to project. Size: XS (docs) / M (projection).
- **Verdict.** Follow-up (docs), Accept (projection).

### 6. A zero recorded budget makes every pass retry forever without publishing (newly introduced hardening gap)

- **Trigger.** A profile node carries `max_profile_tokens: 0` (not writable by today's code, since a 0-budget build
  publishes nothing, `src/prme/storage/engine.py:4834-4835`; reachable through an importer, a hand-written node, or a
  future writer that stores a config floor).
- **Mechanism.** `_profile_budget` accepts `budget >= 0` (`src/prme/organizer/owner_profiles.py:87`); `build_profile`
  returns `None`, `_rebuild` reports `{"published": False}` while the stale profile stays (`:238`), and the next pass
  sees it stale again.
- **Likelihood.** Very low today.
- **Impact.** An unbounded retry of a full namespace scan on every opportunistic pass for that owner.
- **Detection.** Quiet: repeated `profiles_rebuilt` entries with `published: False`.
- **Cheapest mitigation.** Require `budget > 0` and treat 0 as unknown. Size: XS.
- **Verdict.** Follow-up.

## Attacked hardest and why each failed

1. **"A legacy rebuild at `tokens_used` drops a quoted source."** Failed in the ordinary case: `build_profile` is fully
   deterministic for an unchanged tokenizer (`src/prme/organizer/profiles.py:98-114`), and `tokens_used` is the exact
   count of the text it produced (`:114`), so re-running at exactly that budget reproduces the same selection. Only the
   tokenizer-change corner (scenario 3) survives.
2. **"A zero or malformed budget destroys the previous profile."** Failed: type checks reject `bool`, floats and
   negatives (`src/prme/organizer/owner_profiles.py:86-91`); a 0 budget returns `None` from `build_profile` before any
   publication (`src/prme/storage/engine.py:4834-4835`), so the prior profile is preserved; only the useless retry in
   scenario 6 remains.
3. **"A sibling entry point rebuilds without the recorded budget."** Failed: both refresh callers pass the replaced
   profile (`src/prme/organizer/owner_profiles.py:276-278`, `:319-321`); `resume_profile` republishes an
   already-prepared plan whose node carries its own budget (`src/prme/storage/engine.py:4962-4972`); an explicit
   `consolidate_knowledge` takes the caller's budget by definition; the privacy migration writes protected profiles,
   which the refresh skips.

## Pre-mortem headline

No plausible incident in a default deployment (the option is off); the likely postmortem is "an app that had built a
4000-token profile kept getting a stale ~3000-token one after going live with `enable_owner_profile_context`, because
the first refresh recorded its usage as the budget and the truncation guard then froze it".

Status: COMPLETE
