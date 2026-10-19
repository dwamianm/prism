# Offline evidence gate

Run: commit `d810ee5552e388f85aac62f4cf8c6c6038f0b1c8`, current defaults.

**Built packs, not the saved run.** Build `full-defaults-lme-deepseek-d810ee55` under `/Users/dmac/Sites/prism/data/extracted-packs-v1/full-defaults-lme-deepseek-d810ee55` (500 packs, extraction model `deepseek-v4.1-flash:cloud`) was made with `ingest()`, so no saved context exists to match. Evidence counts include turns that packed extracted records cite.

| Benchmark | Questions | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 | Records per context | Memory text share | Records without text | All evidence packed | Median evidence rank | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| longmemeval | 500 | 0/500 | 1169.7 | 0.301 / 0.920 s | 46.2 | 80.4% | 0 | 455/470 (96.8%) | 11 | 92.7% |

## longmemeval

2026-09-23 baseline: 23.9 records per context, 32% memory text, all evidence packed for 403/470 annotated questions. This run: 46.2, 80.4%, 455/470.

| Category | Questions | All evidence among the candidates | All evidence packed | Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| knowledge-update | 78 | 72/72 (100.0%) | 72/72 (100.0%) | 100.0% | 10 | 94.4% | 93.3% |
| multi-session | 133 | 121/121 (100.0%) | 115/121 (95.0%) | 95.0% | 14 | 65.6% | 90.8% |
| single-session-assistant | 56 | 56/56 (100.0%) | 55/56 (98.2%) | 98.2% | 7 | 94.6% | 94.4% |
| single-session-preference | 30 | 30/30 (100.0%) | 30/30 (100.0%) | 100.0% | 10 | 70.5% | 95.5% |
| single-session-user | 70 | 64/64 (100.0%) | 62/64 (96.9%) | 96.9% | 6 | 95.5% | 94.1% |
| temporal-reasoning | 133 | 127/127 (100.0%) | 121/127 (95.3%) | 95.3% | 13 | 77.1% | 92.1% |

Packed records through session expansion: 7344 reached (31.8% of packed records), 133 found by no other path, 2754 scored by a session decay.

Aggregation: 180 questions read as counts or lists, whose vector, lexical and graph limits are multiplied by `aggregation_k_multiplier` up to `aggregation_k_max`; 180 still filled a widened limit. Questions by path at its limit, including the fixed keyword-scan (LEXICAL_AGG) and pinned limits: LEXICAL_AGG 153, VECTOR 180.

Annotated evidence turns cited by a packed extracted record: 243 of 886 (27.4%).

**Projected accuracy is a planning estimate, not an answer score.** Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions that retrieval cannot move (no resolvable annotation, or abstention) keep their category's measured rate. At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by construction; LongMemEval-S category values are pooled estimates. The audit's re-pack simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3% against 64.0% measured (memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1).

- It ignores distractor effects: added or reordered context can change answers without changing evidence coverage.
- It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can exist elsewhere.
- Its conditional accuracies come from one reader and one strict judge (GPT-5.4).
- All 2,040 questions have already been examined, so this is a development gate. Publication claims need fresh or held-out data.
