# Offline evidence gate comparison

Before: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.
After: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| longmemeval | All evidence packed | 71.1% | 86.8% | +15.8 pp | +5.3 pp to +28.9 pp | 6 | 0 | 32 |
| longmemeval | All evidence packed with memory text | 71.1% | 86.8% | +15.8 pp | +5.3 pp to +28.9 pp | 6 | 0 | 32 |
| longmemeval | Evidence recall | 84.1% | 93.2% | +9.1 pp | +4.1 pp to +14.9 pp | 9 | 0 | 29 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | Projected accuracy | 76.6% | 85.7% | +9.2 pp | +3.1 pp to +16.8 pp | 6 | 0 | 36 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, multi-session | 40.0% | 90.0% | +50.0 pp | +20.0 pp to +80.0 pp | 5 | 0 | 5 |
| longmemeval | All evidence packed, single-session-assistant | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-preference | 80.0% | 100.0% | +20.0 pp | +0.0 pp to +60.0 pp | 1 | 0 | 4 |
| longmemeval | All evidence packed, single-session-user | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 4 |
| longmemeval | All evidence packed, temporal-reasoning | 55.6% | 55.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 9 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| longmemeval | 0/42 to 0/42 | 1093.8 to 1128.2 | 0.297 / 0.906 s to 0.301 / 0.531 s | 49.5 to 61.7 | 80.9% to 76.0% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 9.7 to 8.2, after 9.2 to 7.0.

- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 12, VECTOR 13.
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
| longmemeval | all | 1026 (49.3%) / 17 / 164 | 1111 (42.8%) / 13 / 422 |
| longmemeval | knowledge-update | 162 (54.2%) / 2 / 24 | 170 (45.7%) / 4 / 78 |
| longmemeval | multi-session | 342 (54.1%) / 5 / 67 | 403 (47.3%) / 0 / 162 |
| longmemeval | single-session-assistant | 133 (56.6%) / 3 / 27 | 117 (48.3%) / 0 / 41 |
| longmemeval | single-session-preference | 113 (45.9%) / 3 / 12 | 102 (31.0%) / 2 / 35 |
| longmemeval | single-session-user | 82 (37.4%) / 1 / 13 | 87 (36.0%) / 1 / 22 |
| longmemeval | temporal-reasoning | 194 (43.1%) / 3 / 21 | 232 (41.7%) / 6 / 84 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
