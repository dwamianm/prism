# Offline evidence gate

Run: commit `93935a165257a96bcf6613df6dc7e9316c545b22`, current defaults.

**Built packs, not the saved run.** Build `batch-512-lme-deepseek-slice` under `/Users/dwamianm/Sites/prism/data/extracted-packs-v1/batch-512-lme-deepseek-slice` (42 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| longmemeval | 42 | 0/42 | 1232.3 | 0.302 / 0.633 s | 47.1 | 80.3% | 0 | 33/38 (86.8%) | 16 | 85.7% |

## longmemeval

2026-09-23 baseline: 23.9 records per context, 32% memory text, all evidence packed for 403/470 annotated questions. This run: 47.1, 80.3%, 33/38.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| knowledge-update | 6 | 5/5 (100.0%) | 5/5 (100.0%) | 100.0% | 9 | 80.0% | 90.7% |
| multi-session | 13 | 10/10 (100.0%) | 8/10 (80.0%) | 80.0% | 35 | 48.4% | 80.9% |
| single-session-assistant | 5 | 5/5 (100.0%) | 5/5 (100.0%) | 100.0% | 6 | 100.0% | 95.5% |
| single-session-preference | 5 | 5/5 (100.0%) | 5/5 (100.0%) | 100.0% | 19 | 71.4% | 95.5% |
| single-session-user | 4 | 4/4 (100.0%) | 4/4 (100.0%) | 100.0% | 6.5 | 100.0% | 95.5% |
| temporal-reasoning | 9 | 9/9 (100.0%) | 6/9 (66.7%) | 66.7% | 19.5 | 54.5% | 74.1% |

Packed records through session expansion: 691 reached (35.0% of packed records), 16 found by no other path, 305 scored by a session decay.

Aggregation: 13 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 13 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 11, VECTOR 13.

Annotated evidence turns cited by a packed extracted record: 24 of 79 (30.4%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
