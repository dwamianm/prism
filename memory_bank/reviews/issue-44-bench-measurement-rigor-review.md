# Review — issue-44-bench-measurement-rigor

Issue #44: bench measurement rigor — pinned judge model, cached verdicts,
multi-run scoring (gates #33). Base branch: `main`.

## Files Changed

- `benchmarks/llm_judge.py` — pinned judge (`judge_provider`/`judge_model` +
  `judge_provider_string`); `JUDGE_ERROR` NaN sentinel + `is_judge_error`;
  file-backed `VerdictCache`; `judge_answer` takes a `cache` arg, uses the
  pinned judge, returns the sentinel on infra failure; generation failure also
  signals an infra error (see fixes).
- `benchmarks/scoring.py` (NEW) — `aggregate_runs`, `summarize_multi_run`,
  `QuestionAggregate`, `MultiRunSummary`, `CORRECT_THRESHOLD`, mixed-run
  guardrail (`detect_mixed_run`, `assert_single_run_provenance`,
  `MixedRunReportError`).
- `benchmarks/models.py` — `QueryResult` gains `run_id` + `judge_error`;
  serialized in `to_dict`.
- `benchmarks/report.py` — `load_report` applies the mixed-run guardrail.
- `benchmarks/runner.py` — `verdict_cache` threaded through runner → adapters.
- `benchmarks/__main__.py` — `--judge-provider/--judge-model/--judge-cache/--runs`;
  multi-run loop stamping `run_id`; guardrail on `--retry-failed`;
  `_print_multi_run_summary`.
- `benchmarks/locomo.py`, `benchmarks/longmemeval.py` — `run_with_llm` gains
  `verdict_cache`; judge calls pass the cache; infra errors flagged via
  `judge_error`, excluded from accuracy.

## Approach Summary

Make movement toward the 98% target measurable: pin a judge independent of the
answering model (kills same-family leniency), cache verdicts per (judge model,
Q, A) triple (deterministic, cheaper re-runs), score across N runs reporting
mean ± spread and majority verdict (so a delta can be told apart from noise),
distinguish infra errors from wrong answers, and reject hand-merged mixed-run
reports.

## Must Fix

| # | Finding | Resolution |
|---|---------|------------|
| 1 | NaN sentinel converted to 0.0 in adapters before aggregation → multi-run summary counts infra errors as wrong; error-exclusion code unreachable. (Agents 1, 5) | FIXED — adapters keep the NaN score on the `QueryResult` for errored questions; `_print_multi_run_summary` feeds raw scores so `aggregate_runs`/`summarize_multi_run` exclude them; `judge_error` count surfaced in summary. |
| 2 | `CORRECT_THRESHOLD` declared as the shared cut point but adapters hardcode `>= 0.5`. (Agents 4, 5) | FIXED — both LLM-judge adapters import and use `CORRECT_THRESHOLD`. |
| 3 | `scoring._is_error` duplicates `llm_judge.is_judge_error` with divergent logic. (Agents 3, 4, 5) | FIXED — `scoring` imports and delegates to `is_judge_error`; `math` import dropped. |
| 4 | No unit tests for new logic; CI collects only `tests/`. (Agents 3, 6) | FIXED — `tests/test_bench_measurement.py` added. |

## Should Fix

| # | Finding | Resolution |
|---|---------|------------|
| 5 | `report.load_report` is dead/duplicated; `--retry-failed` re-implements it inline. (Agents 3, 4, 5, 6) | FIXED — `--retry-failed` routed through `load_report`. |
| 6 | `judge_error` is write-only output; a degraded run looks like a regression. (Agents 1, 6) | FIXED — judge-error counts surfaced in both single-run and multi-run summaries; `MultiRunSummary` carries `error_questions`. |
| 7 | NaN-sentinel contract applied to `judge_answer` but not `generate_answer` — generation infra failures still scored as wrong. (Agent 4) | FIXED — `generate_answer` returns the sentinel on failure; adapters treat it as an infra error. |
| 8 | `VerdictCache.put` rewrites the whole file per verdict (blocking write in async loop). (Agents 2, 3, 5) | FIXED — buffered flush with `flush()` / `close()`; runner flushes at end. |
| 9 | `--retry-failed` silently no-ops for LoCoMo (no `only_questions`); asymmetric signatures papered over with `inspect`. (Agents 1, 4, 6) | FIXED — `LoCoMoRealBenchmark.run_with_llm` honors `only_questions`. |

## Consider (not actioned, noted)

- CI triggers on `master` but the integration branch is `main` (Agent 6 M1) —
  pre-existing repo-level mismatch, out of scope for this issue; noted in PR body.
- `benchmarks/` is not in the ruff CI target (Agent 6 C1) — pre-existing; the
  changed files pass `ruff check` locally.
- `benchmarks/__init__.py __all__` not updated for new symbols (Agent 6 C2) —
  internal call sites use qualified imports; left as-is.

## Security Audit Results

| Area | Result | Details |
|------|--------|---------|
| Secrets/keys in cache or logs | PASS | Cache stores `{sha256: float}`; logs print provider/model + path only. |
| PII in cache | N/A | Cache holds hashes + scores; benchmark data, not user PII. |
| Path traversal / arbitrary write | PASS | Cache/report paths are CLI-controlled by the invoking developer. |
| Unsafe deserialization | PASS | JSON only; no pickle/yaml.load/eval; cache values `float()`-coerced. |
| Injection via provider/model | PASS | Strings used as dict keys / instructor provider id only. |
| Unbounded cache growth | PASS | Bounded by distinct triples; sentinels never cached. |
| Credentials in code/fixtures | PASS | None. |

No Must/Should security findings (developer-run CLI harness, no network surface).

## Pattern Consistency Assessment

`scoring.py` mirrors `metrics.py` (pure-utility module, docstrings, return
conventions). `VerdictCache.make_key` matches the full-digest sha256 convention
in `storage/embedding.py`. Config fields/property mirror the existing
`provider_string`. After fixes #2/#3 the module is the single source for both the
pass threshold and the error predicate, as designed.

## Redundancy Check

`load_report` orphan resolved (#5). `_is_error` duplication resolved (#3).
`VerdictCache` is not redundant with `CachedEmbeddingProvider` (persistent vs
in-memory). No new third-party dependencies (stdlib only).

## Wiring Findings

All five end-to-end paths verified connected: pinned judge → judge call; cache →
every hop (both adapters declare the param); `--runs` → `run_id` stamped on all
details → guardrail; `--retry-failed` → provenance assert before trust. After
fix #6 `judge_error` is consumed in summaries (was write-only).

## Resolution Status

| # | Severity | Status |
|---|----------|--------|
| 1 | Must Fix | Resolved |
| 2 | Must Fix | Resolved |
| 3 | Must Fix | Resolved |
| 4 | Must Fix | Resolved |
| 5 | Should Fix | Resolved |
| 6 | Should Fix | Resolved |
| 7 | Should Fix | Resolved |
| 8 | Should Fix | Resolved |
| 9 | Should Fix | Resolved |
</content>
