# Offline evidence gate comparison

Before: commit `eca1004e92e14fae6003aa5c4698a5c04ab2b7a8`, current defaults.
After: commit `eca1004e92e14fae6003aa5c4698a5c04ab2b7a8`, overrides `{"packing": {"session_context_packing": "adjacent"}}`.

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.0% | 84.9% | +0.9 pp | +0.1 pp to +1.8 pp | 28 | 14 | 1494 |
| locomo | All evidence packed with memory text | 84.0% | 84.9% | +0.9 pp | +0.1 pp to +1.8 pp | 28 | 14 | 1494 |
| locomo | Evidence recall | 89.2% | 89.9% | +0.8 pp | +0.2 pp to +1.4 pp | 34 | 25 | 1477 |
| locomo | Projected accuracy | 77.6% | 78.3% | +0.7 pp | +0.2 pp to +1.3 pp | 28 | 14 | 1498 |
| locomo | All evidence packed, multi-hop | 49.6% | 49.6% | +0.0 pp | -2.8 pp to +2.8 pp | 9 | 9 | 264 |
| locomo | All evidence packed, open-domain | 57.6% | 57.6% | +0.0 pp | -4.3 pp to +4.3 pp | 2 | 2 | 88 |
| locomo | All evidence packed, single-hop | 95.2% | 96.8% | +1.5 pp | +0.7 pp to +2.5 pp | 14 | 1 | 826 |
| locomo | All evidence packed, temporal | 92.2% | 92.5% | +0.3 pp | -0.9 pp to +1.6 pp | 3 | 2 | 316 |
| longmemeval | All evidence packed | 94.7% | 95.1% | +0.4 pp | +0.0 pp to +1.1 pp | 2 | 0 | 468 |
| longmemeval | All evidence packed with memory text | 94.7% | 95.1% | +0.4 pp | +0.0 pp to +1.1 pp | 2 | 0 | 468 |
| longmemeval | Evidence recall | 97.3% | 97.6% | +0.3 pp | +0.0 pp to +0.9 pp | 2 | 0 | 468 |
| longmemeval | Projected accuracy | 91.4% | 91.6% | +0.3 pp | +0.0 pp to +0.6 pp | 2 | 0 | 498 |
| longmemeval | All evidence packed, knowledge-update | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 72 |
| longmemeval | All evidence packed, multi-session | 89.3% | 90.1% | +0.8 pp | +0.0 pp to +2.5 pp | 1 | 0 | 120 |
| longmemeval | All evidence packed, single-session-assistant | 96.4% | 96.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 56 |
| longmemeval | All evidence packed, single-session-preference | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 30 |
| longmemeval | All evidence packed, single-session-user | 96.9% | 98.4% | +1.6 pp | +0.0 pp to +4.7 pp | 1 | 0 | 63 |
| longmemeval | All evidence packed, temporal-reasoning | 93.7% | 93.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 127 |

| Benchmark | Saved contexts reproduced | Records per context | Memory text share | Records without text |
|---|---|---|---|---|
| locomo | 0/1540 to 0/1540 | 84.5 to 86.5 | 96.6% to 96.6% | 0 to 0 |
| longmemeval | 0/500 to 0/500 | 39.9 to 40.4 | 82.6% to 82.4% | 0 to 0 |

Packed records through session expansion: reached (share of packed records) / found by no other path / scored by a session decay.

| Benchmark | Category | Before | After |
|---|---|---|---|
| locomo | all | 59802 (45.9%) / 9 / 32771 | 70362 (52.8%) / 3232 / 43932 |
| locomo | multi-hop | 10766 (45.7%) / 0 / 5872 | 12703 (52.8%) / 579 / 7921 |
| locomo | open-domain | 3508 (42.4%) / 0 / 1939 | 4213 (49.9%) / 241 / 2687 |
| locomo | single-hop | 33297 (46.9%) / 3 / 18204 | 38944 (53.6%) / 1715 / 24165 |
| locomo | temporal | 12231 (44.7%) / 6 / 6756 | 14502 (51.7%) / 697 / 9159 |
| longmemeval | all | 10904 (54.7%) / 0 / 2431 | 11246 (55.6%) / 1 / 2782 |
| longmemeval | knowledge-update | 1692 (55.7%) / 0 / 368 | 1728 (56.5%) / 0 / 406 |
| longmemeval | multi-session | 2918 (59.3%) / 0 / 657 | 2984 (60.0%) / 0 / 723 |
| longmemeval | single-session-assistant | 1314 (53.9%) / 0 / 327 | 1365 (55.2%) / 0 / 379 |
| longmemeval | single-session-preference | 670 (45.5%) / 0 / 149 | 687 (46.4%) / 0 / 164 |
| longmemeval | single-session-user | 1427 (48.6%) / 0 / 303 | 1510 (50.0%) / 0 / 391 |
| longmemeval | temporal-reasoning | 2883 (56.2%) / 0 / 627 | 2972 (57.0%) / 1 / 719 |

Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so they are narrower than conversation-level intervals.

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
