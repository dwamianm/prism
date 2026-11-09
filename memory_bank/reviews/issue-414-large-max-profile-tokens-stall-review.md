# Issue #414 review: a very large max_profile_tokens can stall the event loop during a profile build

## Review Scope

- Base commit: `deb11a68b73f299d847efabf0e27195fb433dceb` (main, Release 0.17.0a3).
- Initial reviewed snapshot: `2c646c32c9bf1f36f3b2427ad11d6f97b47f55157d347e713274f090b0f4b64e`.
- Snapshot after the review fixes: `acb59ca9dad6f658893fc2adcc6c7d8d1446e7c19eb5027f54122e4944ce513a`.
- Tier: L (DATA, PERSISTED_FIELD, NEW_SYMBOLS; INFRA false, SECURITY false; semantic risk check applied).
- Roster from `housekeeping.py review-plan --base main`: core (1) merged with 3, 4, 5; dedicated security (2); dedicated data (7); adversarial (8). Checklist 6 (infrastructure) not applicable.
- All four reports are in `memory_bank/reviews/issue-414-large-max-profile-tokens-stall-{core,security,data,adversarial}.md`. Affected reviewers re-checked the fix snapshot.

## Files Changed

- `CHANGELOG.md`
- `src/prme/models/profile.py`
- `src/prme/organizer/profiles.py`
- `src/prme/storage/engine.py`
- `src/prme/storage/lexical_index.py`
- `src/prme/storage/profile_publication.py`
- `src/prme/storage/profile_work.py`
- `src/prme/storage/vector_index.py`
- `tests/test_profile_event_loop.py`

## Approach Summary

`consolidate_knowledge` now keeps every accepted `max_profile_tokens` value (no new bound, no public API change), and moves the work after `build_profile` that scales with the profile text onto worker threads:

- `profile_request_hash` and the `ProfilePublication(...)` construction (which checksums every source for a protected plan) run through `run_profile_work`. `run_profile_work` now runs each step in a copy of the caller's context, like `asyncio.to_thread`, so protected diagnostics and journal deadlines follow it.
- `ProfileWorkStore.get`/`prepare`, `ProfileStageFence.__init__`, `LexicalIndex.stage_profile`, `VectorIndex.stage` and the DuckDB/PostgreSQL prepare and commit paths take their validated copies and compute their checksums and payloads in a worker. The DuckDB commit takes its copy before the shared connection lock.
- `ProfilePublication.request_hash` is computed once and remembered in a private attribute that is excluded from dumps and persisted checksums, so stored bytes, checksums and `DERIVATION_VERSION` are unchanged.
- Tests: the huge-budget test asserts each preparation step runs off the event loop and that the budget quotes every source; a new test measures the loop gap while the hash sleeps 0.3 s.

## Must Fix

1. **PostgreSQL prepare/publish still serialized whole plans on the loop** (core F1, data F3, adversarial 1, security residual). Fixed: `_prepare_pg`, `commit_postgres` and `validate_pg_work` run `replayed_preparation`, `unpack`, `payload`, `_replayed`, `_record_to_node` mapping, `_validate` and `_payload` through `run_to_completion`. Verified against a temporary PostgreSQL 55432: 236 passed, 23 skipped.
2. **`commit_duckdb` held the shared connection lock across the new copy** (core F7, adversarial 4, data F5). Fixed: the copy runs before `async with store._conn_lock`.
3. **The cache comment claimed private attributes are excluded from equality** (core F3, data F1, security 1, adversarial 8). Fixed: the comment now says dumps and the persisted checksum only, and names the JSON copy as the staleness guard.

## Should Fix

1. **Protected plan construction hashed every source on the loop** (core Unresolved, adversarial 2). Fixed: the plan is built in `run_profile_work`.
2. **`run_profile_work` did not copy contextvars** (security 2). Fixed: `contextvars.copy_context()` per step.
3. **No test tied the change to loop responsiveness** (core F2/F5, adversarial 7). Fixed with `test_a_slow_profile_hash_does_not_hold_the_event_loop`; the thread-placement test is retained.
4. **`ProfileStageFence.__init__` did not use `copy_plan`** (core F4). Fixed.

## Consider

1. **GIL residual** (core F2, adversarial 3). A single `json.dumps` of the whole profile still holds the interpreter lock; the loop is free between C calls but not during one. Follow-up, because removing it needs chunked hashing with a format change, a subprocess, or a budget bound (API decision).
2. **Worker-pool fairness and default-executor routing** (core/security notes, adversarial 6). Follow-up.
3. **Vector staging reads `_provider`/`_index.ndim` outside the write lock** (data F6). Accept: both are stable for the index lifetime and `_do_stage` re-reads under both locks.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Ownership/tenant isolation across threads | PASS | The moved work is pure and receives scoped arguments; `unpack`, `validate_work_row` and `ProfileStageFence` keep their owner checks. |
| Privacy journal admission | PASS | `prepare`, `commit_duckdb` and `commit_postgres` keep `@protected_mutation`; the context now follows `run_profile_work`. |
| Fence verified on the committed object | PASS | Same checksum comparison, before the same locks; worker placement only. |
| Cached request hash staleness | PASS | Deterministic function of the plan; every boundary copies through JSON, which resets the cache; publication revalidates dependencies under the transaction. |
| Validation not weakened | PASS | Same copies/checks in workers. |
| Error swallowing / protected text in logs | PASS | No new except/log in moved functions; `extra="forbid"` rejects an injected `_request_hash`. |

## Pattern Consistency Assessment

- `models/profile.py`: the cached private property deviates from `ConsolidationPublication`'s real `request_hash` field; acceptable and documented, with the equality caveat.
- `storage/profile_work.py`: helpers stay untyped like `payload`/`unpack`/`pg_sql`; storage-to-organizer lazy imports match existing sites.
- `storage/lexical_index.py`: `stage_profile` now differs from `stage_consolidation` (only the profile was moved); recorded as a follow-up.
- `storage/vector_index.py`: uses `run_to_completion`, like the rest of the file.
- `storage/profile_publication.py`: DuckDB and PostgreSQL now both copy through `copy_plan` before their locks.

## Redundancy Check

New symbols: `copy_plan`, `decode_plan`, `replayed_preparation` (`profile_work.py`), `ProfilePublication._request_hash`. No dead code; `copy_plan` centralizes an idiom repeated at six sites. No new dependency.

## Wiring Findings

- No new configuration or environment reads; no migration or registration step.
- `run_profile_work` remains the profile worker boundary and is reset after fork.
- CI picks up the test through the existing `tests/` collection; the PostgreSQL path is exercised by the parametrized `config` fixture in CI.

## Data Safety and Read/Write Proof

- No DDL, SQL shape, key or schema change.
- `_request_hash` is a pydantic private attribute: absent from `model_dump`/`model_dump_json`, checksum and stored payloads; old payloads decode unchanged.
- `DERIVATION_VERSION` unchanged; no `src/prme/ingestion/` change.
- Retry/idempotency: the same JSON copies, checksum comparisons, `request_hash` values and replay branches; only their thread changed.
- Lock order: DuckDB copy before the connection lock; PostgreSQL copy before the pool acquisition; sync validation inside the transaction performs no DB work.
- Verified against a live temporary PostgreSQL and the DuckDB suite.

## Break Scenarios (adversarial) and Adjudication

| # | Trigger | Likelihood | Impact | Detection | Verdict |
|---|---|---|---|---|---|
| 1 | PostgreSQL backend, any owner, huge budget | Medium | High (shared loop held) | Silent | **Fix now** (fixed and re-reviewed) |
| 2 | Privacy enabled, large protected profile | Medium | High for those installs | Silent | **Fix now** (plan construction fixed and re-reviewed; admission serialization remains, follow-up 2) |
| 3 | Profile text large enough that one serialization exceeds 0.1 s | Medium at extreme size | High when hit | Quiet | **Follow-up** (GIL; measurements below) |
| 4 | Any DuckDB profile publication | High | Medium (storage lock held) | Quiet | **Fix now** (fixed and re-reviewed) |
| 5 | `collect_profile_staging`, `discard_profile`, maintenance | Low-Medium | Medium | Silent | **Follow-up** |
| 6 | Concurrent builds, organiser passes | Medium | Low-Medium (latency) | Quiet | **Follow-up** |
| 7 | Test suite proving the criterion | High | Medium (false confidence) | This is the detection finding | **Should Fix** (fixed for thread placement on both backends; GIL residual recorded) |
| 8 | Future mutation or `model_copy` of a warmed plan | Low | Low-Medium | Silent | **Accept** (comment corrected; no live path) |
| A | PostgreSQL transaction and `FOR UPDATE` rows held across default-executor dispatch | Low-Medium | Medium (contended writers wait) | Quiet | **Follow-up** (same class as 6; work is pure, no deadlock) |
| D | Protected `prepare` copy queues for a profile slot while the publication guard is held | Low | Low-Medium | Quiet | **Accept** (bounded, pure work; follow-up 3) |

Measurements (worker probes, `python` venv): a 7.7 MB profile kept the loop gap at about 48-59 ms (was about 103-109 ms before the fix); a 15.4 MB profile at about 86-92 ms (was about 207 ms). A single whole-profile `json.dumps` holds the interpreter lock, so a profile whose text takes more than about 0.1 s to serialize can still hold the loop for that call. Pre-mortem headline from the adversarial pass: "We moved the profile serialization to threads, but PostgreSQL and the privacy path still serialized the whole profile on the loop, and the worker copies still held the GIL; the fixes close the first half, the GIL residual is a follow-up."

## Follow-ups Raised

1. Chunked hashing/serialization (or an API decision on a maximum profile text size) so one C call cannot hold the interpreter lock for a store-sized profile. Filed as #489.
2. Move the remaining whole-plan copies and serializations off the loop: `ProfileCollectionFence`, `delete_profile_stage` in both indexes, `stage_consolidation`, `profile_registry.decode`, and the privacy admission `canonical()` dumps in `privacy/reads.py`. Filed as #490.
3. Worker-pool fairness: the two profile workers are process-wide and now also carry publication JSON work; vector staging and the PostgreSQL copy use the shared default executor, and PostgreSQL holds its transaction and row locks across those dispatches. Filed as #491.

## Re-review

All four reviewers re-checked snapshot `acb59ca9` with the fixes below and confirmed:

- Core: F1, F3, F4, F7 and the protected plan construction resolved; no new issue in the reworked code. Residuals: F2 (GIL) and the privacy admission `json.dumps` over whole text on the loop (`src/prme/privacy/reads.py:338-357`), pre-existing and outside this diff.
- Data: F1, F2, F3, F5 resolved; F4 and the F6 note remain follow-ups; no new durable-data issue; durable bytes, idempotency, lock order and cache staleness hold.
- Security: Finding 2 (contextvars) fixed correctly with no re-entry, journal-scope or cancellation defect; Finding 1 comment accurate; the new off-loop PostgreSQL work keeps every validation, owner check and journal guard.
- Adversarial: scenarios 1, 2, 4 and 8 closed; scenario 7 closed for the hash wiring on both backends; scenario 3 recorded as a bounded residual; new scenarios A and D recorded as follow-ups/accept.

## Resolution Status

| Item | Status |
|---|---|
| PostgreSQL prepare/commit off the loop | Resolved, tested on a temporary PostgreSQL, re-reviewed |
| DuckDB commit copy outside the connection lock | Resolved, re-reviewed |
| Protected plan construction off the loop | Resolved, re-reviewed |
| Contextvars in `run_profile_work` | Resolved, re-reviewed |
| Comment accuracy | Resolved, re-reviewed |
| Tests: thread placement and loop gap | Resolved, re-reviewed |
| GIL residual at extreme profile size | Open, follow-up 1 |
| Sibling maintenance paths and privacy admission | Open, follow-up 2 |
| Worker-pool fairness and PostgreSQL lock hold across dispatch | Open, follow-up 3 |
| Static checks (ruff, mypy public API) | Passing |
| Full suite | 7134 passed, 1536 skipped, 2 xfailed, 1 failed on the known local `.env` derivation-version test; that test passes with `.env` moved aside (CI has no `.env`) |
