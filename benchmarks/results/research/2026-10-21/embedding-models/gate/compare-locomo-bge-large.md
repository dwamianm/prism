# Offline evidence gate comparison

Before: commit `349a9926d7e4132952c4b4f5cc82a89875bc9e09` with uncommitted changes, current defaults.
After: commit `349a9926d7e4132952c4b4f5cc82a89875bc9e09` with uncommitted changes, current defaults.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 86.1% | +2.1 pp | +1.2 pp to +3.0 pp | 41 | 9 | 1486 |
| locomo | All evidence packed with memory text | 84.0% | 86.1% | +2.1 pp | +1.2 pp to +3.0 pp | 41 | 9 | 1486 |
| locomo | Evidence recall | 89.2% | 91.1% | +2.0 pp | +1.3 pp to +2.7 pp | 75 | 16 | 1445 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.1 pp | +0.0 pp to +0.2 pp | 1 | 0 | 1535 |
| locomo | Projected accuracy | 77.6% | 79.0% | +1.4 pp | +0.8 pp to +2.0 pp | 41 | 9 | 1490 |
| locomo | All evidence packed, multi-hop | 49.6% | 54.6% | +5.0 pp | +1.8 pp to +8.5 pp | 19 | 5 | 258 |
| locomo | All evidence packed, open-domain | 57.6% | 62.0% | +4.3 pp | +1.1 pp to +8.7 pp | 4 | 0 | 88 |
| locomo | All evidence packed, single-hop | 95.2% | 96.1% | +0.8 pp | +0.1 pp to +1.7 pp | 9 | 2 | 830 |
| locomo | All evidence packed, temporal | 92.2% | 94.4% | +2.2 pp | +0.3 pp to +4.0 pp | 9 | 2 | 310 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 551.8 | 0.283 / 0.507 s to 0.261 / 0.395 s | 84.5 to 80.3 | 96.6% to 96.7% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 3.4 to 25.2, after 24.7 to 20.9.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 1 (conv-44-q0043).
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.

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
| locomo | all | 59802 (45.9%) / 9 / 32771 | 59050 (47.7%) / 16 / 31553 |
| locomo | multi-hop | 10766 (45.7%) / 0 / 5872 | 10546 (47.4%) / 1 / 5598 |
| locomo | open-domain | 3508 (42.4%) / 0 / 1939 | 3446 (44.2%) / 0 / 1855 |
| locomo | single-hop | 33297 (46.9%) / 3 / 18204 | 32698 (48.6%) / 6 / 17336 |
| locomo | temporal | 12231 (44.7%) / 6 / 6756 | 12360 (46.9%) / 9 / 6764 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
