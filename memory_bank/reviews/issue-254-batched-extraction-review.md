# Code Review: issue-254-batched-extraction

## Plan (written before implementation)

Issue #254 asks for one option, `extraction_batch_target_tokens` (default 0), that lets one model
call extract several speaker-tagged turns of a session, with every claim citing its turn, per-turn
grounding unchanged, per-event records unchanged downstream, the batch request and response saved
once, and a response that fails validation asked again in halves. Acceptance: at 0 the default
configuration's identity, determinism tests, receipts and extraction records are unchanged; with the
option on every claim cites one turn of its batch and grounding still rejects a quote from another
turn; `prme rebuild` of a batched pack makes no model call; tests cover a split into halves and a
session boundary inside the pending jobs. The cost and coverage report (criterion 5) is the owner's
LoCoMo measurement and is still open, so the pull request must say "Part of #254".

Invariants found in the code that the design keeps:

- Extraction jobs are claimed strictly in work order per owner and scope: `claim()` refuses a job
  while an earlier job of the same owner and scope is pending or running
  (`src/prme/storage/extraction_work.py`, the `NOT EXISTS` clause). RFC-0016: "Pending/running work
  follows append order within an owner and scope".
- Every durable extraction write is fenced by a claim checked inside its transaction
  (`validate_duck_claim`, `validate_pg_claim`).
- `ExtractionRecord` is frozen with `extra="forbid"`; existing records must keep their bytes.
- `benchmarks/integrations/gpt54_baselines.py` `_check_defaults_build` compares a build's recorded
  `engine_config` (a full `PRMEConfig.model_dump`) with the defaults at the current commit, key by
  key. A new field that appears in the dump at its default would make every earlier `ingest()`
  baseline, including the build running now, look like it was not built with the defaults.

Approach:

1. Config: `extraction_batch_target_tokens` with `exclude_if=lambda value: value == 0`, so the
   serialized default configuration is byte-identical; a validator refuses it with the window or fact
   text resolution; a hypothesis audit gate.
2. Pipeline: the worker that claims a session's first pending job composes the batch from that job and
   the session's next pending jobs in work order (same owner, scope and session, no saved record),
   cut greedily at the target by cl100k token count, sends one request, splits the response by cited
   turn, and checks each part with the existing single-turn validators. In one transaction fenced by
   its own claim it saves an `EXTRACTION_BATCH_RECORDED` operation and each turn's
   `EXTRACTION_VALIDATED` record with a new optional `batch_id`. The other jobs stay pending behind it
   and load their records when their own claims come, so per-event claims, leases, retries, plans and
   receipts are untouched.
3. Composition depends on data and flush points, not timing: with batching on, `ingest()` without
   waiting only queues the turn; a waiting `ingest()`, `ingest_batch()`, `process_extractions()`, the
   engine's close and a 30-second flush timer extract the queue. Each flush is bounded by the work
   order seen when it starts.
4. Builder: batched builds ingest each session with `ingest_batch(wait_for_extraction=True)`, log calls
   and tokens once per session, key the cache on the full batch request, and gain a `summary` command.

Alternatives considered and rejected, with evidence:

- Claiming every member job before the request: `claim()` refuses later jobs while the first is
  running (`extraction_work.py`, `NOT EXISTS ... prior.status IN ('pending', 'running')`), so this would
  need a new multi-claim protocol and would break the work-order rule.
- Recording member records with the existing `record_extraction`: it validates the member's own claim
  (`event_store.py` `_record_extraction_sync` calls `validate_duck_claim` for the record's event), which
  the head's worker does not hold. A batch writer fenced by the head's claim is the smallest change.
- A per-event link table instead of `batch_id` on the record: the issue asks that each event's
  extraction record point to the batch; `exclude_if` keeps unbatched records' bytes, so the field costs
  nothing at the default. No new table is needed: `operations` accepts any `op_type` on both backends
  (`storage/schema.py:210`, `pg/schema.py:114`).
- Spawning the per-event background task as today and composing from whatever is pending: the task
  starts while the caller is still ingesting the session, so composition would depend on timing; the
  builder could not reproduce batches on a rebuild.
- A pure debounce timer: unbounded latency under continuous traffic. The timer is armed once per owner
  and scope and not re-armed by later turns (maximum wait), and explicit flushes win.

Shared state touched: the `operations` table (two op types, new rows only when the option is on),
`event_extractions` (read only, plus the existing claim with two optional filters), and the build
records of the benchmark builder. Nothing at the default.

## Review Scope

- Base: `origin/main` 58caddb1a88dd60d51fc97c15742aee6ec8cf34a.
- Reviewed snapshots (housekeeping `review-plan --base origin/main --risk PERSISTED_FIELD`):
  017afd20e777a657b6d7f85d4a341aff69753dbd666e8e5dd2858f1ccfcc2c8d (first full review),
  8f8a5bdf540d82f68bc39d8c172c3f9bcd2419616e29a73c7475b1262c70a4c7 (re-validation of all fixes),
  7d655fb4158e2e65665d7d8625594477408fcf71481affb6a100ca0e23916088 (re-validation of the split bound and
  the re-arm). Final snapshot 1f9c5d2b5c7e1175706470a583cfff55be9a1150883cd41f4599690f4c1db122 differs
  from the last validated one only by the two changes that review recommended (the stop exception's
  name, `except Exception` instead of `except BaseException`) and a docstring reflow.
- Tier L. Predicates: DATA, NEW_SYMBOLS, PERSISTED_FIELD (raised by me: `ExtractionRecord.batch_id`
  and the two durable operation types). INFRA and SECURITY were not detected; a dedicated security
  review ran because DATA applies.
- Roster: core (checklists 1, 3, 4, 5; Opus), security (2; Opus), data (7; Sonnet), adversarial (8;
  Opus). Checklist 6 (infrastructure) not applicable. Merged coverage: 3, 4 and 5 in the core report.
- Every required report completed. The reviewers could not write report files (the harness refuses
  report files from subagents), so each returned its full report as its final message; the findings
  below are taken from those messages.

## Files Changed

- `src/prme/config.py`: `extraction_batch_target_tokens` (0 to 65536, excluded from the dump at 0) and
  `_validate_extraction_batching`.
- `src/prme/config_audit.py`: activation gate.
- `src/prme/ingestion/extraction.py`: `BatchTurn`, `BATCH_EXTRACTION_SYSTEM_PROMPT` (general rules,
  batch guidance, every role policy), `batch_request_messages`, `batch_request_sha256`,
  `_BatchExtractionResult` (lenient items, raw capture), `validate_batch_output`,
  `batch_response_fields`, `BatchExtractionProvider`, `InstructorExtractionProvider.extract_batch`;
  `_DEFAULT_ROLE_GUIDANCE` factored out with identical bytes.
- `src/prme/ingestion/pipeline.py`: batching in `ingest`, `ingest_batch`, `process_extractions`,
  `_extract_or_load`, retry and shutdown; `_flush`, `_arm_flush`, `_arm_if_unfinished`, `_spawn`,
  `_extract_in_batch`, `_batch_events`, `_request_batch`, `BatchSplitStopped`; `_append_event`,
  `_scope_lock` and `_GROUNDING_POLICY` factored out with identical behavior.
- `src/prme/ingestion/__init__.py`: exports `BatchExtractionProvider` and `BatchTurn`.
- `src/prme/models/extraction.py`: `ExtractionRecord.batch_id`, `ExtractionBatchTurn`,
  `ExtractionBatchRecord`, `extraction_batch_id`.
- `src/prme/storage/extraction_work.py`: optional `scope` and `through_order` on `claim`;
  `work_order`, `pending_count`, `pending_in_session`.
- `src/prme/storage/event_store.py`, `src/prme/storage/pg/event_store.py`: `record_extraction_batch`
  and `get_extraction_batch`.
- `src/prme/storage/engine.py`: wiring at both pipeline constructions.
- `benchmarks/diagnostics/extracted_packs.py`: batched builds, batch cache, batch metrics, `summary`.
- Tests: `tests/test_batched_extraction.py` (new), `tests/test_extracted_packs.py`,
  `tests/test_config_audit.py`.
- Docs: `docs/INTEGRATION.md`, `docs/RFC-0016-Durable-Derivation-Commits.md`, `BENCHMARKS.md`,
  `CHANGELOG.md`, `documentation/configuration.md`, `documentation/async-engine.md`.

## Approach Summary

See the plan. During review the following were added: timeouts and provider refusals split as well as
validation failures, with a single-turn timeout or refusal stopping the split; a flush that leaves
work behind arms the next one; the stored response limited to the fields the checks read; the batch
writer checks the sending job's owner and scope and refuses managed turns without a claim; builder
metrics for turns without an entry and claims dropped by grounding; `--max-turns` stops at a
session's end.

## Must Fix

None from any reviewer.

## Should Fix

| # | Source | Finding | Resolution |
|---|---|---|---|
| S1 | Core | A flush that claims nothing (another worker holds an earlier job, a write conflict) left the queued turns with no in-process driver | Fixed: `_arm_if_unfinished` after the flush loop, using `pending_count`; test `test_a_flush_left_behind_another_workers_job_arms_the_next` |
| S2 | Core, adversarial 1 | Timeouts were never split, and the retry rebuilt the same batch, so a large batch under the default 30-second timeout could fail a session turn after turn | Fixed: `TimeoutError` and `BadRequestError` split; the builder caches only validation failures; tests for both |
| S3 | Core | No tests of the batch writer's fencing and validation | Fixed: `TestTheBatchRecord` on both backends |
| S4 | Core | No unit test of `extract_batch` | Fixed: `TestTheProvider` |
| S5 | Core | `documentation/configuration.md` lacked the environment variable | Fixed, plus a paragraph in `documentation/async-engine.md` |
| S6 | Core (re-validation) | Splitting timeouts let a provider that never answers cost up to 2n - 1 timeouts per attempt | Fixed: `BatchSplitStopped`; a single-turn timeout or refusal stops the split (about log2(n) + 1 requests); documented; test `test_a_provider_that_does_not_answer_stops_the_split` |

## Consider

| Source | Finding | Resolution |
|---|---|---|
| Core | A partial `ingest_batch` (append failure) left earlier messages unflushed | Fixed: arms a flush; test |
| Core | `scope.value` assumed a `Scope` | Fixed: `Scope(scope)` coercion |
| Core, data, adversarial 2 | Turns without an entry were silent | Fixed: warning `extraction_batch_turns_missing`; builder `turns_without_entry`; asking again is follow-up #260 |
| Core | A swallowed failure of a split's other half was not logged | Fixed: `ingestion.extraction_batch_part_failed` |
| Core | Head's half failing had no test | Fixed: `test_the_senders_half_failing_fails_it_and_keeps_the_other_half` |
| Core | `BatchExtractionProvider` unused and not exported | Fixed: exported and used in the pipeline guard |
| Core | `_batch_attribute` wrapper | Fixed: inlined `html.escape` |
| Core | Third wording of the assistant policy | Fixed: comment ties `_BATCH_ASSISTANT_GUIDANCE` to the assistant prompt |
| Core | `batched_turns` counted single-turn requests | Fixed: replaced by `turns_per_batch`, `single_turn_batches`, `turns_without_entry`, `response_claims`, `saved_claims` |
| Core, data 5, adversarial 7 | `--max-turns` cut sessions, so a resumed build composed other batches | Fixed: stops at a session's end; test |
| Core | Cache hits per request vs per turn | Documented in `BENCHMARKS.md` |
| Core (re-validation) | A waiting flush that raised skipped the re-arm | Fixed: re-arm in the exception path, not on cancellation; test |
| Core (re-validation) | `except BaseException` too wide; private class name in failure logs | Fixed: `except Exception`; the class is `BatchSplitStopped` |
| Core | No post-insert re-read in `record_extraction_batch` | Accepted: first writer wins by design, fencing makes a conflicting earlier record practically unreachable, and a pre-existing record is the one every reader uses |
| Core | Two queries per candidate in `_batch_events` | Accepted: only matters after a restart or a split, bounded by `_BATCH_SCAN_LIMIT` |
| Core, security C1 | Turn content is not escaped inside `<turn>` tags | Accepted and documented: quotes must stay exact spans of the source, and grounding checks each quote against the cited turn's real text |
| Security C1 | The docs overstated grounding ("a claim that only another turn supports is rejected") | Fixed: `docs/INTEGRATION.md` now says the quote, subject and object are checked and the rest is model output, and that turns of a batch can affect each other |
| Security C2, data 2 | The batch record stores unchecked output, including extra text the model returned | Fixed: `batch_response_fields` keeps only turn, entities, facts and relationships; docstrings and RFC-0016 say this record keeps unchecked output |
| Security C3 | The batch writer did not check the sending job's owner | Fixed: both stores check the sender's owner and scope; test |
| Security C4 | No cross-tenant test | Fixed: `test_owners_and_scopes_never_share_a_batch` |
| Data 1 | The batch record had no production reader | Fixed: the builder's `_batch_summary` reads it |
| Data 3, adversarial 6 | Older releases cannot read records with `batch_id` | Documented in `CHANGELOG.md`, `docs/INTEGRATION.md` and the model docstring |
| Data 4 | No fencing or idempotence test | Fixed in `TestTheBatchRecord`; a write without a claim for managed turns is now refused in both stores |

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or private data in logs | PASS | New log events carry IDs, counts, fixed reasons and reason codes only |
| Error messages returned to callers | PASS | Fixed strings; provider causes only as `__cause__` |
| Owner scoping of new reads | PASS | `get_extraction_batch`, `work_order`, `pending_count`, `pending_in_session` are owner-scoped |
| Owner scoping of new writes | PASS | Every turn verified against its source; sender owner and scope checked (after fix C3) |
| Claim and flush filters | PASS | `scope` and `through_order` only narrow the owner-filtered claim |
| Tenant isolation | PASS | SQL and Python both require owner, scope and session; test added (C4) |
| SQL injection | PASS | f-strings insert only `$N` placeholders and `int(limit)` |
| Prompt injection via attributes | PASS | Speaker and role escaped |
| Prompt injection via turn content | Consider (C1) | Documented; grounding guards quotes |
| Unsafe deserialization | PASS | JSON and pydantic only |
| Instructor template injection | PASS | No validation context passed to the batched call |
| Credentials | PASS | None |

## Pattern Consistency Assessment

Compared with the windowed extraction option (#218), fact text resolution (#219), speaker grounding
(#230), `record_extraction` in both stores and the existing `ExtractionCache`: config field with a
`[HYPOTHESIS]` tag, cross-option validator, audit gate, wiring at both engine constructions, both
backends, builder cache key, CHANGELOG, INTEGRATION and BENCHMARKS entries all match. The one missing
piece, the `documentation/configuration.md` row, was added (S5).

## Redundancy Check

No duplicated utility remains: the wrapper was inlined, the protocol is used, the batch reader has a
production caller, and the factored-out constants keep identical bytes. `_admitted_items` overlaps the
first half of `discard_malformed_claims` without its quantity repair; it is kept because the repair
still runs on the raw output in the per-turn check.

## Wiring Findings

The option is read at both pipeline constructions in `src/prme/storage/engine.py`, through
pydantic-settings (`PRME_EXTRACTION_BATCH_TARGET_TOKENS`). The builder reads it from the build config.
New exports are registered in `prme.ingestion.__init__`. No migration is needed. The new test file is
picked up by `pytest tests/`.

## Data Safety and Read/Write Proof

| Item | Writer | Reader |
|---|---|---|
| `ExtractionRecord.batch_id` | `IngestionPipeline._request_batch` | `get_extraction` on both stores; `benchmarks/diagnostics/extracted_packs.py` `_batch_summary` |
| `EXTRACTION_BATCH_RECORDED` | `EventStore` and `PgEventStore` `record_extraction_batch` | `get_extraction_batch` on both stores; `_batch_summary` |
| `work_order`, `pending_count`, `pending_in_session` | read only | `pipeline.py` flush, `process_extractions`, `_batch_events` |
| `claim(scope, through_order)` | existing claim write | flush and `process_extractions` |

Existing records keep their bytes (`exclude_if`; pinned JSON in
`test_the_single_turn_request_and_record_keep_their_bytes`). The default configuration's serialized
form is unchanged (`test_the_default_configuration_keeps_its_identity`, which runs
`_check_defaults_build` against a recorded `engine_config` without the key). Writes are one transaction
fenced by the sender's claim before and after, first writer wins, and nothing is ever deleted.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 8, verbatim): "Batched extraction wedged a user's session for hours: every
batch exceeded the 30 s extraction timeout, timeouts were never halved, and each turn failed in turn
while new turns kept the batch too large, while the coverage numbers hid the turns the model skipped."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | Timed-out or oversized batches never split; the session fails turn after turn | Newly introduced | Medium-High | Fix now | Contained; timeouts and refusals now split, and a single-turn timeout stops the split so a dead provider stays cheap |
| 2 | A turn the model leaves out is saved as stating nothing | Newly introduced | Medium | Fix now (detection), Follow-up #260 (asking again) | The warning and builder counts make it visible for the measurement; whether to ask again depends on how often it happens |
| 3 | The speaker's name is always shown, and claims written with the name are dropped by grounding | Newly introduced | High | Fix now (counts), Accept (showing the speaker) | The issue requires the speaker in the request; the prompt now tells the model to keep I, me, my, you and we (the second smoke run kept 44 claims against 33), and the builder reports `response_claims` next to `saved_claims` |
| 4 | Close gives the batched flush only the 10-second drain; MCP stdio sessions end with queued turns | Newly introduced | High for MCP with batching | Follow-up #258 | Fixing it needs a drain budget or a decision about starting model work at open; documented |
| 5 | A waiting ingest fails because another worker claimed the next job between claims | Pre-existing pattern, widened | Low-Medium | Accept | Same semantics as a waiting `ingest()` today ("pending behind earlier work"); the source is saved and the flush re-arm finishes the work; loud to the caller |
| 6 | Older releases reject batched records | Newly introduced | Low | Accept | Opt-in; documented in CHANGELOG, INTEGRATION and the docstring |
| 7 | Builder batches depend on `--max-turns` and crash points | Newly introduced | Low-Medium | Fix now | `--max-turns` now stops at a session's end; a crash in the middle of a split remains a documented edge |

Attacked and held: a stale worker writing member records (fenced by the sender's claim in one
transaction), the same batch requested twice by concurrent flushes (scope lock, work order, first
writer wins), process death between saving a batch and publishing its turns (records are durable per
turn and loaded later without a model call).

Tally: Fix now 4 (1, 2 detection, 3 counts, 7), Follow-up 2 (#258, #260), Accept 3 (3 speaker shown,
5, 6), Dismissed 0. Follow-up #259 records a design limitation found in the plan, not a scenario.

## Follow-ups Raised

- #258 Turns queued for batched extraction stay pending when the engine closes before its flush finishes.
- #259 Batched extraction cannot be combined with the extraction window or fact text resolution.
- #260 A batched extraction response that leaves out a turn saves that turn as stating nothing.

## Live smoke

Two runs on LoCoMo conv-26, 40 turns, `extraction_batch_target_tokens=512`, `deepseek-v4.1-flash:cloud`
through the local Ollama server (0.40.4), builds kept in a scratch folder. Each run made 5 calls (2, 2
and 1 per session), about 6,400 prompt and 2,200 to 2,600 completion tokens per call, and every
response parsed with one entry per turn; no request was split. The first run kept 33 claims on 19
turns: the model wrote the speaker's name where the turn says I, and grounding dropped those claims.
After the batch guidance was changed to keep I, me, my, you and we as written, the second run kept 44
claims on 25 turns. No more model runs were made. These numbers are a parse check, not a measurement.

## Test results

- `uv run pytest -q` (full suite, DuckDB) on the final snapshot: 5115 passed, 1008 skipped, 1 failed.
  The failure is `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`,
  which fails in any git worktree and also failed on the base in this worktree. One earlier full run
  crashed with a segmentation fault in a DuckDB worker thread at about 44 percent while other
  processes were running; it did not recur in three runs of the file at that point or in the two
  later full runs.
- PostgreSQL 16 with pgvector, run locally: `tests/test_batched_extraction.py` 57 passed; with
  `test_extraction_journal.py`, `test_extraction_work.py` and `test_derivation_ingestion.py`, 140 passed.
- `uv run ruff check src/ tests/` passed; the CI typing check of `tests/typing/public_api.py` passed.

## Resolution Status

| Item | Status |
|---|---|
| Must Fix | None raised |
| Should Fix S1 to S6 | Resolved |
| Consider items | Resolved or accepted as recorded above |
| Adversarial 1, 2 (detection), 3 (counts), 7 | Resolved |
| Adversarial 2 (asking again), 4 | Follow-ups #260, #258 |
| Adversarial 3 (speaker shown), 5, 6 | Accepted as recorded |
| Acceptance criteria 1 to 4 | Met and tested |
| Acceptance criterion 5 (LoCoMo cost and coverage report) | Met: `benchmarks/results/research/2026-10-15/batched-extraction/BATCHED-EXTRACTION.md` (all three sizes, a repeat per-turn build and a one-turn control) |
| Issue step 4 (LongMemEval-S) | In progress: the 42-question development slice first, by the owner's choice |
