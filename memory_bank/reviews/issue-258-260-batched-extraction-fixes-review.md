# Code Review: issue-258-260-batched-extraction-fixes

Two follow-ups to the opt-in batched extraction of #254 (open pull request #263), built on its branch
`issue-254-batched-extraction` at `4cc1d5e1`, one commit per issue:

- #258: turns queued for batched extraction stay pending when the engine closes before its flush finishes.
- #260: a batched response that leaves out a turn saves that turn as stating nothing.

## Plan (written before implementation)

### #258

The close (`IngestionPipeline.shutdown`) started every armed flush and then waited the shared 10-second
drain, which one batched request can outlast (each is allowed `extraction.timeout`, 30 seconds by default).
The contained mitigation, as the issue and the brief asked: give the batched flushes at close their own
drain time, the usual drain plus `extraction.timeout` for each request their flushes send, counted the way
a flush composes batches, with a cap so a close cannot hang. No model work at open (RFC-0016: retrieval
never starts model work and there is no daemon).

- Premise: the close waits too little for batched requests. Sibling paths that end in the same drain:
  `MemoryEngine.close()` (scripts, HTTP lifespan, MCP stdio lifespan), the failed-startup callback, and
  `MemoryClient` exit cleanup. All go through `pipeline.shutdown()`, so one change covers them, except
  that `MemoryClient.close()` has its own 30-second wait (found in review, fixed).
- Alternatives: counting queued turns instead of batches (simpler, but a 20-turn single batch would get
  20 timeouts and a provider that does not answer would hold the close through its whole split); waiting
  while flushes make progress (adapts to splits, but is not what the issue asks and needs new progress
  state); flushing queued work at open (ruled out by RFC-0016 and the brief).
- Shared state: none written. One new read-only query (`ExtractionWorkRepository.unfinished`), owner and
  scope bound, like `pending_count`.

### #260

The brief preferred the issue's first mitigation: before saving records, ask again, as a request of their
own, for the turns a response gives no entry, record in the batch record which turns were asked again,
keep per-turn grounding as is, count the extra request in the builder, keep the cache deterministic, keep
existing #254 packs replaying without a model call, and change nothing at option 0.

- Mechanism: `_request_batch(..., ask_again=True)` finds the turns without an entry, saves the batch with
  a new `ExtractionBatchRecord.turns_asked_again` (left out of the JSON when empty, so #254 records keep
  their bytes; not part of the batch identity) and records for the other turns, then calls
  `_request_batch(head, missing turns, ask_again=False)`. That request and its halves never ask again, so
  each response is asked again at most once. Both stores check that a batch saves records for exactly
  its turns not asked again.
- Saving the first batch before asking again means a failed follow-up costs only the turns left out;
  their own jobs ask for them later, as a failed half of a split does today.
- A response that gives none of its turns an entry is not asked again: the request for "the missing
  turns" would be the identical request, with the same cache key and batch identity. It is saved as
  before (follow-up #265).
- Alternative rejected: treating any response with a missing entry as a validation failure and splitting
  it in halves (the issue's second option): it costs up to 2n - 1 requests and re-asks turns that had
  entries; the brief preferred the first option.
- Shared state: `operations` rows of type `EXTRACTION_BATCH_RECORDED` gain an optional field; readers are
  both stores' `get_extraction_batch` and the builder's `_batch_summary`. `EXTRACTION_VALIDATED` records
  are unchanged.

## Review Scope

- Base: `origin/issue-254-batched-extraction` = `4cc1d5e17878e92900f142526ecb494a51c3fabc`.
- Tier L. Predicates: DATA, NEW_SYMBOLS, PERSISTED_FIELD (raised by me for `turns_asked_again`).
  INFRA and SECURITY not detected; a dedicated security review ran because DATA applies.
- Roster (housekeeping `review-plan --base origin/issue-254-batched-extraction --risk PERSISTED_FIELD`):
  core (checklists 1, 3, 4, 5; Opus), security (2; Opus), data (7; Sonnet), adversarial (8; Opus).
  Checklist 6 (infrastructure) not applicable. Checklists 3, 4 and 5 merged into the core report.
- Snapshots:
  - `62e7369a4502f65293bc55e40fea6b6e591f091f323cceb294bb40c47312e54a`: the first full review of all four
    reviewers (head `591b90b1`).
  - `cb7113d1eeb261d1ea69eed8e503eaec459814bf6b4c659c8150c4987a6d24e8`: re-validation of every fix by the
    core and adversarial reviewers.
  - `859001a93956fb4f6f640f36ae1ba0927bea9fa3d1aea1005a00b68073380011`: final code (head `9384209e`, before
    this file was added). It differs from the re-validated snapshot only by the two changes that
    re-validation recommended: a warning when the close-time count is cut off by the cap, and a
    `_closing` check before a batched retry starts its flush, each with a test that fails without it.
- Every required report completed. The reviewers returned their reports as their final messages
  (subagents do not write report files here); the findings below are taken from them.

## Files Changed

- `src/prme/ingestion/pipeline.py`: #258 `BATCH_CLOSE_SECONDS`, `extraction_timeout`, `_spawn_flush` and
  `_flushing`, `_drain_seconds`, `_queued_requests`, the close order in `shutdown()`, batched retries
  through `_spawn_flush`; #260 `ask_again` in `_request_batch`, the follow-up request, the two warnings.
- `src/prme/storage/extraction_work.py`: `unfinished()` (#258).
- `src/prme/storage/engine.py`: passes `extraction.timeout` at both pipeline constructions (#258).
- `src/prme/client.py`: `_CLOSE_SECONDS`; the close allows for the batched close (#258).
- `src/prme/ingestion/extraction.py`: `_parse_batch_output`, `batch_turns_without_entry`; the missing-turn
  warning moved to the pipeline (#260).
- `src/prme/models/extraction.py`: `ExtractionBatchRecord.turns_asked_again`, `saved_turns`, turn numbering
  check (#260).
- `src/prme/storage/event_store.py`, `src/prme/storage/pg/event_store.py`: records must match `saved_turns`
  (#260).
- `benchmarks/diagnostics/extracted_packs.py`: `turns_asked_again`, `turns_without_entry` and
  `turns_per_batch` in `_batch_summary` and `build_summary`; cache docstring (#260).
- Tests: `tests/test_batched_extraction.py`, `tests/test_extracted_packs.py`.
- Docs: `docs/INTEGRATION.md` (Turns left out, Closing, Stopping the process), `docs/RFC-0016-Durable-
  Derivation-Commits.md` (#260 paragraph), `documentation/async-engine.md`,
  `documentation/configuration.md`, `documentation/mcp-server.md`, `BENCHMARKS.md`, `CHANGELOG.md`.

## Approach Summary

See the plan. Review added for #258: `MemoryClient.close()` allows for the batched close; the close-time
flushes start before their requests are counted, the count runs under `asyncio.wait_for` at the cap and is
part of the wait; a batched retry runs its flush as a tracked background flush and starts none after the
close begins; docs for process-manager stop timeouts and the lease a killed process leaves. For #260: the
warnings name only what happened (asked again, or saved as stating nothing), batch records require turns
numbered 1 to n, `turns_per_batch` counts each turn once, and a test pins the bytes and identity #254 gave a
batch record.

## Must Fix

None from any reviewer, in the first review or the re-validation.

## Should Fix

| # | Source | Finding | Resolution |
|---|---|---|---|
| S1 | Core S1, adversarial S2 | `MemoryClient.close()` waited 30 seconds and abandoned a longer engine close, leaving the write queue, indexes and connection open and an encrypted directory pack unencrypted, with no `EncryptionError` | Fixed: with batching on it waits `_CLOSE_SECONDS` plus `BATCH_CLOSE_SECONDS`; `test_a_client_close_waits_for_the_batched_close` fails with the old timeout |

## Consider

| Source | Finding | Resolution |
|---|---|---|
| Core C1 | "One request per batch" in the Closing docs did not name the #260 request | Fixed |
| Core C2 | Two warnings for turns that were then asked again | Fixed: the pipeline warns `ingestion.extraction_batch_turns_asked_again` when it asks again and `ingestion.extraction_batch_turns_missing` only for turns saved as stating nothing; tests capture both |
| Core C3 | Each response is parsed twice | Accepted: a pydantic parse next to a model call; one definition of "no entry" (`batch_turns_without_entry`) |
| Core C4 | `_queued_requests` repeats the composition rule of `_batch_events` | Fixed: `_batch_events` says the count must change with it; `test_the_close_allows_the_timeout_for_each_queued_request` checks the count against the requests a flush then sends |
| Core C5 | The #260 commit message named this file before it existed | Fixed: this file is in that commit |
| Security C1, core note | The close-time count had no time limit and was outside the budget | Fixed: flushes start first, the count runs under `asyncio.wait_for` at the cap and its time is subtracted; tests for both (each fails without its fix) |
| Security C2 | Turn indices were assumed unique | Fixed: batch records require turns numbered 1 to n in order, as the pipeline has always written them |
| Data C1 | A cache filled before #260 lacks the request for turns left out | Documented in the cache docstring and `BENCHMARKS.md`; no LoCoMo response left a turn out |
| Data C2, adversarial S6 | `turns_per_batch` counted a turn asked again twice | Fixed: counts `saved_turns`; test pins 1.5 |
| Data C3 | The #254 bytes were checked by simulation only | Fixed: a test pins the exact JSON and identity #254's own model gives a batch record |
| Core re-validation 1 | No log when the count is cut off | Fixed: warning `ingestion.extraction_close_count_timed_out`; test |
| Core re-validation 2 | One in-flight store query can pass the cap | Accepted: milliseconds, covered by the client's 30-second margin |
| Core re-validation 3 | The cap is computed in `shutdown()` and in `_drain_seconds` | Accepted: one expression in two adjacent methods |
| Core re-validation 4 | Interpreter exit can now wait minutes | Intended and documented |

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or private data in logs | PASS | New events carry counts, ids, seconds and an error type name |
| Tenant isolation of `unfinished()` | PASS | Owner and scope bound parameters; empty owner refused |
| Tenant isolation of the close-time count | PASS | Reads only the owner's jobs, and only a number leaves it |
| Follow-up request mixing owners, scopes or sessions | PASS | Its turns are a subset of a request already limited to one owner, scope and session |
| Relaxed per-turn record check | PASS | Every saved record still passes `verify_source`, `verify_turn`, the batch id check and the claim checks before and after; a turn asked again gets no write |
| SQL injection | PASS | Only `int(limit)` after a range check is formatted in |
| Deserialization of the new field | PASS | Pydantic with `extra="forbid"`, the validator, and the identity check on read |
| Request amplification through turn content | PASS | At most one more request per response, at the same owner's cost |
| One tenant lengthening the close | Consider, accepted | Bounded by the cap; a close is an operator action; MCP stdio serves one owner |
| MCP advice | PASS | `memory_process_extractions` is scoped to the server's owner and budgeted |

## Pattern Consistency Assessment

`extraction_timeout` follows `extraction_lease_seconds` and `extraction_batch_target_tokens` (constructor
default matching the config, passed at both engine constructions). `BATCH_CLOSE_SECONDS` and
`_batch_close_seconds` mirror `BATCH_FLUSH_SECONDS` and `_batch_flush_seconds`. `unfinished()` follows
`pending_count` and `pending_in_session`. `turns_asked_again` uses `exclude_if` like #254's
`ExtractionRecord.batch_id`. The follow-up reuses the split recursion, the head's claim and the write
queue. The client margin reads the pipeline's module constant.

## Redundancy Check

No dead code. `_turns_without_entry` was inlined into `batch_turns_without_entry` after the warning moved.
`_flushing` is needed because `_background_tasks` holds no owner and scope; `unfinished()` has no
equivalent (`pending_in_session` is per session, `pending_count` only counts).

## Wiring Findings

`extraction.timeout` reaches the pipeline at both constructions in `src/prme/storage/engine.py`. All close
paths (scripts, HTTP and MCP lifespans, failed startup, `MemoryClient` close and exit cleanup) end in
`pipeline.shutdown()`. No migration: `operations` accepts any payload on both backends. No new config
field, so the serialized defaults are unchanged.

## Data Safety and Read/Write Proof

| Item | Writer | Reader |
|---|---|---|
| `ExtractionBatchRecord.turns_asked_again` | `IngestionPipeline._request_batch` through `record_extraction_batch` on both stores | `get_extraction_batch` on both stores; builder `_batch_summary` |
| `saved_turns` (derived) | none | both stores' per-turn record check; builder |
| Records of turns asked again | the follow-up's `_request_batch`, `EXTRACTION_VALIDATED`, one per event id | `get_extraction`, `_extract_or_load` |
| Manifest `turns_asked_again`; `turns_without_entry` and `turns_per_batch` meanings | `_batch_summary` | `build_summary` (`manifest.get(key) or 0` for older manifests) |

The data reviewer ran the #254 and new models side by side: identical bytes and identity without the
field, the new model reads #254 bytes, and the #254 model refuses a record that sets it (accepted: #254 is
unreleased and ships with this). PostgreSQL keeps the inner JSON string unchanged. A crash between the two
saves leaves each turn with exactly one record or a pending job; no turn can get two records (the record id
derives from the event id). Both saves run under the head's claim, checked before and after.

## Break Scenarios (adversarial)

Pre-mortem headline of the first review, verbatim: "Batched close waited minutes: Kubernetes SIGKILLed the
server before the pack was re-encrypted, tenants' ingests stalled for 5 minutes behind a dead lease, and
`MemoryClient` gave up at 30 s with `atexit` encryption already disarmed."

Headline of the re-validation, verbatim: "A user hit Ctrl+C on a script that seemed hung, during a
five-minute batched close, and the encrypted pack stayed plaintext because the exit-time encryption had
already been switched off."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| S1 | A process manager kills the server during the longer close; the head job stays leased until `extraction.lease_seconds`, blocking that owner's later turns; an encrypted directory pack stays unencrypted | Newly introduced (wider window) | Medium for batched servers | Fix now (docs), Follow-up #264 | The docs now tell operators to set the stop timeout to cover the close and explain the lease; making the close budget configurable needs a default decision |
| S2 | `MemoryClient` abandons the close at 30 seconds | Newly reachable | Low | Fix now | Contained; done with a test |
| S3 | MCP stdio sessions still end with queued turns when the client stops the server first | Pre-existing (#254) | High for MCP with batching | Accept | Documented, with `memory_process_extractions` as the remedy; nothing is lost; the stronger fix needs the owner's decision below |
| S4 | A response with no entry at all is still saved as stating nothing | Pre-existing, kept on purpose | Low to Medium | Follow-up #265 | Asking again would repeat the request; whether to split needs data from the LongMemEval-S builds |
| S5 | A retry's flush running at close was cancelled at once | Pre-existing (#254) | Low | Fix now | Retries run their flush through `_spawn_flush`; the impact was a repeated request rather than lost turns, because the failed flush re-arms a timer that the close runs |
| S6 | Builder `turns_per_batch` counted a turn asked again twice | Newly introduced | Low | Fix now | One line |
| R1 | Ctrl+C during the longer `MemoryClient` exit leaves an encrypted pack unencrypted (`_atexit_encrypt` skips once `close()` has begun) | Pre-existing gate, wider window | Low | Follow-up #264 | The gate predates this change; the fix belongs with the close budget |
| R2 | A retry dropped from `_retry_tasks` wakes after the close and starts a flush | Pre-existing | Low | Fix now | One-line `_closing` check; test fails without it |

Attacked and held (first review and re-validation): lease expiry or a crash between the two saves, with or
without the head turn left out; `ask_again` through splits and the stores' check; record save order
against claim and supersedence order (graph writes stay per job in work order); flushes spawned before the
count are counted; cancelling the count mid-query on DuckDB (`run_to_completion` finishes the query) and
PostgreSQL; a retry whose sleep ended when the close began; the client timeout with an embedding provider
override; #254 records under the new turn numbering check.

## Follow-ups Raised

- #264 Let operators bound how long a batched close waits (configurable close budget, leases left by a
  killed process, the exit-time encryption gate after an interrupted close).
- #265 Decide what to do with a batched response that gives none of its turns an entry.

## Starting model work when an engine opens

Not done, as the brief required. I still think an opt-in setting that flushes an owner's queued batched
work when an engine opens would help MCP stdio deployments, because many MCP clients stop the server
shortly after the session ends, before even a short close can finish, and an owner who does not come back
leaves those turns unextracted. It goes against RFC-0016's rule that opening and retrieval never start
model work, so it needs the owner's decision; until then the docs point MCP clients to
`memory_process_extractions` before ending a session.

## Live smoke

None. The brief allowed no model calls; every test uses scripted providers.

## Test results

- Full suite (`uv run --no-sync python -m pytest tests/ -q`, DuckDB, `--dev --extra api --extra mcp
  --extra postgres` as in CI) on the final code (snapshot `859001a9`): 5027 passed, 1020 skipped, 1 failed,
  2 deselected, in 11 minutes.
  - The failure is `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`,
    which fails in any git worktree.
  - The two deselected are `tests/test_ingested_corrections.py::test_mcp_owner_declaration_is_strict_and_persisted`
    (DuckDB and PostgreSQL). Two full runs without the deselection died with a segmentation fault right
    after its DuckDB case passed: a worker thread was running a DuckDB query in
    `src/prme/storage/extraction_work.py` (`_Session.fetch`) while the main thread shut the test's event
    loop down. It is not from this change: that test does not turn batching on, where this change leaves
    the close and retry paths as they were, and the base code (`4cc1d5e1`, run from an exported copy with
    the same environment) crashed the same way in 1 of 8 runs of the file, against 1 of 5 runs here. The
    #254 review saw a segmentation fault at the same point of the suite. The test passes whenever the
    process survives its teardown.
- Earlier full run on the first reviewed snapshot: 5023 passed, 1016 skipped, 1 deselected (the worktree
  test), completed without the crash.
- PostgreSQL 16.11 with pgvector 0.8.1 in a local scratch database, final code:
  `tests/test_batched_extraction.py`, `test_extraction_journal.py`, `test_extraction_work.py`,
  `test_derivation_ingestion.py`, `test_durable_ingestion.py`, `test_extracted_packs.py` and
  `test_extraction_retries.py`: 230 passed, 12 skipped (backend-specific skips).
- `uv run ruff check src/ tests/` passed, and the CI mypy check of `tests/typing/public_api.py` passed.
- Each new close test was run against the behavior it covers (old drain, old close order, unbounded
  count, retry inside the retry task, no `_closing` check, old client timeout) and failed there.

## Resolution Status

| Item | Status |
|---|---|
| Must Fix | None raised |
| Should Fix S1 | Resolved |
| Consider items | Resolved or accepted as recorded above |
| Adversarial S2, S5, S6, R2 | Resolved |
| Adversarial S1 | Docs resolved; configurable budget is #264 |
| Adversarial S3 | Accepted; flushing at open needs the owner's decision |
| Adversarial S4, R1 | Follow-ups #265, #264 |
| #258 acceptance (own drain budget from the timeout and queued batches, capped; MCP stdio documented; no model work at open; test past the old drain) | Met and tested |
| #260 acceptance (one more request for the turns left out; batch record names them; grounding unchanged; builder counts the call; cache replays it; #254 packs replay without a model call; nothing changes at 0) | Met and tested |
