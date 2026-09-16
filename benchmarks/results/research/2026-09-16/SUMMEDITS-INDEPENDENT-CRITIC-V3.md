# SummEdits independent critic trial v3

**Result: failed.** An independent adversarial DeepSeek critic reduced false
support but rejected too many correct summaries and still did not reach the
fixed precision requirement.

## Bound protocol

- Runner revision: `c9c9383302cac6d418d7e6db124238d804432c73`
- Registration commit: `3710b49875e02f514310f710ab3413551e43d930`
- Registration SHA-256:
  `bc6202d34e6cdfdf9bfc79dd4350ec212191ac93679082c686c75c0044587a6f`
- Primary artifact: exact v2 result and private generated-atom state, both
  SHA-256 bound before critic inference
- Candidate set: all 46 v2 support decisions; all 54 v2 rejections remained
  rejected without a critic call
- Critic: `deepseek-v4.1-flash:cloud`, Ollama manifest
  `e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`
- Acceptance: both Mistral and DeepSeek must support every identical fixed atom
  with valid document segment IDs

The critic completed in 306.63 wall seconds with 1,094.57 aggregate call
seconds under four-call concurrency. All 46 critic responses passed reference
integrity.

## Result

| Metric | V3 independent critic | V2 primary | Change |
|---|---:|---:|---:|
| Accuracy | 61.00% | 76.00% | -15.00 points |
| Balanced accuracy | 61.00% | 76.00% | -15.00 points |
| Supported precision | 78.95% | 78.26% | +0.69 points |
| Supported recall | 30.00% | 72.00% | -42.00 points |
| Supported F1 | 43.48% | 75.00% | -31.52 points |
| False-support rate | 8.00% | 20.00% | -12.00 points |
| Critic reference integrity | 100% | — | — |

The combined confusion matrix was 15 true positives, 4 false positives, 46
true negatives, and 35 false negatives. Among the 46 primary candidates, the
critic rejected 21/36 correct summaries and 6/10 incorrect summaries.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Cases evaluated | at least 100 | 100 | pass |
| Supported precision | at least 90% | 78.95% | **fail** |
| Supported recall | at least 60% | 30.00% | **fail** |
| Balanced accuracy | at least 75% | 61.00% | **fail** |
| False-support rate | at most 10% | 8.00% | pass |
| Critic reference integrity | at least 98% | 100% | pass |

## Decision

Do not integrate model intersection. Independent skepticism lowered coverage
without distinguishing correct from incorrect primary supports well enough.
The small precision gain and large recall loss rule out a two-model unanimity
default on this cohort.

The next investigation should use a task-specific verifier over fixed atomic
claims or an evidence-local deterministic mismatch check, rather than another
general-model vote. Any selected approach needs a separately bound external
cohort because all SummEdits evaluation outcomes are now observed.

## Artifact integrity

- Result file SHA-256:
  `742d42b0d8c38e2e158bb8c7307311cfb0ad9f873ac400bbac28fea4d5289676`
- Canonical result SHA-256:
  `0420c884549d358402301b38a7188347b75f5ad01b8ad2fe4a4e89f7e2704bc3`
