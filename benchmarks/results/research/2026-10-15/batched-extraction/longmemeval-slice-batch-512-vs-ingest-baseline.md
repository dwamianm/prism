# Offline evidence gate comparison

Before: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.
After: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| longmemeval | All evidence packed | 89.5% | 86.8% | -2.6 pp | -7.9 pp to +0.0 pp | 0 | 1 | 37 |
| longmemeval | All evidence packed with memory text | 89.5% | 86.8% | -2.6 pp | -7.9 pp to +0.0 pp | 0 | 1 | 37 |
| longmemeval | Evidence recall | 93.7% | 93.2% | -0.4 pp | -1.3 pp to +0.0 pp | 0 | 1 | 37 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | Projected accuracy | 87.3% | 85.7% | -1.5 pp | -4.6 pp to +0.0 pp | 0 | 1 | 41 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, multi-session | 90.0% | 80.0% | -10.0 pp | -30.0 pp to +0.0 pp | 0 | 1 | 9 |
| longmemeval | All evidence packed, single-session-assistant | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-user | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 4 |
| longmemeval | All evidence packed, temporal-reasoning | 66.7% | 66.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 9 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| longmemeval | 0/42 to 0/42 | 1134.2 to 1232.3 | 0.297 / 0.504 s to 0.302 / 0.633 s | 43.2 to 47.1 | 81.3% to 80.3% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 4.3 to 3.5, after 3.5 to 3.6.

- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 10, VECTOR 13.
- longmemeval after: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 11, VECTOR 13.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| longmemeval | knowledge-update | 6 | 1 | 1 |
| longmemeval | multi-session | 13 | 1 | 1 |
| longmemeval | single-session-assistant | 5 | 0 | 0 |
| longmemeval | single-session-preference | 5 | 0 | 0 |
| longmemeval | single-session-user | 4 | 0 | 0 |
| longmemeval | temporal-reasoning | 9 | 3 | 3 |

- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| longmemeval | all | 455 (25.1%) / 7 / 218 | 691 (35.0%) / 16 / 305 |
| longmemeval | knowledge-update | 58 (25.9%) / 0 / 27 | 97 (41.3%) / 1 / 41 |
| longmemeval | multi-session | 160 (27.9%) / 0 / 93 | 222 (34.0%) / 5 / 111 |
| longmemeval | single-session-assistant | 68 (29.6%) / 0 / 15 | 112 (45.2%) / 1 / 34 |
| longmemeval | single-session-preference | 40 (16.1%) / 2 / 21 | 74 (29.5%) / 2 / 32 |
| longmemeval | single-session-user | 35 (19.6%) / 0 / 16 | 51 (29.3%) / 1 / 23 |
| longmemeval | temporal-reasoning | 94 (26.1%) / 5 / 46 | 135 (32.4%) / 6 / 64 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
