# Offline evidence gate comparison

Before: commit `f05b0ff0bcb293e1019f9c717f92dcca8d070168`, current defaults.
After: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| longmemeval | All evidence packed | 71.1% | 76.3% | +5.3 pp | +0.0 pp to +13.2 pp | 2 | 0 | 36 |
| longmemeval | All evidence packed with memory text | 71.1% | 76.3% | +5.3 pp | +0.0 pp to +13.2 pp | 2 | 0 | 36 |
| longmemeval | Evidence recall | 84.1% | 88.4% | +4.3 pp | +1.3 pp to +7.9 pp | 6 | 0 | 32 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 38 |
| longmemeval | Projected accuracy | 76.6% | 79.6% | +3.1 pp | +0.0 pp to +7.6 pp | 2 | 0 | 40 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, multi-session | 40.0% | 40.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 10 |
| longmemeval | All evidence packed, single-session-assistant | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 5 |
| longmemeval | All evidence packed, single-session-preference | 80.0% | 100.0% | +20.0 pp | +0.0 pp to +60.0 pp | 1 | 0 | 4 |
| longmemeval | All evidence packed, single-session-user | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 4 |
| longmemeval | All evidence packed, temporal-reasoning | 55.6% | 66.7% | +11.1 pp | +0.0 pp to +33.3 pp | 1 | 0 | 8 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| longmemeval | 0/42 to 0/42 | 1093.8 to 1093.8 | 0.297 / 0.906 s to 0.378 / 1.001 s | 49.5 to 32.8 | 80.9% to 85.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 9.7 to 8.2, after 9.9 to 10.1.

- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 12, VECTOR 13.
- longmemeval after: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 12, VECTOR 13.

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
| longmemeval | all | 1026 (49.3%) / 17 / 164 | 493 (35.8%) / 8 / 166 |
| longmemeval | knowledge-update | 162 (54.2%) / 2 / 24 | 68 (38.4%) / 0 / 24 |
| longmemeval | multi-session | 342 (54.1%) / 5 / 67 | 161 (38.4%) / 0 / 59 |
| longmemeval | single-session-assistant | 133 (56.6%) / 3 / 27 | 72 (38.3%) / 2 / 16 |
| longmemeval | single-session-preference | 113 (45.9%) / 3 / 12 | 54 (30.5%) / 0 / 22 |
| longmemeval | single-session-user | 82 (37.4%) / 1 / 13 | 45 (34.6%) / 2 / 15 |
| longmemeval | temporal-reasoning | 194 (43.1%) / 3 / 21 | 93 (32.4%) / 4 / 30 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
