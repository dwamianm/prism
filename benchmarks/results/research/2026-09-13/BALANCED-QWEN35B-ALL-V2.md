# Balanced Packing Full-Cohort Answer Trial

## Decision

Promote `balanced` to the default multi-path packing policy. On the complete 119-question development cohort, the fixed 35B reader answered 83 questions correctly with balanced contexts and 67 with density contexts. Balanced won 26 paired questions, lost 10, and tied 83. No category had a lower total score under balanced.

This is evidence for the product default, not a competitive leadership claim. The cohort and source-retention results had already been examined, one local reader and one custom calibrated local judge were used, and there is no independent answer holdout.

## Protocol

- Inputs: all 119 questions in the fixed LongMemEval-derived PRME development cohort.
- Arms: saved 4K density context, byte-identical current balanced context, and empty context.
- Reader: `prme-qwen3.5:35b-a3b-8k`, digest `45870b70b6fa65ab09355aeb7901897763ae40745c6cdd11d28bc33a6b818ffc`.
- Judge: calibrated `gemma4:31b`, digest `6316f0629137b426c9d9b853ffc4c8209589f30ee39aebede6285096c0ff47e7`.
- Reader controls: temperature 0, seed 42, 8,192-token context, 1,024-token generation ceiling, direct answers limited by prompt to two sentences and 80 words.
- Coverage: 357 valid reader calls and 357 judged outcomes. The judge made 347 unique calls because exactly identical question/reference/answer inputs reuse one deterministic evaluation.
- Selection: every case, arm, and category is retained. References were hidden from the reader.

The initial preregistered v1 run stopped after 173 reader calls when one response reached its 512-token ceiling. Its failure is retained in [BALANCED-QWEN35B-ALL-V1-FAILURE.md](BALANCED-QWEN35B-ALL-V1-FAILURE.md). V2 was separately registered after adding an arm-neutral short-answer instruction and increasing the ceiling. The failed v1 outcomes were not resumed or scored.

## Results

| Category | Questions | Density | Balanced | Empty | Balanced wins | Balanced losses |
|---|---:|---:|---:|---:|---:|---:|
| Abstention | 8 | 7 | 7 | 7 | 1 | 1 |
| Knowledge update | 20 | 13 | 14 | 1 | 2 | 1 |
| Multi-session | 30 | 12 | 18 | 0 | 7 | 1 |
| Single-session assistant | 9 | 2 | 8 | 1 | 7 | 1 |
| Single-session preference | 7 | 2 | 2 | 0 | 0 | 0 |
| Single-session user | 17 | 14 | 14 | 3 | 1 | 1 |
| Temporal reasoning | 28 | 17 | 20 | 0 | 8 | 5 |
| **Overall** | **119** | **67** | **83** | **12** | **26** | **10** |

Balanced improves answer accuracy by 16 questions and 13.4 percentage points over density. A descriptive two-sided exact binomial test over the 36 discordant density/balanced pairs gives `p = 0.0113`; this statistic was calculated after the registered run and does not turn the examined cohort into a holdout.

The direction agrees with the separately measured context evidence. At 4K, balanced retained 95.91% versus 74.85% of labelled source evidence on these 119 questions. On the frozen 381-question regression partition, balanced retained 90.55% versus 65.04%, with two question-level losses and no negative category mean.

## Error audit

All ten model-judge losses were inspected. Most are real reader errors after the balanced context includes a different mix of evidence: missed arithmetic or chronology, excessive caution around resolvable updates, and one unsupported bus-fare inference. Temporal reasoning has the largest tradeoff, with eight wins and five losses.

The judge also has visible binary-scoring artifacts. For `case-0066`, density and empty both answer “I don't know” despite a non-abstention reference, but the judge marks both correct; balanced is also incorrect, so this recorded loss should be read as a tie. There are similarly debatable extra-detail and insufficient-information decisions among both wins and losses. The registered model-judge result remains the primary result; the audit does not rewrite outcomes.

## Artifact identity

- Registration SHA-256: `2478f4d747073b8747cc1b1414239cf81a1c0f93c2abf4fb7b147e95924c6001`
- Prepared inputs: `a7f63f25c44fe583b74205ab66649d3c5777ec62ef2cfbd99f5e2639edbd741e`
- Reader report: `3a5edfc0c3025f1717bcc4017a2087e5429d9e9d26ebc466be73e1afa697a9ca`
- Reader state: `339a6a3cdaebdbfa5cb99ce3cb9ef206edc2d0e66f835a90fa46fa605f54c6b3`
- Judge inputs: `62743713b035c15249fa61310f3d8c7329d62186f0e0bdb8d982bb2091a243f8`
- Judge mapping: `df5a016bd838c6bbb395cb2808396a68b494e3d3de0d01df0c874f059c74cfce`
- Judge report: `cb416e9e5e50047d0f66b95cac5364f0727ab6ebbbe53a87f4be9a289be01941`
- Judge state: `0ea057508f295fb8978ee300734a30c2756b155b6fbd02f7af52e84e3ffdd498`
- Results: `e092b672f83cb9fee18d2391a7c43389439b92f9412eda58a8b8d17c823c613c`

Raw reader and judge artifacts remain under `data/benchmarks/balanced-qwen35b-all-v2/`. The compact registered result is [balanced-qwen35b-all-v2-results.json](balanced-qwen35b-all-v2-results.json).
