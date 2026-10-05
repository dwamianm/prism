# Offline evidence gate comparison

Before: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.
After: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 78.9% | 81.7% | +2.8 pp | +2.0 pp to +3.7 pp | 45 | 2 | 1489 |
| locomo | All evidence packed with memory text | 78.9% | 81.7% | +2.8 pp | +2.0 pp to +3.7 pp | 45 | 2 | 1489 |
| locomo | Evidence recall | 85.1% | 87.6% | +2.5 pp | +1.9 pp to +3.2 pp | 86 | 4 | 1446 |
| locomo | All evidence among the candidates | 96.1% | 96.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 74.2% | 76.1% | +1.9 pp | +1.3 pp to +2.5 pp | 45 | 2 | 1493 |
| locomo | All evidence packed, multi-hop | 42.2% | 47.2% | +5.0 pp | +2.5 pp to +7.8 pp | 15 | 1 | 266 |
| locomo | All evidence packed, open-domain | 51.1% | 54.3% | +3.3 pp | +0.0 pp to +7.6 pp | 3 | 0 | 89 |
| locomo | All evidence packed, single-hop | 91.1% | 93.1% | +2.0 pp | +1.2 pp to +3.0 pp | 17 | 0 | 824 |
| locomo | All evidence packed, temporal | 87.2% | 90.0% | +2.8 pp | +0.9 pp to +4.7 pp | 10 | 1 | 310 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 920.2 to 920.2 | 0.328 / 0.511 s to 0.414 / 0.603 s | 103.3 to 95.7 | 94.3% to 95.2% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 7.9 to 4.4, after 4.5 to 7.6.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 1, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 1, LEXICAL_AGG 41, VECTOR 41.

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
| locomo | all | 39795 (25.0%) / 451 / 16505 | 23661 (16.1%) / 706 / 12574 |
| locomo | multi-hop | 7280 (25.0%) / 58 / 3160 | 4425 (16.4%) / 90 / 2422 |
| locomo | open-domain | 2308 (22.5%) / 46 / 954 | 1374 (14.3%) / 61 / 740 |
| locomo | single-hop | 21827 (25.5%) / 214 / 8971 | 12873 (16.4%) / 356 / 6733 |
| locomo | temporal | 8380 (24.5%) / 133 / 3420 | 4989 (15.5%) / 199 / 2679 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
