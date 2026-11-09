# Core review: issue #413, mention links off the caller's event loop

Status: COMPLETE

- Reviewed snapshot: `38f98c59fccd01f4d67323c3f2d8e1775919ef13d4b1c4ab50d56143c01fb932`
- Re-reviewed snapshot (addendum): `ea21588b3f5192888c9d92475dbf2826263e4b93e0283719056d04ad9d6a6296`
- Base commit: `deb11a68b73f299d847efabf0e27195fb433dceb` (branch `main`)
- Working branch: `issue-413-mention-links-off-event-loop` (changes uncommitted)
- Changed files: `src/prme/ingestion/mention_links.py`, `tests/test_mention_links.py`, `CHANGELOG.md`
- Checklist: (1) correctness and logic, including concurrency of the shared 2-thread pool; (3) code quality; (4) pattern consistency with the #405 analogue; (5) redundancy and simpler mechanisms; plus security and runtime wiring.

## Verdict

The change holds up. It does exactly what the issue asks: every `mentions_entity()` call in `link_event_mentions()` now runs on the shared `prme-profile` worker threads through `run_profile_work`, the outer per-source loop stays on the event loop, and the edge writes stay on the loop. The matching predicate is a literal De Morgan equivalent of the old loop, iteration order is preserved, and the edge ids and metadata are unchanged. No Must Fix or Should Fix findings. Two Consider items about the new test, both resolved in the addendum snapshot.

## 1. Correctness and logic

- Semantics preserved. Old `for name, entity in targets: if (source.id, entity.id) in linked or (entity.id, source.id) in linked: continue; if not any(...): continue` becomes the comprehension at `src/prme/ingestion/mention_links.py:110-114` with `not in linked and not in linked` plus the same `any(isinstance(text, str) and mentions_entity(text, name) ...)`. Same predicate, same `targets` order, same list of survivors, so `created` and every `MemoryEdge` (id via `uuid5(UUID(event_id), f"prme:text-link:{source.id}:{entity.id}")`, `source_id`, `target_id`, metadata) are identical (`mention_links.py:125-131`). No `DERIVATION_VERSION` bump is needed, which matches `CHANGELOG.md:12` ("The links created, their ids and their metadata are unchanged").
- Shared mutable state: `linked` (`mention_links.py:116-119`) is built once and never mutated afterwards, and `targets`/`texts` are read-only in the worker. Nothing is written from a non-loop thread. The worker receives only `source.id`, `texts` and `linked` (`mention_links.py:125`), and the closure captures only `targets`.
- `targets` holds `MemoryNode` objects read from another thread. They are only attribute-read (`name`, `entity.id`); `MemoryNode` is `frozen=False` but nothing in this coroutine mutates it, and the node was already materialized by `query_nodes` (`mention_links.py:91-95`). No cross-thread mutation.
- GIL: the design is sound because `profiles.mentions_entity` windows every regex call to about `_MATCH_WORK` (`profiles.py:55-82`), so the worker holds the GIL only in short stretches and the loop thread gets it back between windows; the worker thread also yields between `pattern.search` calls. The comment at `mention_links.py:107-109` and the docstring at `profiles.py:47-50` say the same thing.
- Cancellation: `link_event_mentions` awaits `run_profile_work` (`mention_links.py:125`). A cancel while queued drops the step (the #405 behavior in `tests/test_profile_event_loop.py:147-163`); a cancel while running discards the result. `asyncio.CancelledError` is a `BaseException`, so the `except Exception` at `mention_links.py:141-142` does not swallow it, and cancellation still propagates as before. No durable effect is lost: matching writes nothing and edge writes are best effort.
- Duplicate-link race: the matching phase now yields, so the window between building `linked` and writing an edge (`mention_links.py:116` to `:133`) is wider, and two concurrent link passes for one event (note materialization and derivation, `engine.py:2223`, `pipeline.py:2029`, `reingest.py:539`) can interleave more. This is already handled by the fixed edge id plus the re-query in the `except` branch (`mention_links.py:132-139`); no new outcome. Not a finding.
- None/empty edge cases unchanged: `with_note`/`note_id` (`:69-70`), missing extraction (`:77-80`), empty `listed` (`:87-88`), empty `targets` (`:101-102`), non-str `metadata["object"]` (`:124`, guarded by `isinstance` at `:113`).
- Return-value sentinel check on utilities called from the changed lines: `mentions_entity` returns `bool` and is consumed by `any()`; `run_profile_work` returns the helper's list, consumed by the `for` loop; no `None`-sentinel path is introduced. `ACTIVE_LIFECYCLE_STATES` membership (`:73`, `types.py:284-288`) is unchanged. Clean.
- Type safety: the helper is a plain `Callable[P, T]` argument to `run_profile_work` (`profiles.py:46-52`); no type change on the public signature. Ruff passed per the sweep.

## 3. Code quality

- The nested helper is small, named for what it does, and carries a comment that explains the GIL reasoning (`mention_links.py:106-114`). The outer loop comment is still accurate.
- Error handling is unchanged: worker exceptions propagate through the future into the existing `except Exception` at `mention_links.py:141-142`; best-effort behavior is preserved.
- Performance in the hot loop: same total work; one executor submission per source instead of inline calls. Typical sources per event are few (a plan's claims), so the scheduling overhead is negligible. With many sources the per-source await adds wall-clock latency only when the two profile workers are busy with an organizer profile build; it does not block the loop. Not a defect.
- Test coverage: the new ticker test fails on the pre-fix tree and passes after (sweep evidence), and the existing behavioral tests still pin the links created (`tests/test_mention_links.py:131-145`, `:148-175`, `:178-195`). The new test uses no `config`/`user` fixture, so it runs in both the DuckDB and PostgreSQL CI matrices.
- The measured window included the lazy import at `mention_links.py:104` and first-use executor thread creation (see C2, resolved in the addendum).

## 4. Pattern consistency with the #405 analogue

- Executor: uses the same shared, fork-reset `_profile_executor()` with two workers (`profiles.py:23-52`), as `engine.consolidate_knowledge` does at `engine.py:4592`, `:4694`, `:4732`, `:4771-4772`, `:4829` and `owner_profiles.py:130-142`.
- Nested pure-function-plus-`run_profile_work` shape: matches `engine.consolidate_knowledge`'s `mentioning`/`naming`/`said_by` (`engine.py:4638-4648`) and `owner_profiles._mentioning`'s `matching` (`owner_profiles.py:130-142`).
- Fork handling: inherited unchanged from `profiles.py:36-43`.
- Yielding: `consolidate_knowledge` yields between scopes with `await asyncio.sleep(0)` (`engine.py:4616-4617`); this change yields between sources by awaiting the executor, which is the stronger yield. Consistent.
- Cancellation: the same executor semantics as #405, verified above.
- Deferred import inside the function is the pre-existing pattern (`mention_links.py:104`); the module set imported is the same as before, so no new import cost from the production change.

## 5. Redundancy and unnecessary code

- The production helper `matching` is not redundant with anything existing: `owner_profiles._mentioning`'s `matching`, `engine`'s `mentioning`/`naming`/`said_by`, and `group_links` operate on different inputs and outputs. No simpler mechanism is available that respects the #405 rule; `asyncio.to_thread` would use the default executor that `profiles.py:19-22` deliberately avoids, and the issue itself suggests `run_profile_work`.
- No dead code or unused imports in the changed source; `tests/test_mention_links.py` imports (`uuid4`, `MemoryNode`, `ExtractionRecord`, `LifecycleState`, `Scope`, and the three `tests.test_profile_event_loop` helpers) are all used (ruff passed per the sweep; re-checked by source read in the addendum).

## Security and runtime wiring

- Ownership scoping unchanged: the entity lookup is still `graph_store.query_nodes(node_type=NodeType.ENTITY, user_id=user_id, scope=extraction.scope, ..., limit=500)` (`mention_links.py:91-94`) and `linked` is still read with `node_ids` of the event's own sources (`:116-119`). The change moves only CPU work; it reads no env/config and makes no network call (sweep, confirmed by reading the file).
- Which thread performs writes: the edge writes remain `await create_edge(edge)` on the loop (`mention_links.py:133`), and the callers' `create_edge` still routes through the engine `WriteQueueGraphWriter`/write queue (`pipeline.py:2031-2034`, `reingest.py:526-535`, `engine.py:2223-2228`). No graph write happens on a profile worker.
- Data exposure unchanged: the same edges with the same `user_id` and `TEXT_LINK_METADATA` are created.

## Findings

Locations in this table are the original reviewed snapshot's line numbers; both items are resolved in the addendum `ea21588b`.

| # | Class | Location (original snapshot) | Finding |
|---|-------|----------|---------|
| C1 | Consider | `tests/test_mention_links.py:355-382` vs `tests/test_profile_event_loop.py:20`, `:23-50` | The new test re-implemented the ticker, the `MAX_GAP = 0.1` bound and the `measurable()` skip instead of reusing `longest_gap`/`measurable` from the #405 test. `longest_gap(link)` works directly because it awaits its argument, so `result, elapsed, gap = await longest_gap(link)` would replace the whole block. Importing `tests.test_profile_event_loop` would also warm `prme.organizer.profiles`, removing C2. |
| C2 | Consider | `tests/test_mention_links.py:350-374` and `src/prme/ingestion/mention_links.py:104` | The timed window included the lazy `from prme.organizer.profiles import ...` at `mention_links.py:104` and first-use creation of the two `prme-profile` threads. That module is not loaded by `import prme` (its only other call sites are lazy: `engine.py:4592`, `owner_profiles.py:36`, `maintenance.py:193`), so on an xdist worker where this test ran first, the import ran on the loop inside the measured region. Risk was low (small module bodies, most dependencies already loaded), but a cold import near 0.1 s would trip the assert. Warming the module in the test (or taking C1) removes it. |

No Must Fix. No Should Fix. Both Consider items are resolved in the addendum snapshot.

## Evidence and validation notes

- Source was verified independently; the sweep's `pytest tests/test_mention_links.py` (17 passed, 14 skipped) and `ruff` results are consistent with the source read.
- The change is order- and predicate-equivalent, so the pinned derivation fixture's `mentions` edges (`tests/fixtures/derivation/trip_derived.json:67` onward) should be unchanged by `tests/test_derivation_version.py`. That test was not in the sweep; CI's full suite (`ci.yml:67`) runs it, and no `DERIVATION_VERSION` bump is required because no derived record changes.
- Timing tests of this shape already run in the parallel job (`ci.yml:30`, `-n auto --dist worksteal`) as `tests/test_profile_event_loop.py`; C2 was the only test-specific robustness caveat.

## Addendum: re-review of snapshot `ea21588b`

- New snapshot: `ea21588b3f5192888c9d92475dbf2826263e4b93e0283719056d04ad9d6a6296` (working tree). Only `tests/test_mention_links.py` changed (blob `8d672ce8`); `src/prme/ingestion/mention_links.py` (blob `20b7f046`) and `CHANGELOG.md` (blob `c2c705d7`) are byte-identical to the reviewed snapshot, matching the diff indices `a5c225e4..20b7f046` and `8b1786de..c2c705d7` seen in the first review.
- Delta: `tests/test_mention_links.py:3-20` adds `from tests.test_profile_event_loop import MAX_GAP, longest_gap, measurable` and drops `import time`; the new test at `:318-360` replaces the local ticker block with `created, elapsed, gap = await longest_gap(link)` (line 355) and then runs the same assertions with `measurable(elapsed)` and `MAX_GAP` (lines 358-360).
- C1 resolved: the ticker, the 0.1 s bound and the skip helper are now the single #405 implementation (`tests/test_profile_event_loop.py:20`, `:23-50`). `longest_gap(link)` awaits the coroutine the same way and returns `(created, elapsed, max(gaps))`, so the unpack and both assertions are unchanged; `max(gaps)` is never empty because the ticker runs during `longest_gap`'s initial `await asyncio.sleep(0.02)` before it awaits the build (`tests/test_profile_event_loop.py:36-44`).
- C2 resolved: importing `tests.test_profile_event_loop` imports `prme.organizer` and `prme.organizer.profiles` at module import time (`tests/test_profile_event_loop.py:11-12`), so the lazy import at `src/prme/ingestion/mention_links.py:104` and the profile-worker thread creation now happen before the timed region rather than inside it.
- No new finding: the added import has no collection side effect (that module's body is imports, `MAX_GAP = 0.1`, function definitions and the `config`/`user` aliases); there is no circular import (both files import `tests.test_durable_ingestion`, neither imports the other back); importing a test module does not duplicate its tests; the assertion order is unchanged; `asyncio` remains used elsewhere in the file (lines 70, 100) and no import is left unused, so the earlier ruff result should stand (this delta was verified by source read; test execution remains the worker's).
- Addendum verdict: both Consider items are resolved and the delta introduces no Must Fix, Should Fix or new Consider finding. The core verdict is unchanged.

Status: COMPLETE
