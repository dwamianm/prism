# Review — issue-43-productize-multi-query-reformulation

## Files Changed

- `src/prme/retrieval/reformulation.py` (NEW) — productized LLM multi-query reformulation module, mirroring `abstention.py`.
- `src/prme/retrieval/pipeline.py` — new Stage 2.6 (opt-in), `_expand_reformulated_queries` helper, 4 new `__init__` params, docstring updates.
- `src/prme/retrieval/__init__.py` — export `reformulate_query`.
- `src/prme/config.py` — `enable_query_reformulation` (default False), `query_reformulation_count` (1-5, default 2).
- `src/prme/storage/engine.py` — wire the 4 new params at both pipeline construction sites (DuckDB + PostgreSQL).

## Approach Summary

Move the benchmark-harness-only multi-query reformulation (`benchmarks/llm_judge.py`) into the product retrieval path as an opt-in mode (default OFF). When `enable_query_reformulation=True`, `retrieve()` asks the configured (extraction) LLM for N alternative phrasings, runs each through the same rule-based `analyze_query` + `generate_candidates` as the original query, and merges new candidates (deduped by node id) into the pool before scoring. With the flag off (default), `retrieve()` makes zero LLM calls — behavior unchanged. The product prompt is domain-neutral (no benchmark-specific examples), addressing the issue's no-benchmark-hacks requirement for the productized path.

## Must Fix

| # | Finding | Resolution |
|---|---------|------------|
| 1 | No tests for `reformulation.py` or the pipeline integration (Agents 3, 6). | RESOLVED — added `tests/test_reformulation.py`. |

## Should Fix

| # | Finding | Resolution |
|---|---------|------------|
| 2 | `retrieval_mode` not forwarded to `analyze_query` in the helper (Agent 1). | RESOLVED — threaded `retrieval_mode` through `_expand_reformulated_queries` to each alt-query `analyze_query`. |
| 3 | Param naming `reformulation_provider`/`reformulation_model` inconsistent with the `query_reformulation_*` stem (Agent 3). | RESOLVED — renamed to `query_reformulation_provider`/`query_reformulation_model` in pipeline + both engine sites. |
| 4 | Stale "6-stage"/"7-stage" docstrings; Stage 2.6 missing from module docstring (Agent 3). | RESOLVED — updated module/class/method docstrings and stage list. |
| 5 | Redundant outer try/except around `reformulate_query` (callee never raises) (Agents 3, 5). | RESOLVED — removed; per-query errors now isolated via `asyncio.gather(return_exceptions=True)`. |

## Consider

| # | Finding | Resolution |
|---|---------|------------|
| 6 | Sequential alt-query retrieval vs the `asyncio.gather` convention used by sibling stages (Agents 3, 4). | RESOLVED — converted to `asyncio.gather` to match the aggregation-scan and entity-expansion stages. |
| 7 | Export `reformulate_query` in `retrieval/__init__.py` for parity with `should_abstain` (Agent 6). | RESOLVED — exported. |
| 8 | Extract shared `_get_client`/`_client_cache` (now duplicated across `abstention.py`/`reformulation.py`) (Agents 4, 5). | DEFERRED — pre-existing pattern; refactoring would touch `abstention.py` and make this module the odd one out. Tracked as a follow-up, not a gate. |
| 9 | Harness `benchmarks/llm_judge.py` reformulation prompt still contains the `"X Sweden"` test-mirroring example; could delegate to the new product `reformulate_query` (Agents 4, 5). | DEFERRED — out of scope for this PR (product path only). Noted in PR body for follow-up. |
| 10 | `query_reformulation_count` is `[HYPOTHESIS]`-tagged; consider an `experimental` config namespace before the v1.0 API freeze (Agent 6). | DEFERRED — broader config-API decision, not specific to this change. |

## Security Audit Results

| Area | Result | Details |
|------|--------|---------|
| Secrets/PII in logs | PASS | No raw query text logged at INFO/WARNING; alt-query logged only at DEBUG. |
| Data egress gated by opt-in | PASS | Only LLM call reached via `enable_query_reformulation` (default False). Zero network calls on default path. |
| Input validation / injection | PASS | Reformulated string flows to tantivy `parse_query_lenient` (structured parser) and parameterized SQL — no injection sink. |
| Credentials in code/fixtures | PASS | No hardcoded keys; auth delegated to `instructor.from_provider` via provider env vars. |
| Parity with `abstention.py` | PASS | Same cached-client + safe-fallback pattern, with extra input hardening. |

## Pattern Consistency Assessment

`reformulation.py` is a near-exact structural clone of `abstention.py` (client cache, prompt constant, pydantic model, keyword-only params, try/except-safe-default). Config fields match the `enable_reranker`/`reranker_*` cluster conventions (Field style, `[HYPOTHESIS]` tag, `ge/le` validation aligned with the runtime clamp). Both engine construction sites updated identically — no PG-mode silent-skip. Product prompt correctly diverges from the harness prompt to remove the benchmark-specific `"X Sweden"` example.

## Redundancy Check

No dead code. Config fields and all 4 pipeline params are wired through and used at both engine paths. The `reformulate_query`/`QueryReformulations`/prompt copy from `benchmarks/llm_judge.py` is justified (product code cannot import from `benchmarks/`, which is not in the installed wheel). The `_get_client`/`_client_cache` duplication is the established per-module pattern (also in `abstention.py`); extraction deferred as a follow-up.

## Wiring Findings

Config → engine (both sites) → pipeline `__init__` → `self._` attrs → use site chain is complete and verified. Provider/model sourced from `config.extraction.{provider,model}`. Default-off guarantee verified (zero LLM calls when flag is False). No new module registration required beyond the optional `__init__` export (added). New test file auto-collected by pytest (no `testpaths` restriction). No user-facing docs enumerate feature flags, so no doc update required by precedent.

## Resolution Status

All Must Fix and Should Fix findings resolved. Consider items either resolved or deferred with rationale (and surfaced in the PR body where cross-cutting).
