# Reciprocal rank fusion: acceptance criteria (2026-11-04)

**Status:** Evidence for dwamianm/prism#82. Every acceptance criterion is met. Rank fusion shipped
opt-in in #112, the semantic `min_score` in #152 (#110) with its vector failure fix in #162 (#150),
the rank fusion session decay in #153 (#111) and the current-state recency boost with an event-time
tie-break in #172 (#168); #177 made them the default. #112 left the first criterion open because, with session expansion on, LongMemEval-S lost
about a point. This record measures that criterion again on today's `main` with the settings that
became the default, and points at the evidence for the others. No model was called and no paid API
was used.

## Criteria

| Criterion | Evidence | Met |
|---|---|---|
| Evidence gate (#78): the overall all-evidence share improves on both benchmarks at 4K and 8K, reported with and without session expansion after fusion | The gate on `60c37730`, below: the current defaults (rank fusion) against the weighted composite, with every setting that is not specific to rank fusion the same. The all-evidence share rises on both benchmarks at both budgets, with rank fusion's session expansion on and off, and every overall 95% interval over questions is above zero | Yes |
| Any category more than 1 point below the current composite is stated before the default changes | No category of either benchmark is below the weighted composite in any of the four comparisons below. #112 stated the categories below the composite for the opt-in rank fusion of that day, before any default changed (its pull request, and finding G of `memory_bank/reviews/issue-82-rrf-fusion-drop-constant-signals-review.md`): chiefly LongMemEval-S multi-session with session expansion on (-8.3 pp at 4K, -5.8 pp at 8K). The 0.6 session decay of #153 brought it back above the weighted composite in the same reader format before #177. One answer-level category result was also stated before the flip: in both #177 pairs LongMemEval-S knowledge-update went from 73 to 71 of 78 (-2.6 points, intervals including zero; `BENCHMARKS.md`, and #169 tracks it). That is an answer score, not the gate's evidence share | Yes |
| Determinism and receipt-replay tests pass for both the old and new formula versions | `tests/test_rank_fusion.py` (#112): `test_weighted_default_keeps_its_bytes_and_version`, `test_ranking_is_independent_of_input_order_and_repeatable`, `test_constant_and_query_independent_signals_do_not_move_scores`, `test_provenance_replays_formula_two_from_saved_ranks_alone`, `test_formula_one_provenance_keeps_its_bytes`, `test_rank_fusion_receipt_is_version_16_and_replays_its_ranking` and `test_weighted_receipts_keep_their_version_and_bytes`; the recency and session decay settings in `tests/test_rank_fusion_recency.py` and `tests/test_rank_fusion_session_context.py`. Receipts of the current defaults (version 22) round-trip and replay their ranking in `tests/test_fold_repeated_text.py` (`test_version_22_records_folding_and_round_trips`) and `tests/test_graph_seed_count.py` (`test_a_receipt_records_the_list_and_replays_its_ranking`). On this branch: 166 passed and 23 PostgreSQL variants skipped in the six rank fusion and determinism test files, and 60 passed and 9 skipped in the two version 22 files | Yes |
| The default changes only with the phase 1 and 2 paired answer run, with a LoCoMo gain whose 95% interval excludes zero (owner approval) | The DeepSeek-track variant `prme-reader-rrf-sd06-rec` (rank fusion with `rrf_k` 60, the 0.25 recency boost, the event-time tie-break, the reader format and the 0.6 session decay) passed the epic #77 default-change rule against `prme@46647825` before #177: LoCoMo +15.19 points (95% interval +12.99 to +17.40) in the first pair and +15.71 (+13.51 to +17.92) in the confirmation, with the conversation-level intervals (+13.63 to +16.69 and +13.95 to +17.48) also excluding zero; LongMemEval-S +1.0 (-1.6 to +3.6) and +0.2 (-2.6 to +3.0). The owner approved default changes under that rule in advance (2026-09-23). The four lines are in `ollama-deepseek-v4.1-flash-cloud-pair-verdicts.jsonl` | Yes, on the DeepSeek track |

The paired run combined phase 1 (the reader format, #79) and phase 2 (rank fusion) as the epic
asked. It was answered on the DeepSeek track (#117), which replaced the paid GPT-5.4 runs for default
changes; no GPT-5.4 pair was answered with rank fusion, and DeepSeek scores are never compared with
GPT-5.4 scores.

## The proposed change, on `main`

- Rank fusion over each channel's competition ranks,
  `(1/(k + semantic_rank) + 1/(k + lexical_rank)) * (k + 1) / 2` with `k` 60, is the `PRMEConfig`
  default (`scoring.fusion="rrf"`, set in `src/prme/retrieval/config.py`;
  the formula is `_rank_fused_scores` in `src/prme/retrieval/scoring.py`). The graph became a
  channel only where it carries information: `scoring.rrf_graph_seed_count`, an alpha-channel
  default.
- Salience and confidence do not enter the fused score, and graph proximity enters only through the
  seed count list above.
- Recency did not return through #83, which stayed opt-in for the weighted formula after it lost
  LoCoMo evidence on the gate. It returned as the current-state recency boost of #168 (#172), which
  uses event time and acts only on current-state questions. It was added on the owner's decision in
  #168 so that the newer of two conflicting memories ranks first, not because the gate showed it
  helps: on the gate it moved LoCoMo from 1,308 to 1,305 of 1,536 at 4K, with no significant
  change, and the gate cannot see which of two conflicting memories comes first. No answer run
  measured the boost on its own; it was part of the settings the #177 pairs passed.
- The epistemic, node-type and temporal adjustments multiply the fused score after fusion, each
  divided by the largest value among ranked candidates (`_pool_relative`), so a factor every
  candidate shares is 1.0. Temporal affinity applies only to temporal questions.
- Score provenance records `formula_version: 2` with the saved ranks and factors, and receipts
  replay both formulas.

## Gate: rank fusion against the weighted composite

```sh
C='--lme-captures data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline'
# The weighted composite, every other setting at its default
uv run python -m benchmarks.diagnostics.product_packing gate $C --set 'scoring.fusion="weighted"' --output weighted-4k.json
# Rank fusion (the current defaults), session expansion on, then off
uv run python -m benchmarks.diagnostics.product_packing gate $C --output defaults-4k.json
uv run python -m benchmarks.diagnostics.product_packing gate $C --set packing.session_context_window=0 --output defaults-noexp-4k.json
# The same three with --set packing.token_budget=8192 for 8K, then for each budget
uv run python -m benchmarks.diagnostics.product_packing gate-compare weighted-4k.json defaults-4k.json --output cmp-exp-4k.json
uv run python -m benchmarks.diagnostics.product_packing gate-compare weighted-4k.json defaults-noexp-4k.json --output cmp-noexp-4k.json
```

All six runs used commit `60c37730`, the 2026-09-23 saved packs and the regenerated LongMemEval-S
controls (manifest `0103b9509a35`). Both sides use the reader format and balanced order, the
defaults since #177 and #187. A weighted `fusion` takes none of the rank fusion defaults, so the
weighted side has no recency boost or tie-break and keeps the weighted session decay of 0.85. The
gain therefore belongs to the rank fusion defaults together, not to the fusion formula alone. As in
#112, there is one weighted run per budget, with session expansion on as the product runs it, and
the criterion's "with and without session expansion after fusion" is read as rank fusion with and
without expansion against that same weighted run. The
four comparisons' summaries are in this folder (`rrf-fusion-gate-*-comparison.md`). The six runs
ran at the same time on one machine, so their retrieval times are not comparable; nothing else in
the reports depends on timing.

All annotated evidence packed, weighted composite against rank fusion, with the paired 95% interval
over questions (LoCoMo's 1,536 questions come from 10 conversations, so its question-level
intervals are narrow; the gate does not compute conversation-level ones):

| Budget | Benchmark | Weighted | Rank fusion, session expansion on | Rank fusion, session expansion off |
|---|---|---:|---:|---:|
| 4K | LoCoMo | 1,067/1,536 (69.5%) | 84.0% (+14.5 pp, +12.7 to +16.3) | 81.6% (+12.2 pp, +10.3 to +14.0) |
| 4K | LongMemEval-S | 411/470 (87.4%) | 94.7% (+7.2 pp, +4.7 to +9.8) | 94.3% (+6.8 pp, +4.3 to +9.4) |
| 8K | LoCoMo | 1,252/1,536 (81.5%) | 89.6% (+8.1 pp, +6.8 to +9.7) | 88.0% (+6.5 pp, +5.1 to +8.2) |
| 8K | LongMemEval-S | 436/470 (92.8%) | 97.2% (+4.5 pp, +2.3 to +6.8) | 96.8% (+4.0 pp, +1.9 to +6.4) |

Projected accuracy, a planning estimate and not an answer score, moves the same way: LoCoMo 67.8% to
77.6% (expansion on) and 76.0% (off) at 4K, and 76.0% to 81.3% and 80.2% at 8K; LongMemEval-S 87.0%
to 91.4% and 91.1% at 4K, and 90.2% to 92.9% and 92.7% at 8K. The current defaults' 4K numbers match
the "current defaults" columns of the gate table in `BENCHMARKS.md` (84.0% and 94.7%).

By category, no category of either benchmark is below the weighted composite in any of the four
comparisons. The smallest changes:

- LongMemEval-S temporal-reasoning: +2.4 pp (4K) and +0.0 pp (8K) with session expansion on,
  +3.1 pp and +0.0 pp with it off. Each interval includes zero.
- LongMemEval-S single-session-preference: +0.0 pp with session expansion off at 4K (one question
  won, one lost, of 30), and +0.0 pp at 8K with it on and off (no question changed).
- LoCoMo single-hop with session expansion off at 8K: +1.1 pp (interval -0.4 to +2.5).

LongMemEval-S multi-session, the category #111 was about, gains in all four: +11.6 pp and +10.7 pp at
4K, +7.4 pp and +5.8 pp at 8K, each interval above zero. With the 0.6 decay, session expansion no longer
costs LongMemEval-S evidence: turning it off loses a net 2 of 470 LongMemEval-S questions (0.4 pp) at
both budgets, and 36 (2.3 pp) and 25 (1.6 pp) of 1,536 LoCoMo questions at 4K and 8K.

With session expansion off, two LoCoMo questions lose all their evidence among the candidates
against the weighted run, which keeps expansion (99.3% to 99.2%), because expansion adds neighbors
the channels did not return. With it on, no
question of either benchmark gains or loses all its evidence among the candidates.

## Still open elsewhere

- #111 is still open; the gate above shows multi-session evidence no longer lost under the defaults.
- #81 is still open. These comparisons use balanced order, the default since #187.
- #84 is still open. Under rank fusion the confidence split it describes no longer enters the score.
