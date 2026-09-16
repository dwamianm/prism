# Balanced packing on the assistant-memory failure cohort

The registered focused trial completed all 27 reader generations and all 27
judge calls without a provider failure. It used the installed
`prme-qwen3.5:35b-a3b-8k` reader at an 8K context limit and the previously
calibrated `gemma4:31b` judge. Generation received only the question, question
date and exact 4K context. References entered after every reader answer was
saved. Density, balanced and empty arms used the same nine questions.

| Arm | Judge-correct answers |
|---|---:|
| Empty memory | 1/9 |
| Default density | 2/9 |
| Balanced | 8/9 |

The registered comparison reports seven balanced wins, one loss and one tie
against density. The underlying verified source study retained zero of nine
labelled assistant sources with density and eight of nine with the current
balanced implementation. All nine density and balanced contexts were reproduced
byte-for-byte from the current product packer before model calls.

## Judgment disagreement

The sole reported balanced loss is not persuasive evidence of a packing
regression. For `case-0066`, the reference contains a chord progression. Both
the empty and density readers answered that they did not know, while balanced
answered that the context contained only one of the two requested songs and did
not support the second progression. The judge marked both unsupported answers
correct and the more specific balanced abstention incorrect. This conflicts with
the registered non-abstention rubric, which requires the requested answer.

A deterministic reading therefore gives density 1/9, balanced 8/9 and empty
0/9, with no balanced loss. This is a post-outcome audit and does not replace the
registered model-judge result. Both interpretations are retained.

## Decision boundary

Balanced packing directly addresses the observed mechanism: density penalizes
long assistant answers enough to place top-scored evidence behind short records.
This result supports recommending the existing opt-in policy for raw chat
histories. It does not justify changing the global default yet. The nine cases
were selected after the failure was known, use one reader, and come from an
already examined development cohort. Run the same fixed comparison across all
categories and then on an independent holdout before default promotion.

Artifacts: [registration](assistant-balanced-qwen35b-registration.json) and
[judge result](assistant-balanced-qwen35b-results.json). Raw reader and judge
states remain in `data/benchmarks/assistant-balanced-qwen35b-v1`; their hashes
are bound by the result artifact.
