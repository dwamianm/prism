# Code Review: issue-95-full-context-and-plain-rag-baselines

## Files Changed
- `benchmarks/integrations/gpt54_baselines.py` (new): the baseline arms for the registered GPT-5.4
  comparison. `full_context` renders a LoCoMo conversation with a date header per session and one
  `Speaker: text` line per turn, from the registered `source_turns` boundary. `registered_protocol`
  checks the parts the arms reuse against the v1 registration. `prepare`, `load_prepared` and
  `estimate` make no model calls. `run`, `_answer` and `report` make the paid reader and judge calls
  through the frozen `gpt54_budget.call`, then authenticate every answer with `verify_call`. The CLI
  asks for confirmation at an interactive terminal before `run`.
- `benchmarks/diagnostics/product_packing.py`: the evidence gate gains `--plain vector|bm25|rrf`.
  New `PLAIN_METHODS`, `PLAIN_RRF_K`, `check_plain`, `context_limit`, `plain_ranking`,
  `plain_record`, `pack_records` and `_replay_plain_case`. `_gate_row` and `_first_ranks` are shared
  by the PRME and plain replays. `_write_capture` takes the record to store. Plain reports record
  `plain` and `plain_rrf_k` in their provenance and say in their summary that no context is expected
  to match the saved PRME run. Default gate output is unchanged.
- `tests/test_gpt54_baselines.py` (new) and `tests/test_product_packing_diagnostic.py`: tests for the
  arms, the paid-run path against a mocked provider, and the plain gate mode.
- `BENCHMARKS.md`: the `--plain` gate option and a new section on the baseline arms, their gate
  numbers, cost estimates, commands, safeguards and a results table awaiting the paid runs.

## Approach Summary
The registered harness (`run_gpt54_comparison.py`) is frozen: its digest is in the registration, and
its `validate()` already fails on `main` because it pins every `src/prme` file. The arms therefore live
in a new module that imports the harness's prompts, question rows, verdict rule and bootstrap, the
budgeted `call()` and `Ledger`, the amended official judge loader, and the v1 call verifier. They
check the registration's prompts, model settings, frozen sources, loader amendment, dataset digests
and question order before doing anything.

Plain RAG ranks the saved packs' stored turns directly through the vector and lexical indexes, as
`benchmarks/retrieval_eval.py` does, fuses with the existing `reciprocal_rank_fusion`, and packs
plain `(date) speaker: text` lines to 3,996 tokens. It lives in the gate module, so `prepare` for a
plain arm runs the gate with the same code and the gate report describes the exact contexts sent to
the reader.

Alternatives considered:
- Configuring `retrieve()` as plain RAG through `--set`: rejected. `ScoringWeights` must sum to 1 and
  keep recency, salience and confidence (`src/prme/retrieval/config.py:20-128`), and session
  expansion, epistemic filters and the renderer would still run. That measures PRME, not a baseline.
- Adding arms to `run_gpt54_comparison.py`: rejected. Its digest is recorded in
  `gpt54-comparison-v1-registration.json` (`sources`), and `analyze_gpt54_comparison.verify_protocol`
  calls its `validate()`.
- Reusing `benchmarks/retrieval_eval.evaluate_question`: rejected. It re-ingests LongMemEval into
  fresh packs, covers LongMemEval only, and ranks source IDs to a fixed `k`.
- Reusing `benchmarks.evidence.pack_sources`: rejected. It re-tokenizes the whole context for every
  candidate, and its `[id; date; role]` rendering and `\n\n` separator would change results that
  `retrieval_eval` reports depend on if it were generalized.

Offline evidence gate at 3,996 tokens (final tree; defaults still reproduce 1,540/1,540 and 500/500
saved contexts):

| Run | LoCoMo all evidence | LoCoMo multi-hop | LoCoMo projected | LongMemEval-S all evidence | LongMemEval-S projected |
|---|---:|---:|---:|---:|---:|
| PRME defaults | 983/1,536 (64.0%) | 45/282 | 64.0% | 403/470 (85.7%) | 86.0% |
| PRME reader format, score order | 1,202/1,536 (78.3%) | 103/282 | 73.7% | 396/470 (84.3%) | 85.1% |
| Plain vector | 1,232/1,536 (80.2%) | 149/282 | 74.8% | 403/470 (85.7%) | 86.0% |
| Plain BM25 | 1,112/1,536 (72.4%) | 101/282 | 69.7% | 357/470 (76.0%) | 80.1% |
| Plain RRF | 1,283/1,536 (83.5%) | 153/282 | 77.3% | 403/470 (85.7%) | 86.0% |

Plain RRF beats plain vector on LoCoMo (+3.3 pp, +1.8 to +4.9) and ties it on LongMemEval-S, so it is
the plain arm to run. Offline estimates from the prepared contexts: LoCoMo full context $44.92
(minimum cap $46.98), LoCoMo plain RRF $12.11 ($13.28), LongMemEval-S plain RRF $4.46 ($5.69).

## Must Fix (all resolved)
- **Resume retried failures the registered retry policy treats as final** (Agents 1, 3, 4). Resolved:
  a later run asks again only when no answer or verdict was received (budget stop, provider HTTP
  error, ambiguous transport failure, interrupted attempt). Truncated or malformed responses and
  invalid verdicts are final. `RETRY_POLICY` is recorded in every result.
- **A resumed run with a different cap recorded a false failure and crashed** (Agents 1, 2, 3, 4, 6,
  7). Resolved: `_ledger` checks the cap before any attempt folder exists. It refuses to lower the cap
  and records a raised cap in the ledger's `cap_history`.

## Should Fix (all resolved)
- **Run state belonged to one checkout** (Agents 2, 6, 7): data, ledgers and locks now live in the
  main checkout's `data/gpt54-baselines-v1/`, and each arm's ledger sits outside its folder, so
  removing a preparation keeps the spend and `prepare` refuses an arm that has made paid calls.
- **Approval was self-asserted** (Agents 2, 7): `run` needs an interactive terminal and the arm name
  typed back; the docs no longer show a dollar figure.
- **Interrupted attempts vanished** (Agents 1, 7): an attempt with neither result nor failure is
  reported as `Interrupted` and asked again.
- **The registration check skipped the frozen call path** (Agents 4, 7): `FROZEN_SOURCES` digests and
  the loader amendment are checked; the calibration result must be complete and registered.
- **`report()` checked less than `verify_benchmark`** (Agents 1, 4): each row must equal the values
  rebuilt from its context, calls and verdict, and the context is recounted against the budget.
- **Result schema fell short of v1** (Agent 4): start and finish times, ledger digest, retrieval
  seconds, HTTP attempts and statuses, observed cost, provenance and module digests are recorded.
- **Circular import between the gate and the harness** (Agents 3, 4, 5, 6): the plain primitives
  moved into the gate module.
- **Worker exceptions could kill the run mid-call** (Agent 3): the whole question body is inside the
  `try`; records are written through a temporary file.
- **LongMemEval-S parsed up to four times** (Agent 3): questions are loaded once and passed down.
- **Two manifest layouts; 1,540 copies of 10 contexts** (Agents 3, 4, 5): every entry has a `path`,
  full context stores one file per conversation (135 MB to 1.3 MB), and the manifest's text digest is
  named `text_sha256`.
- **Untested plain `prepare` path** (Agent 3): a test prepares a plain arm through the gate and then
  answers its contexts.

## Consider
- Resolved: official judge loaded and verified once per run (Agent 2); failure messages left out of
  the published copy (Agent 2); manifest paths contained (Agent 2); result written inside the lock
  (Agents 1, 2, 3); `plain_ranking` limited to stored turns with full vector coverage (Agent 1); UTC
  dates (Agent 1); `plain` omitted from default gate provenance (Agent 1); concurrency read from the
  registration and progress printed every 25 answers (Agent 4); dated results folder (Agents 4, 6);
  shared `_gate_row` (Agents 3, 4, 5); `context_limit` helper (Agent 5); clear missing-key error
  (Agents 3, 6); `--max-usd` rejected outside `run` and when not finite (Agents 3, 6); prepare progress
  every 100 questions (Agent 6); CLI `prepare` refuses a dirty tree (Agent 7).
- Accepted: `pack_records` counts the joined context once per admitted record, about 45 ms a question;
  a full plain gate over 2,040 questions takes about 5 minutes. `plain_ranking` loads the user's turns
  per question, about 11 s over LoCoMo. Cross-module use of `gate._write_report` and
  `gate._quiet_offline_cli` follows the repo's existing habit (`lme._reader_prompt`,
  `lme._tree_identity`). `estimate` reads the saved archive without digest checks; it is a planning
  number only.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| API key in logs, failures, results | PASS | Key only in the request header; the frozen `call()` replaces transport exceptions with fixed text. |
| Benchmark text or private paths in public results | PASS | Public rows hold IDs, types, verdicts, digests and counts; failure messages stay private. |
| Path traversal | PASS | Arm and benchmark are fixed choices; manifest paths must stay in the arm folder. |
| Unsafe deserialization or code execution | PASS | JSON only; the official judge is exec'd once, from the digest-checked file, as the v1 run did. |
| Spending safety | PASS | Interactive confirmation, enforced cap, one ledger and lock per arm shared by every worktree. |
| Credentials in tests | PASS | Tests pass `api_key="test"` and a mock transport. |

## Pattern Consistency Assessment
The run loop, usage totals and verification mirror `run_gpt54_comparison.run` and
`analyze_gpt54_comparison.verify_benchmark`. Those modules are pinned by the registration and the
published verification, so the pattern is copied rather than shared, and the new module calls their
helpers wherever it can. The plain gate path reuses `_source_key`, `projected_correct`,
`summarize_gate`, `compare_gates` and the capture layout.

## Redundancy Check
No new dependencies. `reciprocal_rank_fusion`, `source_turns`, `verify_call`, `usage_cost`, `Ledger`,
`call`, `client_for`, `confidence`, `verdict`, the prompt builders, `load_prompt`, `_clone_pack` and the
gate's configuration and reporting are reused. `pack_records` differs from `pack_sources` for the
reason given above; its docstring says so.

## Wiring Findings
CI collects both test files; they need no local data, network or model files. `data/` is gitignored.
`BENCHMARKS.md` documents every command, input and safeguard, and its commands match the CLI.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "The owner approves $4.50, $12 and $45, and every arm stalls a few
percent short on reservation headroom. The ledger refuses a raised cap, and the workaround people reach
for (a new worktree or deleting `spending.json`) publishes a 'complete' result whose cost silently
leaves out the first run."

| # | Scenario | Label | Likelihood | Verdict | Resolution |
|---|---|---|---|---|---|
| 1 | Approved caps too small for in-flight reservations; cap cannot be raised; reset ledger understates cost | New | High | Fix now | `estimate` reports `minimum_cap_usd`; caps can be raised with history; `report` refuses a ledger below the verified cost |
| 2 | Run state per checkout, deletable, so spend and results can be re-rolled | New | Medium | Fix now | Data at the main checkout; ledger outside the arm; `prepare` refuses an arm with spend |
| 3 | Nothing ties `--max-usd` to an approval | New | Low | Fix now | Interactive confirmation; no dollar figure in docs |
| 4 | Killed attempts vanish from the failure list | New | Medium | Fix now | Reported as `Interrupted` and retried |
| 5 | Registration check skips the frozen call path | New | Low | Fix now | Frozen source and loader amendment digests checked |
| 6 | Prepared contexts not tied to a commit; gate numbers from uncommitted code | New | Medium | Fix now | CLI `prepare` needs a clean tree; gate re-run on the final code, numbers identical |
| 7 | Retry policy differs from the registration | New | Low | Accept (part) | Outcome-bearing failures are final. Accepted: an ambiguous transport failure is asked again in a later run. No answer was seen, so the retry cannot depend on the outcome; it can charge one request twice, which the cap bounds. Re-checked alternative: treating it as final would leave an arm permanently incomplete after one network error, against the issue's zero-unreplaced-failures criterion. Disclosed in `RETRY_POLICY` and `BENCHMARKS.md`. |

## Follow-ups Raised
None. Every scenario was fixed in this change or accepted above. The paid runs and the results table
remain as acceptance criteria of #95 itself.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Retry policy | Must Fix | Resolved |
| Cap change on resume | Must Fix | Resolved |
| Per-checkout state | Should Fix | Resolved |
| Approval | Should Fix | Resolved |
| Interrupted attempts | Should Fix | Resolved |
| Registration coverage and calibration | Should Fix | Resolved |
| Report verification and schema | Should Fix | Resolved |
| Circular import | Should Fix | Resolved |
| Worker exceptions, atomic records | Should Fix | Resolved |
| Repeated dataset parsing | Should Fix | Resolved |
| Manifest layout and naming | Should Fix | Resolved |
| Plain prepare test | Should Fix | Resolved |
| Consider items | Consider | Resolved or accepted as listed |
