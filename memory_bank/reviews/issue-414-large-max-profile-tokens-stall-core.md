Status: COMPLETE

# Core review: dwamianm/prism#414, large `max_profile_tokens` event-loop stall

- Branch: `issue-414-large-max-profile-tokens-stall` (working tree, uncommitted)
- Base: `main` at `deb11a68b73f299d847efabf0e27195fb433dceb`
- Reviewed snapshot: `2c646c32c9bf1f36f3b2427ad11d6f97b47f55157d347e713274f090b0f4b64e`
- Re-reviewed snapshot: `acb59ca9dad6f658893fc2adcc6c7d8d1446e7c19eb5027f54122e4944ce513a`
- Tier: L (DATA, PERSISTED_FIELD, NEW_SYMBOLS)
- Checklists: 1 (correctness/logic, concurrency, sentinels, breaking changes),
  3 (quality/test coverage/performance), 4 (pattern consistency/DRY),
  5 (redundancy/new symbols)
- Method: read the full diff, then the changed functions in their actual files
  (`profile_work.py`, `profile_publication.py`, `lexical_index.py`,
  `vector_index.py`, `engine.py`, `models/profile.py`, `organizer/profiles.py`,
  `_threading.py`, `write_queue.py`) and their callers. Ran four read-only
  measurements with the project venv (pydantic 2.12.5) for the pydantic
  equality and GIL questions; no source was modified and no suite was run.

## Acceptance criteria

| Criterion | Result |
|---|---|
| A very large `max_profile_tokens` must not stall the shared event loop during a profile build | **Not met at scale.** DuckDB: the work is off the loop thread, but the moved work still holds the GIL, so a large profile still blocks the loop (F2: 62 ms at an 80 MB profile, and the per-call stall passes the project's 0.1 s `MAX_GAP` at roughly 120-130 MB). PostgreSQL: whole-plan serializations and checksums still execute on the loop in the first reviewed snapshot (F1, resolved in the re-review). |
| Every value accepted before is still accepted (no new bound, no public API break) | Met. No new validation or bound in the diff; `consolidate_knowledge(..., max_profile_tokens=...)` is untouched; `ProfilePublication.request_hash` is still a `str` property; private attrs are absent from `model_fields` and the JSON schema. |
| The profile a given store builds must be unchanged | Met. `profile_request_hash` (`src/prme/models/profile.py:41-56`) and `checksum` (`:131-132`) are byte-for-byte unaffected: pydantic private attrs are excluded from `model_dump`/`model_dump_json`, so the persisted `{"checksum", "publication"}` payload is identical. Verified empirically with pydantic 2.12.5. |
| `DERIVATION_VERSION` must not change | Met. `src/prme/ingestion/reingest.py:89` is still `DERIVATION_VERSION = 1`; no `src/prme/ingestion/` or `tests/fixtures/derivation/` diff. |

## Findings

### F1 (blocking for PostgreSQL-backed stores): the Postgres prepare/publish path still serializes and checksums the whole plan on the event loop

Resolved in the re-review; see the Re-review section. This is the original
finding against snapshot `2c646c32`.

The diff moves only `commit_postgres`'s `copy_plan` off the loop
(`src/prme/storage/profile_publication.py:211`). The rest of the Postgres
commit, which is the same O(profile-text) work the ticket names, still runs
synchronously inside the coroutine on the caller's loop:

- line 232: `validate_pg_work` (`src/prme/storage/profile_work.py:476-494`)
  always fetches the prepared `payload` and hands it to `validate_work_row`
  (`:419-442`), whose line 436 does `unpack(saved, ...)` (full parse,
  `profile_work.py:59-66`) plus `saved.checksum != plan.checksum` (two full
  node/source dumps). This runs on every first-time publish of a managed
  preparation.
- line 250: `_validate(plan, current, ...)` calls `node_checksum(expected)`
  for every source and previous profile (`profile_publication.py:57`), and
  `node_checksum` dumps and `json.dumps` each node
  (`src/prme/models/derivation.py:20-23, 34-40`), i.e. the whole source text.
- line 281: `_payload(plan)` (`:31-35`) computes `plan.checksum` and
  `plan.model_dump_json()` on the loop.
- line 228: `_replayed(plan, value)` on the replay path calls
  `ProfilePublication.model_validate_json(payload["publication"])`
  (`:42`) and computes two more `checksum`s (`:43`).
- The same pattern exists in `ProfileWorkStore._prepare_pg`
  (`src/prme/storage/profile_work.py:261-335`): `unpack(prior, ...)` at
  `:268`, `unpack(value, ...)` at `:297`, and `payload(plan)` at `:315`
  (`payload` at `:53-56` re-dumps and re-hashes the whole plan).

This is not an artifact of test wiring. For PostgreSQL the engine uses
`NoOpWriteQueue`, which runs the coroutine factory inline on the caller's loop
(`src/prme/storage/write_queue.py:166-172`), and `commit_postgres` is reached
from `engine._publish_prepared_profile` via
`self._write_queue.submit(lambda: self._graph_store.publish_profile(plan))`
(`src/prme/storage/engine.py:4948-4951`). `ProfileWorkStore` is wired with
`pool=pool` when `database_url` selects Postgres (`engine.py:443`), and
`_prepare_pg` is called directly from `prepare()` on the loop
(`profile_work.py:146-147`).

Effect: with a very large budget on a large store, a Postgres-backed
`consolidate_knowledge` still holds the shared loop for several whole-profile
serializations and checksums, one per source for `_validate`. The
CHANGELOG claim that "the profile commit ... now run on worker threads"
(`CHANGELOG.md:12`) was then inaccurate for Postgres.

Coverage: no test asserted thread placement or loop responsiveness on Postgres.
`tests/test_pg_workspace.py:260` exercises `consolidate_knowledge` against a
live Postgres but not this property, and the new test only opened a DuckDB
config at that snapshot.

### F2 (blocking at scale): thread offload does not remove the stall, because the moved work holds the GIL

The moved steps are dominated by GIL-holding C code, not by Python bytecode or
I/O:

- `canonical_hash` is `json.dumps` (`src/prme/models/derivation.py:20-23`) and
  is what `profile_request_hash` (`src/prme/models/profile.py:48-56`, via one
  `node_checksum` per source: `derivation.py:34-40`) and `checksum`
  (`profile.py:131-132`) run. Each of those serializes roughly the whole
  profile plus every source, so each pass is about twice the store text.
- pydantic-core's `model_dump_json`/`model_validate_json` (the `copy_plan`
  path, `src/prme/storage/profile_work.py:41`) hold the GIL for most of the
  call as well.

Measured with the project venv (`/Users/dwamianm/Sites/prism/.venv/bin/python`,
pydantic 2.12.5). In the asyncio form, a coroutine ticker stands in for the
event loop and the work runs through `asyncio.to_thread` (the same mechanism as
`run_profile_work`'s `run_in_executor`, `src/prme/organizer/profiles.py:51-52`):

| Work on the worker thread | Worker time | Longest event-loop gap |
|---|---|---|
| `time.sleep(0.150)` via `to_thread` (control) | 0.151 s | 0.003 s |
| `profile_request_hash` on a 40 MB profile node + 40 MB source, via `to_thread` | 0.134 s | **0.062 s** |
| `canonical_hash` on a 120 MB payload, via a ticker thread | 0.221 s | **0.171 s** |
| pydantic `model_dump_json` of an 80 MB model | 0.048 s | **0.034 s** |
| pydantic `model_validate_json` of that 80 MB document | 0.013 s | **0.012 s** |

So for a profile whose text is the size of a large store, the loop thread still
cannot execute for the duration of each individual C call, even though the call
is now on a worker. The change reduces the number of operations that run on the
loop thread and computes the request hash once instead of repeatedly (a real
improvement), but the stall remains and grows linearly with profile size; a
single call crosses the project's own 0.1 s `MAX_GAP`
(`tests/test_profile_event_loop.py:20`) at roughly 120-130 MB of serialized
text per call. A publish still performs on the order of a dozen whole-profile
dumps/hashes across `copy_plan` (prepare, fence, lexical stage, commit), the
vector copy, the fence checks and the commit checks, so the loop sees a series
of such gaps.

Because the ticket's premise is "a large budget grows its text to the size of
the whole store", this is the ticket's own scenario, and the acceptance
criterion ("must not stall") is not met for stores large enough for one
serialization to exceed the project's stall threshold. If the project defines
success as "the work runs on a worker thread" rather than "the loop stays
responsive", the change meets that weaker reading; the parent should decide.
Note that the pre-fix code blocked the loop for the whole call (for the 80 MB
`profile_request_hash` case, about 134 ms instead of 62 ms), so the change is a
strict improvement, not a regression.

The new test cannot detect this: it asserts thread identity, not the loop-gap
property the sibling tests in the same file measure
(`tests/test_profile_event_loop.py:23-50, 64-68, 133-137`). A criterion test
should reuse `longest_gap` with a budget that produces a profile large enough
for the moved work to be measurable.

### F3 (minor; correctness comment and latent cache semantics): a private attr is not excluded from pydantic equality, and `model_copy` propagates the cache

Resolved in the re-review (comment corrected); see the Re-review section. This
is the original finding against snapshot `2c646c32`.

`src/prme/models/profile.py:84-87` justifies the cache with "a private
attribute is left out of the model's dump and equality". The dump half is true;
the equality half is false in pydantic 2.12.5: `BaseModel.__eq__` also compares
`__pydantic_private__`. Verified in the project venv: a frozen model with
`PrivateAttr(default=None)` compares equal to another fresh instance, but
unequal to a field-identical instance whose private attr was set. So a
`ProfilePublication` whose `request_hash` was read no longer compares equal to
an otherwise identical plan.

Consequences today are latent, not failing:

- No production code compares plans (all comparisons found are on `checksum`,
  `key`, ids or `PreparedEmbedding`).
- `model_copy(update=...)` copies the private attr (verified), so the changed
  plans built that way in `tests/test_profile_work.py:151-165` inherit a stale
  cache. They do not read `request_hash` before `prepare()` re-copies through
  JSON, so the tests still pass; a future caller that does would get the old
  hash.
- The cache also assumes the plan's nodes are never mutated in place after it
  is warmed. `ProfilePublication` is frozen, but `MemoryNode` is not
  (`src/prme/models/base.py:21` `frozen=False`), so `plan.node.content = ...`
  is allowed. No current code does that; the assumption is enforced only by
  convention.

The safety comment should be corrected (private attrs are excluded from dumps
and persistence, not from equality), and the cache either documented as "do not
mutate and do not `model_copy(update=...)` a warmed plan" or dropped in favour
of not caching on the model.

### F4 (minor; consistency): the new `copy_plan` helper is not used by `ProfileStageFence.__init__`

Resolved in the re-review; see the Re-review section. This is the original
finding against snapshot `2c646c32`.

`ProfileStageFence.__init__` still inlined
`ProfilePublication.model_validate_json(plan.model_dump_json())`
(`src/prme/storage/profile_work.py:500-502`) instead of calling the new
`copy_plan` (`:33-43`). The fence was the one copy whose `request_hash` is not
warmed; its first `request_hash` read therefore happens later inside `hold()`
-> `validate_duck_work` -> `validate_work_row` (`:436`), on the default executor
rather than the profile worker the fence was constructed on. No loop stall
results (that read is off-loop), but it left two copy idioms for the same
object, and the new centralization did not cover the one site the ticket
explicitly lists.

### F5 (minor; test coverage)

- The new test (`tests/test_profile_event_loop.py:77-123`) never exercises
  `decode_plan`/`ProfileWorkStore.get`: on a first build `reusable()` finds no
  pending row (`src/prme/storage/engine.py:4895`), so the newly off-loaded
  decode path is only covered by older functional tests, not for thread
  placement.
- It never covers the Postgres path in the first reviewed snapshot (F1); the
  re-review notes that the shared `config` fixture is parameterized over both
  backends, so the tests now do run on Postgres.
- Its name says "prepares_on_the_profile_workers", but
  `PreparedEmbedding.model_validate_json` runs via `run_to_completion` on the
  shared default executor (`src/prme/storage/vector_index.py:490`), not on
  `prme-profile*`; the test only asserts "not MainThread" for that label, which
  is weaker than the other three assertions.
- No assertion ties the test to the acceptance criterion (see F2).

### F6 (follow-up, outside the ticket's "profile build" scope): sibling maintenance paths still copy whole plans on the loop

The same idiom that was moved here remains on the loop in
`ProfileCollectionFence` (`src/prme/storage/profile_collection.py:67`),
`VectorIndex.delete_profile_stage` (`src/prme/storage/vector_index.py:593`),
`LexicalIndex.delete_profile_stage` (`src/prme/storage/lexical_index.py:741`),
`LexicalIndex.stage_consolidation` (`:557`), and `profile_registry.decode`
(`src/prme/storage/profile_registry.py:45`). These are collection, retirement
and consolidation paths, not a profile build, so they do not fail the
acceptance criterion, but a store with a very large profile stalls the
corresponding maintenance command the same way.

### F7 (minor; lock hold time, not correctness): `_commit_duckdb` copies inside the shared connection lock

Resolved in the re-review; see the Re-review section. This is the original
finding against snapshot `2c646c32`.

`commit_duckdb` acquired `store._conn_lock` and then ran `_commit_duckdb`,
which at that snapshot performed `copy_plan(plan)` at its first statement
(`src/prme/storage/profile_publication.py:87-89, 96-100`). Before the change
the copy happened before the lock. The event loop was not blocked, but the
O(profile-text) dump/validate lengthened a lock shared with other storage
work; `commit_postgres` did its copy before acquiring the pool
(`:211-213`).

## Checklist 1: correctness and logic

- No logic error found in the changed control flow. Arguments and returns of
  `run_profile_work`, `run_to_completion`, `copy_plan`, `decode_plan` and
  `unpack` are passed correctly; `ProfilePublication` round-trips through JSON
  with an unchanged `checksum`.
- Concurrency: no nested `run_profile_work` call (all offloaded callees are
  synchronous), so the two-thread pool cannot deadlock; no async lock is held
  across an offload await in `prepare`, `get`, `stage_profile` or `stage`; the
  lazy `_request_hash` write is an idempotent same-value store, benign under
  concurrent reads. `VectorIndex.stage` reads `self._provider` and
  `self._index.ndim` from the worker without `_write_lock`, but it did so before
  acquiring the lock before this change too (pre-existing race, no new torn
  read).
- Sentinels: `build_profile` `None` is handled (`engine.py:4833`); `get` `None`
  is handled in `resume_profile` (`:4975-4977`) and in `reusable`
  (`profile_work.py:139`); `status` `None` raises (`engine.py:4923-4924`);
  `unpack`/`copy_plan`/`run_to_completion`/`run_profile_work` raise rather than
  return sentinels. No ignored failure sentinel found.
- Breaking changes: none (no signature, config, field or bound change; the
  private attr is absent from schema and dumps).
- Incomplete implementation at the first snapshot: F1 (Postgres), F4 (fence
  copy), F2 (GIL scope). Re-review: F1, F4 and F7 are resolved; F2 remains a
  recorded residual.

## Checklist 3: code quality, error handling, tests, performance

- Complexity/naming: the `prepared` closures in `stage_profile`/`stage` are
  small and well commented; `decode_plan` and `copy_plan` are clearly named
  (the duplicate name with `derivation_registry.decode_plan` is a different
  module and a different codec, acceptable).
- Error handling: exceptions from workers propagate with the same types as the
  former synchronous calls; `run_to_completion` shielding is used for the
  Postgres copy and the DuckDB copy, and unshielded `run_profile_work` is used
  only for side-effect-free work.
- Performance: the request hash is now computed once and reused across
  prepare/fence/stage/commit, which removes repeated whole-plan hashing;
  `checksum` is still recomputed at every fence/stage/commit check (an obvious
  further cache, with the same determinism argument as `_request_hash`).
  F2 documents the GIL limit.
- Test coverage: F2, F5.

## Checklist 4: pattern consistency and DRY

- `models/profile.py` (pydantic model): analogue `ConsolidationPublication`
  keeps its `request_hash` as a real field
  (`src/prme/models/consolidation.py:86`); using a lazily cached private
  property is a deviation, acceptable but see F3.
- `storage/profile_work.py` (storage helper module): the new helpers stay
  untyped, as `payload`/`unpack`/`pg_sql` already are, so they are consistent.
  Storage -> organizer lazy imports match existing sites (`engine.py:4592`,
  `alias_proposal.py:20`, `oscillation_penalty.py:18`).
- `storage/profile_publication.py`: deliberately deviates from the
  consolidation analogue, which still copies on the loop
  (`src/prme/storage/consolidation_publication.py:539`); the deviation is the
  point of the ticket, but it leaves two commit styles in the same package
  (F7).
- `storage/lexical_index.py`: `stage_profile` and `stage_consolidation`
  (`:519-551` vs `:553-574`) are near-identical; only the profile copy was
  moved off the loop, so the pair now differs (F6).
- `storage/vector_index.py`: uses `run_to_completion`, like every other native
  storage write in the file; consistent.
- Tests: the `record` monkeypatch wrapper follows the recording-wrapper pattern
  already used at `tests/test_profile_event_loop.py:156-166`.

## Checklist 5: redundancy and new symbols

Inventory of new symbols: `copy_plan` (`profile_work.py:33`), `decode_plan`
(`:46`), `ProfilePublication._request_hash` (`models/profile.py:87`). No dead
code. `copy_plan` centralizes an idiom repeated at least six times (e.g.
`vector_index.py:593`, `lexical_index.py:741`, `profile_collection.py:67`,
`profile_work.py:502`). `decode_plan` is a thin unpack-plus-warm wrapper, but it
is intentionally distinct from `unpack`, because `unpack` is also called on the
loop by `_prepare_pg`; warming inside `unpack` would add a loop stall. No
over-engineering found; the only redundancy is the remaining inline copy at
`profile_work.py:502` (F4). The re-review adds one more helper,
`replayed_preparation` (`profile_work.py:68-73`), which is used once and keeps
the former behaviour; not redundant.

## Adversarial scenarios checked

- Very large profile on DuckDB: the hash/fence/stage/commit copies are off the
  loop thread, but each C call still holds the GIL (F2).
- Very large profile on Postgres: still serialized whole plans on the loop in
  the first snapshot (F1), fixed in the re-review.
- Plan equality, `model_copy(update=...)`, and in-place node mutation after the
  hash is warmed: no current failure; the stated safety property was false and
  is corrected in the re-review (F3).
- Worker-pool starvation/deadlock: none found; no nesting, and no lock is held
  across an offload.
- Cancellation: the offloaded copies have no durable side effect; DB work keeps
  `run_to_completion` shielding.
- Persisted format and derivation output: unchanged; `DERIVATION_VERSION`
  unchanged.

## Unresolved risks

- The protected (privacy) build path ran `validate_generated_publication`
  (`src/prme/privacy/journals.py:224-247`) in the `ProfilePublication(...)`
  constructor on the loop (`src/prme/storage/engine.py:4901`) at the first
  snapshot; the construction is off the loop in the re-review. A further
  pre-existing serialization remains on the loop inside
  `admit_generated_node`/`context.generated(_sources)` -> `canonical`
  (`src/prme/privacy/reads.py:338, 345, 351, 357`), recorded in the re-review.
- Whether F2 is blocking depends on the size that counts as "very large". The
  evidence is recorded above so the parent can decide; F2 is a measured limit
  that the new test cannot see.
- F1 was a definite gap against the ticket's own approach in the first
  snapshot; the re-review confirms it is closed.

## Re-review (snapshot acb59ca9dad6f658893fc2adcc6c7d8d1446e7c19eb5027f54122e4944ce513a)

Scope: re-verified only the affected findings against the new working tree
(base `main` at `deb11a68`). I read the changed source; I did not rerun the
suites. The PostgreSQL suite result supplied with the re-review request (236
passed, 23 skipped against a temporary server) is accepted as worker evidence.

Status of the earlier findings:

- **F1 (Postgres on-loop serialization): resolved at source level.** Every
  whole-plan operation I listed now runs in a worker:
  `commit_duckdb`'s copy before the lock (`profile_publication.py:91`),
  `commit_postgres`'s copy (`:212`), `_replayed` (`:229`), the
  `_record_to_node` mapping (`:242-243`), `_validate` (`:252`), `_payload`
  (`:253`); in `profile_work.py`, `replayed_preparation` (`:274`), the
  same-request `unpack` (`:303-304`), `payload` (`:318`), and
  `validate_work_row` inside `validate_pg_work` (`:494-496`). The remaining
  loop-side work in `commit_postgres` is O(number of sources) id handling, the
  O(dimension) embedding string and O(1) property reads; the actual content
  write is `_create_node_on_connection`, inherent to the backend write. The
  CHANGELOG sentence (`CHANGELOG.md:12`) is now accurate for Postgres.
- **F3 (false equality comment): resolved.** The comment at
  `src/prme/models/profile.py:84-89` now says the private attribute is excluded
  from dumps and the persisted checksum but not from equality, and states the
  copy-through-JSON guard. That matches the pydantic 2.12.5 behaviour I
  measured. The remaining caveat is unchanged and latent:
  `model_copy(update=...)` still copies the cache, and `MemoryNode` is still
  mutable (`src/prme/models/base.py:21`), but no production caller reads a
  `request_hash` on a plan built either way.
- **F4 (fence did not use `copy_plan`): resolved.**
  `ProfileStageFence.__init__` now calls `copy_plan(plan)`
  (`src/prme/storage/profile_work.py:512`), which also warms the hash on
  whichever worker constructs the fence; the engine still constructs it via
  `run_profile_work` (`src/prme/storage/engine.py:4937`).
- **F7 (copy inside the DuckDB connection lock): resolved.** `commit_duckdb`
  takes `copy_plan` through `run_to_completion` before acquiring
  `store._conn_lock` (`src/prme/storage/profile_publication.py:89-92`), and
  `_commit_duckdb` no longer copies (`:101`), so there is exactly one copy.
- **Protected path construction: resolved.** `ProfilePublication(...)` is now
  built by the `build_plan` closure through `run_profile_work`
  (`src/prme/storage/engine.py:4901-4915`), so
  `validate_generated_publication`'s per-source checksums
  (`src/prme/privacy/journals.py:224-247`) no longer run on the loop.
  One residual remains and is pre-existing, not part of this diff:
  `admit_generated_node` still calls `context.generated_sources` and
  `context.generated`, whose `canonical(...)` is `json.dumps` over all source
  text and the profile text on the loop (`src/prme/privacy/reads.py:338, 345,
  357`, with a `json.loads` at `:351`). A protected store with a very large
  profile therefore still stalls inside privacy admission. This was not one of
  the ticket's named root causes, but it means the protected path is not
  stall-free; record as follow-up.
- **F2 (GIL limit): unchanged, now covered by a placement test.**
  `test_a_slow_profile_hash_does_not_hold_the_event_loop`
  (`tests/test_profile_event_loop.py:128-150`) measures the loop gap while the
  patched hash calls `time.sleep(0.3)`. `time.sleep` releases the GIL, so the
  test proves the hash is off the loop, not that a real `json.dumps` keeps the
  loop responsive; my earlier measurements stand and the acknowledged
  follow-up (chunked hashing or a documented budget bound) is still needed. The
  test does run under both `config` parameterizations
  (`tests/test_durable_ingestion.py:265-280`), so when
  `PRME_TEST_DATABASE_URL` is set it also covers the Postgres copy path.
- **F5 (test coverage): partially improved, minor gaps remain.** The shared
  `config` fixture is parameterized over DuckDB and Postgres, so the new tests
  now exercise the Postgres prepare/publish path too; the weakening of the
  embedding assertion to `seen.get("embedding", set())`
  (`tests/test_profile_event_loop.py:125`) is justified because the vector
  index is not used on Postgres. Still uncovered: a placement assertion for
  `decode_plan`/`ProfileWorkStore.get`, and a placement assertion for the
  Postgres commit's `_payload`/`_validate`/`validate_pg_work` (the slow-hash
  test only slows `profile_request_hash`, so a regression that moved
  `_validate` or `_payload` back onto the loop would not be caught; a slow
  `canonical_hash` patch would).
- **F6 (sibling maintenance paths): unchanged follow-up.** The copies at
  `profile_collection.py:67`, `vector_index.py:593`, `lexical_index.py:741`,
  `lexical_index.py:557` and `profile_registry.py:45` are still on the loop.

New issues from the changed code: **none found.**

- `run_profile_work` context propagation (`src/prme/organizer/profiles.py:51-57`):
  one `contextvars.copy_context()` per call and `Context.run` matches
  `asyncio.to_thread`; each context is entered once, the offloaded callees are
  synchronous and do not re-enter it, and none of them mutate context-held
  objects, so no new race or `RuntimeError` path.
- `replayed_preparation` (`src/prme/storage/profile_work.py:68-73`) is
  behaviourally identical to the former inline `unpack` + checksum comparison
  and keeps the same `ValueError` path.
- All newly offloaded Postgres callees are pure or thread-safe reads of their
  arguments: `_record_to_node` is `_record_to_node = staticmethod(record_to_node)`
  (`src/prme/storage/pg/graph_store.py:1579`), and `_validate`, `_payload`,
  `_replayed`, `unpack`, `payload` only read their arguments.
- `validate_pg_work` now awaits `run_to_completion`; all of its callers are
  async and on the loop (`profile_publication.py:233`,
  `profile_retirement.py:118`, `profile_collection.py:56`), so it is never
  entered from a worker without a running loop.
- Minor observation, not a defect: `commit_postgres` now makes five
  default-executor round trips while holding a Postgres transaction and row
  locks (`:229-253`), which lengthens the transaction and puts whole-profile
  serialization on the shared default executor that the `_PROFILE_WORKERS`
  comment (`src/prme/organizer/profiles.py:19-23`) says profile work should
  avoid. It is off the event loop and correct; only a lock-hold/contention
  note.

Status: COMPLETE
