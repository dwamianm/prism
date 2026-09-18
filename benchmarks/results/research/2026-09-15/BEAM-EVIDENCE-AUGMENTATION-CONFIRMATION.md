# BEAM evidence augmentation untouched confirmation

Unconditional top-10 evidence augmentation did not generalize to the separately
registered conversation. The fresh baseline scored **15/20** with a **0.64125**
mean rubric score. Augmentation scored **14/20** with a **0.60750** mean. The
paired result was zero wins, one loss, nineteen ties, and a **-0.03375** mean
delta. This fails the registered confirmation rule and blocks a default change.

The [master registration](beam-100k-evidence-augmentation-confirmation-v1-registration.json)
fixed conversation 1, both policies, model identities, and the decision rule
before any conversation-1 content was inspected or ingested. Fail-closed
validation then confirmed 200/200 durable events, raw materializations, and
extractions before the candidate arm ran. The
[candidate registration](beam-100k-evidence-augmentation-confirmation-v1-candidate-registration.json)
hash-bound that validated pack and baseline without changing the preregistered
policy. The [machine-readable result](beam-100k-evidence-augmentation-confirmation-v1-results.json)
binds the aggregate outcome and source artifacts.

## Results

| Ability | Baseline pass | Augmented pass | Baseline score | Augmented score |
| --- | ---: | ---: | ---: | ---: |
| Abstention | 0/2 | 0/2 | 0.00000 | 0.00000 |
| Contradiction resolution | 2/2 | 1/2 | 0.50000 | 0.37500 |
| Event ordering | 2/2 | 2/2 | 0.85000 | 0.70000 |
| Information extraction | 2/2 | 2/2 | 1.00000 | 1.00000 |
| Instruction following | 2/2 | 2/2 | 1.00000 | 1.00000 |
| Knowledge update | 1/2 | 1/2 | 0.50000 | 0.50000 |
| Multi-session reasoning | 1/2 | 1/2 | 0.50000 | 0.50000 |
| Preference following | 2/2 | 2/2 | 1.00000 | 1.00000 |
| Summarization | 2/2 | 2/2 | 0.56250 | 0.50000 |
| Temporal reasoning | 1/2 | 1/2 | 0.50000 | 0.50000 |
| **Overall** | **15/20** | **14/20** | **0.64125** | **0.60750** |

## Failure analysis

The pass-level loss asked whether the user had obtained an API key. Both arms
ranked the explicit statement that no key had ever been obtained first. The
augmented arm also inserted raw setup and implementation passages that mentioned
an environment variable, placeholder keys, and domain restriction. The reader
treated those implementation details as proof of ownership, asserted that a key
existed, and omitted the explicit opposing claim. The baseline also failed to
state that the evidence conflicted, but retained enough of the negative claim to
pass two of four rubric nuggets.

This is a policy failure rather than missing source recall. Direct evidence helps
some chronological and omitted-detail questions, but unconditional augmentation
can amplify text that is lexically relevant without entailing the queried state.
The next candidate should use query intent and structured epistemic state to
admit sources selectively, especially around contradictions and current claims.

## Scope

This is one untouched 100K conversation, twenty questions, and mutable hosted
model aliases. It is strong enough to reject unconditional top-10 augmentation
as the default under the registered rule. It does not establish universal
behavior or compare PRME with another product.
