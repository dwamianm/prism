# LongMemEval-V2 DeepSeek context-budget curve

## Result

On the registered 149-question web development cohort, increasing PRME's
internal context budget from 16,384 to 32,768 `cl100k_base` tokens improved the
DeepSeek v4.1 Flash reader from 67/149 to 82/149 correct.

| Arm | Correct | Accuracy | Unknown | Mean packed tokens | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16K | 67/149 | 44.97% | 46 | 24,132.87 | 619.88 s |
| 32K | 82/149 | 55.03% | 36 | 43,195.28 | 897.90 s |

The paired gain was 15 questions, or 10.07 percentage points. The question
bootstrap 95% interval was +4.03 to +16.78 points. The exact two-sided McNemar
p-value was 0.0041, with 20 wins, 5 losses, and 124 ties for 32K relative to
16K.

| Category | 16K | 32K | Delta | Paired 95% interval |
| --- | ---: | ---: | ---: | ---: |
| Dynamic | 17/49 | 21/49 | +8.16 points | 0.00 to +18.37 |
| Procedure | 26/41 | 29/41 | +7.32 points | -4.88 to +19.51 |
| Static | 24/59 | 32/59 | +13.56 points | +3.39 to +25.42 |

This result supports a larger default retrieval budget when answer quality is
the priority and the downstream model can accept the resulting context. It does
not establish that every model or workload benefits from the same budget.

## Registered protocol

- Cohort: all 149 web questions in the previously scored development slice.
- Memory payload: identical 1,768-file, 762,468,935-byte PRME pack in both arms.
- Reader: `deepseek-v4.1-flash:cloud`, temperature 0, top-p 1, top-k 20,
  reasoning effort `none`, thinking disabled, one concurrent request.
- Context format: auditable, with up to eight source screenshots.
- Source revisions: PRME `1a4c48b3d0adde5825c7452353bc471ad258c0b1` and
  LongMemEval-V2 `2cc8c540bdb87fe6761629b585e727e1c4704520`.
- Registration SHA-256:
  `6f1e86596b858130dd0a86feffea053ead34bac95b751449966868291ae9b355`.
- Comparison SHA-256:
  `bbce4bf29b02ea2ac6692a2a44c747cacf63484a077a2176556888238e00f1d6`.

The launcher bound the local Ollama cloud manifest digest, remote host and model
name, capabilities, and Ollama version. Ollama does not expose an immutable
revision for the hosted weights, so the execution records
`remote_weights_pinned: false` rather than treating the manifest digest as a
remote weight hash.

An initial 16K diagnostic opened a pack that had already accumulated retrieval
receipts from a prior run. The strict curve comparator rejected its starting
artifact. That output was excluded, a pristine copy was verified against the
registered full-artifact and payload hashes, and all 149 answers were regenerated
for the reported 16K result.

## Claim boundary

This is a development budget curve on a previously scored, web-only cohort with
a shared haystack. It is evidence for PRME context-budget decisions. It is not a
fresh holdout, a full LongMemEval-V2 Small score, or a competitor comparison.
Question-level bootstrap intervals also condition on this selected cohort and do
not model dependence introduced by the shared haystack.

The aggregate-only machine-readable report is
[`longmemeval-v2-deepseek-budget-curve.json`](longmemeval-v2-deepseek-budget-curve.json).
