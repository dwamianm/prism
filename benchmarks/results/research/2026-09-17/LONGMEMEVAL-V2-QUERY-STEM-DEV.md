# LongMemEval-V2 multiple-choice query-stem trial

## Result

On the registered 149-question web development cohort, removing contiguous
lettered answer choices from PRME's retrieval query did not improve downstream
answer quality. The reader still received the original question and choices.

| Arm | Correct | Accuracy | Unknown | Mean packed tokens | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Verbatim | 79/149 | 53.02% | 35 | 43,195.28 | 669.84 s |
| Question stem v1 | 78/149 | 52.35% | 38 | 43,301.66 | 902.44 s |

The paired difference for question-stem retrieval was -1 question, or -0.67
percentage points. The question-bootstrap 95% interval was -4.70 to +3.36
points. The exact two-sided McNemar p-value was 1.0, with four wins, five losses,
and 140 ties.

The policy altered only 25 retrieval queries. On that causal subset, question
stem scored 11/25 versus 12/25 for verbatim, with zero wins, one loss, and 24
ties. Its paired interval was -12.00 to 0.00 points. On the 124 unchanged
queries, both arms scored 67/124, with four wins and four losses. That unchanged
subset demonstrates reader run-to-run variation rather than a policy effect.

| Subset | Verbatim | Question stem v1 | Wins | Losses | Ties |
| --- | ---: | ---: | ---: | ---: | ---: |
| Retrieval query changed | 12/25 | 11/25 | 0 | 1 | 24 |
| Retrieval query unchanged | 67/124 | 67/124 | 4 | 4 | 116 |
| All multiple choice | 18/35 | 17/35 | 0 | 1 | 34 |

Removing choices also did not reduce packed context: the packer filled the
available budget with other evidence, increasing the mean slightly. These data
do not support promoting `question_stem_v1`; `verbatim` remains the adapter
default.

## Registered protocol

- Cohort: all 149 web questions in the previously scored development slice.
- Memory payload: identical 1,768-file, 762,468,935-byte PRME pack in both arms.
- PRME budget: 32,768 `cl100k_base` tokens, auditable context, and up to eight
  source screenshots.
- Reader: `deepseek-v4.1-flash:cloud`, temperature 0, top-p 1, top-k 20,
  reasoning effort `none`, thinking disabled, one concurrent request.
- Source revisions: PRME `88e3f47ca1b8a087f647ba4a302781edadaa2d9e` and
  LongMemEval-V2 `2cc8c540bdb87fe6761629b585e727e1c4704520`.
- Registration SHA-256:
  `e230b9c481d71f3cf2d43d43773d6bc3145607b575baa365aa336800e6dfd99b`.
- Comparison SHA-256:
  `3c224f475f7e306a211683179f4b5f739c77631f00a5cbba0c56fa264e3ad7a7`.

The fail-closed comparator verified every result artifact, source and input
hash, saved-memory identity, selected configuration, reader runtime, and
per-question transformed query hash. The launcher bound the local Ollama cloud
manifest digest, remote host and model name, capabilities, and Ollama version.
Ollama does not expose an immutable revision for the hosted weights, so the
execution records `remote_weights_pinned: false`.

## Claim boundary

This is a negative development result on a previously scored, web-only cohort
with a shared haystack. It rejects promotion of this specific deterministic
query transform under the registered conditions. It does not establish that
all query rewriting, decomposition, or reader models are ineffective. Question
bootstrap intervals condition on this cohort and do not model dependence from
the shared haystack. Wall time is observational because the sequential cloud
runs experienced different service latency.

The aggregate-only machine-readable report is
[`longmemeval-v2-query-stem-dev.json`](longmemeval-v2-query-stem-dev.json).
