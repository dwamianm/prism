# LongMemEval-V2 4K-budget development study

**Completed:** 2026-09-14

**Systems:** 4K-budget PRME versus the official no-memory adapter

**Reader:** local Ollama `qwen3.5:9b`, thinking disabled

**Questions:** the same 149 web-small static, dynamic and procedure questions used by the completed 32K study

**Scoring:** unmodified official deterministic evaluation functions

## Result

The 4K-budget PRME arm answered 49 of 149 questions correctly (32.89%). The fresh
no-memory arm answered 10 of 149 (6.71%), a paired improvement of 26.17
percentage points. PRME won 45 pairs, lost 6 and tied 98. The question-bootstrap
95% interval for the paired difference is 17.45 to 34.90 points, and the exact
two-sided McNemar p-value is `1.83e-8`.

| Category | Questions | 4K-budget PRME | No memory | Paired difference | Wins / losses | 95% bootstrap interval |
|---|---:|---:|---:|---:|---:|---:|
| Dynamic | 49 | 11 (22.45%) | 1 (2.04%) | +20.41 points | 11 / 1 | +8.16 to +32.65 |
| Procedure | 41 | 21 (51.22%) | 3 (7.32%) | +43.90 points | 20 / 2 | +24.39 to +60.98 |
| Static | 59 | 17 (28.81%) | 6 (10.17%) | +18.64 points | 14 / 3 | +6.78 to +32.20 |
| **Overall** | **149** | **49 (32.89%)** | **10 (6.71%)** | **+26.17 points** | **45 / 6** | **+17.45 to +34.90** |

PRME returned `UNKNOWN` on 35 questions (23.49%); no memory returned it on 63
(42.28%). Neither arm produced an empty response. A separate standard-library
recount of the two 149-row files reproduced every overall and category score and
the 45/6/98 paired outcomes.

## Efficiency and the 32K reference

The 4K bundle used an exact internal budget of 4,096 `cl100k_base` tokens.
Under the upstream reader tokenizer, mean memory context was 7,321 tokens,
versus 43,195 in the earlier 32K study, an 83.05% reduction. Total prompt tokens
fell from 6,528,210 to 1,146,080 (82.44%). The PRME arm took 4,613 seconds versus
10,683 seconds in the earlier run, while mean memory-query latency fell from
0.520 to 0.436 seconds. Arm order, local generation variance and different
prompt lengths prevent treating wall time as a controlled latency comparison.

The efficiency gain did not preserve answer quality. On the same question IDs,
the 4K arm scored 49 versus 80 for the earlier auditable 32K arm. The 4K arm won
5 pairs, lost 36 and tied 108, a 20.81-point accuracy regression. The loss was
largest on static questions: 17 correct versus 35. Inspection confirms that both
arms use auditable rendering; the installed configuration named `prme_compact`
is a 4K budget preset. The PRME revision, downstream reader cap and generation
time also differ, so the result is not a strict single-variable budget ablation.

| Same-cohort arm | Correct | Accuracy | Mean reader memory context |
|---|---:|---:|---:|
| Auditable 32K | 80 / 149 | 53.69% | 43,195 tokens |
| 4K budget | 49 / 149 | 32.89% | 7,321 tokens |
| Change | -31 | -20.81 points | -83.05% |

## Protocol and decision

The 4K study was registered before its answer generation with a frozen
PRME `08311ac`, adapter, launcher, upstream harness, configurations, cohort order,
reader settings and saved-memory artifact. PRME ran first, followed by a fresh
no-memory arm. Both used the same Qwen model digest, temperature 0.6, top-p 0.95,
top-k 20, one concurrent request, 4,096 maximum completion tokens and disabled
reasoning. The schema-2 comparator accepted the exact source and system binding.

This is a post-result development study: all 149 questions and their earlier
32K outcomes were already known. It shows that 4K PRME still adds substantial
memory utility over no memory, but it does not support adopting this budget as
the quality reference. The 4K preset remains an explicit option. The next
efficiency work should measure an intermediate budget curve and improve evidence
selection before changing a quality-oriented default.

The [registration](longmemeval-v2-web-compact4k-deterministic-dev-v1-registration.json),
[paired comparison](longmemeval-v2-web-compact4k-deterministic-dev-v1-comparison.json),
[same-cohort diagnostic](longmemeval-v2-web-compact4k-vs-32k-diagnostic.json),
[PRME metrics](longmemeval-v2-web-compact4k-deterministic-dev-v1-prme-metrics.json),
and [no-memory metrics](longmemeval-v2-web-compact4k-deterministic-dev-v1-no-memory-metrics.json)
contain the machine-readable evidence. The two execution manifests bind each
arm's exact invocation and artifacts without publishing raw model answers.

This result does not establish market leadership. It uses one known cohort, one
local reader, one saved memory pack, no competing memory product and only the
deterministically scored web subset.
