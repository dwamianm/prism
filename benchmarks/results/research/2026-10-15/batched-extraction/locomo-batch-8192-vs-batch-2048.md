# Offline evidence gate comparison

Before: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.
After: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 83.7% | -0.4 pp | -1.0 pp to +0.3 pp | 12 | 18 | 1506 |
| locomo | All evidence packed with memory text | 84.0% | 83.7% | -0.4 pp | -1.0 pp to +0.3 pp | 12 | 18 | 1506 |
| locomo | Evidence recall | 89.5% | 89.2% | -0.2 pp | -0.8 pp to +0.3 pp | 25 | 26 | 1485 |
| locomo | All evidence among the candidates | 98.4% | 98.5% | +0.1 pp | +0.0 pp to +0.3 pp | 2 | 0 | 1534 |
| locomo | Projected accuracy | 77.7% | 77.4% | -0.3 pp | -0.8 pp to +0.2 pp | 12 | 18 | 1510 |
| locomo | All evidence packed, multi-hop | 51.8% | 50.7% | -1.1 pp | -3.5 pp to +1.4 pp | 5 | 8 | 269 |
| locomo | All evidence packed, open-domain | 55.4% | 57.6% | +2.2 pp | +0.0 pp to +5.4 pp | 2 | 0 | 90 |
| locomo | All evidence packed, single-hop | 95.5% | 94.8% | -0.7 pp | -1.5 pp to +0.1 pp | 3 | 9 | 829 |
| locomo | All evidence packed, temporal | 90.7% | 91.0% | +0.3 pp | -0.6 pp to +1.6 pp | 2 | 1 | 318 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 853.2 to 857.1 | 0.308 / 0.435 s to 0.324 / 0.457 s | 88.4 to 88.4 | 94.5% to 94.5% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.9 to 4.3, after 4.3 to 5.5.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 2 (conv-47-q0002, conv-49-q0001).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 28 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41, VECTOR 28.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 32 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 4, LEXICAL_AGG 41, VECTOR 32.

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
| locomo | all | 40324 (29.6%) / 520 / 26224 | 40334 (29.6%) / 499 / 26138 |
| locomo | multi-hop | 7505 (30.0%) / 65 / 5050 | 7537 (30.1%) / 66 / 5090 |
| locomo | open-domain | 2403 (27.9%) / 32 / 1586 | 2392 (27.8%) / 45 / 1583 |
| locomo | single-hop | 22378 (30.4%) / 285 / 14172 | 22358 (30.4%) / 240 / 14082 |
| locomo | temporal | 8038 (27.7%) / 138 / 5416 | 8047 (27.7%) / 148 / 5383 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
