# Adversarial review, round 2: issue #422 (a knowledge profile keeps its token budget)

Status: COMPLETE

Base: `main` at `404993f528a4e2987b00f15436b583b721b296cb`
Final reviewed snapshot: `1e383082b45f76d898b9cf9f76e1626ced6cb8bbeca920f7bad4158fc8bdf499`
Diff: `/tmp/issue-422-review-round2.diff`
Checklist: 8 (adversarial)

## Checklist worked

- Round-1 scenarios S1-S6 re-attacked against the final snapshot: kept those that still hold.
- S6 closure verified at the source (not only in the test).
- New code inspected for defects the fixes introduced: the positive-budget check, the shared
  `DEFAULT_MAX_PROFILE_TOKENS`, the required `_rebuild(profile=...)` parameter, the recorded
  `max_profile_tokens` metadata, and the new tests.
- Callers/callees re-checked: both refresh paths, `consolidate_knowledge`, `build_profile`,
  `_is_stale`, `resume_profile`, privacy projection, packing.
- Adversarial premises: trusted stored metadata, source-set drift between builds, retirement
  and re-creation, tokenizer change, sub-500 caps, protected projection, zero/malformed budget,
  import cycles, metadata roundtrip types.

## Verdict

The bounded fix does what the ticket asks: `consolidate_knowledge` records `max_profile_tokens`
(`src/prme/storage/engine.py:4868`) and both refresh paths now pass the replaced profile's budget
(`src/prme/organizer/owner_profiles.py:235`, `:278-280`, `:321-323`). S6 (a zero recorded budget
retried forever) is closed at the source (`:83`), the shared constant is in place, `_rebuild`'s
required `profile` parameter has exactly two call sites, both updated, and the docs are now
qualified for protected profiles. The residual risk has moved, but it has not gone away: the
fallback for a legacy profile is a *lower bound* that is then persisted as the budget
(`:85-87`) and frozen by the truncation guard (`:208-210`), and the recorded budget is replayed
on automatic paths with no ceiling. One new interaction is worth naming: the reused budget was
sized for the app's source set, while the refresh rebuilds a *larger* set (speaker turns,
`include_speaker_turns=True`), so a rebuild can still drop a source the earlier build quoted.
Everything remains behind the default-off `enable_owner_profile_context`; a default deployment
is unchanged, and the option's own fast-tier evidence does not exercise a rebuild at a
non-default budget.

### 1. A legacy profile's budget ratchets to `tokens_used` on its first rebuild and the truncation guard freezes it there (newly introduced)

- **Trigger.** A store holds a profile built before this release with `max_profile_tokens=4000`
  (every existing profile is legacy: the key is new) and spare room (`tokens_used` about 2100 of
  4000, so `source_count_included == source_count_available`). The app enables
  `enable_owner_profile_context`. A new 700-token record mentions the person.
- **Mechanism.** `_profile_budget` returns `max(500, tokens_used)` = about 2100
  (`src/prme/organizer/owner_profiles.py:85-87`), `_rebuild` passes it (`:235`), and the
  replacement records that value as its budget (`src/prme/storage/engine.py:4868`). The new
  source does not fit under 2100 even though it fit under 4000, so the build leaves it out
  (`src/prme/organizer/profiles.py:99-108`), `source_count_included < source_count_available`,
  and `_is_stale` then returns `False` for every later mention
  (`src/prme/organizer/owner_profiles.py:208-210`). The original 4000 is never seen again.
- **Likelihood.** High for the motivating deployment: it is the first new mention after the
  upgrade, and every profile in the field is legacy. Only the option must be on.
- **Impact.** The owner's profile permanently stops tracking new records and omits sources the
  application's own budget would have included, which is the outcome #422 exists to prevent,
  one level down. Retrieval answers lose the omitted facts, not the profile.
- **Detection.** Quiet. Only `source_count_available > source_count_included` on the stored node
  shows it; nothing logs the budget used (`:240` returns only entity name, scope and published).
- **Cheapest mitigation.** Do not persist an inferred lower bound as if it were the budget: when
  `max_profile_tokens` is absent, mark the profile as inferred (a distinct metadata key) and
  rebuild it at a configured ceiling, or at least add headroom; and do not let the truncation
  guard freeze an inferred-budget profile. Size: S-M.
- **Verdict.** Follow-up.

### 2. A recorded budget is replayed on the automatic paths with no ceiling, and stored metadata now selects it (newly introduced)

- **Trigger.** An application called `consolidate_knowledge(max_profile_tokens=4000)` or much
  larger (the parameter is public on the engine and the client,
  `src/prme/storage/engine.py:4552`, `src/prme/client.py:1110`), or set it once for an ad-hoc
  build. The profile goes stale and the organizer's pass rebuilds it; `end_session` rebuilds it
  inline.
- **Mechanism.** `_profile_budget` returns the stored integer unchanged (`:83-84`) and `_rebuild`
  passes it to `consolidate_knowledge` (`:235`). `build_profile` re-tokenizes the whole
  accumulated text for every candidate (`src/prme/organizer/profiles.py:99-107`), so work grows
  roughly with the square of the number of sources that fit, and the result is embedded in one
  call. The only validation is "nonnegative integer"
  (`src/prme/storage/engine.py:4596-4597`); the opportunistic deadline only gates *starting* a
  rebuild (`src/prme/organizer/owner_profiles.py:271-280`), and `end_session` awaits the refresh
  directly (`src/prme/storage/engine.py:5066-5068`). Before this change the organizer always
  rebuilt at 500. A second input matters: `store()` writes caller metadata straight to the node
  (`src/prme/storage/engine.py` `MemoryNode(..., metadata=metadata)`), so a caller can plant a
  `SUMMARY` whose `metadata.entity_profile` is true and whose `max_profile_tokens` is enormous,
  and the refresh will use it; the pre-existing spoofing of a profile node is not new, but
  controlling the rebuild's budget from stored data is.
- **Likelihood.** Medium: the larger budget is the ticket's motivating case, so the recorded value
  is *by construction* larger than the default on exactly the deployments that enable the option.
  A hostile or careless caller needs one `store()` plus one newer mention.
- **Impact.** `end_session()` blocks for the duration of a large rebuild, the background pass
  spends CPU and thread-pool time on the same event loop that serves requests, and a very large
  profile exceeds the packing budget so the embedding work buys nothing. In a shared store the
  planted-metadata path is a cross-tenant CPU amplifier.
- **Detection.** Quiet. `profiles_rebuilt` (`:240`) carries no budget, no inferred flag and no
  truncation count.
- **Cheapest mitigation.** Clamp the reused budget to a configured ceiling (an organizer/profile
  field, or a hard cap) and log the budget chosen for each rebuild, including whether it was
  recorded or inferred. Reject a stored value above the cap rather than trusting it. Size: S.
- **Verdict.** Follow-up.

### 3. The reused budget was sized for the app's source set, but the refresh rebuilds a larger set, so a rebuild can still drop a source the profile quoted (newly introduced interaction)

- **Trigger.** The app built the profile with the default `include_speaker_turns=False`. The
  person has turns stored with a `speaker` (the hosted-chat shape), and the app's budget is
  nearly full (`source_count_included == source_count_available`). The app enables
  `enable_owner_profile_context` and a newer record mentions the person.
- **Mechanism.** `_rebuild` calls `consolidate_knowledge(... include_speaker_turns=True)`
  (`src/prme/organizer/owner_profiles.py:233-236`), which adds the person's own turns as sources.
  `build_profile` orders oldest first and drops any source that no longer fits
  (`src/prme/organizer/profiles.py:99-108`); an added older turn can consume budget and push out
  a name-matched record that the previous profile quoted. The replacement then has
  `source_count_included < source_count_available` and is frozen
  (`src/prme/organizer/owner_profiles.py:208-210`). The recorded budget is correct for the build
  the app made, but not for the build the refresh makes.
- **Likelihood.** Medium: it is the normal path for any app that ever built a profile explicitly
  (the default is no speaker turns) and later enables the option with a speaker-named owner.
- **Impact.** A source the earlier build quoted silently leaves the profile, contradicting the
  ticket's own wording ("silently dropped sources the earlier build had included"), and the
  profile is then frozen for new mentions.
- **Detection.** Quiet; only the `source_paths`/counters show the reshuffle.
- **Cheapest mitigation.** Size the budget for the rebuild's source set: either keep the app's
  `include_speaker_turns` choice with the recorded profile rather than forcing `True`, or add
  headroom for the speaker-turn delta and log the resulting truncation. Size: S-M.
- **Verdict.** Follow-up.

### 4. The recorded budget is lost when the profile is retired and rebuilt from scratch (newly introduced feature gap)

- **Trigger.** A rebuild finds fewer than two related records and retires the profile
  (`src/prme/storage/engine.py:4825-4828`), or the profile is archived by policy. Later two new
  records mention the person and `end_session` builds a profile again.
- **Mechanism.** `refresh_owner_profiles` looks only at active profiles
  (`src/prme/retrieval/owner_profile.py:80-98`; `query_nodes` defaults to active states) and sets
  `profile = None` when there are none (`src/prme/organizer/owner_profiles.py:311-318`), so
  `_rebuild` calls `_profile_budget(None)` and gets the default 500 (`:88`, `:235`). The archived
  predecessor still holds the app's budget in its metadata but is never read.
- **Likelihood.** Low-Medium: it needs every source retired (or an explicit retirement) and then
  two fresh sources, all while the option is on.
- **Impact.** The app's larger budget is silently gone; the new profile is 500 tokens, can
  truncate immediately and is frozen, and the docs' promise that "a rebuild uses that budget"
  does not hold for a build that has no live predecessor.
- **Detection.** Quiet; the new profile simply records 500.
- **Cheapest mitigation.** When no active profile exists, look up the most recent archived or
  superseded profile of that name and scope (the graph store can read those states) and reuse its
  recorded budget; if none, document the limitation. Size: S.
- **Verdict.** Follow-up (outside the literal ticket scope; the ticket says "reuses it", which
  presupposes a replaced profile).

### 5. A changed `packing.tokenizer` invalidates the reused budget, legacy or recorded (newly introduced)

- **Trigger.** A profile was built under one packing tokenizer and the operator later changes
  `packing.tokenizer` to an encoding that counts more tokens for the same text, then a rebuild
  runs.
- **Mechanism.** `_profile_budget` returns the stored budget without consulting the recorded
  `tokenizer` (`src/prme/organizer/owner_profiles.py:81-88`), while `build_profile` re-counts
  under the *configured* tokenizer (`src/prme/organizer/profiles.py:107`). For a legacy profile
  the fallback `max(500, tokens_used)` is also an old-tokenizer count (`:85-87`). If the same
  selection now exceeds the budget, the tail source is dropped and the replacement records the
  same budget and the new tokenizer, then freezes on the truncation guard (`:208-210`).
- **Likelihood.** Low: changing the packing tokenizer is unusual, and the two encodings often
  differ by only a few percent.
- **Impact.** One or more quoted sources vanish and cannot come back through the refresh path.
  The CHANGELOG wording "does not drop a source it already quotes" (CHANGELOG.md:12) is false in
  this corner.
- **Detection.** Quiet, as above.
- **Cheapest mitigation.** When `metadata["tokenizer"]` differs from
  `engine._config.packing.tokenizer`, treat the budget as unknown (use a ceiling or skip the
  rebuild) rather than replaying an old count; qualify the CHANGELOG sentence. Size: S.
- **Verdict.** Follow-up.

### 6. A legacy profile's deliberate sub-500 budget is inflated to 500 on its first refresh (newly introduced, now legacy-only)

- **Trigger.** An application built a profile with `max_profile_tokens=250` before this release
  (the API and the suite use 250, `tests/test_knowledge_profile_scopes.py:149`, `:185`) and then
  enables `enable_owner_profile_context`.
- **Mechanism.** The old profile has no `max_profile_tokens` key, so `_profile_budget` returns
  `max(500, tokens_used)` (`src/prme/organizer/owner_profiles.py:85-87`) even when `tokens_used`
  is at or below 250, and the replacement records 500 (`src/prme/storage/engine.py:4868`).
  Newly built 250-token profiles are fine: they record 250 and `:83-84` returns it. This is now
  strictly a legacy-profile case.
- **Likelihood.** Low: a sub-500 budget is unusual, and only pre-upgrade profiles are affected.
- **Impact.** The profile can double on the next refresh; it is the reserved `OWNER_PROFILE` slot
  in packing, so it can crowd out ordinary records in a tight context budget.
- **Detection.** Quiet; the recorded `tokens_used` is the only trace.
- **Cheapest mitigation.** Use `used` (not `max(500, used)`) when it is present, and fall back to
  the constant only when `tokens_used` is missing. This conflicts with scenario 1's need for
  headroom, so the coherent fix for both is a configured ceiling for inferred budgets rather
  than a usage-derived value.
- **Verdict.** Follow-up.

### 7. A protected profile still does not record its budget (pre-existing projection; the docs are now qualified)

- **Trigger.** A store with a privacy policy (or an owner with records under one) calls
  `consolidate_knowledge(max_profile_tokens=4000)`.
- **Mechanism.** `admit_generated_node` returns the node projected through
  `project_generated_node`, whose profile allowlist is only `source_count_available`,
  `source_count_included` and `tokens_used` (`src/prme/privacy/journals.py:111-119`), so
  `max_profile_tokens` is dropped before storage. Both refresh paths already refuse an owner
  under a policy (`src/prme/organizer/owner_profiles.py:255`, `:299`), so no protected rebuild
  happens today.
- **Likelihood.** Low as a runtime fault; it matters only to a future protected rebuild path or a
  store opened without its privacy ledger.
- **Impact.** No behavior change now; an operator inspecting metadata finds the key missing, and
  a future protected refresh would fall back to `max(500, tokens_used)`.
- **Detection.** Silent.
- **Cheapest mitigation.** The docs already say this (`docs/ENTITY-PROFILES.md:57-64`,
  `docs/PACKING.md:507-514`, `docs/RFC-0015-Self-Organizing-Memory.md:367-371`). A real fix needs
  a `profile_format_version` bump plus a conditional allowlist, because the same allowlist is the
  re-validation path (`src/prme/privacy/journals.py:224-247`). Size: XS (docs, done) / M
  (projection).
- **Verdict.** Accept (projection), docs fixed.

## S6 closure confirmation

Closed at the source. `_profile_budget` now requires `type(budget) is int and budget > 0`
(`src/prme/organizer/owner_profiles.py:83`), so a stored `0`, a bool, a float, a string or a
negative value falls through to `tokens_used`/the default (`:85-88`) and never yields the
unpublishable zero budget; the unit test pins the zero and bool cases
(`tests/test_owner_profile_context.py:367-369`). The fix did not introduce a new retry path:
a budget this code writes always fitted at least one source when it published, so a rebuild at
that budget publishes again. The only remaining "positive budget that publishes nothing" loop
needs an imported or hand-edited node whose budget is below every individual source; that is the
same non-writable class as round-1 S6 and is not reachable from the engine.

## Attacked hardest and why each failed

1. **"The required `profile` parameter breaks a caller."** Failed: `_rebuild` has exactly two
   call sites (`src/prme/organizer/owner_profiles.py:278-280`, `:321-323`), both updated; no test
   or other module calls it.
2. **"The shared `DEFAULT_MAX_PROFILE_TOKENS` creates an import cycle."** Failed:
   `src/prme/config.py` does not import `prme.organizer`; the constant sits above
   `PRMEConfig` and is imported by `client.py`, `engine.py` and `owner_profiles.py`.
3. **"Recording `max_profile_tokens` breaks protected-profile projection or validation."**
   Failed: `admit_generated_node` returns the already-projected node
   (`src/prme/privacy/journals.py:327-329`), so the key is stripped before `profile_request_hash`
   and before `validate_generated_publication` compares checksums; existing protected profiles
   still project because the allowlist does not require the key.
4. **"A malformed or zero budget destroys the previous profile."** Failed: the type/positive
   checks (`:83`) and `build_profile`'s `None` return before publication
   (`src/prme/storage/engine.py:4834-4835`) leave the previous profile in place. The residual is
   only the imported-node retry loop in the confirmation above.
5. **"A sibling entry point rebuilds without the recorded budget."** Failed: both refresh paths
   pass the replaced profile; `resume_profile` republishes a prepared plan whose node carries its
   own metadata (`src/prme/storage/engine.py:4962-4972`); an explicit `consolidate_knowledge`
   takes the caller's budget by definition; the protected path is skipped.
6. **"The new key changes retrieval, receipts or determinism."** Failed: only the node metadata
   and internal request hashes change; profile content, ranking, packing and receipts are
   untouched, and no test asserts an exact profile metadata mapping.

## Residual notes

- Detection remains the weak half of the ticket: `profiles_rebuilt` entries
  (`src/prme/organizer/owner_profiles.py:240`) still carry no budget, no "inferred" flag and no
  truncation count, so scenarios 1, 2, 3, 5 and 6 are all invisible in a result object.
- The `enable_owner_profile_context` field description (`src/prme/config.py:1305-1339`) was not
  updated although `documentation/configuration.md:148` was; the hypothesis audit reads the field
  description, so the two now say different things about the rebuild budget.
- Round-1 scenarios that no longer hold as written: S6 (closed), and S5's "the docs overclaim"
  (docs are qualified); S4 is now legacy-only.

## Pre-mortem headline

Two weeks out, the incident is a store that turned `enable_owner_profile_context` on and found
each existing 4000-token profile ratcheted to its old usage on the first new mention and then
frozen by the truncation guard, so the owner's profile silently stopped tracking new records and
nothing logged the budget it had been rebuilt at.
