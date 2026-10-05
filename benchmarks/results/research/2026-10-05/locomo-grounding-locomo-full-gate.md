# Offline evidence gate

Run: commit `d48769a0c98bcd03ec0576266409e20b9aa98034`, current defaults.

**Built packs, not the saved run.** Build `grounding-locomo-full` under `/Users/dmac/Sites/prism/data/extracted-packs-v1/grounding-locomo-full` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 941.1 | 0.357 / 0.525 s | 73.7 | 95.9% | 0 | 1124/1536 (73.2%) | 25 | 70.2% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 73.7, 95.9%, 90/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 235/282 (83.3%) | 90/282 (31.9%) | 31.9% | 91 | 25.1% | 38.6% |
| open-domain | 96 | 67/92 (72.8%) | 41/92 (44.6%) | 44.6% | 132 | 20.9% | 55.5% |
| single-hop | 841 | 830/841 (98.7%) | 725/841 (86.2%) | 86.2% | 12 | 71.9% | 81.0% |
| temporal | 321 | 312/321 (97.2%) | 268/321 (83.5%) | 83.5% | 13 | 66.7% | 74.3% |

Packed records through session expansion: 40875 reached (36.0% of packed records), 432 found by no other path, 8500 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 19, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 1586 of 2345 (67.6%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
