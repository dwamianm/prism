# Offline evidence gate comparison

Before: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.
After: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 75.2% | 79.9% | +4.7 pp | +3.6 pp to +5.8 pp | 76 | 4 | 1456 |
| locomo | All evidence packed with memory text | 75.2% | 79.9% | +4.7 pp | +3.6 pp to +5.8 pp | 76 | 4 | 1456 |
| locomo | Evidence recall | 81.5% | 85.8% | +4.3 pp | +3.4 pp to +5.2 pp | 127 | 5 | 1404 |
| locomo | All evidence among the candidates | 95.4% | 95.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 71.6% | 74.8% | +3.2 pp | +2.4 pp to +3.9 pp | 76 | 4 | 1460 |
| locomo | All evidence packed, multi-hop | 36.2% | 45.0% | +8.9 pp | +5.7 pp to +12.8 pp | 27 | 2 | 253 |
| locomo | All evidence packed, open-domain | 46.7% | 52.2% | +5.4 pp | +1.1 pp to +10.9 pp | 5 | 0 | 87 |
| locomo | All evidence packed, single-hop | 87.4% | 90.7% | +3.3 pp | +2.1 pp to +4.6 pp | 29 | 1 | 811 |
| locomo | All evidence packed, temporal | 85.7% | 90.0% | +4.4 pp | +2.2 pp to +6.9 pp | 15 | 1 | 305 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 909.0 to 909.0 | 0.349 / 0.581 s to 0.424 / 0.601 s | 79.8 to 80.4 | 96.0% to 95.8% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 8.8 to 7.1, after 4.5 to 7.5.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 5, LEXICAL_AGG 41, VECTOR 41.

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
| locomo | all | 42469 (34.6%) / 452 / 11836 | 22894 (18.5%) / 685 / 9714 |
| locomo | multi-hop | 7870 (35.3%) / 63 / 2303 | 4375 (19.4%) / 104 / 1984 |
| locomo | open-domain | 2532 (32.2%) / 35 / 679 | 1366 (17.2%) / 59 / 561 |
| locomo | single-hop | 23377 (35.0%) / 261 / 6539 | 12389 (18.4%) / 339 / 5203 |
| locomo | temporal | 8690 (33.7%) / 93 / 2315 | 4764 (18.3%) / 183 / 1966 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
