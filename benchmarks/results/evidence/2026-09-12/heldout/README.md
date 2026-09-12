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
An original-version held-out before/after run has **not** been completed. The
119-question development comparison cannot be treated as that missing evidence.

Elapsed wall time: 3,423.83 seconds, concurrency four. Other local tests and
experiments overlapped; baseline timings use already-warm shared indexes.
Reported timings are not an isolated performance comparison or an SLO.

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
```

Keep this completed test set out of future tuning. New selection, reranking or
retrieval policies need an independently reserved evaluation before being
claimed as validated improvements.
