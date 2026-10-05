# Offline evidence gate

Run: commit `8ed4e559b840dc03370b092f7c8d779dfd342ef2`, overrides `{"packing": {"fold_repeated_text": true}}`.

**Built packs, not the saved run.** Build `grounding-locomo-full` under `/Users/dmac/Sites/prism/data/extracted-packs-v1/grounding-locomo-full` (10 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| locomo | 1540 | 0/1540 | 941.1 | 0.431 / 0.601 s | 77.9 | 95.2% | 0 | 1227/1536 (79.9%) | 25 | 74.7% |

## locomo

2026-09-23 baseline: 25.2 records per context, 29% memory text, all evidence packed for 45/282 multi-hop questions. This run: 77.9, 95.2%, 132/282.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi-hop | 282 | 235/282 (83.3%) | 132/282 (46.8%) | 46.8% | 91 | 25.1% | 48.2% |
| open-domain | 96 | 67/92 (72.8%) | 50/92 (54.3%) | 54.3% | 132 | 20.9% | 59.7% |
| single-hop | 841 | 830/841 (98.7%) | 762/841 (90.6%) | 90.6% | 12 | 71.9% | 84.3% |
| temporal | 321 | 312/321 (97.2%) | 283/321 (88.2%) | 88.2% | 13 | 66.7% | 77.6% |

Packed records through session expansion: 18619 reached (15.5% of packed records), 1355 found by no other path, 7484 scored by a session decay.

Aggregation: 41 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 41 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: GRAPH 41, LEXICAL 19, LEXICAL_AGG 41, VECTOR 41.

Annotated evidence turns cited by a packed extracted record: 564 of 2345 (24.1%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
