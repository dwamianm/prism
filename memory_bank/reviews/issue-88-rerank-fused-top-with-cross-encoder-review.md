# Code Review: issue-88-rerank-fused-top-with-cross-encoder

## Plan (written before implementation)

Issue #88 asks to rerank the fused top 100 to 300 candidates with a local cross-encoder in rank order,
building on the existing `reranker_policy` envelope options, off by default until the acceptance run
passes. Acceptance: the evidence gate at 4K and 8K on both benchmarks by category, with a higher
all-evidence share than rank fusion alone overall on each benchmark and on LoCoMo multi-hop; reranking
latency at p50 and p95; determinism and receipt-replay tests with the reranker on; and a paired answer
run before any default change (owner approval).

Where things stand: rank fusion (#82), the reader format (#79) and balanced ordering (#81) are the
defaults since #177 and #187, so the dependencies' code has landed; their issues stay open only for
default decisions. The reranker already runs after `score_and_rank` (Stage 5a,
`src/prme/retrieval/pipeline.py`), which is rank fusion by default, and the envelope policies already
give the reranked prefix its original scores in the new order (`reranker.py`, `assign_envelope`). What
is missing is the order itself: the pipeline never passed `prior_weight`, so the prefix was always
ordered by `0.7 * model + 0.3 * fused score`.

Approach:
1. `PRMEConfig.reranker_prior_weight` (default 0.3, unchanged), passed by both engines to the pipeline
   and stored on `CrossEncoderReranker`; `rerank(prior_weight=None)` uses it. 0.0 orders the prefix by
   the model alone, and `score_envelope` then keeps the fused scores in that order.
2. Record a weight other than 0.3 in the receipt's execution parameters and the reranker's feature
   identity (so a ranking profile learned under another weight does not activate); omit it at 0.3 so
   existing receipts and identities keep their bytes. No new receipt schema: rank fusion receipts
   (versions 16 to 21) already admit `neural_rank_assignment`, and the `neural_blend` coefficient has
   allowed 0 since it was added.
3. Gate: refuse reranker settings without `enable_reranker`, and time the reranker per question
   (p50 and p95) for the latency criterion. Run the gate at 4K and 8K on both benchmarks.

Alternatives considered:
- A new `reranker_policy` value that fixes the weight at 0: rejected. The policies decide how scores
  are assigned and whether the anchor leads (`reranker.py`, `rerank` and `assign_envelope`); the order
  comes from the blend weight (`rerank`, `blended = (1 - prior_weight) * ce + prior_weight * original`).
  One setting per mechanism keeps the anchored variant available without a fourth policy value.
- Rank order under `legacy` (raw model scores on the prefix): the scale failure recorded in
  `benchmarks/results/research/2026-09-22/RERANKER-SCORE-REPAIR.md` (284/500). Rank fusion's tail
  scores reach about 0.38 at rank 101 (`RankFusion.score`), far above an unrelated pair's model score.
- Fusing the model as a third rank channel: score mixing in rank space, which the issue rules out.
- Reranking inside packing: packing has neither the query nor the model.

Shared state: receipts (a new parameter key only when the weight is not 0.3), the reranker feature
identity (read by ranking-profile activation), nothing in stored memories. Defaults are unchanged.

Exploratory probe before implementation (LoCoMo only, current defaults, not committed): all evidence
packed at 4K went from 84.0% to 85.7% with the top 100 in rank order and to 86.1% with the top 300;
multi-hop from 49.6% to 53.5% and 55.3%.

## Files Changed
- `src/prme/config.py`: `reranker_prior_weight` with normalization and a refusal of a non-default
  weight under `legacy` when the reranker is on.
- `src/prme/retrieval/config.py`: `DEFAULT_RERANKER_PRIOR_WEIGHT`, `RERANKER_ENVELOPE_POLICIES`,
  `LEGACY_PRIOR_WEIGHT_ERROR` and `normalize_reranker_prior_weight`.
- `src/prme/retrieval/reranker.py`: the weight on the reranker; `rerank(prior_weight=None)`.
- `src/prme/retrieval/pipeline.py`: constructor argument and checks; receipt parameter.
- `src/prme/retrieval/execution.py`: `prior_weight` in the reranker identity when not 0.3.
- `src/prme/storage/engine.py`: both backends pass the setting.
- `benchmarks/diagnostics/product_packing.py`: reranker-only settings refusal, no-op refusal,
  `_RerankTimer`, reranking p50/p95 in reports and comparisons, `reranker_runtime` provenance.
- Tests: `tests/test_reranker_rank_order.py` (new), `tests/test_product_packing_diagnostic.py`,
  `tests/test_experimental_retrieval_policies.py`.
- Docs: `docs/EXPERIMENTAL-RETRIEVAL-POLICIES.md`, `docs/RFC-0005-Hybrid-Retrieval-Pipeline.md` (7.3 and
  section 11), `docs/INTEGRATION.md`, `documentation/configuration.md`, `AGENTS.md`, `BENCHMARKS.md`,
  `CHANGELOG.md`.
- Results: `benchmarks/results/research/2026-09-25/rank-order-*`.

## Approach Summary
Opt-in rank order for the existing reranker; defaults unchanged. See the plan above.

## Must Fix
None from any reviewer.

## Should Fix (all resolved)
1. The pipeline read `self._reranker._prior_weight` directly inside the receipt block, whose errors are
   only logged, while the feature identity used a fallback (Agents 1, 3, 4, 5, 6). Resolved: the
   pipeline reads it with the same `getattr` fallback before building the parameters.
2. The PostgreSQL wiring line had no test (Agent 3). Resolved: the new end-to-end test uses the
   parametrized `durable_config` fixture, which runs on PostgreSQL in CI.

## Consider (resolved or recorded)
- The range check was typed three times (Agents 3, 4, 5). Resolved: one `normalize_reranker_prior_weight`
  in `prme.retrieval.config`, used by the reranker, the pipeline and the config validator. Pydantic's
  bounds still reject values on the configuration path first.
- Int 0, `-0.0` and bool weights were recorded with other bytes than 0.0 (Agents 1, 2, 3, 4, 6).
  Resolved: the weight is normalized to a float and `-0.0` becomes 0.0; a bool or a non-number is refused
  in code (configuration keeps pydantic's coercion).
- `DEFAULT_PRIOR_WEIGHT` lived in `reranker.py`, so receipt code imported the reranker module
  (Agents 4, 5). Resolved: the constant is in `prme.retrieval.config`, next to `DEFAULT_RRF_K`; the
  pipeline's reranker import is lazy again, and the config field uses the constant.
- The field had no status marker (Agent 4). Resolved: its description starts with "Experimental."; it is
  not tagged `[HYPOTHESIS]`, like the other reranker settings.
- No test showed that a weight change stops a ranking profile (Agents 3, 4, 5, 6). Resolved: a `prior`
  case in `test_policy_change_prevents_profile_activation`, learned with a reranker at 0.3 so only the
  weight changes; the same test without the change does not raise (checked by hand).
- No environment variable test, and `PRMEConfig()` read `.env` in a test (Agent 4). Resolved.
- `_RerankTimer`: `+=` on None before `start()`, and a docstring that said retrieval reports no stage
  times (Agents 1, 3, 4, 5). Resolved: the sum starts from 0 when unset; the docstring says retrieval
  metadata has no reranker time and that the replay is sequential.
- Reranking time compared without the warm-up check that retrieval time has (Agent 4). Resolved.
- `GATE_RERANKER_SETTINGS` was named and placed unlike the gate's other constants and listed by hand
  (Agents 3, 4). Resolved: `GATE_RERANKER_ONLY_SETTINGS`, derived from `PRMEConfig` fields named
  `reranker_*`, beside `GATE_FIXED_SETTINGS`.
- The comparison said reranking is part of "the retrieval time above" even when that time is n/a
  (Agents 3, 6). Resolved: "Retrieval time includes it."
- The new doc heading swallowed the reformulation section (Agent 4). Resolved: rank order has its own
  `##` section before "Evidence and limits".
- The documented gate command needs the `reranker` extra and a cached model (Agent 6). Resolved.
- A test block repeated an assertion and popped a key never set (Agents 3, 5). Resolved: it now checks
  that reranking time is not compared with a report timed before the warm-up.
- The gate test did not show the weight reached retrieval (Agent 3). Resolved: it reads the captured
  receipt's parameters and feature identity.
- Weight recorded in both parameters and feature identity (Agent 5): kept. `query_intent_order` and the
  reformulation merge policy record both; the identity gates profiles, the parameters describe the
  request.
- `_RerankTimer` could be a `reranking_ms` field in `RetrievalMetadata` (Agent 5): kept in the gate, so
  the API response does not change for a measurement need.
- Exactly equal model scores (identical text) order by UUID (Agent 1): accepted. Identical text reads the
  same in the context, and fused-score ties inside the prefix are rare (below).
- Two frozen research rerankers (`opt_in_rank_envelope.py`, `opt_in_anchored_rank.py`) pass 0.3 at the
  call, ignoring a constructor weight (Agents 1, 3, 4, 5, 6): accepted. They are registered research
  code, nothing builds them with another weight, and changing their bytes would touch recorded studies.
- `reranker_top_k` has no lower bound in `PRMEConfig` (Agents 1, 6; pre-existing): accepted. A negative
  value fails loudly on the first retrieval; the gate now refuses 0.
- The gate's offline mode uses `setdefault`, so an exported `HF_HUB_OFFLINE=0` could download a model
  (Agent 2; pre-existing): accepted, not changed by this issue.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs, responses, receipts | PASS | Only a float weight is recorded. |
| Authorization and tenant scoping | PASS | Receipt scoping and user isolation are untouched. |
| Input validation | PASS | Pydantic bounds plus normalization; code callers get a clear error. |
| Per-request override through API or MCP | N/A | Configuration only. |
| Model loading | PASS | Unchanged `CrossEncoder(model_name)`; no `trust_remote_code`. |
| Credentials in code or fixtures | PASS | None. |

## Pattern Consistency Assessment
The setting follows `query_intent_order` and `reranker_policy`: a config field, pipeline constructor
validation, a conditional execution parameter and feature identity entry, wiring in both engines, a
profile-activation test case, and a gate refusal when dormant (as `session_context_packing` does). All
deviations Agent 4 found are resolved above, except the frozen research subclasses.

## Redundancy Check
No duplicated helper remains: the p50/p95 use the gate's `_latency`, the validation is one helper, the
dormant check follows the existing gate checks. `_RerankTimer` and the dual recording are justified above.

## Wiring Findings
Both engine constructors pass the setting; nothing else builds a pipeline or reranker; every
`PRMEConfig` built from the environment reads `PRME_RERANKER_PRIOR_WEIGHT`; the DeepSeek harness's
variant identity diffs against the defaults, so the new field at its default adds no identity churn; no
recorded variant or gate test passes a reranker setting without `enable_reranker`. CHANGELOG, the gate
command and the gate results are added (Agent 6 M1, M2, I4).

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Operator sets PRME_ENABLE_RERANKER=true and PRME_RERANKER_PRIOR_WEIGHT=0
from the new configuration table, keeps the default `legacy` policy, and every context silently fills
with fused ranks 101+."

| # | Scenario | Label | Likelihood / impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | `legacy` with weight 0.0 puts raw model scores on the prefix; the tail's fused scores (up to 0.38) outrank it in session expansion and packing | Newly reachable | M / H, silent | Fix now | The diff made it reachable. `PRMEConfig` and the pipeline now refuse any weight other than 0.3 under `legacy` with the reranker on; the gate inherits it; the docs say so. |
| 2 | At 0.0 the prefix order ignores epistemic weight, node-type boost, temporal affinity and current-state recency; the turn-only gate packs cannot show the first two | New lever | M / M, silent | Follow-up | Inherent to "order by the model alone", which the issue asks for; the fix (multiply the model score by those factors, or an extraction-built gate pack) is a design and product call for the default-flip PR. Documented now. |
| 3 | With `reranker_top_k=300` on CPU and concurrent requests, reranks waiting on the model lock hold shared executor workers, stalling writes and retrievals | Pre-existing design, made likelier by the example | L-M / H, quiet | Follow-up | Needs its own executor and eager loading, a runtime change outside this issue. Documented now. |
| 4 | The model reads about 512 tokens, so long records are judged by their start; at 0.0 BM25's full-text match no longer counts | Pre-existing limit, now deciding | M / M, quiet | Follow-up (with 2) | Diagnostics (evidence length against the window, rank before and after) belong with scenario 2's gate work. Documented now. |
| 5 | Gate provenance lacks torch, transformers, the device and the model revision, which decide scores and latency | Newly relevant | M / L-M, quiet | Fix now | Contained: `reranker_runtime` in the gate provenance of reranker runs, outside `dependencies`. |
| 6 | A missing model makes every retrieval fail and retry the load under the lock | Pre-existing | L / H, loud | Follow-up (with 3) | Same fix as scenario 3: load at engine open or cache the failure. |
| 7 | The gate accepted no-op reranker runs (`reranker_top_k=0`, weight 1 under an envelope) | Newly introduced guard gap | L / L, quiet | Fix now | One line: the gate refuses both. |

Attacked hardest and held (Agent 7): receipt, identity and install compatibility at the default (0.3 is
omitted and parses exactly from the environment); the gate timer (sequential replay, started before
every retrieval); replay exactness at 0.0 (the blend and its replay give identical doubles).

Measured for scenario-adjacent tie concerns: before session expansion, fused scores tie for 0.07% to
0.1% of the top 100 to 300 LoCoMo candidates and 0.5% to 0.8% of LongMemEval-S's (runs of two or
three), so the envelope's UUID tie-break rarely reorders the model's order. Ties after expansion are the
neighbors that share their trigger's decayed score, which the reranker does not see.

## Follow-ups Raised
- #200: Cross-encoder rank order drops trust, temporal and recency signals from the reranked candidates
  (scenarios 2 and 4; the gate's LongMemEval-S temporal-reasoning losses are its evidence).
- #201: Enabling the reranker can stall or fail every retrieval on a shared engine (scenarios 3 and 6).

## Evidence gate (offline)
Full runs over all 1,540 LoCoMo and 500 LongMemEval-S questions, current defaults otherwise (rank fusion,
reader format, balanced order), one at a time on one laptop. Rank fusion alone ran at `c7e0a274`, the
variants at `725ddb34` (clean trees). The later commit changes no code that runs with the reranker off,
and the commits after it change only documentation and results. The reranker is
`cross-encoder/ms-marco-MiniLM-L-6-v2` (revision `233902d2`), on the Apple GPU (MPS), torch 2.10.0,
sentence-transformers 5.3.0. Comparisons: `benchmarks/results/research/2026-09-25/rank-order-*`.

All evidence packed, change against rank fusion alone (paired 95% interval over questions):

| Variant | LoCoMo 4K | LoCoMo multi-hop 4K | LongMemEval-S 4K | LoCoMo 8K | LoCoMo multi-hop 8K | LongMemEval-S 8K |
|---|---|---|---|---|---|---|
| Rank fusion alone | 1,290/1,536 (84.0%) | 140/282 (49.6%) | 445/470 (94.7%) | 1,377/1,536 (89.6%) | 186/282 (66.0%) | 457/470 (97.2%) |
| Top 100 | +1.7 (+0.8 to +2.7) | +3.9 (+0.4 to +7.1) | +0.9 (-0.4 to +2.1) | +0.1 (-0.5 to +0.8) | +0.4 (-2.1 to +2.8) | -0.4 (-1.3 to +0.4) |
| Top 100, anchored | +1.5 (+0.7 to +2.5) | +3.9 (+0.4 to +7.1) | +1.3 (+0.0 to +2.6) | +0.1 (-0.6 to +0.7) | +0.0 (-2.5 to +2.5) | -0.4 (-1.3 to +0.4) |
| Top 300 | +2.1 (+1.0 to +3.3) | +5.7 (+1.4 to +9.9) | -0.4 (-2.1 to +1.5) | +0.4 (-0.5 to +1.3) | +2.5 (-1.1 to +6.4) | -0.9 (-2.1 to +0.4) |

Verdict against the issue's bar (higher than rank fusion alone overall on each benchmark and on LoCoMo
multi-hop, at 4K and 8K): no variant passes. The anchored top 100 passes at 4K only. The reranker stays
off by default, and the answer-run criterion is not attempted.

Reranking time inside `retrieve()`, p50 / p95 per question at 4K: top 100, LoCoMo 0.106 / 0.135 s and
LongMemEval-S 0.546 / 0.708 s; top 300, 0.270 / 0.328 s and 1.586 / 1.799 s (repeat: 0.274 / 0.328 s and
1.571 / 1.738 s). Retrieval p50 without the reranker: 0.141 s and 0.130 s.

Determinism: the top 300 run at 4K, repeated, rendered identical contexts for all 2,040 questions; every
replay's receipt reproduced the returned ranking (the gate checks each one).

## Tests
- `tests/test_reranker_rank_order.py` (new): rank order under rank fusion (prefix order, fused scores kept,
  tail untouched, provenance replay), legacy scores when called directly, the call weight overriding the
  instance weight, range and type checks in the reranker, config and pipeline, the legacy refusal,
  normalization and identity bytes, the environment variable, a rank-fusion receipt replay, and an
  end-to-end retrieval under the product defaults on DuckDB and PostgreSQL (receipt version unchanged,
  replay, parameters and identity, repeat retrieval and restart identical, default weight omitted).
- `tests/test_product_packing_diagnostic.py`: reranker-only settings refused without the reranker,
  no-op runs refused, runtime provenance keys, reranker time recorded per question with the receipt
  showing the weight and identical repeated contexts, and the summary, Markdown and comparison of
  reranking time.
- `tests/test_experimental_retrieval_policies.py`: a weight change stops a ranking profile.
- Full suite: `uv run pytest -q`, 4,708 passed, 897 skipped. `ruff check src/ tests/` and the public
  API mypy check pass.

## Resolution Status
| Finding | Source | Status |
|---|---|---|
| Legacy policy with a non-default weight | Agent 7 #1 | Fixed |
| Receipt parameter read without fallback | Agents 1, 3, 4, 5, 6 | Fixed |
| PostgreSQL wiring untested | Agent 3 | Covered by the parametrized test |
| Duplicated range check; int, bool and -0.0 identities | Agents 1-6 | Fixed |
| Constant location and eager reranker import | Agents 4, 5 | Fixed |
| Status marker on the field | Agent 4 | Fixed |
| Profile activation and environment tests | Agents 3, 4, 5, 6 | Fixed |
| Timer robustness, docstring and warm-up check | Agents 1, 3, 4, 5 | Fixed |
| Gate constant naming and derivation | Agents 3, 4 | Fixed |
| Comparison wording | Agents 3, 6 | Fixed |
| Doc structure and gate command | Agents 4, 6 | Fixed |
| Redundant test block; weight reaching retrieval | Agents 3, 5 | Fixed |
| Gate runtime provenance | Agent 7 #5, Agent 6 L1 | Fixed |
| No-op gate runs | Agent 7 #7, Agent 1 C6 | Fixed |
| Rank order drops non-text signals; model window | Agent 7 #2, #4 | Follow-up |
| Reranker runtime integration (lock, executor, load) | Agent 7 #3, #6 | Follow-up |
| Research subclasses at 0.3 | Agents 1, 3, 4, 5, 6 | Accepted |
| Identical-text ties by UUID | Agent 1 | Accepted |
| `reranker_top_k` lower bound; offline `setdefault` | Agents 1, 2, 6 | Accepted (pre-existing) |
| CHANGELOG | Agents 4, 6 | Added |
