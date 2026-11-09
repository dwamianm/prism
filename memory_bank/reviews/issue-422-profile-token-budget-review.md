# Review: issue #422, a knowledge profile keeps its token budget

## Review Scope

- Base commit: `404993f528a4e2987b00f15436b583b721b296cb` (`main`).
- Round 1 snapshot: `84d62853cb28868fa2a295fa7d9adc5f8754803b858e6b4d313064d33e0fde33`.
- Round 2 snapshot: `1e383082b45f76d898b9cf9f76e1626ced6cb8bbeca920f7bad4158fc8bdf499`.
- Final snapshot: `a0da8453a439819d25ae90988ac2d7450070ff31ed26b4c9352f23857d8c9ebc` (round 2 plus documentation strings only: `docs/PACKING.md`, `CHANGELOG.md`, and the `enable_owner_profile_context` Field description; no behavioral code changed).
- Tier: L. Predicates: `DATA`, `PERSISTED_FIELD`, `NEW_SYMBOLS`; `INFRA` false. Agent 2 (security) is required for `DATA`. Merged checklists 3, 4, 5 into the core reviewer. Checklist 6 (infrastructure/ops) not applicable: no new wiring, config, route or deployment surface.
- Required reviewers and reports, all read and verified for checklist and snapshot:
  - Core (1, 3, 4, 5): `issue-422-profile-token-budget-core.md`, `issue-422-profile-token-budget-core-round2.md`.
  - Security (2): `issue-422-profile-token-budget-security.md`, `issue-422-profile-token-budget-security-round2.md`.
  - Data (7): `issue-422-profile-token-budget-data.md`, `issue-422-profile-token-budget-data-round2.md`.
  - Adversarial (8): `issue-422-profile-token-budget-adversarial.md`, `issue-422-profile-token-budget-adversarial-round2.md`.
- Acceptance criteria for this PR: (1) a rebuilt profile keeps the token budget it was built with; (2) a profile written before the budget was recorded does not lose sources it already quotes under the same packing tokenizer; (3) profile ordering, the `enable_owner_profile_context` default and retrieval receipts are unchanged; (4) tests cover the rebuild behavior. The ordering policy (oldest first, current claims, compact facts) stays open, as the issue states.

## Files Changed

- `src/prme/config.py`: module constant `DEFAULT_MAX_PROFILE_TOKENS = 500`, a single source for the API default and the organizer fallback; `enable_owner_profile_context` description notes the recorded budget.
- `src/prme/storage/engine.py`: `consolidate_knowledge()` records `max_profile_tokens` in profile metadata.
- `src/prme/client.py`: the sync `consolidate_knowledge()` uses the shared default.
- `src/prme/organizer/owner_profiles.py`: `_profile_budget()` resolves the rebuild budget from the replaced profile; `_rebuild()` takes the replaced profile as a required argument and passes the budget; both refresh paths pass it.
- `tests/test_owner_profile_context.py`: a rebuild keeps a large recorded budget through the organizer pass; an `end_session()` rebuild keeps it; the fallback helper cases.
- `CHANGELOG.md`, `docs/ENTITY-PROFILES.md`, `docs/PACKING.md`, `docs/RFC-0015-Self-Organizing-Memory.md`, `documentation/configuration.md`: describe the recorded budget, the legacy fallback and the privacy-policy limitation.

## Approach Summary

`consolidate_knowledge()` already stored `tokens_used` and `tokenizer` on a profile but not the budget it was built with. The two refresh paths (`end_session()` and the organizer's stale-profile pass) called it without `max_profile_tokens`, so every rebuild fell back to 500 and silently dropped sources a larger build had included. The profile now records `max_profile_tokens`; `_profile_budget()` reads it from the profile being replaced and `_rebuild()` passes it. A profile written before the key existed falls back to `max(500, tokens_used)`, which is a lower bound on the lost budget and reproduces at least the sources the profile quotes under an unchanged tokenizer. A recorded zero, a bool, a float or a negative value is treated as unknown. Ordering, the option default and receipts are untouched.

## Must Fix

None.

## Should Fix

None outstanding. The round-1 findings were resolved before round 2:

- Core F1 (duplicate default): one constant in `src/prme/config.py`, used by the engine, the client and the organizer; the test asserts both signatures use it.
- Core F2 (`_rebuild` default): the `profile` parameter is required; both call sites pass it.
- Core F3 (missing caller coverage): an `end_session()` test now drives the refresh path.
- Core F4 (overclaiming docstring and docs): softened, and qualified for the legacy fallback and the tokenizer.
- Adversarial S6 (zero budget retried forever): the recorded budget must be positive.
- Security O1 and Data LOW-1 (docs claim the budget is always recorded): docs and the CHANGELOG now qualify the privacy-policy projection.
- Core post-round-2 nit (`docs/PACKING.md` "within 500 tokens"): now "within its token budget (500 tokens by default)".

## Consider

- Detection is still weak: a `profiles_rebuilt` entry carries the person, the scope and whether a profile was published, but not the budget used, whether it was recorded or inferred, or whether the build truncated. Scenarios 1, 2, 3, 5 and 6 below are invisible in a result object. Recorded for follow-up with the ceiling work.
- A prepared profile plan created before the upgrade is not reused after it, because `profile_request_hash` includes node metadata. One extra embedding is spent and the old plan is abandoned as a replaced preparation. Accepted: it is one-time, internal and safe.
- A recorded budget is not cross-checked against `tokens_used`. Not reachable through the engine (a build records exactly the budget it used, and a zero-budget build publishes nothing). Accepted.

## Security Audit Results

| Area | Result | Details |
| --- | --- | --- |
| Secrets or PII in logs or responses | PASS | The new value is a caller-supplied integer. No source text is logged. |
| Authorization and ownership | PASS | The budget is read from an owner-scoped profile and applied only to the same owner and scope; both refresh paths refuse a policy owner first. |
| Input validation | PASS | Write and read require a strict int; the read side now requires a positive value and rejects bool, float, string and negative values. No upper bound; see follow-up B. |
| Unsafe deserialization | PASS | Plain dict access; no dynamic evaluation. |
| Protected allowlist | PASS | The privacy projection still drops the new key, so no new field reaches a protected read, snapshot or receipt, and checksum consistency is preserved because the projection is applied before the request hash. |
| Overly permissive surface | PASS | No new route, tool, flag or env read; the option stays off. |
| Credentials in code or tests | PASS | None. |

Observations O2 (no ceiling on a reused budget) and O3 (a directly written profile can select its own budget) are low, self-scoped and non-blocking; O2 is follow-up B. O3 adds no cross-tenant authority: the same caller can pass the budget explicitly.

## Pattern Consistency Assessment

The fix follows the profile metadata pattern already used for `tokens_used`, `tokenizer` and the source counters. The default now lives beside `PRMEConfig` and is imported by the engine, the client and the organizer, matching how other cross-module defaults are shared. The refresh helper follows the module's existing style: a pure function, type-checked metadata, and docstrings that name the issue. The new tests use the file's existing mock providers and tmp-path packs. No structural divergence found in round 2.

## Redundancy Check

No duplicate helper or constant remains. `_profile_budget` has no existing equivalent; a repository search finds only the engine and client parameters, the new metadata key and the tests. The shared constant replaced the duplicate. No dead code, no new dependency.

## Wiring Findings

- Writer: `src/prme/storage/engine.py` at the profile metadata construction.
- Reader: `src/prme/organizer/owner_profiles.py` `_profile_budget`, called by `_rebuild` from both `refresh_stale_profiles` and `refresh_owner_profiles`.
- No config or env value is read at runtime by the new code; no registration step is needed.
- The config field description and `documentation/configuration.md` now agree about the recorded budget.
- No schema migration is needed: node metadata is a generic JSON column in both backends.

## Data Safety and Read/Write Proof

- New durable field: `max_profile_tokens` in profile node metadata. Writer: `consolidate_knowledge()` at `src/prme/storage/engine.py` (the metadata dictionary that already carries `tokens_used`). Reader: `_profile_budget()` in `src/prme/organizer/owner_profiles.py`, used by both refresh paths. Round-2 data and adversarial reviews each cited both locations.
- Existing records: a profile without the key is read safely and falls back to `max(500, tokens_used)`; a v1 profile without `tokens_used` falls back to 500, which matches the previous behavior.
- No migration or backfill. The key is additive; `profile_format_version` stays 2.
- Retry and replay: within a version the budget is deterministic, so retries are stable. A pre-upgrade prepared plan is not reused because the request hash covers node metadata; it is abandoned safely and costs one embedding.
- Existing records are preserved; no deletion or rewrite of source records. Profile replacement archives the prior profile as before.

## Break Scenarios (adversarial)

| # | Trigger | Mechanism (file:line) | Likelihood | Impact | Detection | Cheapest mitigation | Label | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | A legacy profile was built with 4000 tokens and has spare room; the option is turned on and a new mention arrives | `_profile_budget` returns `max(500, tokens_used)` and the replacement records it (`owner_profiles.py:85-87`, `engine.py:4868`); the new source no longer fits, `included < available`, and `_is_stale` freezes the profile (`owner_profiles.py:208-210`) | High for the motivating deployment (every stored profile is legacy; the option must be on) | The profile permanently omits sources the original budget allowed; silent | Quiet; only the counters show it | Do not persist an inferred lower bound as the budget: mark it inferred, use a configured ceiling, and do not freeze an inferred-budget profile (S-M) | Newly introduced | Follow-up #494 |
| 2 | An application recorded a large budget, then a rebuild runs on the request path | The stored integer is reused unchanged (`owner_profiles.py:83-84,235`); `build_profile` tokenizes a growing candidate per source (`profiles.py:99-107`); deadlines gate only the start (`owner_profiles.py:271-280`) | Medium (the motivating case records a larger budget; a directly written profile can also set one) | Longer `end_session()` and background CPU work; a very large profile exceeds packing anyway | Quiet; no budget in the result | Clamp to a configured ceiling and log the chosen budget and whether it was recorded or inferred (S) | Newly introduced | Follow-up #495 |
| 3 | The app built without speaker turns; the refresh rebuilds with them | `_rebuild` forces `include_speaker_turns=True` (`owner_profiles.py:233-236`); an added older turn can displace a name-matched source under the same budget, then freezes (`profiles.py:99-108`, `owner_profiles.py:208-210`) | Medium for the hosted-chat shape | A previously quoted source silently leaves the profile | Quiet; `source_paths` counters only | Keep the app's speaker-turn choice with the profile, or add headroom and log truncation (S-M) | Newly introduced interaction | Follow-up #496 |
| 4 | A profile is retired, then two new records arrive | Only active profiles are read, so `profile=None` and `_profile_budget(None)` returns 500 (`owner_profiles.py:311-318`); the archived predecessor still holds the budget | Low-Medium | The app's larger budget is silently lost; the new profile can truncate and freeze | Quiet; the new profile records 500 | Read the newest archived or superseded profile of the name and scope and reuse its budget (S) | Newly introduced gap | Follow-up #496 |
| 5 | The packing tokenizer changes after a profile was built | The recorded budget and the legacy `tokens_used` were counted under the old tokenizer; the rebuild recounts under the new one (`profiles.py:107`) and can drop a source, then freezes | Low | A quoted source vanishes and cannot return through the refresh | Quiet | Treat the budget as unknown when the recorded tokenizer differs, or skip the rebuild (S); docs now say "under the same packing tokenizer" | Newly introduced | Follow-up #494 |
| 6 | A legacy profile was built with a sub-500 budget | `max(500, tokens_used)` inflates it to 500 and records that (`owner_profiles.py:85-87`) | Low (legacy only) | The profile can double; it is the reserved owner-profile slot | Quiet | Coherent with scenario 1: a configured policy for inferred budgets (S) | Newly introduced, legacy-only | Follow-up #494 |
| 7 | A store with a privacy policy builds a profile | The journal projection keeps only the source counts and tokens (`privacy/journals.py:111-119`), so the key is dropped; both refresh paths refuse a policy owner (`owner_profiles.py:255,299`) | Low as a runtime fault | No behavior change today; metadata inspection finds no key | Silent | Docs now qualified; a real fix needs a `profile_format_version` bump and a conditional allowlist (M) | Pre-existing projection | Accept |

Adjudication notes:

- Scenario 1 is the strongest finding. It is newly introduced by the fallback choice, but it is strictly better than the previous rebuild at 500, and the original budget is not recoverable from data that was never written. Closing it soundly needs a policy for inferred budgets and a change to the `_is_stale` truncation guard, which belongs to the #398 staleness design, not this bounded defect. Follow-up #494, and the PR says the ordering and budget policy stays open.
- Scenario 2 does not add authority: the caller can already pass the budget explicitly on every build. A ceiling is a new configuration decision and a product call. Follow-up #495.
- Scenario 3 follows from `include_speaker_turns=True`, which the refresh has forced since #398; this change improves the outcome by keeping a larger budget instead of 500. Follow-up #496.
- Scenario 4 needs a policy for inheriting a retired profile's budget. Follow-up #496.
- Scenario 5 needs a tokenizer-compatibility decision. The documentation claim is now qualified; Follow-up #494.
- Scenario 6 conflicts with scenario 1's headroom. The coherent fix for both is one configured inferred-budget policy. Follow-up #494.
- Scenario 7 is accepted: no runtime effect now, and a real fix would change the privacy allowlist and the format version.

Pre-mortem headline (agent 8, verbatim): "Two weeks out, the incident is a store that turned `enable_owner_profile_context` on and found each existing 4000-token profile ratcheted to its old usage on the first new mention and then frozen by the truncation guard, so the owner's profile silently stopped tracking new records and nothing logged the budget it had been rebuilt at."

## Follow-ups Raised

1. #494: choose the rebuild budget for a legacy profile and stop the truncation guard freezing an inferred-budget profile. Covers scenarios 1, 5 and 6, Data INFO-1 and Data LOW-3.
2. #495: bound a recorded profile budget before the automatic rebuild paths reuse it, and log the budget chosen. Covers scenario 2, Security O2 and O3, and Data INFO-2.
3. #496: keep a profile's recorded budget across source-set changes (speaker turns) and a rebuild from scratch. Covers scenarios 3 and 4.

## Resolution Status

| Item | Source | Severity | Status |
| --- | --- | --- | --- |
| Duplicate default constant | Core F1 | Low | Resolved: one constant in `src/prme/config.py` |
| `_rebuild` silent default | Core F2 | Low | Resolved: required parameter |
| Missing `end_session` coverage | Core F3 | Low | Resolved: new test |
| Overclaiming docstring and docs | Core F4 | Low | Resolved |
| Zero-budget retry loop | Adversarial S6 | Low | Resolved: positive-budget guard |
| Docs claim for protected profiles | Security O1, Data LOW-1 | Low | Resolved: docs qualified |
| PACKING "within 500 tokens" | Core round-2 nit | Nit | Resolved |
| Legacy budget ratchet and freeze | Adversarial 1 | Medium | Follow-up #494 |
| Unbounded reused budget | Adversarial 2, Security O2/O3, Data INFO-2 | Low-Medium | Follow-up #495 |
| Speaker-turn source-set drift | Adversarial 3 | Medium | Follow-up #496 |
| Budget lost on retirement | Adversarial 4 | Low-Medium | Follow-up #496 |
| Tokenizer change | Adversarial 5, Data INFO-1 | Low | Follow-up #494 (docs qualified) |
| Legacy sub-500 inflation | Adversarial 6 | Low | Follow-up #494 |
| Protected projection drops the key | Adversarial 7 | Low | Accept (no runtime effect) |
| Pre-upgrade prepared plan not reused | Data LOW-2 | Low | Accept (one extra embedding) |
| v1 profile fallback | Data LOW-3 | Low | Accept (same as before; part of A) |
| Recorded budget not cross-checked with `tokens_used` | Data INFO-3 | Info | Accept (not reachable through the engine) |
| Detection carries no budget | Adversarial residual | Low | Follow-up #495 |

Checks on the final source: `uv run --no-sync ruff check` on every changed Python file passed; `uv run --no-sync pytest -q tests/test_owner_profile_context.py tests/test_knowledge_profile_scopes.py tests/test_retired_profile_sources.py` passed (61 passed, 28 skipped); `uv run --no-sync mypy --follow-imports=silent --ignore-missing-imports --strict tests/typing/public_api.py` succeeded. The full suite runs in Phase 5.
