# Offline evidence gate comparison

Before: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.
After: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 73.2% | 79.9% | +6.7 pp | +5.4 pp to +7.9 pp | 107 | 4 | 1425 |
| locomo | All evidence packed with memory text | 73.2% | 79.9% | +6.7 pp | +5.4 pp to +7.9 pp | 107 | 4 | 1425 |
| locomo | Evidence recall | 79.6% | 85.9% | +6.3 pp | +5.2 pp to +7.3 pp | 169 | 6 | 1361 |
| locomo | All evidence among the candidates | 94.0% | 94.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 70.2% | 74.7% | +4.5 pp | +3.6 pp to +5.4 pp | 107 | 4 | 1429 |
| locomo | All evidence packed, multi-hop | 31.9% | 46.8% | +14.9 pp | +10.6 pp to +19.5 pp | 43 | 1 | 238 |
| locomo | All evidence packed, open-domain | 44.6% | 54.3% | +9.8 pp | +4.3 pp to +16.3 pp | 9 | 0 | 83 |
| locomo | All evidence packed, single-hop | 86.2% | 90.6% | +4.4 pp | +3.1 pp to +5.8 pp | 38 | 1 | 802 |
| locomo | All evidence packed, temporal | 83.5% | 88.2% | +4.7 pp | +2.2 pp to +7.2 pp | 17 | 2 | 302 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 941.1 to 941.1 | 0.357 / 0.525 s to 0.431 / 0.601 s | 73.7 to 77.9 | 95.9% to 95.2% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 7.1 to 6.1, after 9.9 to 6.1.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 19, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 19, LEXICAL_AGG 41, VECTOR 41.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 40875 (36.0%) / 432 / 8500 | 18619 (15.5%) / 1355 / 7484 |
| locomo | multi-hop | 7565 (36.8%) / 63 / 1595 | 3463 (15.9%) / 191 / 1453 |
| locomo | open-domain | 2375 (32.7%) / 28 / 461 | 1127 (14.6%) / 99 / 444 |
| locomo | single-hop | 22582 (36.4%) / 254 / 4833 | 10135 (15.5%) / 735 / 4092 |
| locomo | temporal | 8353 (35.3%) / 87 / 1611 | 3894 (15.6%) / 330 / 1495 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
