# Full-history development evidence evaluation

These are complete source-evidence retrieval runs, not answer-accuracy or
cross-product superiority results. All 119 selected development questions
completed without errors; 114 have positive supporting-turn labels. The other
five are excluded from recall means and do not become perfect scores.

Dataset: [official LongMemEval S cleaned histories](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned), fixed split seed
`prme-evidence-v1`. Source text is ingested as raw NOTE turns with neutral source
IDs. Answers, answer-session markers, and supporting-turn labels are not ingested.
Each report records the dataset checksum, selected question IDs, source commit,
configuration, dependencies, and per-question results. Each question receives an
isolated memory pack. The shared evaluation packer keeps whole turns within real
token budgets; it does not measure the product context formatter or LLM extraction.

| Run | Query clock | Recall@10 | Support recall / 2,048 tokens | MRR |
|---|---|---:|---:|---:|
| Baseline `168fa0e` | Wall time | 89.77% | 90.79% | 0.8042 |
| Initial clock correction `0b997bd` | Dataset question time | 86.26% | 86.99% | 0.7997 |
| Intent correction `a478027` | Dataset question time | 90.50% | 91.52% | 0.8135 |
| Duration correction `6fc6b67` | Dataset question time | 90.94% | 91.96% | 0.8135 |
| BM25 baseline | — | 86.84% | 86.70% | 0.7470 |
| Vector baseline | — | 88.08% | 88.23% | 0.6547 |
| RRF baseline | — | 90.35% | 90.42% | 0.7748 |

The clock correction exposed an existing heuristic that treated temporal
questions without parsed dates as current-state questions. Strong recency
weighting then suppressed earlier supporting episodes. The intent correction
preserves earlier evidence for historical and aggregate questions. Its 2,048-token
support recall change against baseline is **+0.73 percentage points**, with a
paired question-bootstrap 95% interval of **−1.02 to +2.92 points**. This interval
does not establish an overall gain. There are two wins, one loss, and 111 ties.
The remaining loss asks for the duration of living in a current apartment; a
subsequent duration fix recovered its support in a targeted diagnostic and then
completed the same full development run. Its support recall change against
baseline is **+1.17 percentage points**, with a paired 95% interval of **0.00 to
+3.22 points**, two wins, no losses, and 112 ties. This is a small development-set
result, not a demonstrated general improvement. The frozen `6fc6b67` profile is
now being evaluated on the untouched 381-question test split.

Do not infer latency gains from these reports. The first two runs use one
question at a time; the later two use four, and other machine workloads overlapped.
The baseline methods share warmed indexes. Bootstrap intervals resample questions,
which can share histories; held-out confirmation is still required. The selected
381-question test split has not been used to tune these changes.

Run from the exact source commit shown in each report, with its disclosed
dependencies, using the dataset identified by its checksum:

```bash
python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_s_cleaned.json \
  --variant s --split dev --clock question --concurrency 4 \
  --output run.json
python -m benchmarks.compare_evidence baseline.json run.json --output comparison.json
```

The initial baseline predates `--clock` and uses wall time. The initial correction
used `--clock question` with one concurrent question. Do not relabel these
historical configurations as the current release or combine partial runs.


The [381-question held-out run](heldout/README.md) is now complete with zero
errors. The original-version run also completed all 381 questions without errors.
The paired comparison does not establish a held-out improvement: 2,048-token
support recall changed from 85.12% to 85.27%, with a 95% interval for the change
of −1.28 to +1.74 percentage points. The evaluation clock also changed. Neither
that endpoint comparison nor the vector/RRF comparisons establish superiority.
