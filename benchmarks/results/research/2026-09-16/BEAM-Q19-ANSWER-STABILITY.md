# BEAM q19 answer stability diagnostic

The apparent temporal-reasoning loss in the non-entity augmentation diagnostic
was not repeatable. With both top-50 retrieval lists frozen, the baseline passed
3/5 repeats and the candidate passed 4/5. Both arms had a mean rubric score of
**0.40**. The exact baseline context ranged from 0.0 to 1.0, so the earlier
single 0.5-to-0.0 transition cannot support a causal retrieval-regression claim.

The [registration](beam-100k-q19-answer-stability-v1-registration.json) fixed
the two question artifacts, source revisions, dataset, model manifests, five
repeats, and alternating arm order before these answer and judge calls. The
[machine-readable result](beam-100k-q19-answer-stability-v1-results.json)
retains every generated answer and nugget verdict. The runner made no retrieval
calls.

## Results

| Frozen context | Scores | Passes | Mean | Range |
| --- | --- | ---: | ---: | ---: |
| Baseline | 1.0, 0.0, 0.5, 0.5, 0.0 | 3/5 | 0.40 | 1.00 |
| Candidate | 0.5, 0.5, 0.5, 0.5, 0.0 | 4/5 | 0.40 | 0.50 |

The registered regression rule required at least four baseline passes and at
most one candidate pass. The result met neither condition. It also met both
registered rejection conditions: the candidate passed more repeats, and each
arm contained both pass and fail outcomes.

## What varied

The answerer alternated between treating March 29 and the later March 31 target
as the end of sprint 1. It also repeatedly named March 29 and April 19 while
claiming their difference was 19 days. The judge followed the two-item rubric:
those answers received 0 for the required 21-day count and 1 for naming the
required date interval, producing a 0.5 question score. Selecting March 31 lost
both rubric items and produced 0.0. One baseline repeat selected March 29,
calculated 21 days, and scored 1.0.

This variation occurred while retrieval was byte-for-byte fixed within each
arm. Mutable hosted model execution therefore dominates any defensible
interpretation of this one transition.

## Decision

The prior two-conversation diagnostic remains a failed promotion trial: its
registered combined mean delta was negative, and its other single-call changes
have not received repeatability trials. The `non_entity` safety control remains
opt-in. This result narrows the reason: q19 is no longer evidence that the
policy caused a pass-level loss.

Future model-judged retrieval comparisons should repeat changed questions from
both arms contemporaneously or use a sufficiently deterministic local reader.
Single hosted-model transitions near the pass threshold are diagnostic leads,
not stable product evidence.

## Scope

This is a post-hoc stability diagnostic for one examined question. The local
Ollama manifests were pinned, but their cloud aliases do not expose immutable
remote weights. The result measures answerer and judge repeatability; it does
not confirm retrieval quality or generalization.
