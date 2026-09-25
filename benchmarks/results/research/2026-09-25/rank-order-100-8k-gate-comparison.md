# Offline evidence gate comparison

Before: commit `c7e0a274deadc2e00ad599d4ac742598149a548b`, overrides `{"packing": {"token_budget": 8192}}`.
After: commit `725ddb34f370cb88c696221bb512c98998000d8f`, overrides `{"enable_reranker": true, "packing": {"token_budget": 8192}, "reranker_policy": "score_envelope", "reranker_prior_weight": 0, "reranker_top_k": 100}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 89.6% | 89.8% | +0.1 pp | -0.5 pp to +0.8 pp | 13 | 11 | 1512 |
| locomo | All evidence packed with memory text | 89.6% | 89.8% | +0.1 pp | -0.5 pp to +0.8 pp | 13 | 11 | 1512 |
| locomo | Evidence recall | 93.9% | 93.8% | -0.1 pp | -0.5 pp to +0.4 pp | 17 | 19 | 1500 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 81.3% | 81.4% | +0.1 pp | -0.3 pp to +0.5 pp | 13 | 11 | 1516 |
| locomo | All evidence packed, multi-hop | 66.0% | 66.3% | +0.4 pp | -2.1 pp to +2.8 pp | 7 | 6 | 269 |
| locomo | All evidence packed, open-domain | 67.4% | 67.4% | +0.0 pp | -3.3 pp to +3.3 pp | 1 | 1 | 90 |
| locomo | All evidence packed, single-hop | 97.4% | 97.0% | -0.4 pp | -1.0 pp to +0.1 pp | 1 | 4 | 836 |
| locomo | All evidence packed, temporal | 96.6% | 97.8% | +1.2 pp | +0.3 pp to +2.8 pp | 4 | 0 | 317 |
| longmemeval | All evidence packed | 97.2% | 96.8% | -0.4 pp | -1.3 pp to +0.4 pp | 1 | 3 | 466 |
| longmemeval | All evidence packed with memory text | 97.2% | 96.8% | -0.4 pp | -1.3 pp to +0.4 pp | 1 | 3 | 466 |
| longmemeval | Evidence recall | 98.2% | 98.0% | -0.2 pp | -0.7 pp to +0.1 pp | 1 | 3 | 466 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 92.9% | 92.7% | -0.3 pp | -0.8 pp to +0.3 pp | 1 | 3 | 496 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 94.2% | 94.2% | +0.0 pp | -2.5 pp to +2.5 pp | 1 | 1 | 119 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 96.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 56 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 98.4% | 98.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 64 |
| longmemeval | All evidence packed, temporal-reasoning | 97.6% | 96.1% | -1.6 pp | -3.9 pp to +0.0 pp | 0 | 2 | 125 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 554.8 | 0.290 / 0.361 s to 0.445 / 0.573 s | 165.7 to 166.1 | 97.3% to 97.3% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 492.0 | 0.183 / 0.257 s to 0.746 / 0.997 s | 69.6 to 63.9 | 84.9% to 85.6% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.7 to 4.4, after 2.9 to 4.5.

After, cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.117 / 0.160 s; longmemeval 0.554 / 0.750 s. Retrieval time includes it.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 114.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 114.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |
| longmemeval | knowledge-update | 78 | 11 | 11 |
| longmemeval | multi-session | 133 | 23 | 23 |
| longmemeval | single-session-assistant | 56 | 3 | 3 |
| longmemeval | single-session-preference | 30 | 1 | 1 |
| longmemeval | single-session-user | 70 | 7 | 7 |
| longmemeval | temporal-reasoning | 133 | 37 | 37 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 95042 (37.2%) / 254 / 64122 | 93293 (36.5%) / 213 / 64481 |
| locomo | multi-hop | 17499 (37.8%) / 39 / 11844 | 17260 (37.2%) / 35 / 11978 |
| locomo | open-domain | 5878 (36.5%) / 27 / 4083 | 5792 (35.8%) / 20 / 4130 |
| locomo | single-hop | 51884 (37.4%) / 75 / 34740 | 51200 (36.8%) / 79 / 35180 |
| locomo | temporal | 19781 (36.7%) / 113 / 13455 | 19041 (35.2%) / 79 / 13193 |
| longmemeval | all | 15386 (44.2%) / 0 / 4409 | 15673 (49.0%) / 0 / 5389 |
| longmemeval | knowledge-update | 2304 (43.0%) / 0 / 606 | 2276 (48.8%) / 0 / 748 |
| longmemeval | multi-session | 4056 (48.0%) / 0 / 1123 | 4213 (55.5%) / 0 / 1436 |
| longmemeval | single-session-assistant | 1856 (42.7%) / 0 / 625 | 1830 (39.4%) / 0 / 631 |
| longmemeval | single-session-preference | 962 (39.3%) / 0 / 285 | 1003 (45.2%) / 0 / 354 |
| longmemeval | single-session-user | 2117 (40.0%) / 0 / 648 | 2169 (46.5%) / 0 / 792 |
| longmemeval | temporal-reasoning | 4091 (46.0%) / 0 / 1122 | 4182 (51.1%) / 0 / 1428 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
