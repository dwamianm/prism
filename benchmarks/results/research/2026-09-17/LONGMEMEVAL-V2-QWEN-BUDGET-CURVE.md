# LongMemEval-V2 local Qwen context-budget curve

## Result

On the registered 149-question web development cohort, increasing PRME's
internal context budget from 4,096 to 32,768 `cl100k_base` tokens improved the
local Qwen3.5 9B reader from 50/149 to 76/149 correct.

| Arm | Correct | Accuracy | Unknown | Mean packed tokens | Total reader tokens | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4K | 50/149 | 33.56% | 39 | 7,320.53 | 1,240,273 | 4,548.56 s |
| 8K | 58/149 | 38.93% | 29 | 13,345.89 | 2,169,291 | 5,426.24 s |
| 16K | 71/149 | 47.65% | 21 | 24,132.87 | 3,778,299 | 7,943.23 s |
| 32K | 76/149 | 51.01% | 13 | 43,195.28 | 6,624,931 | 13,440.84 s |

The 32K arm gained 26 questions over 4K, or 17.45 percentage points. The
question-bootstrap 95% interval was +10.07 to +25.50 points. The exact
two-sided McNemar p-value was 0.0000243, with 32 wins, 6 losses, and 111 ties.

| Comparison | Delta | Paired 95% interval | Wins | Losses | McNemar p |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8K - 4K | +5.37 points | -2.01 to +12.75 | 20 | 12 | 0.2153 |
| 16K - 8K | +8.72 points | +2.01 to +15.44 | 21 | 8 | 0.0241 |
| 32K - 16K | +3.36 points | -2.01 to +8.72 | 12 | 7 | 0.3593 |

Against 4K, 32K improved dynamic questions from 13/49 to 21/49, procedure
questions from 21/41 to 23/41, and static questions from 16/59 to 32/59. The
last step from 16K to 32K was positive in all three categories but inconclusive
overall. This supports 16K as a more efficient local-reader preset and 32K as a
quality-oriented preset; it does not show that every workload benefits from the
larger budget.

Wall time is included for completeness, but it is not valid comparative latency
evidence. These long local runs overlapped other model, indexing, and benchmark
work on the same machine. Token counts and answer scores remain protocol-valid.

## Registered protocol

- Cohort: all 149 web questions in the previously scored development slice.
- Memory payload: identical artifact digest
  `225d45889ac546fbeab258b79150dc4ad3ff52c1fcf696e969da51936afeaa46`
  in every arm.
- Reader: local `qwen3.5:9b`, digest
  `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`,
  Q4_K_M, temperature 0, top-p 1, top-k 20, thinking disabled, one concurrent
  request.
- Context format: auditable, with up to eight source screenshots.
- Source revisions: PRME `7ae3a8461b872fe209a3041a81789ed11544f5cf` and
  LongMemEval-V2 `2cc8c540bdb87fe6761629b585e727e1c4704520`.
- Registration SHA-256:
  `70750425d9f1fc1f0c41d8d3d8d4aa5ba869f8de1d3ce8f9b5fe82fe99b5a4c8`.
- Comparison SHA-256:
  `eb09f9d8e7fa4ee31b382715f830e6945d12b40deadd34f6d6f58547d2431d8b`.

## Claim boundary

This is a development budget curve on a previously scored, web-only cohort with
a shared haystack. It is evidence for PRME context-budget decisions and a
reproducible offline baseline. It is not a fresh holdout, a full LongMemEval-V2
Small score, or a competitor comparison. Question-level bootstrap intervals
condition on this selected cohort and do not model dependence introduced by the
shared haystack.

The aggregate-only machine-readable report is
[`longmemeval-v2-qwen-budget-curve.json`](longmemeval-v2-qwen-budget-curve.json).
