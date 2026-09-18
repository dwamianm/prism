# LLM-AggreFact structured FactCG stack v1b

## Verdict

Rejected. The registered stage used five document-grouped outer folds. Within
each outer training partition, a four-fold grouped cross-fit had to find a
threshold with at least 90% precision and 60% recall before the held-out outer
fold could emit supported decisions. None of the five inner cross-fits found
such a threshold. Every outer fold therefore failed closed, and the runner had
no test-file argument.

The deterministic structure did not improve the underlying MIT-licensed FactCG
scorer. A post hoc common threshold over the 1,100 outer out-of-fold
probabilities reached only 81.13% precision at 60.18% recall. At 90% precision,
recall was 25.09%. The first point raises precision only 1.69 points over direct
FactCG's 79.44% at 61.82% recall, while the safety-constrained point reduces
recall from FactCG's 28.73%. The stack remains far from the fixed joint gate.

## Bound method

- Base score: the exact source-free development scores from
  `yaxili96/FactCG-DeBERTa-v3-Large`, an MIT-licensed model pinned by the FactCG
  v1 registration.
- Deterministic features: FactCG logit, minimum and mean claim-sentence lexical
  alignment, numeric-anchor coverage, capitalized-token coverage, aligned
  negation agreement, claim-sentence and content-token counts, and a fixed
  FactCG/alignment interaction.
- Classifier: standardized L2 logistic regression with fixed `C=0.1`,
  `scikit-learn==1.8.0`, and no class weighting or hyperparameter search.
- Leakage control: exact documents within a dataset share a SHA-256 group and
  cannot cross an outer or inner fold. Each outer threshold uses only inner
  out-of-fold predictions from its training partition.
- Cohort: the exact balanced 1,100-claim FactCG development identities. The five
  outer validation folds contained 202, 235, 222, 205 and 236 claims.
- Registration SHA-256:
  `7c6f461e126f8bcb0f8dc8200f0b151d36220ef3622dbb1f5076fc62cb4ff42a`.
- Result payload SHA-256:
  `824b4c9e6e646324c00c525b535fe75908c1ca25a111b94cc16d6d9db714d266`.
- Result file SHA-256:
  `68bcaa446f78f3376e42c611492e260ec2a616151546d070026715c108556e20`.

The source-free result contains identifiers, labels, scalar features,
probabilities, fold assignments, fitted coefficients, thresholds and metrics.
It contains no document, claim or prompt text. Its canonical payload and exact
registration bindings verify.

## Registered result

All five outer thresholds were unavailable. Following the fixed fail-closed
rule, the registered aggregate emitted zero supported decisions: 550 true
negatives, 550 false negatives, zero false positives and zero true positives.
Only the 1,100-case coverage and false-support gates passed; precision, recall,
balanced accuracy and threshold-availability gates failed.

The following common-threshold results are post hoc diagnostics over outer
out-of-fold probabilities. They did not participate in the registered decision.

| Selection | Threshold | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|---:|
| Maximum recall at precision ≥ 90% | 0.855645 | 90.79% | 25.09% | 61.27% | 2.55% |
| Maximum precision at recall ≥ 60% | 0.625136 | 81.13% | 60.18% | 73.09% | 14.00% |
| Maximum balanced accuracy | 0.498198 | 76.82% | 74.73% | 76.09% | 22.55% |
| Maximum F1 | 0.405913 | 72.86% | 82.00% | 75.73% | 30.55% |

## Predecessor failure

The first [v1 execution](LLM-AGGREFACT-STRUCTURED-STACK-V1-INCOMPLETE.md)
stopped before feature extraction because the runner supplied the descriptive
split literal `development` rather than the frozen selector key `dev`. The
identity hash caught the mismatch. V1b changed only that literal and retained
the registered features, folds, classifier and gates.

## Decision

Do not integrate or tune this stack and do not open the sealed LLM-AggreFact
test cohort. Simple lexical alignment, exact anchors and negation agreement do
not supply the missing semantic relation evidence. Further work should use a
new development source and represent claims and evidence as typed relations with
explicit argument, qualifier and temporal alignment before another sealed test
attempt. Reusing this development cohort for feature selection would no longer
provide an honest confirmation.
