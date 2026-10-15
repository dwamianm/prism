# Offline evidence gate

Run: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.

**Built packs, not the saved run.** Build `batch-1-ablation-locomo-deepseek` under `/Users/dwamianm/Sites/prism/data/extracted-packs-v1/batch-1-ablation-locomo-deepseek` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 931.8 | 0.394 / 0.581 s | 90.5 | 94.4% | 0 | 1263/1536 (82.2%) | 19 | 76.3% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 90.5, 94.4%, 145/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 259/282 (91.8%) | 145/282 (51.4%) | 51.4% | 65.5 | 33.9% | 51.1% |
| open-domain | 96 | 77/92 (83.7%) | 52/92 (56.5%) | 56.5% | 106 | 26.3% | 60.6% |
| single-hop | 841 | 839/841 (99.8%) | 779/841 (92.6%) | 92.6% | 8 | 75.1% | 85.8% |
| temporal | 321 | 319/321 (99.4%) | 287/321 (89.4%) | 89.4% | 8 | 70.0% | 78.5% |

Packed records through session expansion: 27144 reached (19.5% of packed records), 690 found by no other path, 17524 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 385 of 2345 (16.4%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
