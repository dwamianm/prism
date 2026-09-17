# LLM-AggreFact HHEM windowed capacity trial v2

**Result: failed calibration; external test remained sealed.** Pinned
HHEM-2.1-Open did not reach the registered 90% supported precision and 60%
supported recall boundary on a fresh, balanced 780-claim development cohort.
Its best precision above the recall floor was 77.67%. The existing FactCG
ranker performed better on the same claims, so HHEM is not integrated.

## Registered protocol

- Dataset: LLM-AggreFact revision
  `981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4`
- Cohort: 390 supported and 390 unsupported claims, excluding all 310 identities
  observed by the preceding typed-reference program
- Evidence ranker: pinned `yaxili96/FactCG-DeBERTa-v3-Large`, selecting the top
  two 550-word-token chunks
- Verifier: `vectara/hallucination_evaluation_model` revision
  `8e4a2e6e96c708cc76c2344f7e4757df2515292c` (Apache-2.0), loaded through
  audited standard Transformers classes from pinned safetensors without remote
  code execution
- Foundation tokenizer/config: `google/flan-t5-base` revision
  `7bcac572ce56db69c1ea7c8af255c5d7c9672fc2` (Apache-2.0)
- Evidence protocol: normalize whitespace, preserve every selected evidence word
  in source order, greedily pack maximal contiguous windows of at most 512 prompt
  tokens, score every window, and take the maximum support probability with the
  lowest window index breaking ties
- Gate: at least 90% supported precision, 60% supported recall, 75% balanced
  accuracy, and at most 10% false-support rate on all 780 claims

The first registration joined both chunks into one prompt with a 4,096-token
non-truncating limit. It failed closed before model inference when one prompt
reached 4,822 tokens. A private input-only diagnostic found that 452/780 joined
prompts exceeded the model tokenizer's 512-token limit. The replacement runner
proved that 1,512 windows reconstructed every selected evidence word exactly
once and never exceeded 512 tokens. The failed registration remains immutable;
v2 binds its hash and records that zero selected HHEM predictions were observed.

## Development result

No threshold met the precision and recall calibration requirements.

| HHEM operating point | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|
| Published threshold 0.5 | 69.50% | 75.38% | 71.15% | 33.08% |
| Maximum balanced accuracy | 75.00% | 68.46% | 72.82% | 22.82% |
| Best precision with recall at least 60% | 77.67% | 61.54% | 71.92% | 17.69% |
| Best recall with precision at least 90% | 100.00% | 0.26% | 50.13% | 0.00% |

At threshold 0.5, HHEM produced 294 true positives, 129 false positives, 261
true negatives and 96 false negatives. Requiring 90% precision retained only one
supported claim.

The result retains each selected FactCG chunk score, so its direct maximum-chunk
control is reproducible without another model run. This post hoc comparator
reached 74.87% maximum balanced accuracy. Above the same recall floor it reached
79.46% precision at 60.51% recall; above the precision floor it reached 90.91%
precision at 17.95% recall. HHEM did not improve the exact-cohort frontier.

FactCG evidence ranking scored 1,395 chunk/claim pairs in 240.09 seconds on MPS.
HHEM scored 1,512 bounded windows in 41.56 seconds. This isolates evidence
ranking as the dominant runtime cost for this cascade.

## Decision

Reject HHEM-2.1-Open with maximum-over-window aggregation as PRME's claim
verifier. The model increased false support relative to the same-cohort FactCG
control and still missed the joint safety/utility target. Keep claim verification
opt-in and leave the external test cohort unopened.

The next experiment should not add another static maximum-score cascade to this
exposed cohort. A new verifier direction needs a fresh registered development
cohort, explicit compound-claim handling, and a decision rule that can abstain
when only part of a claim is supported. Provider inference should use the fast
DeepSeek cloud route for development throughput while a pinned local path
remains the reproducibility control.

## Artifact integrity

- Failed joined-evidence registration SHA-256:
  `1a3a563db1382b76742baa3f15613fcb7d942e5fc7bb296883caeb4209fbc172`
- Windowed registration SHA-256:
  `240affb8e0bb684f863b64575a97a9117b4a4b0012428aba02eccb8b957a45c7`
- Result file SHA-256:
  `67ae7ddaa09a9bac9e92828f234b4390e80d2affb59f209c6d4a89651bb92e4f`
- Canonical result SHA-256:
  `447d2ae75e33e367bc0dae63052d31d816e65465861ec4f939b07409667fb580`

The source-free result is
[`llm-aggrefact-hhem-windowed-v2-result.json`](llm-aggrefact-hhem-windowed-v2-result.json).
It contains all 780 identifiers, labels, scores, window/chunk counts, metrics,
runtimes and model hashes. It states `development_only: true` and
`test_accessed: false` and contains no document, claim, evidence or provider text.
