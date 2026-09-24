# Code Review: issue-117-deepseek-ollama-answer-runs

## Files Changed
- `benchmarks/integrations/ollama_answers.py` (new): reader and judge calls for the DeepSeek answer
  track. `AnswerModel` holds the model, loopback endpoint and sampling settings (temperature 0, seed
  20260923, `reasoning_effort` `none`, registered token limits). `check_endpoint` accepts only
  `127.0.0.1` or `::1` with `/v1` and no credentials. `describe` records the Ollama server's identity
  (through `run_longmemeval_v2._ollama_reader_identity`) and accepts only cloud models. `call` keeps the
  request, every HTTP attempt and the answer, write-once, with the same retry policy and failure
  messages as the frozen `gpt54_budget.call`. `verify_call` re-checks a recorded call against its prompt,
  settings and retry policy. `same_model` compares recorded settings and identity, ignoring the server
  version.
- `benchmarks/integrations/gpt54_baselines.py`:
  - `--provider {openai,ollama}`; `openai` (GPT-5.4) stays the default and its paid path is unchanged
    apart from recording an `answer_model` block in results and refusing arms the Ollama track started.
  - New `prme` arm (current defaults through the gate's product replay) and named variants
    `prme-<name>` (`--variant NAME` with `--set`), on the Ollama track only. `prepare prme` needs a
    commit on `main`.
  - `calibrate` for the Ollama track: the registered authored cases with both benchmarks' prompts; every
    attempt kept; `run` needs a passed attempt with the same model identity and settings.
  - `run --sample N` smoke checks (Ollama only), labeled as samples with no accuracy or intervals.
  - Arm binding (`answer-model.json`), an append-only run log outside the arm folder (prepare refuses an
    answered arm), an identity re-check after each run, and a summary of what prepared the contexts in
    every result.
  - `compare`: pairs two complete results of the same model and questions with
    `benchmarks.compare_evidence.paired_statistics`.
- `tests/test_ollama_answers.py` (new), `tests/test_gpt54_baselines.py`: offline tests against a mocked
  Ollama server; the paid client, call, key reader, ledger and `dotenv` are replaced with failures.
- `BENCHMARKS.md`: the two tracks, how to run the DeepSeek one, its safeguards, and a baseline table.

## Approach Summary
The issue asked for the option in `gpt54_budget.py` and `gpt54_baselines.py`. `gpt54_budget.py`,
`run_gpt54_comparison.py` and `run_longmemeval_s_baseline.py` are digest-pinned by
`gpt54-comparison-v1-registration.json` and checked by `registered_protocol`
(`gpt54_baselines.py`, `FROZEN_SOURCES`), and `gpt54_budget.py` is also pinned by the Jev registration,
so any edit there would stop every arm from running. The Ollama client therefore lives in a new module,
and the baseline arms select it. `run_longmemeval_v2.py` and `diagnostics/reader_judge.py` are pinned by
other registrations too, so their helpers are imported, not edited.

Alternatives considered:
- Ollama's native `/api/chat` (used by `run_opt_in_interactions.py` and `run_memoryarena_travel.py`, and
  the only route that can set `num_ctx`): rejected because the issue names the OpenAI-compatible
  endpoint, and the chat completions shape keeps the receipts close to the GPT-5.4 ones. The context-size
  gap is closed by accepting only cloud models, which are served at full context (1,048,576 tokens for
  this model).
- `benchmarks/llm_judge.py` or the PRME extraction client (instructor/OpenAI SDK): rejected; neither
  writes per-attempt receipts, so results could not be verified again.
- Reusing `analyze_gpt54_comparison.verify_call`: rejected; it hard-codes the Responses body and the flex
  tier.
- Sharing prepared contexts between tracks: rejected; separate data roots keep one track's answers from
  ever being read as the other's, at the cost of preparing again (offline, about 10 minutes).

Shared state written: the main checkout's `data/ollama-answers-v1/<track>/` (new; contexts, answers,
calibration attempts, run logs) and `benchmarks/results/research/<date>/` (published results with a new
file-name prefix). Nothing else reads the new folder. The GPT-5.4 folder `data/gpt54-baselines-v1/` is
untouched by the Ollama track.

## Must Fix
None from any agent.

## Should Fix
| # | Finding | Source | Status |
|---|---|---|---|
| 1 | `calibrate` refused once any result existed, while `run` refused a calibration of another identity: after an `ollama pull` there was no way forward | Agents 1, 3 | Fixed: every attempt is kept; `calibrate` refuses only when an attempt already passed for this identity and these settings |
| 2 | A calibration that failed partway left no record | Agent 3 | Fixed: errors and malformed verdicts are recorded in the attempt's `result.json` |
| 3 | Replay-verification branches of `verify_call` untested | Agent 3 | Fixed: one tamper test per branch |
| 4 | Retry classification depends on message prefixes across modules, untested | Agent 3 | Fixed: test asserts `_retryable` for each Ollama message and a rerun asks the failed question again |
| 5 | Sample reuse test was trivial (sample equal to the whole arm) | Agent 3 | Fixed: two questions, sample is a strict subset, full run asks only the remainder |
| 6 | BENCHMARKS.md promised paired variant runs the harness could not do | Agents 1, 7 | Fixed: `--variant` arms and `compare` |
| 7 | Identity checked only before a run | Agents 4, 7 | Fixed: checked again after calibration and after each run; a change publishes nothing and is logged |
| 8 | No guard against silent prompt truncation (`/v1` cannot set `num_ctx`) | Agents 4, 7 | Fixed: only Ollama cloud models are accepted |
| 9 | `_module_identity` missed the imported verification helpers | Agent 4 | Fixed: adds `run_longmemeval_v2.py`, `diagnostics/reader_judge.py`, `compare_evidence.py` |
| 10 | GPT-5.4 settings literals duplicated, only one copy checked | Agents 4, 5 | Fixed: `registered_protocol` compares against `OPENAI_ANSWER_MODEL`; a test ties its endpoint to the frozen client |
| 11 | The prme override rule written twice | Agents 4, 5 | Fixed: `_check_overrides` used by `prepare` and the CLI |

## Consider
| # | Finding | Source | Status |
|---|---|---|---|
| 1 | Non-object JSON 200 was retryable; a transient 5xx with a non-JSON body was not retried | Agent 1, 3 | Fixed: status first, raw text kept, malformed 200 is final |
| 2 | `cached_tokens` null or above the prompt count broke `report()` | Agent 1, 3 | Fixed: validated at call time |
| 3 | Results recorded the identity at run start, not the bound one | Agent 1 | Fixed: results carry the bound settings |
| 4 | `calibrate` took no lock | Agent 1 | Fixed: run lock on the calibration folder; records written atomically |
| 5 | A sample result looked like a score | Agents 1, 3 | Fixed: `-sample` kind, no accuracy, intervals or categories |
| 6 | `--sample` reached the paid path | Agent 1 | Fixed: Ollama only |
| 7 | GPT path could answer an Ollama-bound arm | Agent 1 | Fixed: refused |
| 8 | `--archive` ignored on Ollama run and calibrate | Agents 1, 3 | Fixed: refused |
| 9 | `localhost` depends on the hosts file | Agent 2 | Fixed: literal loopback addresses only |
| 10 | Identity lookup honors proxy settings, unlike the chat client | Agents 1, 2, 4 | Accepted: the helper is pinned by other registrations; it carries no prompt or credential, and a proxy would have to impersonate an Ollama server to change the identity. Documented in `identity()` |
| 11 | Calibration kinds and benchmark order differed from the frozen calibration | Agents 4, 5 | Fixed: `positive`/`negative`, LongMemEval-S first |
| 12 | Seed differs from earlier DeepSeek runs (42) | Agent 4 | Kept, commented: the registration's bootstrap seed; this track starts a new series |
| 13 | `contexts_matching_saved_run` recomputed | Agents 4, 5 | Fixed: read from the gate summary |
| 14 | Import alias `ollama` could be confused with the `ollama` SDK | Agent 3 | Fixed: imported as `ollama_answers` |
| 15 | `calibrate` reads the 277 MB LongMemEval-S file to check the registration | Agent 3 | Accepted: once per calibration |
| 16 | Duplicated test fixtures between the two test files | Agent 5 | Accepted: each file stays self-contained |
| 17 | `$0` cost is an assumption about the Ollama plan | Agent 2 | Accepted: the owner's decision in the issue; stated in the PR |

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Ollama path builds the OpenAI client | PASS | Only `ollama_answers.client_for`; tests replace every paid entry point with a failure |
| `OPENAI_API_KEY` read on the Ollama path | PASS | `_api_key` and `dotenv` never called; `run` refuses an API key or cap with a model |
| Credentials in requests | PASS | No auth header; `trust_env=False` |
| Endpoint guard | PASS | Literal loopback only, `/v1`, no userinfo, query or fragment |
| Redirects on the chat call | PASS | httpx does not follow redirects by default |
| Identity lookup proxies | Consider | Accepted, see Consider 10 |
| Private and published receipts | PASS | No keys; published failures drop messages; prepared provenance is summarized without local paths |
| Path traversal | PASS | Track name reduced to `[a-z0-9.-]`; variant names `[a-z0-9-]`, 40 characters |
| Paid run still needs terminal confirmation and a cap | PASS | Unchanged; `--sample` and `prme` refused on the paid path |

## Pattern Consistency Assessment
The call layer mirrors `gpt54_budget.call` (retry set, backoff, attempt files, failure messages) and
`analyze_gpt54_comparison.verify_call`, which cannot be reused because both are pinned to the Responses
API. Response-model matching reuses `reader_judge.matches_response_model`; identity reuses
`_ollama_reader_identity`, as `run_memoryarena_travel.py` already does. `reasoning_effort` `none` on
`/v1` follows `register_agentmembench.py` and PRME extraction. The paired comparison reuses
`compare_evidence.paired_statistics`, as the gate's `compare_gates` does.

## Redundancy Check
No new dependency. `AUTHORED_CASES` repeats the frozen calibration's inline cases; an AST test keeps them
equal. `ollama_answers.call` and `verify_call` repeat logic that lives only in frozen or Responses-bound
code. `answer-model.json` and the calibration identity check overlap, but the binding is what keeps an
arm from mixing answers across a re-pull and recalibration.

## Wiring Findings
No Must Fix. Verified: the Ollama path reads no key or environment; CLI routing across about 40 flag
combinations; the frozen sources and the helper modules pinned by other registrations are unchanged and
still match; `registered_protocol` passes on the real registration (1,540 and 500 questions); CI picks up
the new test file, which needs no network, Ollama or `origin/main`; importing `ollama_answers` adds no
import-time I/O.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | BENCHMARKS.md stated the new default-change policy while `CLAUDE.md` still requires a paid run | Should Fix | Fixed: BENCHMARKS.md says the issue proposes it and the `CLAUDE.md` rules decide until the owner updates them |
| 2 | The DeepSeek runbook left out its prerequisites (Ollama server and sign-in, datasets and official judge for calibration, `git fetch` before `prepare prme`) | Should Fix | Fixed: a prerequisites list |
| 3 | One final failure could block the baseline arm for good, with no documented recovery | Should Fix | Fixed: an arm with no complete run can be prepared again after removing its folder; the run log records it and results report the count |
| 4 | `compare` did not flag contexts prepared from different commits or dirty trees | Consider | Fixed: warnings in the comparison |
| 5 | `compare` CLI tracebacks on a missing file; awkward refusal text; seed not passed explicitly | Consider | Fixed |
| 6 | Model, endpoint and concurrency are fixed on the CLI | Consider | Accepted: the issue names one model and endpoint; concurrency stays the registered 4, and a 429 stop resumes (documented) |
| 7 | Proxy settings affect the identity lookup | Consider | Documented in the prerequisites |

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "DeepSeek paired run flips a default: the variant was measured by
committing the flip and deleting the baseline, against model weights nobody had pinned."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | No way to run a variant, so a variant is measured by committing a default flip or deleting the baseline | new | High | High | Fix now | `--variant NAME` with `--set` prepares `prme-<name>` next to the defaults; `compare` pairs them |
| 2a | Hosted model changes mid-run and the change is credited to the variant | new | Medium-High | High | Fix now | Identity re-checked after calibration and after every run; a change publishes nothing |
| 2b | Cloud nondeterminism with no measured noise floor | pre-existing in kind, newly exposed | Medium-High | High | Follow-up | Needs an owner decision on the protocol (for example a repeated baseline) |
| 3 | Deleting and redoing a run or calibration leaves no trace | new | Medium | High | Fix now | Append-only run log outside the arm; prepare refuses an answered arm; every calibration attempt kept and counted in results |
| 4 | The defaults baseline is whatever branch the main checkout is on | new | Medium-High | Medium | Fix now | `prepare prme` needs a commit on `main`; results carry the prepare commit, dirty flag and overrides |
| 5a | `prme` added to the paid track | new | Low-Medium | Medium | Fix now | `prme` arms and samples run on the Ollama track only |
| 5b | The legacy `python -m benchmarks --llm` suite still defaults to paid OpenAI with no cap or confirmation; `_confirm_spend` only checks for a terminal | pre-existing | Low-Medium | Medium | Follow-up | A system this diff does not touch |
| 6 | The provider silently truncates a long prompt | new | Low | High | Fix now | Only cloud models (full context) are accepted |

## Follow-ups Raised
- #118: DeepSeek paired runs have no measured noise floor, so a small gain can be run-to-run variation
  (adversarial 2b; needs an owner decision on the protocol).
- #119: The legacy benchmark runner still defaults to a paid OpenAI reader and judge with no cap or
  confirmation (adversarial 5b; also covers the terminal-only spend confirmation).

## Resolution Status
| Item | Status |
|---|---|
| Must Fix | none raised |
| Should Fix 1 to 11 | resolved |
| Consider | resolved or accepted as recorded above |
| Adversarial 1, 2a, 3, 4, 5a, 6 | fixed |
| Adversarial 2b, 5b | follow-up issues |
| CLAUDE.md work-rule update (issue criterion 3) | left to the owner, see PR |
| DeepSeek baseline on both benchmarks (issue criterion 6) | not run; needs the merged `prme` arm on `main` |
