# Issue #414 adversarial review: a very large max_profile_tokens can still stall the event loop during a profile build

Status: COMPLETE

- Reviewers checklist: adversarial (checklist 8) for dwamianm/prism#414.
- Base commit: `deb11a68b73f299d847efabf0e27195fb433dceb` (main, Release 0.17.0a3).
- Reviewed snapshot: `2c646c32c9bf1f36f3b2427ad11d6f97b47f55157d347e713274f090b0f4b64e` (uncommitted working tree; `git diff main`).
- Acceptance criteria judged against: (1) a very large `max_profile_tokens` must not stall the shared event loop; (2) every previously accepted value still works; (3) the built profile is unchanged; (4) `DERIVATION_VERSION` unchanged.
- Files read beyond the diff: `src/prme/storage/profile_work.py`, `profile_publication.py`, `profile_collection.py`, `profile_retirement.py`, `lexical_index.py`, `vector_index.py`, `engine.py` (profile section), `organizer/profiles.py`, `organizer/owner_profiles.py`, `models/derivation.py`, `models/base.py`, `prme/privacy/journals.py`, plus pydantic 2.12.5 / pydantic-core 2.41.5 sources in `.venv`.
- Evidence: the worker's probes under `/tmp/prme414_*.py` were read as context. This review did not execute them (review-only); all findings below are source-verified.

## Assigned checklist (worked in order)

Premise challenge; guard loopholes; existing data and installs; state and lifecycle; idempotency and retries; ordering/timing/consistency; scale; partial failure and cancellation; backward compatibility; blast radius; detection.

## Clean checks (one line each)

- **Existing data, installs, stored bytes:** `_request_hash` is a pydantic `PrivateAttr` (`src/prme/models/profile.py:84-87`); private attributes are never in `model_dump`/`model_dump_json`/`model_json_schema`, so stored `PROFILE_PREPARED`/`PROFILE_PUBLISHED` payloads and `checksum` bytes are unchanged, and old payloads decode under new code. Verified in pydantic `main.py` (`model_dump` path) and `_model_construction.py` (private attrs are initialized, not fields).
- **Built profile unchanged / DERIVATION_VERSION:** the diff touches no selection, prompt, embedding, config or `max_profile_tokens` validation code; `src/prme/ingestion/reingest.py:89` still reads `DERIVATION_VERSION = 1`; `consolidate_knowledge`'s text, source list, metadata and confidence are untouched.
- **Every previously accepted value:** `max_profile_tokens` validation is untouched (`src/prme/storage/engine.py:4595-4596`); worker copies round-trip through the same model, so `0`, `1`, and huge values behave as before.
- **State and lifecycle / idempotency:** `prepare` still writes its immutable operation and work row in one transaction; repeated prepare/stage/commit still replay (`profile_work.py:166-173`, `profile_publication.py:38-47, 105-113`); abandoned/complete transitions and `discard`/`collect` semantics are untouched.
- **Backward compatibility of the public API:** no signature changes; `collect_profile_staging`, `resume_profile`, `process_profiles`, `discard_profile` keep their contracts.
- **Cancellation of a queued pure step:** `run_profile_work` uses a bare `run_in_executor` (`organizer/profiles.py:46-52`); a cancelled build simply drops an unstarted copy/validation, which is side-effect free (no storage, no journal), so no partial durable effect is introduced. Native writes still go through `run_to_completion` (`vector_index.py:490`, `profile_publication.py:211`).
- **Contextvars:** no moved function reads the privacy journal `ContextVar` (`journal_scope`); pydantic validation and hashing are context-free, so the fact that `run_in_executor` (unlike `asyncio.to_thread`) does not copy context does not change behaviour today (see scenario 8 for the hazard).
- **Fork safety:** `os.register_at_fork(after_in_child=_reset_after_fork)` rebuilds the pool (`organizer/profiles.py:36-43`).
- **Guard ordering:** every fence check still runs before the write lock it precedes (`lexical_index.py:530-551`, `vector_index.py:471-497`), so the change moves where the check runs, not whether it runs.
- **Nested-executor deadlock:** no moved function awaits another `run_profile_work`; the pool cannot self-deadlock, and `commit_postgres`'s copy runs on the default executor, not the profile pool (`profile_publication.py:211`).

## Findings

### 1. PostgreSQL profile publication still serializes and validates the whole profile on the event loop

- **Label:** pre-existing gap in a path this change claims to fix (the diff edits `commit_postgres`, so the path is in scope).
- **Trigger:** `database_url` is set (PostgreSQL backend) and any owner runs `consolidate_knowledge(..., max_profile_tokens=<huge>)`.
- **Mechanism:**
  - `MemoryEngine._publish_prepared_profile` skips staging for PG (`src/prme/storage/engine.py:4928`) and submits `publish_profile` (`:4948-4951`); `PgGraphStore.publish_profile` calls `commit_postgres` (`src/prme/storage/pg/graph_store.py:94-97`).
  - `commit_postgres` is `async` and still runs on the loop: `_replayed(plan, value)` re-validates the stored publication JSON (`src/prme/storage/profile_publication.py:228`, `:38-47`), `validate_pg_work` unpacks and validates the stored prepared payload (`:232`, `profile_work.py:476-486` -> `:419-441`, `:59-66`), `_validate` calls `node_checksum` for every source and previous profile (`:250`, `:50-58`), and `_payload(plan)` does `plan.checksum` (`model_dump` + `canonical_hash`) plus `plan.model_dump_json()` right in the `await conn.execute(...)` argument list (`:281`, `_payload` at `:31-35`).
  - The prepare side is the same: `_prepare_pg` is `async` and evaluates `payload(plan)` on the loop (`src/prme/storage/profile_work.py:311-318`; `payload` at `:53-56` does `checksum` + `model_dump_json`), and `unpack(prior/value, ...)` at `:268` and `:297` validates whole plans on the loop.
  - Only the *copy* was moved to a worker (`profile_publication.py:210-211`), so the change's CHANGELOG sentence ("the profile commit ... now run on worker threads") is true for DuckDB and false for PostgreSQL, where roughly six to eight further full passes over the profile text remain on the loop (payload in prepare, payload in commit, replayed validation, source checksums, work-row validation).
  - `PgGraphStore._record_to_node` also reconstructs every source `MemoryNode` on the loop (`src/prme/storage/pg/graph_store.py:237-241`).
- **Likelihood:** Medium. Every PostgreSQL deployment with a large budget hits it; DuckDB-only deployments do not.
- **Impact:** High. This is exactly the reported symptom (whole-store profile text serialized on the shared loop), on a backend the CLAUDE.md documents as supported.
- **Detection:** Silent. `tests/test_pg_*.py` exercise correctness, not loop gaps, and are skipped without `PRME_TEST_DATABASE_URL`.
- **Cheapest mitigation:** Build the `payload` string in the worker (compute `checksum`, `model_dump_json` and `json.dumps` alongside `copy_plan`, e.g. have `copy_plan` return the prepared payload or add `prepare_payload(plan)`) and pass the finished `(payload, checksum)` into `commit_postgres`/`_prepare_pg`; precompute the per-source checksums in `copy_plan` so `_validate` compares strings. Size: S-M (one helper plus call-site edits in `profile_publication.py` and `profile_work.py`).
- **Verdict:** Fix now, or explicitly scope the ticket to DuckDB in the CHANGELOG and open a PG follow-up. Leaving it silent is the postmortem risk.

### 2. Protected profiles still hash the whole profile and every source on the loop while constructing the plan

- **Label:** pre-existing, left on the loop in the same function this change edited; not introduced.
- **Trigger:** privacy is enabled (`_privacy.selected()` / an owner with a policy) and `consolidate_knowledge` builds a large profile.
- **Mechanism:** the plan is constructed on the loop at `src/prme/storage/engine.py:4901-4909`; `ProfilePublication`'s `model_validator(mode="after")` calls `validate_generated_publication` (`src/prme/models/profile.py:185-187`), which for `schema_version == 2` loops over `(node, *sources, *previous)` and runs `node_checksum(project_manifest_node(...))` and `node_checksum(node)` for each (`src/prme/privacy/journals.py:243-247`). Each `node_checksum` is a full `model_dump` + `json.dumps` + sha256 (`src/prme/models/derivation.py:34-40`), so a protected build hashes the profile text and every quoted source twice on the loop. `admit_generated_node` also runs on the loop (`engine.py:4882-4884`).
- **Likelihood:** Medium (privacy deployments only; protected profiles are the same size as their sources).
- **Impact:** High on those deployments: the acceptance criterion is "must not stall", with no privacy carve-out.
- **Detection:** Silent; no privacy profile test measures a loop gap.
- **Cheapest mitigation:** factor plan construction into a function and dispatch it through `run_profile_work` (the loop only needs the finished plan), or accept the plan as built by `copy_plan` in the worker. Size: M.
- **Verdict:** Fix now, or record it as an explicit residual in the CHANGELOG/PR. Unlike scenario 1 this is not touched by the diff, so "follow-up" is defensible; the risk is that the CHANGELOG sentence reads as unconditional.

### 3. The moved serializations still hold the GIL, so the loop is only free between whole-profile C calls

- **Label:** pre-existing mechanism, partially mitigated by the change.
- **Trigger:** a profile whose text is large enough that one JSON pass exceeds the loop's tolerance (the issue's own 10,000,000-token budget with a store large enough to fill it).
- **Mechanism:** `canonical_hash` is CPython `json.dumps` over the whole node + source dict plus a sha256 (`src/prme/models/derivation.py:20-23`); `_json` is C and does not release the GIL, so the entire dump blocks every thread, the event loop included, for its duration. It is called once per `profile_request_hash` (`src/prme/models/profile.py:41-56`) and once per `checksum` (`:130-132`), i.e. in `copy_plan`/`decode_plan` (`profile_work.py:33-50`), every `_payload`/`unpack`, and every fence check. pydantic-core 2.41.5's binary does reference `PyEval_SaveThread` (so some operations may release the GIL), but the `json.dumps` calls certainly do not. Moving them to a worker removes loop-thread scheduling but not the GIL hold.
- **Likelihood:** Medium; depends on profile text size, which now scales with the store because the budget no longer caps it.
- **Impact:** High when hit: the exact symptom the issue reports.
- **Detection:** Quiet. The only gap test uses the default 500-token budget, where the long sources are excluded (`tests/test_profile_event_loop.py:53-74`), and the new huge-budget test asserts thread names, not elapsed gaps (`:118-123`).
- **Cheapest mitigation:** Add the gap measurement to the huge-budget test at a size near the reported case (S). If it exceeds the bound, hash the profile content incrementally (per-node dumps already exist; hash pieces then combine) or cap the profile text. Size: S to measure, M to fix.
- **Verdict:** Fix now (measure); follow-up for chunking if the measurement fails.

### 4. `commit_duckdb` now holds the DuckDB connection lock across the new worker copy

- **Label:** newly introduced by this change.
- **Trigger:** any DuckDB profile publication, worst with a large plan.
- **Mechanism:** before the change the validated copy was taken *before* `async with store._conn_lock`; now `commit_duckdb` takes the lock first and `_commit_duckdb` copies inside it (`src/prme/storage/profile_publication.py:86-100`). `_conn_lock` is the single `asyncio.Lock` that serializes every DuckDB graph/event/vector call (`src/prme/storage/duckpgq_graph.py:70`), so the copy's dump + validate + hash now blocks all storage for its duration. The transaction that follows already serializes the payload, so the marginal hold is roughly another one to three full passes.
- **Likelihood:** High for any large profile (every publish takes this path).
- **Impact:** Medium: the loop is free, but concurrent storage/retrieval on the same pack waits; on a large store this is a multi-hundred-millisecond freeze of the whole storage layer.
- **Detection:** Quiet (latency only).
- **Cheapest mitigation:** call `plan = await run_to_completion(copy_plan, plan)` in `commit_duckdb` before `async with store._conn_lock`, keeping `_commit_duckdb` unchanged. Three lines.
- **Verdict:** Fix now (cheap, removes a regression).

### 5. The profile collection path still validates whole plans on the loop

- **Label:** pre-existing sibling path (checklist: "profile recovery / collect_profile_staging").
- **Trigger:** `collect_profile_staging` (CLI `prme profiles collect`, or maintenance) over abandoned jobs, or `discard_profile`, each holding a large plan.
- **Mechanism:** `ProfileCollectionFence.__init__` validates a full plan copy on the loop (`src/prme/storage/profile_collection.py:67`, constructed at `:250-252`); `LexicalIndex.delete_profile_stage` and `VectorIndex.delete_profile_stage` each validate another copy before dispatching (`src/prme/storage/lexical_index.py:741`, `src/prme/storage/vector_index.py:593`); `validate_collection_receipt` calls `plan.checksum` on the loop (`profile_collection.py:102`, used at `:131,146,172`); the PG `acknowledge` builds `json.dumps({"checksum": plan.checksum})` on the loop (`:123`). `_discard_pg` similarly evaluates `plan.checksum` on the loop (`src/prme/storage/profile_retirement.py:128`).
- **Likelihood:** Low-Medium (maintenance paths, bounded per call).
- **Impact:** Medium (same stall class, smaller blast radius).
- **Detection:** Silent.
- **Cheapest mitigation:** reuse `run_profile_work(copy_plan, plan)` for the fences and delete methods, and compute the receipt checksum in the same worker. Size: S.
- **Verdict:** Follow-up (record it in the PR so it is not mistaken for fixed by the CHANGELOG wording).

### 6. Worker-pool scheduling: profile head-of-line blocking, and vector staging on the shared default executor

- **Label:** pre-existing pool design, extended by this change.
- **Trigger:** two long builds, or one pathological `build_profile` (a quadratic token-count case is already possible on this pool) alongside other profile work.
- **Mechanism:** the pool is two whole-process threads (`src/prme/organizer/profiles.py:23-33`) and now carries the publication JSON work as well: hash (`engine.py:4891`), prepare copy (`profile_work.py:145`), decode (`:102`), fence init (`engine.py:4931`), lexical staging (`lexical_index.py:542`). `owner_profiles.matching` (`src/prme/organizer/owner_profiles.py:142`) is used by `end_session`'s owner-profile refresh and by the opportunistic organizer, so unrelated requests queue behind a build's steps. Separately, `VectorIndex.stage` and `commit_postgres`'s copy run on the loop's default executor via `run_to_completion` (`vector_index.py:490`, `profile_publication.py:211`), which is precisely what the pool's own comment says to avoid (`organizer/profiles.py:19-22`); N concurrent builds can pin N default-executor threads in copies.
- **Likelihood:** Medium (concurrent builds or organiser passes).
- **Impact:** Low-Medium: latency, not an event-loop stall; cancellation still propagates.
- **Detection:** Quiet.
- **Cheapest mitigation:** route the vector-staging validation through `run_profile_work` for consistency; optionally give publication steps their own worker count. Size: S.
- **Verdict:** Follow-up.

### 7. No test exercises the acceptance criterion at the scale the issue describes

- **Label:** detection gap introduced with the tests (behaviour is correct only as far as the tests show).
- **Trigger:** reviewer or CI relying on the new test to prove the loop is free.
- **Mechanism:** `test_a_huge_profile_budget_prepares_on_the_profile_workers` uses a 10,000,000-token budget over three ~90 KB sources (`tests/test_profile_event_loop.py:102-108`), so the profile stays small and only thread names are asserted (`:118-123`). `test_long_run_of_one_character_keeps_the_loop_running` measures a gap but excludes the long sources under the 500-token budget (`:53-74`). No test measures the gap for the huge-budget profile path, and none measures it at all for PostgreSQL or the privacy path.
- **Likelihood:** High that a residual stall (scenarios 1-3) would ship undetected.
- **Impact:** Medium: false confidence in the acceptance criterion.
- **Detection:** This is the detection finding.
- **Cheapest mitigation:** reuse `longest_gap(...)` with the huge budget and a source set big enough to be measurable, and add a PG-marked variant. Size: S.
- **Verdict:** Follow-up (cheap; strongly recommended before merging).

### 8. The cached request hash rests on an immutability assumption the code does not hold, and the comment about equality is wrong

- **Label:** newly introduced (cache and comments), latent.
- **Trigger:** any future code that mutates a plan's nested nodes after the first `request_hash` access, or that compares two publications.
- **Mechanism:** `ProfilePublication` is frozen, but `node`, `sources` and `previous` are `MemoryNode`, whose base sets `frozen=False` (`src/prme/models/base.py:21`). `request_hash` caches the result of `profile_request_hash` in `_request_hash` (`src/prme/models/profile.py:119-128`), whose inputs are exactly those mutable nodes. The comment says "the fields it reads are frozen, and a private attribute is left out of the model's dump and equality" (`:84-87`); the dump claim is true, the equality claim is false: pydantic's `__eq__` compares `__pydantic_private__` (`pydantic/main.py:1156-1161` in the pinned 2.12.5), so a warmed and an unwarmed identical plan now compare unequal. `model_copy` carries private attributes over, so `plan.model_copy(update=...)` would keep a stale hash. Today no caller mutates a plan's nodes after warming and no code compares two publications (all guards compare `checksum`, not equality), so this is latent, not live.
- **Likelihood:** Low.
- **Impact:** Low-Medium: a stale cached hash would let `prepare`'s same-request reuse (`profile_work.py:196-208`) match a differently-textured plan, and the failure would be silent.
- **Detection:** Silent.
- **Cheapest mitigation:** fix the comment, and either cache only inside the worker copy (as today) with an explicit assertion that the object is not mutated, or compute the hash without caching. Size: S.
- **Verdict:** Accept (fix the comment now; no behavioural change required).

## Hardest attacks that failed

- **"The two-thread pool serialises a single build and holds the GIL."** A single build's steps are awaited sequentially, so it occupies at most one thread; the second exists so another request can proceed. No moved function awaits the loop or another pool step, so the pool cannot deadlock. GIL is real (scenario 3) but does not make a single build hold both threads.
- **"A cancelled build leaves staged indexes because the copy moved."** No: the copies are pure and unstarted-work cancellation drops them; native writes remain shielded by `run_to_completion`, and staged vectors/documents are the same idempotent restage as before.
- **"At-least-once publication replays a stale cached hash."** All replay/guard decisions compare persisted `checksum` values or DB `request_hash` columns, and every copy recomputes from its own snapshot; the cache is per object and never compared across objects.
- **"The plan's stored bytes changed, breaking old packs/derivation markers."** No: private attributes are excluded from serialization, `checksum` and the JSON payload are byte-identical, and `DERIVATION_VERSION` is untouched.
- **"Moving `verify_plan` into the worker opens a fence race."** No: it still runs before the same `_write_lock`/`conn_lock` and compares the same checksums; the fence holds its own validated snapshot, as before.
- **"A cancelled prepare leaves the DuckDB transaction half done."** No: the copy is before `BEGIN TRANSACTION` inside `_prepare_duck`; nothing durable is touched by the worker step.

## Pre-mortem headline

We moved the profile serialization to two threads, but PostgreSQL (and the privacy path) still serialized the whole profile on the event loop, and the worker copies still held the GIL during one whole-profile `json.dumps`, so a large enough profile kept stalling the shared loop while the new test only checked thread names.

## Re-review (snapshot `acb59ca9dad6f658893fc2adcc6c7d8d1446e7c19eb5027f54122e4944ce513a`, base `deb11a68`)

Scope: re-checked only the scenarios affected by the fixes, the new test, and any break the fixes introduce. The findings above are kept as the record of the first snapshot.

### What changed

- `commit_duckdb` copies the plan before `store._conn_lock` (`src/prme/storage/profile_publication.py:86-94`), and `_commit_duckdb` no longer copies (`:101-104`).
- `commit_postgres` runs `_replayed`, `_record_to_node`, `_validate` and `_payload` through `run_to_completion` inside the transaction (`profile_publication.py:229,242-243,252,253,284`).
- `_prepare_pg` runs `replayed_preparation`, `unpack` and `payload` through `run_to_completion` (`src/prme/storage/profile_work.py:274,303-304,318`), with the new `replayed_preparation` helper at `:69-72`.
- `validate_pg_work` runs `validate_work_row` through `run_to_completion` (`profile_work.py:496-497`).
- `consolidate_knowledge` builds the `ProfilePublication` inside `run_profile_work(build_plan)` (`src/prme/storage/engine.py:4900-4912`).
- `run_profile_work` propagates contextvars with `copy_context().run(...)` (`src/prme/organizer/profiles.py:53-56`).
- The cached-hash comment was corrected to say private attributes are out of the dump/checksum but not out of equality (`src/prme/models/profile.py:84-90`).
- New test `test_a_slow_profile_hash_does_not_hold_the_event_loop` (`tests/test_profile_event_loop.py:128-149`); the huge-budget test's embedding assertion now allows the PostgreSQL path (`:118-123`). The `config` fixture is parametrised over `duckdb` and `postgres` (`tests/test_durable_ingestion.py:264`), so both tests run on both backends when `PRME_TEST_DATABASE_URL` is set.

### Scenario status after the fixes

1. **PostgreSQL whole-plan work on the loop — CLOSED.** A scoped grep of the PG profile path (`profile_work.py`, `profile_publication.py`) now finds no `model_dump`/`checksum`/`node_checksum`/`unpack` outside a worker: prepare (`:274,303-304,315-318`), commit (`:229,242-253,284`), and work-row validation (`:496-497`) are all dispatched, and `plan.request_hash` reads on the loop (`:293,333`) hit a hash warmed in `copy_plan`. Residual, not a serialization pass: asyncpg still binds the large profile content and payload strings on the loop thread inside `_create_node_on_connection` and the operations `INSERT` (`profile_publication.py:254,279-287`); that is a memcpy-class cost of any large write and I did not measure it. I could not reproduce the reported PostgreSQL run (236 passed, 23 skipped on port 55432) here; I verified the source and accept the reported run as the execution evidence.
2. **Protected plan construction on the loop — CLOSED.** The plan, including `validate_generated_publication`'s per-node `node_checksum` loop (`src/prme/models/profile.py:185-187`, `src/prme/privacy/journals.py:243-247`), now runs inside `run_profile_work(build_plan)` (`engine.py:4900-4912`). `admit_generated_node`/`encode_generated` remain on the loop but do not checksum the profile text.
3. **GIL residual — OPEN BY DESIGN, recorded.** `canonical_hash` still ends in `json.dumps` (`src/prme/models/derivation.py:20-23`), and the CHANGELOG now states the residual and that the build itself never runs on the loop. I agree this is a follow-up rather than a fix in this change, provided the measured sizes (reported 7.7 MB / 15.4 MB) go into the pull request so the residual is bounded by evidence; I did not rerun the measurement. Note the residual now also applies to the PG `payload`/`_validate` workers.
4. **`commit_duckdb` lock hold — CLOSED.** The copy is before `async with store._conn_lock` (`profile_publication.py:86-94`), restoring the base ordering while keeping it off the loop; `_commit_duckdb` no longer copies. No residual found.
5. **Collection/maintenance path — PARTIALLY IMPROVED, still a follow-up.** `validate_pg_work` is now off-loop, which also helps `validate_collect_pg` (`src/prme/storage/profile_collection.py:56`), but `ProfileCollectionFence.__init__` (`:67`, constructed `:250-252`), both `delete_profile_stage` copies (`src/prme/storage/lexical_index.py:741`, `src/prme/storage/vector_index.py:593`) and the receipt `plan.checksum` (`profile_collection.py:102,123`, `src/prme/storage/profile_retirement.py:128`) still run on the loop. Unchanged verdict: follow-up.
6. **Pool scheduling — UNCHANGED, slightly widened.** The profile pool still carries the publication steps, and the shared default executor now also carries PG's whole-plan `payload`/`_validate`/`_record_to_node` work. Follow-up.
7. **Detection — CLOSED FOR THE WIRING, not for the GIL residual.** `test_a_slow_profile_hash_does_not_hold_the_event_loop` (`tests/test_profile_event_loop.py:128-149`) measures the loop gap while the hash sleeps 0.3 s, and because the `config` fixture is parametrised it runs on both backends. A regression that puts `profile_request_hash` back on the loop would show a ~0.3 s gap and fail. It does not cover the plan copy/validation, `payload`, `unpack` or the PG `_validate`/`_record_to_node` moves (those are covered, on DuckDB only, by the thread-name assertions at `:118-123`), and by construction it cannot catch the GIL residual because `time.sleep` releases the GIL. So the detection gap that allowed #414 is closed for the hash wiring; the residual in scenario 3 still has no measured guard.
8. **Cached-hash comment — CLOSED.** `src/prme/models/profile.py:84-90` now states correctly that the private attribute is out of the dump/checksum but not out of equality, and records the copy-and-warm invariant. The underlying `MemoryNode` mutability (`src/prme/models/base.py:21`) remains as a documented latent assumption; no behavioural change required.

### New break scenarios introduced by the fixes

- **A. PostgreSQL holds its transaction and `FOR UPDATE` row locks across default-executor dispatch (new).** `commit_postgres` now awaits `_record_to_node`, `_validate` and `_payload` after the `FOR UPDATE` fetch (`profile_publication.py:238-253`), and `_prepare_pg` awaits `replayed_preparation`/`payload` while holding the profile row lock (`profile_work.py:274,318`); both are `@protected_mutation`, so a protected store also holds its publication guard across those waits. The dispatched work is pure and touches no connection, so there is no deadlock and the lock-hold duration is the same as the pre-fix inline work; what is new is that the lock is held while queued behind the shared default executor, which scenario 6 also loads with profile copies. Trigger: a PostgreSQL store with concurrent writers and a busy default executor. Likelihood Low-Medium; impact Medium (contended source writers wait for an executor slot). Detection: quiet. Mitigation: for `_record_to_node`/`_validate`/`_payload`, dispatch before the transaction where the ordering allows, or move these to the profile pool with a bounded queue. Verdict: Follow-up (record it; not a correctness break).
- **B. Context propagation changes the failure mode of a future moved step that re-enters a privacy guard (new, latent).** `run_profile_work` now runs the step in a copy of the caller's context (`organizer/profiles.py:53-56`). That is correct for `journal_deadline`, which already runs in `_commit_duckdb`'s `to_thread` worker, and for `diagnostics_protected`. But if a future moved step calls a `@protected_mutation`-decorated entry, `JournalScope.owns_current_task` evaluates `asyncio.current_task()` (`src/prme/privacy/journals.py:369-370,427`) in a worker thread, where it raises `RuntimeError` instead of taking the guard path. No current moved function does this. Likelihood Low; impact Low. Mitigation: one sentence in `run_profile_work`'s docstring. Verdict: Accept.
- **C. `build_plan` closure on the pool (new, checked, no break).** It captures `profile_node`, `selected_sources`, `previous`, `manifest`, `provider`, `profile_text` and `vectors` (`engine.py:4900-4912`); nothing mutates them between submission and execution, the plan is validated inside the worker, and an exception propagates as before. No break found.
- **D. `prepare`'s copy now queues for a profile slot inside `@protected_mutation` (new, privacy-only).** A protected store can hold its publication guard while `run_profile_work(copy_plan)` waits behind a long build on the two-thread pool (`profile_work.py:146-152`). Same class as A; Likelihood Low; impact Low-Medium. Verdict: Accept/Follow-up.

### Re-review verdict

Scenarios 1, 2, 4 and 8 are closed by source verification; scenario 7's detection gap is closed for the hash wiring on both backends; scenario 3 is correctly recorded as a bounded residual; scenarios 5 and 6 remain open follow-ups, unchanged in kind. The fixes add one worth-recording new risk (A: PostgreSQL row locks and the publication guard across default-executor dispatch), which is a contention/latency risk, not a correctness or acceptance blocker. No new break was found in the DuckDB path, the stored bytes, the guards, or the public API.

## Pre-mortem headline (first snapshot)

We moved the profile serialization to two threads, but PostgreSQL (and the privacy path) still serialized the whole profile on the event loop, and the worker copies still held the GIL during one whole-profile `json.dumps`, so a large enough profile kept stalling the shared loop while the new test only checked thread names.

## Updated pre-mortem headline

The PostgreSQL and privacy paths now serialize off the loop and the detection gap is closed for the wiring, so the remaining incident, if it happens, is a profile large enough that one whole-profile `json.dumps` holds the interpreter lock past the latency budget, while a busy shared default executor stretches the PostgreSQL transaction's row-lock hold.
