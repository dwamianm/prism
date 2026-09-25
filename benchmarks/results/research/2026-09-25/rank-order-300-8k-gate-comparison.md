# Offline evidence gate comparison

Before: commit `c7e0a274deadc2e00ad599d4ac742598149a548b`, overrides `{"packing": {"token_budget": 8192}}`.
After: commit `725ddb34f370cb88c696221bb512c98998000d8f`, overrides `{"enable_reranker": true, "packing": {"token_budget": 8192}, "reranker_policy": "score_envelope", "reranker_prior_weight": 0, "reranker_top_k": 300}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 89.6% | 90.0% | +0.4 pp | -0.5 pp to +1.3 pp | 27 | 21 | 1488 |
| locomo | All evidence packed with memory text | 89.6% | 90.0% | +0.4 pp | -0.5 pp to +1.3 pp | 27 | 21 | 1488 |
| locomo | Evidence recall | 93.9% | 93.9% | +0.0 pp | -0.6 pp to +0.7 pp | 37 | 38 | 1461 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 81.3% | 81.6% | +0.2 pp | -0.3 pp to +0.8 pp | 27 | 21 | 1492 |
| locomo | All evidence packed, multi-hop | 66.0% | 68.4% | +2.5 pp | -1.1 pp to +6.4 pp | 17 | 10 | 255 |
| locomo | All evidence packed, open-domain | 67.4% | 68.5% | +1.1 pp | -4.3 pp to +6.5 pp | 4 | 3 | 85 |
| locomo | All evidence packed, single-hop | 97.4% | 97.1% | -0.2 pp | -0.8 pp to +0.4 pp | 2 | 4 | 835 |
| locomo | All evidence packed, temporal | 96.6% | 96.6% | +0.0 pp | -1.9 pp to +1.9 pp | 4 | 4 | 313 |
| longmemeval | All evidence packed | 97.2% | 96.4% | -0.9 pp | -2.1 pp to +0.4 pp | 3 | 7 | 460 |
| longmemeval | All evidence packed with memory text | 97.2% | 96.4% | -0.9 pp | -2.1 pp to +0.4 pp | 3 | 7 | 460 |
| longmemeval | Evidence recall | 98.2% | 98.0% | -0.2 pp | -1.1 pp to +0.7 pp | 3 | 7 | 460 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 92.9% | 92.4% | -0.5 pp | -1.4 pp to +0.3 pp | 3 | 7 | 490 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 94.2% | 93.4% | -0.8 pp | -3.3 pp to +1.7 pp | 1 | 2 | 118 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 96.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 56 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 98.4% | 98.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 64 |
| longmemeval | All evidence packed, temporal-reasoning | 97.6% | 95.3% | -2.4 pp | -6.3 pp to +1.6 pp | 2 | 5 | 120 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 555.0 | 0.290 / 0.361 s to 0.611 / 0.775 s | 165.7 to 171.6 | 97.3% to 97.2% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 492.1 | 0.183 / 0.257 s to 1.786 / 2.042 s | 69.6 to 59.9 | 84.9% to 86.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.7 to 4.4, after 5.3 to 3.0.

After, cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.292 / 0.423 s; longmemeval 1.588 / 1.803 s. Retrieval time includes it.

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
| locomo | all | 95042 (37.2%) / 254 / 64122 | 92975 (35.2%) / 188 / 64023 |
| locomo | multi-hop | 17499 (37.8%) / 39 / 11844 | 17327 (36.5%) / 22 / 12121 |
| locomo | open-domain | 5878 (36.5%) / 27 / 4083 | 5857 (34.9%) / 19 / 4082 |
| locomo | single-hop | 51884 (37.4%) / 75 / 34740 | 50923 (35.8%) / 66 / 34861 |
| locomo | temporal | 19781 (36.7%) / 113 / 13455 | 18868 (32.8%) / 81 / 12959 |
| longmemeval | all | 15386 (44.2%) / 0 / 4409 | 16277 (54.3%) / 0 / 7182 |
| longmemeval | knowledge-update | 2304 (43.0%) / 0 / 606 | 2440 (61.5%) / 0 / 1007 |
| longmemeval | multi-session | 4056 (48.0%) / 0 / 1123 | 4411 (66.3%) / 0 / 1895 |
| longmemeval | single-session-assistant | 1856 (42.7%) / 0 / 625 | 1771 (30.8%) / 0 / 627 |
| longmemeval | single-session-preference | 962 (39.3%) / 0 / 285 | 1026 (48.6%) / 0 / 442 |
| longmemeval | single-session-user | 2117 (40.0%) / 0 / 648 | 2272 (63.5%) / 0 / 1186 |
| longmemeval | temporal-reasoning | 4091 (46.0%) / 0 / 1122 | 4357 (55.1%) / 0 / 2025 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
