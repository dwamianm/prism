# Balanced Packing Answer Confirmation

## Decision

Retain `balanced` as the default multi-path packing policy. On the 381-question
answer confirmation partition, the fixed 35B reader answered 250 questions
correctly with balanced contexts and 185 with density contexts. Balanced won 88
paired questions, lost 23, and tied 270. Every category improved or tied.

This is a confirmation on questions outside the 119-question development cohort,
but it is not an independent benchmark holdout. The same 381-question partition
had already been inspected for source retention. Its answer outcomes were
registered before generation and were not used to choose or tune the policy.

## Protocol

- Inputs: all 381 questions in the frozen LongMemEval-derived PRME regression
  partition.
- Arms: the saved 4K density context and the byte-identical current balanced
  context (`head1_quarter:4096`).
- Reader: `prme-qwen3.5:35b-a3b-8k`, digest
  `45870b70b6fa65ab09355aeb7901897763ae40745c6cdd11d28bc33a6b818ffc`.
- Judge: calibrated `gemma4:31b`, digest
  `6316f0629137b426c9d9b853ffc4c8209589f30ee39aebede6285096c0ff47e7`.
- Reader controls: temperature 0, seed 42, 8,192-token context, 1,024-token
  generation ceiling, and direct answers limited to two sentences and 80 words.
- Coverage: 762 valid reader calls and 762 judged outcomes. The judge made 731
  unique calls because exactly identical question/reference/answer inputs reuse
  one deterministic evaluation.
- Selection: every case, arm, category, and outcome is retained. References were
  hidden from the reader. There were no failed calls or retries.
- Integrity: before generation, the harness verified the source artifacts and all
  381 candidate snapshots, then reproduced both saved context arms byte for byte.

## Results

| Category | Questions | Density | Balanced | Balanced wins | Balanced losses |
|---|---:|---:|---:|---:|---:|
| Abstention | 22 | 21 | 21 | 0 | 0 |
| Knowledge update | 52 | 38 | 43 | 9 | 4 |
| Multi-session | 91 | 28 | 50 | 25 | 3 |
| Single-session assistant | 47 | 10 | 39 | 31 | 2 |
| Single-session preference | 23 | 5 | 9 | 5 | 1 |
| Single-session user | 47 | 39 | 40 | 2 | 1 |
| Temporal reasoning | 99 | 44 | 48 | 16 | 12 |
| **Overall** | **381** | **185** | **250** | **88** | **23** |

Balanced improves judged answer accuracy by 65 questions and 17.06 percentage
points, from 48.56% to 65.62%. A descriptive two-sided exact binomial test over
the 111 discordant pairs gives `p = 3.80e-10`. This statistic was calculated
after the registered run and does not turn the previously inspected partition
into an independent holdout.

Across the separate 119-question development answer trial and this 381-question
confirmation, the fixed reader scored 333/500 with balanced contexts and 252/500
with density contexts. The combined count is descriptive because neither source
partition is an independent competitive benchmark.

The direction agrees with the source-retention study on these same 381 questions:
balanced retained 90.55% versus density at 65.04% of labelled evidence. The
answer result also confirms that the source gain is useful downstream rather than
only improving an intermediate metric.

## Error audit

All 23 recorded balanced losses were inspected against the question, reference,
both answers, both judgments, and both packed contexts. Only `1568498a` is a
clear packing omission: density includes the chess move `28. Kg3`, while balanced
does not. In the other 22 cases, the balanced context contains the central source
facts. Most remaining losses are reader failures in arithmetic, date anchoring,
resolvable updates, or excessive caution after balanced supplies additional
evidence.

Six losses are clear binary-judge inconsistencies and remain unchanged in the
primary result:

| Case | Audit finding |
|---|---|
| `0ddfec37` | Both answers identify 15 baseballs and then express similar caution; density is accepted and balanced rejected. |
| `1903aded` | Both arms abstain because neither context contains the referenced seventh job; density is accepted against the specific reference and balanced rejected. |
| `b29f3365` | Both answers state the same six-weeks and two-weeks components without returning four weeks; only density is accepted. |
| `gpt4_1916e0ea` | Both answers abstain while stating the same January 5 and February 28 dates; only density is accepted. |
| `gpt4_7bc6cf22` | Balanced states the exact accepted answer, 12 days, but is rejected. |
| `gpt4_d84a3211` | Density says the spending evidence is absent and gives no total, yet is accepted against the `$185` reference. |

The preference loss `54026fce` is borderline: balanced uses the user's virtual
coffee-break history and suggests a collaborative framing, while the judge calls
it generic. The registered model-judge outcomes remain the primary results; the
audit does not relabel them.

The 17 non-artifact losses show where future work belongs. Temporal questions
account for most of them, even though balanced still wins that category 16 to 12.
The packer should not be tuned to these inspected outcomes. Future improvements
should be registered on different questions and should test temporal calculation
and conflict presentation separately from source selection.

## Artifact identity

- Registration SHA-256: `f0a0e6f793d1c57d1bf86e11565ed7cd32e833083ff4e49173a12ca9f5ad95fb`
- Prepared inputs: `fb56861559eb4969c4d6eecfeecce0aae34d0210a6ef5faa38fc55d2d8337384`
- Reader report: `eac18084e68dbfcafc169f2e4a29f590e6210e34e5ed325033402acb53485918`
- Reader state: `82ec7508ceeb05f251d6f75fdb46607e2db55b9c8f7f079305861e40389c2660`
- Judge inputs: `1de3ff2c6bce11674336491723117c1d258783cd4ef232bc2a8b21cddc31cf6b`
- Judge mapping: `8136d6858ec8a9f79be3b58675496d5bfc1bbd73c49923af90cbe40a7c771ede`
- Judge report: `a1fa25bc1685e5106572629308e34393130c8355261cbe7dbc8a392f98f0d9e4`
- Judge state: `350a75b98e311a343020743ca0dd2b6b28aeabd3c7a64eb7ce2a0bd0c77d51d0`
- Results: `bdbe71cc5d56c79e623e94b2008bab5bf5d09e1d8faa36f9dee84e85dd0906de`

Raw reader and judge artifacts remain under
`data/benchmarks/balanced-qwen35b-regression-v1/`. The exact compact result is
[balanced-qwen35b-regression-results.json](balanced-qwen35b-regression-results.json).
