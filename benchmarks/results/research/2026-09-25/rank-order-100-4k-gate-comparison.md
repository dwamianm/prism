# Offline evidence gate comparison

Before: commit `c7e0a274deadc2e00ad599d4ac742598149a548b`, current defaults.
After: commit `725ddb34f370cb88c696221bb512c98998000d8f`, overrides `{"enable_reranker": true, "reranker_policy": "score_envelope", "reranker_prior_weight": 0, "reranker_top_k": 100}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 85.7% | +1.7 pp | +0.8 pp to +2.7 pp | 40 | 14 | 1482 |
| locomo | All evidence packed with memory text | 84.0% | 85.7% | +1.7 pp | +0.8 pp to +2.7 pp | 40 | 14 | 1482 |
| locomo | Evidence recall | 89.2% | 90.5% | +1.4 pp | +0.7 pp to +2.1 pp | 69 | 26 | 1441 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 77.6% | 78.8% | +1.1 pp | +0.5 pp to +1.8 pp | 40 | 14 | 1486 |
| locomo | All evidence packed, multi-hop | 49.6% | 53.5% | +3.9 pp | +0.4 pp to +7.1 pp | 19 | 8 | 255 |
| locomo | All evidence packed, open-domain | 57.6% | 59.8% | +2.2 pp | +0.0 pp to +5.4 pp | 2 | 0 | 90 |
| locomo | All evidence packed, single-hop | 95.2% | 95.7% | +0.5 pp | -0.2 pp to +1.2 pp | 7 | 3 | 831 |
| locomo | All evidence packed, temporal | 92.2% | 95.0% | +2.8 pp | +0.6 pp to +5.3 pp | 12 | 3 | 306 |
| longmemeval | All evidence packed | 94.7% | 95.5% | +0.9 pp | -0.4 pp to +2.1 pp | 7 | 3 | 460 |
| longmemeval | All evidence packed with memory text | 94.7% | 95.5% | +0.9 pp | -0.4 pp to +2.1 pp | 7 | 3 | 460 |
| longmemeval | Evidence recall | 97.3% | 97.3% | +0.0 pp | -0.8 pp to +0.7 pp | 8 | 4 | 458 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 91.4% | 91.9% | +0.5 pp | -0.3 pp to +1.3 pp | 7 | 3 | 490 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 91.7% | +2.5 pp | +0.0 pp to +5.8 pp | 3 | 0 | 118 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 94.6% | -1.8 pp | -5.4 pp to +0.0 pp | 0 | 1 | 55 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 98.4% | +1.6 pp | +0.0 pp to +4.7 pp | 1 | 0 | 63 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 94.5% | +0.8 pp | -2.4 pp to +3.9 pp | 3 | 2 | 122 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 554.8 | 0.141 / 0.190 s to 0.253 / 0.344 s | 84.5 to 85.2 | 96.6% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 492.0 | 0.130 / 0.202 s to 0.681 / 0.860 s | 39.9 to 37.1 | 82.6% to 83.3% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 3.3 to 4.8, after 3.0 to 3.6.

After, cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.106 / 0.135 s; longmemeval 0.546 / 0.708 s. Retrieval time includes it.

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
| locomo | all | 59802 (45.9%) / 9 / 32771 | 59065 (45.0%) / 6 / 33641 |
| locomo | multi-hop | 10766 (45.7%) / 0 / 5872 | 10540 (44.6%) / 1 / 5969 |
| locomo | open-domain | 3508 (42.4%) / 0 / 1939 | 3539 (42.2%) / 1 / 2080 |
| locomo | single-hop | 33297 (46.9%) / 3 / 18204 | 32633 (45.9%) / 2 / 18407 |
| locomo | temporal | 12231 (44.7%) / 6 / 6756 | 12353 (43.9%) / 2 / 7185 |
| longmemeval | all | 10904 (54.7%) / 0 / 2431 | 11352 (61.2%) / 0 / 3507 |
| longmemeval | knowledge-update | 1692 (55.7%) / 0 / 368 | 1723 (65.6%) / 0 / 536 |
| longmemeval | multi-session | 2918 (59.3%) / 0 / 657 | 3119 (69.7%) / 0 / 1021 |
| longmemeval | single-session-assistant | 1314 (53.9%) / 0 / 327 | 1294 (47.7%) / 0 / 313 |
| longmemeval | single-session-preference | 670 (45.5%) / 0 / 149 | 715 (51.1%) / 0 / 224 |
| longmemeval | single-session-user | 1427 (48.6%) / 0 / 303 | 1504 (59.2%) / 0 / 495 |
| longmemeval | temporal-reasoning | 2883 (56.2%) / 0 / 627 | 2997 (62.6%) / 0 / 918 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
