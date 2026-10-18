# Offline evidence gate comparison

Before: commit `d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, current defaults.
After: commit `d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| longmemeval | All evidence packed | 97.0% | 96.8% | -0.2 pp | -1.5 pp to +1.1 pp | 4 | 5 | 461 |
| longmemeval | All evidence packed with memory text | 97.0% | 96.8% | -0.2 pp | -1.5 pp to +1.1 pp | 4 | 5 | 461 |
| longmemeval | Evidence recall | 98.4% | 97.9% | -0.4 pp | -1.3 pp to +0.4 pp | 4 | 6 | 460 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 92.8% | 92.7% | -0.1 pp | -0.9 pp to +0.6 pp | 4 | 5 | 491 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 95.0% | 95.0% | +0.0 pp | -3.3 pp to +3.3 pp | 2 | 2 | 117 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 98.2% | +1.8 pp | +0.0 pp to +5.4 pp | 1 | 0 | 55 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 100.0% | 96.9% | -3.1 pp | -7.8 pp to +0.0 pp | 0 | 2 | 62 |
| longmemeval | All evidence packed, temporal-reasoning | 95.3% | 95.3% | +0.0 pp | -2.4 pp to +2.4 pp | 1 | 1 | 125 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| longmemeval | 0/500 to 0/500 | 1220.4 to 1169.7 | 0.303 / 0.878 s to 0.301 / 0.920 s | 42.0 to 46.2 | 81.5% to 80.4% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 2.6 to 3.4, after 2.1 to 2.5.

- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 180 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 159, VECTOR 180.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 180 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 153, VECTOR 180.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| longmemeval | knowledge-update | 78 | 11 | 11 |
| longmemeval | multi-session | 133 | 23 | 23 |
| longmemeval | single-session-assistant | 56 | 3 | 3 |
| longmemeval | single-session-preference | 30 | 1 | 1 |
| longmemeval | single-session-user | 70 | 7 | 7 |
| longmemeval | temporal-reasoning | 133 | 37 | 37 |

- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| longmemeval | all | 4901 (23.3%) / 98 / 2427 | 7344 (31.8%) / 133 / 2754 |
| longmemeval | knowledge-update | 790 (24.2%) / 5 / 454 | 1114 (30.8%) / 9 / 430 |
| longmemeval | multi-session | 1321 (23.2%) / 10 / 727 | 1977 (31.3%) / 12 / 791 |
| longmemeval | single-session-assistant | 618 (24.9%) / 15 / 145 | 1063 (40.7%) / 17 / 284 |
| longmemeval | single-session-preference | 262 (18.8%) / 4 / 134 | 475 (29.9%) / 11 / 208 |
| longmemeval | single-session-user | 657 (22.8%) / 15 / 321 | 958 (30.7%) / 21 / 375 |
| longmemeval | temporal-reasoning | 1253 (23.6%) / 49 / 646 | 1757 (29.9%) / 63 / 666 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
