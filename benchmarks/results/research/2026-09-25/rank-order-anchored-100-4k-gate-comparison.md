# Offline evidence gate comparison

Before: commit `c7e0a274deadc2e00ad599d4ac742598149a548b`, current defaults.
After: commit `725ddb34f370cb88c696221bb512c98998000d8f`, overrides `{"enable_reranker": true, "reranker_policy": "anchored_score_envelope", "reranker_prior_weight": 0, "reranker_top_k": 100}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 85.5% | +1.5 pp | +0.7 pp to +2.5 pp | 37 | 14 | 1485 |
| locomo | All evidence packed with memory text | 84.0% | 85.5% | +1.5 pp | +0.7 pp to +2.5 pp | 37 | 14 | 1485 |
| locomo | Evidence recall | 89.2% | 90.4% | +1.3 pp | +0.6 pp to +2.0 pp | 66 | 28 | 1442 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 77.6% | 78.6% | +1.0 pp | +0.4 pp to +1.6 pp | 37 | 14 | 1489 |
| locomo | All evidence packed, multi-hop | 49.6% | 53.5% | +3.9 pp | +0.4 pp to +7.1 pp | 19 | 8 | 255 |
| locomo | All evidence packed, open-domain | 57.6% | 58.7% | +1.1 pp | +0.0 pp to +3.3 pp | 1 | 0 | 91 |
| locomo | All evidence packed, single-hop | 95.2% | 95.6% | +0.4 pp | -0.4 pp to +1.1 pp | 6 | 3 | 832 |
| locomo | All evidence packed, temporal | 92.2% | 94.7% | +2.5 pp | +0.3 pp to +4.7 pp | 11 | 3 | 307 |
| longmemeval | All evidence packed | 94.7% | 96.0% | +1.3 pp | +0.0 pp to +2.6 pp | 8 | 2 | 460 |
| longmemeval | All evidence packed with memory text | 94.7% | 96.0% | +1.3 pp | +0.0 pp to +2.6 pp | 8 | 2 | 460 |
| longmemeval | Evidence recall | 97.3% | 97.5% | +0.2 pp | -0.6 pp to +0.9 pp | 9 | 3 | 458 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 91.4% | 92.2% | +0.8 pp | +0.0 pp to +1.5 pp | 8 | 2 | 490 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 92.6% | +3.3 pp | +0.8 pp to +6.6 pp | 4 | 0 | 117 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 94.6% | -1.8 pp | -5.4 pp to +0.0 pp | 0 | 1 | 55 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 98.4% | +1.6 pp | +0.0 pp to +4.7 pp | 1 | 0 | 63 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 95.3% | +1.6 pp | -0.8 pp to +4.7 pp | 3 | 1 | 123 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 554.8 | 0.141 / 0.190 s to 0.249 / 0.336 s | 84.5 to 85.2 | 96.6% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 492.0 to 492.0 | 0.130 / 0.202 s to 0.677 / 0.835 s | 39.9 to 37.1 | 82.6% to 83.3% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 3.3 to 4.8, after 4.4 to 3.5.

After, cross-encoder reranking inside retrieval, p50 / p95 per question: locomo 0.105 / 0.129 s; longmemeval 0.544 / 0.678 s. Retrieval time includes it.

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
| locomo | all | 59802 (45.9%) / 9 / 32771 | 59114 (45.1%) / 5 / 33677 |
| locomo | multi-hop | 10766 (45.7%) / 0 / 5872 | 10553 (44.7%) / 0 / 5979 |
| locomo | open-domain | 3508 (42.4%) / 0 / 1939 | 3556 (42.4%) / 1 / 2089 |
| locomo | single-hop | 33297 (46.9%) / 3 / 18204 | 32640 (46.0%) / 2 / 18410 |
| locomo | temporal | 12231 (44.7%) / 6 / 6756 | 12365 (43.9%) / 2 / 7199 |
| longmemeval | all | 10904 (54.7%) / 0 / 2431 | 11343 (61.1%) / 0 / 3509 |
| longmemeval | knowledge-update | 1692 (55.7%) / 0 / 368 | 1724 (65.5%) / 0 / 539 |
| longmemeval | multi-session | 2918 (59.3%) / 0 / 657 | 3115 (69.7%) / 0 / 1025 |
| longmemeval | single-session-assistant | 1314 (53.9%) / 0 / 327 | 1294 (47.7%) / 0 / 314 |
| longmemeval | single-session-preference | 670 (45.5%) / 0 / 149 | 710 (50.4%) / 0 / 221 |
| longmemeval | single-session-user | 1427 (48.6%) / 0 / 303 | 1501 (59.1%) / 0 / 492 |
| longmemeval | temporal-reasoning | 2883 (56.2%) / 0 / 627 | 2999 (62.3%) / 0 / 918 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
