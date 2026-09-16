# Held-out evidence retrieval — 2026-09-12

Frozen PRME `6fc6b67`, official full-history LongMemEval S, PRME split
`test` with seed `prme-evidence-v1`. **381/381 completed, zero errors**;
365 positively labeled questions enter evidence means. Sixteen unlabeled /
abstention questions require a separate answer/abstention evaluation.

The original report retains selected IDs, dataset checksum, source revision,
configuration/dependency versions and all per-question results. No partial
held-out answers or results were used to tune this profile. Later source,
identity, selection and formatting fixes do not change the frozen run's scope.

| Method | MRR | Recall@10 | Support recall / 2,048 tokens | / 4,096 | / 8,192 |
|---|---:|---:|---:|---:|---:|
| PRME | 0.6662 | 0.8291 | 0.8527 | 0.8968 | 0.9372 |
| BM25 | 0.6277 | 0.7556 | 0.7716 | 0.8124 | 0.8673 |
| Vector | 0.5651 | 0.7782 | 0.8315 | 0.9109 | 0.9514 |
| RRF | 0.6046 | 0.8315 | 0.8405 | 0.8998 | 0.9485 |

Paired question bootstrap (2,000 samples, seed 42), PRME minus baseline:

| Baseline | Budget | Delta, percentage points | 95% interval |
|---|---:|---:|---:|
| BM25 | 2,048 | +8.11 | +5.54 to +10.82 |
| Vector | 2,048 | +2.12 | −1.48 to +5.82 |
| RRF | 2,048 | +1.22 | −0.93 to +3.41 |
| Vector | 4,096 | −1.42 | −4.23 to +1.40 |
| RRF | 4,096 | −0.31 | −2.05 to +1.36 |
| Vector | 8,192 | −1.42 | −3.71 to +0.79 |
| RRF | 8,192 | −1.13 | −2.60 to +0.22 |

PRME has the best point estimate at the smallest budget and the best MRR here;
it does not dominate vector/RRF retrieval. All listed PRME-versus-vector/RRF
budget intervals include zero. Larger budgets favor vector/RRF point estimates.
Shared histories can make questions dependent, and these descriptive comparisons
have no multiple-comparison correction. A general improvement is not established.

This is raw NOTE-turn evidence retrieval with a shared whole-turn packer. It
measures neither PRME's product context formatter nor LLM extraction, answer
accuracy, lifecycle interpretation, semantic aggregation or competitor products.

## Original-version comparison

The original `168fa0e` run also completed **381/381, zero errors**, with the same
365 labeled questions, corpus checksum, source counts, evidence labels, token
budgets and candidate limit. `original-168fa0e.json` preserves that run;
`before-after.json` contains the paired comparison against `6fc6b67`.

| PRME measure | Original | Updated | Delta, percentage points | 95% interval |
|---|---:|---:|---:|---:|
| MRR | 0.6583 | 0.6662 | +0.79 | −0.53 to +2.20 |
| Recall@10 | 0.8215 | 0.8291 | +0.76 | −0.60 to +2.28 |
| Support recall / 2,048 tokens | 0.8512 | 0.8527 | +0.15 | −1.28 to +1.74 |
| Support recall / 4,096 tokens | 0.8868 | 0.8968 | +1.00 | −0.52 to +2.68 |
| Support recall / 8,192 tokens | 0.9250 | 0.9372 | +1.22 | −0.16 to +2.80 |

**This does not establish a held-out retrieval improvement.** Every displayed
interval includes zero. At 2,048 tokens, 13 questions improved, 13 worsened and
339 were unchanged. The matched BM25, vector and RRF controls had zero packed
recall change at every budget. This is a descriptive endpoint comparison: the
original used wall-clock query interpretation and the updated run used each
question's timestamp. It therefore combines software and evaluation-clock
changes and cannot isolate an algorithm's effect. No held-out question was used
to revise the frozen profile after observing these results.

Elapsed wall time: 3,423.83 seconds, concurrency four. Other local tests and
experiments overlapped; baseline timings use already-warm shared indexes.
Reported timings are not an isolated performance comparison or an SLO.
The original run took 5,094.04 seconds at concurrency one, also overlapping other
work. Its elapsed time must not be compared as a speedup against concurrency four.

Reproduce from the frozen source checkout:

```sh
python -m benchmarks.retrieval_eval \
  --dataset /absolute/path/to/longmemeval_s_cleaned.json \
  --variant s --split test --clock question --concurrency 4 \
  --budgets 2048 4096 8192 --output /tmp/prme-heldout.json
```

Generate the within-run comparison using the current analysis tool:

```sh
python -m benchmarks.compare_methods /tmp/prme-heldout.json \
  --output /tmp/prme-heldout-comparison.json
python -m benchmarks.compare_evidence original-168fa0e.json duration-6fc6b67.json \
  --output before-after.json
```

Keep this completed test set out of future tuning. New selection, reranking or
retrieval policies need an independently reserved evaluation before being
claimed as validated improvements.
