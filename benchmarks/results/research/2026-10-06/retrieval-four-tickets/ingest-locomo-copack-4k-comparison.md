# Offline evidence gate comparison

Before: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"packing": {"token_budget": 4096}}`.
After: commit `55d3eb61a5c914a801225b69aed7b6c77568b496` with uncommitted changes, overrides `{"packing": {"co_pack_sources": true, "token_budget": 4096}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 85.3% | 85.3% | +0.0 pp | -2.0 pp to +2.0 pp | 1 | 1 | 148 |
| locomo | All evidence packed with memory text | 85.3% | 85.3% | +0.0 pp | -2.0 pp to +2.0 pp | 1 | 1 | 148 |
| locomo | Evidence recall | 89.6% | 89.8% | +0.2 pp | -0.9 pp to +1.8 pp | 1 | 2 | 147 |
| locomo | All evidence among the candidates | 98.7% | 99.3% | +0.7 pp | +0.0 pp to +2.0 pp | 1 | 0 | 149 |
| locomo | Projected accuracy | 78.0% | 78.1% | +0.1 pp | -1.3 pp to +1.5 pp | 1 | 1 | 150 |
| locomo | All evidence packed, multi-hop | 56.2% | 53.1% | -3.1 pp | -9.4 pp to +0.0 pp | 0 | 1 | 31 |
| locomo | All evidence packed, open-domain | 63.6% | 63.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 11 |
| locomo | All evidence packed, single-hop | 94.3% | 95.7% | +1.4 pp | +0.0 pp to +4.3 pp | 1 | 0 | 69 |
| locomo | All evidence packed, temporal | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 37 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/152 to 0/152 | 698.8 to 716.8 | 0.259 / 0.369 s to 0.319 / 0.418 s | 79.2 to 79.3 | 96.0% to 96.1% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 5.8 to 4.7, after 4.7 to 5.4.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 1 (conv-26-q0048).
- locomo before: 3 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 3.
- locomo after: 3 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 3.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 32 | 0 | 0 |
| locomo | open-domain | 13 | 0 | 0 |
| locomo | single-hop | 70 | 0 | 0 |
| locomo | temporal | 37 | 0 | 0 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 4267 (35.4%) / 71 / 2261 | 4043 (33.6%) / 59 / 2071 |
| locomo | multi-hop | 933 (35.4%) / 17 / 485 | 885 (33.6%) / 13 / 448 |
| locomo | open-domain | 346 (33.6%) / 4 / 193 | 324 (31.4%) / 3 / 176 |
| locomo | single-hop | 1950 (35.1%) / 34 / 1020 | 1848 (33.2%) / 28 / 931 |
| locomo | temporal | 1038 (36.7%) / 16 / 563 | 986 (34.9%) / 15 / 516 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
