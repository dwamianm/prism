# Offline evidence gate comparison

Before: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.
After: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 75.2% | 73.2% | -2.0 pp | -3.1 pp to -0.8 pp | 25 | 56 | 1455 |
| locomo | All evidence packed with memory text | 75.2% | 73.2% | -2.0 pp | -3.1 pp to -0.8 pp | 25 | 56 | 1455 |
| locomo | Evidence recall | 81.5% | 79.6% | -1.9 pp | -2.8 pp to -0.9 pp | 37 | 92 | 1407 |
| locomo | All evidence among the candidates | 95.4% | 94.0% | -1.4 pp | -2.0 pp to -0.8 pp | 0 | 21 | 1515 |
| locomo | Projected accuracy | 71.6% | 70.2% | -1.4 pp | -2.2 pp to -0.6 pp | 25 | 56 | 1459 |
| locomo | All evidence packed, multi-hop | 36.2% | 31.9% | -4.3 pp | -7.4 pp to -0.7 pp | 6 | 18 | 258 |
| locomo | All evidence packed, open-domain | 46.7% | 44.6% | -2.2 pp | -7.6 pp to +3.3 pp | 2 | 4 | 86 |
| locomo | All evidence packed, single-hop | 87.4% | 86.2% | -1.2 pp | -2.6 pp to +0.2 pp | 13 | 23 | 805 |
| locomo | All evidence packed, temporal | 85.7% | 83.5% | -2.2 pp | -4.7 pp to +0.0 pp | 4 | 11 | 306 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 909.0 to 941.1 | 0.349 / 0.581 s to 0.357 / 0.525 s | 79.8 to 73.7 | 96.0% to 95.9% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 8.8 to 7.1, after 7.1 to 6.1.

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
| locomo | all | 42469 (34.6%) / 452 / 11836 | 40875 (36.0%) / 432 / 8500 |
| locomo | multi-hop | 7870 (35.3%) / 63 / 2303 | 7565 (36.8%) / 63 / 1595 |
| locomo | open-domain | 2532 (32.2%) / 35 / 679 | 2375 (32.7%) / 28 / 461 |
| locomo | single-hop | 23377 (35.0%) / 261 / 6539 | 22582 (36.4%) / 254 / 4833 |
| locomo | temporal | 8690 (33.7%) / 93 / 2315 | 8353 (35.3%) / 87 / 1611 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
