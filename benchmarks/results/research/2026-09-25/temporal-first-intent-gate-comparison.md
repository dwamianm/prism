# Offline evidence gate comparison

Before: commit `22007c0147517b342c72c5218991ce5783fce866`, current defaults.
After: commit `22007c0147517b342c72c5218991ce5783fce866`, overrides `{"query_intent_order": "temporal_first"}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 84.3% | +0.3 pp | +0.1 pp to +0.7 pp | 5 | 0 | 1531 |
| locomo | All evidence packed with memory text | 84.0% | 84.3% | +0.3 pp | +0.1 pp to +0.7 pp | 5 | 0 | 1531 |
| locomo | Evidence recall | 89.2% | 89.5% | +0.3 pp | +0.1 pp to +0.6 pp | 6 | 0 | 1530 |
| locomo | Projected accuracy | 77.6% | 77.9% | +0.2 pp | +0.0 pp to +0.4 pp | 5 | 0 | 1535 |
| locomo | All evidence packed, multi-hop | 49.6% | 50.0% | +0.4 pp | +0.0 pp to +1.1 pp | 1 | 0 | 281 |
| locomo | All evidence packed, open-domain | 57.6% | 58.7% | +1.1 pp | +0.0 pp to +3.3 pp | 1 | 0 | 91 |
| locomo | All evidence packed, single-hop | 95.2% | 95.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 841 |
| locomo | All evidence packed, temporal | 92.2% | 93.1% | +0.9 pp | +0.0 pp to +2.2 pp | 3 | 0 | 318 |
| longmemeval | All evidence packed | 94.7% | 94.9% | +0.2 pp | +0.0 pp to +0.6 pp | 1 | 0 | 469 |
| longmemeval | All evidence packed with memory text | 94.7% | 94.9% | +0.2 pp | +0.0 pp to +0.6 pp | 1 | 0 | 469 |
| longmemeval | Evidence recall | 97.3% | 97.3% | +0.0 pp | +0.0 pp to +0.1 pp | 1 | 0 | 469 |
| longmemeval | Projected accuracy | 91.4% | 91.5% | +0.1 pp | +0.0 pp to +0.4 pp | 1 | 0 | 499 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 89.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 121 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 96.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 56 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 96.9% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 64 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 94.5% | +0.8 pp | +0.0 pp to +2.4 pp | 1 | 0 | 126 |

| Benchmark | Saved contexts reproduced | Records per context | Memory text share | Records without text |
|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 84.5 to 84.5 | 96.6% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 39.9 to 39.7 | 82.6% to 82.7% | 0 to 0 |

Questions whose candidates do not all share one temporal affinity, so that temporal scoring can reorder them under rank fusion:

| Benchmark | Category | Questions | Before | After |
|---|---|---:|---:|---:|
| locomo | multi-hop | 282 | 0 | 13 |
| locomo | open-domain | 96 | 0 | 8 |
| locomo | single-hop | 841 | 0 | 152 |
| locomo | temporal | 321 | 0 | 37 |
| longmemeval | knowledge-update | 78 | 11 | 23 |
| longmemeval | multi-session | 133 | 23 | 42 |
| longmemeval | single-session-assistant | 56 | 3 | 16 |
| longmemeval | single-session-preference | 30 | 1 | 1 |
| longmemeval | single-session-user | 70 | 7 | 8 |
| longmemeval | temporal-reasoning | 133 | 37 | 78 |

- locomo questions that entered the current-state path: 0.
- locomo questions that left the current-state path: 18 (conv-41-q0088, conv-41-q0101, conv-42-q0124, conv-42-q0163, conv-42-q0164, conv-42-q0187, conv-43-q0095, conv-43-q0127, conv-43-q0147, conv-44-q0080, conv-44-q0114, conv-47-q0072, conv-47-q0146, conv-48-q0061, conv-48-q0093, conv-48-q0154, conv-49-q0054, conv-50-q0107).
- longmemeval questions that entered the current-state path: 0.
- longmemeval questions that left the current-state path: 0.
The JSON report lists every question.

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
