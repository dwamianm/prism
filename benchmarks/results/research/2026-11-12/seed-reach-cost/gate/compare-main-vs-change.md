# Offline evidence gate comparison

Before: commit `56b51d53aa48d0c79a7a6d8b3a94c063273d31d4`, current defaults, LongMemEval-S controls regenerated at `/Users/dwamianm/Sites/prism/data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).
After: commit `56b51d53aa48d0c79a7a6d8b3a94c063273d31d4` with uncommitted changes, current defaults, LongMemEval-S controls regenerated at `/Users/dwamianm/Sites/prism/data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (manifest `0103b9509a35`).

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.1% | 84.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | All evidence packed with memory text | 84.1% | 84.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Evidence recall | 89.2% | 89.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 77.7% | 77.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1540 |
| locomo | All evidence packed, multi-hop | 50.0% | 50.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 282 |
| locomo | All evidence packed, open-domain | 57.6% | 57.6% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 92 |
| locomo | All evidence packed, single-hop | 95.4% | 95.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 841 |
| locomo | All evidence packed, temporal | 92.2% | 92.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 321 |
| longmemeval | All evidence packed | 94.7% | 94.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | All evidence packed with memory text | 94.7% | 94.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Evidence recall | 97.3% | 97.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 91.4% | 91.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 500 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 89.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 121 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 96.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 56 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 96.9% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 64 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 93.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 127 |

| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text |
|---|---|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 554.8 to 554.8 | 0.109 / 0.164 s to 0.129 / 0.212 s | 84.5 to 84.5 | 96.6% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 490.1 to 490.1 | 0.143 / 0.224 s to 0.122 / 0.197 s | 39.7 to 39.7 | 82.6% to 82.6% | 0 to 0 |

Retrieval latency depends on the machine and its load: compare it only between reports run on one machine, one at a time. One-minute load average from start to end: before 22.8 to 16.1, after 16.1 to 6.9.

- locomo questions that lost all their evidence among the candidates: 0.
- locomo questions that gained all their evidence among the candidates: 0.
- locomo before: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- locomo after: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 41.
- longmemeval questions that lost all their evidence among the candidates: 0.
- longmemeval questions that gained all their evidence among the candidates: 0.
- longmemeval before: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 91.
- longmemeval after: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 0 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 91.

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 0 |
| locomo | open-domain | 96 | 0 | 0 |
| locomo | single-hop | 841 | 0 | 0 |
| locomo | temporal | 321 | 0 | 0 |
| longmemeval | knowledge-update | 78 | 11 | 11 |
| longmemeval | multi-session | 133 | 23 | 23 |
| longmemeval | single-session-assistant | 56 | 3 | 3 |
| longmemeval | single-session-preference | 30 | 1 | 1 |
| longmemeval | single-session-user | 70 | 7 | 7 |
| longmemeval | temporal-reasoning | 133 | 37 | 37 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 0.
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 60045 (46.2%) / 8 / 33262 | 60045 (46.2%) / 8 / 33262 |
| locomo | multi-hop | 10846 (46.1%) / 0 / 6056 | 10846 (46.1%) / 0 / 6056 |
| locomo | open-domain | 3521 (42.6%) / 0 / 1963 | 3521 (42.6%) / 0 / 1963 |
| locomo | single-hop | 33447 (47.2%) / 2 / 18488 | 33447 (47.2%) / 2 / 18488 |
| locomo | temporal | 12231 (44.7%) / 6 / 6755 | 12231 (44.7%) / 6 / 6755 |
| longmemeval | all | 10891 (54.8%) / 0 / 2431 | 10891 (54.8%) / 0 / 2431 |
| longmemeval | knowledge-update | 1689 (56.4%) / 0 / 364 | 1689 (56.4%) / 0 / 364 |
| longmemeval | multi-session | 2916 (59.4%) / 0 / 657 | 2916 (59.4%) / 0 / 657 |
| longmemeval | single-session-assistant | 1311 (53.9%) / 0 / 325 | 1311 (53.9%) / 0 / 325 |
| longmemeval | single-session-preference | 669 (45.6%) / 0 / 150 | 669 (45.6%) / 0 / 150 |
| longmemeval | single-session-user | 1425 (48.6%) / 0 / 306 | 1425 (48.6%) / 0 / 306 |
| longmemeval | temporal-reasoning | 2881 (56.2%) / 0 / 629 | 2881 (56.2%) / 0 / 629 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
