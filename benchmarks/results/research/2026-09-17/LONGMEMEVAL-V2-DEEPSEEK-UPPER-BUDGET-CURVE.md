# LongMemEval-V2 DeepSeek upper context-budget curve

## Result

On the registered 149-question web development cohort, increasing PRME's
internal context budget from 32,768 to 49,152 `cl100k_base` tokens improved the
DeepSeek v4.1 Flash reader from 80/149 to 89/149 correct.

| Arm | Correct | Accuracy | Unknown | Mean packed tokens | Total reader tokens | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 32K | 80/149 | 53.69% | 34 | 43,195.28 | 5,751,481 | 721.59 s |
| 40K | 82/149 | 55.03% | 36 | 52,396.22 | 7,026,327 | 817.00 s |
| 48K | 89/149 | 59.73% | 34 | 61,593.97 | 8,303,250 | 1,091.05 s |

The 48K arm gained nine questions over 32K, or 6.04 percentage points. The
question-bootstrap 95% interval was +0.67 to +11.41 points. The exact two-sided
McNemar p-value was 0.0490, with 13 wins, 4 losses, and 132 ties.

| Comparison | Delta | Paired 95% interval | Wins | Losses | McNemar p |
| --- | ---: | ---: | ---: | ---: | ---: |
| 40K - 32K | +1.34 points | -2.68 to +5.37 | 6 | 4 | 0.7539 |
| 48K - 32K | +6.04 points | +0.67 to +11.41 | 13 | 4 | 0.0490 |
| 48K - 40K | +4.70 points | 0.00 to +9.40 | 10 | 3 | 0.0923 |

The largest 48K-versus-32K gain was on procedure questions: 32/41 versus
25/41, a +17.07-point paired difference with no losses. Dynamic questions
improved from 20/49 to 21/49, while static questions improved from 35/59 to
36/59. The 48K arm used 44.37% more total reader tokens and 42.59% more packed
memory tokens than 32K.

This result supports a 48K high-quality preset for downstream models with enough
context, especially for procedure-heavy workloads. It does not by itself justify
making 48K the universal default: the cohort was already observed, the gain over
40K has an interval touching zero, and the token cost is material. A fresh
holdout should evaluate a fixed budget rule or a deterministic procedure-aware
budget policy before general promotion.

## Registered protocol

- Cohort: all 149 web questions in the previously scored development slice.
- Memory payload: identical 1,768-file, 762,468,935-byte PRME pack in every arm.
- Reader: `deepseek-v4.1-flash:cloud`, temperature 0, top-p 1, top-k 20,
  reasoning effort `none`, thinking disabled, one concurrent request.
- Context format: auditable, with up to eight source screenshots.
- Source revisions: PRME `022c7c21f84702d21113d121a7a307446959781c` and
  LongMemEval-V2 `2cc8c540bdb87fe6761629b585e727e1c4704520`.
- Registration SHA-256:
  `c07ce555d65da6d7e3597c29b9374fc4c3e23190eec5202c27ca5dd0f84cfc38`.
- Comparison SHA-256:
  `cae924741eb598b8bd5d5587789b40094662716f983130a86f3ce0c4ac08bee4`.

The launcher bound the local Ollama cloud manifest digest, remote host and model
name, capabilities, and Ollama version. Ollama does not expose an immutable
revision for the hosted weights, so the execution records
`remote_weights_pinned: false` rather than treating the manifest digest as a
remote weight hash.

## Claim boundary

This is a development budget curve on a previously scored, web-only cohort with
a shared haystack. It is evidence for PRME context-budget decisions. It is not a
fresh holdout, a full LongMemEval-V2 Small score, or a competitor comparison.
Question-level bootstrap intervals also condition on this selected cohort and do
not model dependence introduced by the shared haystack.

The aggregate-only machine-readable report is
[`longmemeval-v2-deepseek-upper-budget-curve.json`](longmemeval-v2-deepseek-upper-budget-curve.json).
