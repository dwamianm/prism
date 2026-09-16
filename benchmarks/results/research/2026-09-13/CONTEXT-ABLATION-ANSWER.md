# Gold-source context ablation

## Decision

The exact packed-context ablation is a useful causal diagnostic. Removing the
sole annotated source from 37 qualifying balanced contexts reduced the fixed
reader's judged accuracy from 29/37 to 7/37. Twenty-three answers changed from
correct to wrong. This confirms that balanced packing's retained evidence often
causes downstream answer success rather than only improving a source-recall
metric.

The result does not authorize automatic ranking or retention updates. It uses a
previously examined development cohort, application-verified source annotations,
one reader, and a custom judge. It removes one packed entry without filling the
freed budget; it is not a memory-bank deletion and re-retrieval experiment.

## Protocol

- Cohort: all 37 questions in the 119-question LongMemEval-derived development
  set with exactly one annotated source retained as content in the verified 4K
  balanced context.
- Intervention: the public `ablate_context` helper removes the one mapped source
  node. Every other entry, representation, section, prompt field, and context
  byte remains fixed.
- Baseline: the complete registered balanced answer study, imported only after
  checksum and identity verification.
- Counterfactual reader: `prme-qwen3.5:35b-a3b-8k`, digest
  `45870b70b6fa65ab09355aeb7901897763ae40745c6cdd11d28bc33a6b818ffc`.
- Judge: calibrated `gemma4:31b`, digest
  `6316f0629137b426c9d9b853ffc4c8209589f30ee39aebede6285096c0ff47e7`.
- Coverage: 37/37 counterfactual reader calls and 37/37 unique judge calls, with
  no provider failure or outcome retry in the accepted v2 run.

## Results

| Category | Questions | Baseline correct | Ablated correct | Correct→wrong |
|---|---:|---:|---:|---:|
| Multi-session | 2 | 1 | 1 | 1 |
| Single-session assistant | 8 | 8 | 3 | 5 |
| Single-session preference | 4 | 1 | 0 | 1 |
| Single-session user | 17 | 14 | 2 | 12 |
| Temporal reasoning | 6 | 5 | 1 | 4 |
| **Overall** | **37** | **29** | **7** | **23** |

Accuracy fell by 22 answers, from 78.38% to 18.92%, a 59.46 percentage-point
change. Of the 29 originally correct answers, 23 (79.31%) became wrong after the
annotated source was removed.

The complete transition table is:

| Transition | Count | Interpretation for this fixed prompt |
|---|---:|---|
| Correct→wrong | 23 | Annotated source was load-bearing |
| Correct→correct | 6 | Other context or prior knowledge remained sufficient |
| Wrong→correct | 1 | Removal appeared to cure the answer |
| Wrong→wrong | 7 | Removal did not cure an existing reader failure |

## Error audit

All 14 cases outside the load-bearing branch were inspected against the question,
reference, baseline answer, counterfactual answer, and judge reason.

The six non-flips have alternate support or preserve a valid abstention. Other
packed entries still state Luna's name, MusicTheory.net, the Data Science
certification, Pennsylvania's requirement, and Dr. Arati Prabhakar's role. The
Sacramento Airbnb abstention remains correct because the residual context still
only supports other cities. A non-flip therefore cannot be treated as negative
relevance.

The one wrong-to-correct case, `09ba9854_abs`, is an annotation/reference
inconsistency rather than demonstrated toxic memory. The removed annotated source
says the airport bus costs about $10–$20 and a taxi about $60. The baseline reader
uses those values to calculate a $40–$50 saving, while the reference asserts that
no bus price was mentioned. The registered result is preserved, but it should not
train a negative memory label.

The seven wrong-to-wrong cases were already reader failures. They include
excessive caution over an explicitly implied answer, failure to personalize a
recommendation, and incorrect temporal arithmetic. Their ablations do not show
that the sources lacked value.

The 23 load-bearing cases have no observed judge inconsistency: after removal,
the reader either abstains or gives an answer that omits the referenced fact.

## Reproducibility and failed run

The first registered run completed all reader and judge calls but exited before
summarization because it joined question IDs to the baseline's internal case IDs.
It is retained as a failed run and contributes no accepted score. The corrected
v2 run was freshly registered and regenerated every outcome. Despite separate
calls, v1 and v2 produced 37/37 byte-identical reader answer texts and 37/37
identical judge booleans.

- Failed v1 record:
  `context-ablation-answer-v1-failure.json`
- Accepted v2 registration SHA-256:
  `ecd94457c58d3f9ab436f10cbc72815ba69beafecc0d4e55bd2d48382e2cdac3`
- Prepared inputs SHA-256:
  `fb6324cee0164bafaa144eb97a9c745afb887199f029d71c15b3acba0dec2735`
- Reader report SHA-256:
  `5e85ba2ac558fc443322aeacda482e11fcc81f964f317b2933e2cbe01c6225f1`
- Reader state SHA-256:
  `a3eb388e0193d0c08f92e5248895981d9da0031c8ec060d18c5ecb1243d0f5d0`
- Judge report SHA-256:
  `cf6dacdaa751800388405f6de286d83d3dc2fb5f91b80d140f49f82846e6797a`
- Accepted result SHA-256:
  `7c34357360440b01465d84b51de9b992d25324183599196a52f3e3e70fb1d010`

Raw states and responses remain under
`data/benchmarks/context-ablation-answer-v2/`. The exact compact result is
[context-ablation-answer-v2-results.json](context-ablation-answer-v2-results.json).
