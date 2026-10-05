# Offline evidence gate comparison

Before: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.
After: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 79.9% | 79.9% | +0.0 pp | -1.2 pp to +1.2 pp | 45 | 45 | 1446 |
| locomo | All evidence packed with memory text | 79.9% | 79.9% | +0.0 pp | -1.2 pp to +1.2 pp | 45 | 45 | 1446 |
| locomo | Evidence recall | 85.8% | 85.9% | +0.1 pp | -0.9 pp to +1.1 pp | 67 | 66 | 1403 |
| locomo | All evidence among the candidates | 95.4% | 94.0% | -1.4 pp | -2.0 pp to -0.8 pp | 0 | 21 | 1515 |
| locomo | Projected accuracy | 74.8% | 74.7% | -0.1 pp | -0.9 pp to +0.8 pp | 45 | 45 | 1450 |
| locomo | All evidence packed, multi-hop | 45.0% | 46.8% | +1.8 pp | -1.8 pp to +5.7 pp | 17 | 12 | 253 |
| locomo | All evidence packed, open-domain | 52.2% | 54.3% | +2.2 pp | -2.2 pp to +6.5 pp | 3 | 1 | 88 |
| locomo | All evidence packed, single-hop | 90.7% | 90.6% | -0.1 pp | -1.7 pp to +1.3 pp | 20 | 21 | 800 |
| locomo | All evidence packed, temporal | 90.0% | 88.2% | -1.9 pp | -4.4 pp to +0.3 pp | 5 | 11 | 305 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 909.0 to 941.1 | 0.424 / 0.601 s to 0.431 / 0.601 s | 80.4 to 77.9 | 95.8% to 95.2% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.5 to 7.5, after 9.9 to 6.1.

- locomo questions that lost all their evidence among the candidates: 21 (conv-30-q0002, conv-30-q0003, conv-30-q0057, conv-30-q0074, conv-41-q0010, conv-41-q0035, conv-42-q0054, conv-42-q0135, conv-42-q0173, conv-43-q0026, conv-43-q0034, conv-43-q0067, conv-44-q0017, conv-44-q0047, conv-47-q0005, conv-48-q0015, conv-48-q0065, conv-49-q0011, conv-49-q0068, conv-49-q0130, ...).
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 19, LEXICAL_AGG 41, VECTOR 41.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 11 (conv-42-q0027, conv-42-q0061, conv-42-q0063, conv-42-q0081, conv-42-q0082, conv-42-q0144, conv-42-q0153, conv-43-q0018, conv-43-q0171, conv-50-q0148, conv-50-q0149).
The JSON report lists every question.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 22894 (18.5%) / 685 / 9714 | 18619 (15.5%) / 1355 / 7484 |
| locomo | multi-hop | 4375 (19.4%) / 104 / 1984 | 3463 (15.9%) / 191 / 1453 |
| locomo | open-domain | 1366 (17.2%) / 59 / 561 | 1127 (14.6%) / 99 / 444 |
| locomo | single-hop | 12389 (18.4%) / 339 / 5203 | 10135 (15.5%) / 735 / 4092 |
| locomo | temporal | 4764 (18.3%) / 183 / 1966 | 3894 (15.6%) / 330 / 1495 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
