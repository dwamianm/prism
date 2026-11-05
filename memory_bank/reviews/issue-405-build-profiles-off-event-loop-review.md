# Review: issue #405, build profiles off the event loop

## Review Scope

- Base: `main` at `0b146379bba9c3bf94540dcf52ff959d16e6635f`
- Reviewed snapshots: `90ad5ab4…` (first review), `14a6145c…` (re-review after the executor change), final `c4080a2e7e698d37ab2f66860f80800772cf711bb0273da9006ec8b3eafb598e` (re-review Consider items R1, R2 and R4 applied and checked by tests)
- Tier: M (`review-plan`: 3 code files plus CHANGELOG; predicates NEW_SYMBOLS; no DATA, INFRA, SECURITY or PERSISTED_FIELD: the change adds no durable field and does not change what is read or written, only where pure CPU work runs)
- Roster: core reviewer (checklists 1, 3, 4, 5 merged; Sonnet) and adversarial reviewer (checklist 8; Opus). Checklists 2, 6 and 7 not applicable.

## Files Changed

- `src/prme/organizer/profiles.py`: windowed `mentions_entity`; a process-wide two-worker profile executor (`run_profile_work`), reset after a fork
- `src/prme/storage/engine.py`: `consolidate_knowledge` runs the page filter, name detection, mention selection and `build_profile` through `run_profile_work`, and yields between scopes and names
- `tests/test_profile_event_loop.py`: ticker tests for the long-run and long-name inputs, windowed search equivalence fuzz, executor thread, cap, cancellation and fork tests
- `CHANGELOG.md`

## Approach Summary

Measured first: tiktoken releases the GIL in a worker thread (max loop gap 6.5 ms over an 8.2 s encode), Python `re` does not (a 50 ms search in a thread gave a 52 ms loop gap). The tokenizer work moves to worker threads; regex matching also moves there, and each regex call is bounded by searching long text in windows with `pos` and `endpos`, which gives the same result as one search of the whole text.

## Must Fix

None.

## Should Fix

None.

## Consider

| ID | Finding | Resolution |
|---|---|---|
| C1 | Thread hop for every page even with no names or no sources | Fixed: the hop is skipped |
| C2 | A cancelled build's running worker keeps going | Bounded by the dedicated executor (S1/S2) |
| C3 | Protected path's per-name entity lookup stays on the loop | Left: string equality per node, microseconds, with a yield between names |
| C4 | CHANGELOG said no call exceeds 4M characters, not true for longer names | Fixed wording |
| C5 | `_mention_pattern` cache overlaps `re`'s own cache | Kept: harmless, avoids re's key handling per node |
| C6 | 100 ms threshold could flake on CI | Kept the issue's bound; headroom about 15x locally |
| R1 | Pool lock not reinitialised after fork | Fixed: `os.register_at_fork` resets the pool and lock |
| R2 | No cancel test; cap test could read 1 under load | Fixed: cancel test added; cap test uses a barrier |
| R3 | Two-worker cap is process-wide | Intended; per-loop serial before the change |
| R4 | Style (blank lines, long docstring line) | Fixed |
| R5 | Fork test fakes the fork | Kept; it calls the registered reset directly |

## Security Audit Results

Not applicable as an independent checklist (no SECURITY predicate). The core reviewer confirmed the protected-privacy path (`ReadContext`, `AdmittedGraphView`, `admit_generated_node`) is unchanged and still runs on the loop; only pure matching and counting moved.

## Pattern Consistency Assessment

Off-loop CPU work follows the existing `asyncio.to_thread` uses in `engine.py` (evaluate_learning), with a dedicated executor for the reason in S1. Tests reuse the `test_durable_ingestion` fixtures as other profile tests do.

## Redundancy Check

No duplicate helper exists for a bounded profile executor; `prme.storage._threading` handles completion semantics, not pool size.

## Wiring Findings

No new config or environment reads. The sync client's early shutdown hook (`src/prme/client.py:120-145`) still runs before `concurrent.futures` disables submission; the profile executor is a `ThreadPoolExecutor` under the same rule.

## Data Safety and Read/Write Proof

Not applicable: no new durable field, no schema or storage change. Profiles built from one copied pack hash identically before and after the change (content, source ids, token counts, published counts).

## Break Scenarios (adversarial)

Pre-mortem headline: "Profile builds stopped freezing the event loop and started quietly draining the shared thread pool that every tenant's retrieval and DuckDB calls wait on."

| ID | Scenario | Label | Likelihood / Impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| S1 | Concurrent hostile builds fill the loop's default executor used by DuckDB, embedding and retrieval | newly introduced | Medium / High | Fix now | Contained: a two-worker profile executor |
| S2 | A cancelled build leaves running workers; retries stack them | newly introduced | Low-Medium / Medium | Fix now | Same executor: queued steps cancel with the task, running ones are capped at 2 |
| S3 | Longer window for StaleProfileError under concurrent writes | widened, pre-existing mechanism | Medium / Low-Medium | Accept | Documented, retryable, fails closed; the hosted route should map it to 409 and retry |
| S4a | Mention links match names on the loop during ingest | pre-existing | Low / Medium | Follow-up | #413 |
| S4b | Unbounded `max_profile_tokens` makes later on-loop work scale with the profile | pre-existing | Depends on hosted cap / Medium | Follow-up | #414, needs an API decision on the bound |

Tally: Fix now 2, Follow-up 2, Accept 1, Dismissed 0.

## Follow-ups Raised

- #413 Mention links still match names on the event loop during ingest
- #414 A very large max_profile_tokens can still stall the event loop during a profile build

## Resolution Status

| Item | Status |
|---|---|
| Must Fix | none |
| Should Fix | none |
| C1, C4, R1, R2, R4 | resolved |
| S1, S2 | resolved |
| S3 | accepted, noted in the PR |
| S4a, S4b | follow-ups #413, #414 |
