# Local minimal-evidence claim verification development assay

The first registered real-model assay matched **17/20** authored claim labels
and failed its all-cases and supported-coverage gates. It produced **zero unsafe
support decisions**, correctly found both previously untested two-passage
minimal evidence groups, found all four explicit refutations and both contested
claims, and returned both exhaustive-set claims as `incomplete` without a model
call.

The [registration](claim-verification-minimal-groups-v1-registration.json) was
fixed before the complete run. It binds PRME revision `6c2bf5d`, the runner and
implementation hashes, all 20 exact cases and typed passages, the immutable
`cross-encoder/nli-deberta-v3-base` revision, thresholds, group bounds, and six
machine-evaluated gates. The
[machine-readable result](claim-verification-minimal-groups-v1-results.json)
retains every evaluated group, probability, selected memory identity, digest,
and gate verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Every case has its expected status | true | false (17/20) | Fail |
| Unsafe `supported` decisions | 0 | 0 | Pass |
| Correct supported cases | >=8/8 | 7/8 | Fail |
| Correct refuted cases | >=4/4 | 4/4 | Pass |
| Correct contested cases | >=2/2 | 2/2 | Pass |
| Incomplete cases with no model call | >=2/2 | 2/2 | Pass |

Observed statuses were seven `supported`, six `refuted`, three `insufficient`,
two `contested`, and two `incomplete`.

## Minimal-group result

Neither passage independently established either two-hop claim. For the Aurora
launch claim, the two single passages had entailment probabilities of 0.0001 and
0.0005; their pair scored 0.9899 and was selected. For the Atlas ownership/port
claim, the singles scored 0.0002 and 0.0057; their pair scored 0.9914. These cases
were not executed before registration and provide direct development evidence
that bounded group search can recover compositional support that single-passage
NLI misses.

This does not establish general multi-hop verification. Both cases are short,
authored chains with two passages and one local model.

## Failure analysis

The support miss was conservative: “The team decided to use PostgreSQL” received
0.0304 entailment for “The team approved PostgreSQL.” The terms are close in
this authored context, but not logically identical in every setting; lowering a
global threshold to recover it would weaken the safety boundary.

The two other mismatches were unsafe *refutation semantics*, though neither
became unsafe support. “I want to deploy after the tests pass” received 0.9709
contradiction for “The user deployed the release.” An unfulfilled intention does
not prove non-deployment. A migration plan that merely names covered tables
received 0.9948 contradiction for “The user completed the migration,” even
though it is topically related and silent on completion. The model's
contradiction label cannot be accepted as refutation without an independent
polarity or incompatible-value signal.

## Decision

Keep claim verification opt-in and preserve the 0.80 support threshold. Add a
deterministic refutation corroboration rule before exposing `refuted`: require
explicit negation, correction/alternative language, or a concrete incompatible
value in the claim/evidence group. A high contradiction score without that
corroboration should remain `insufficient`. Re-register the changed
implementation rather than rewriting this result.

Do not tune “decided” versus “approved” on this examined case. Improve support
coverage only through a broader development set or a separately evaluated model,
then confirm on untouched claims.

## Scope

The cases are authored development probes. Four behaviors had smoke coverage
before registration; the two multi-passage cases did not. The assay does not
measure answer decomposition, retrieval recall, model calibration, long-context
claims, domain transfer, or comparative product quality.
