# Guarded-refutation claim verification confirmation

The registered same-cohort confirmation passed **all eight targeted gates** and
matched **19/20** authored claim labels. It produced zero unsafe support
decisions and zero false refutations among the four expected-insufficient cases.
It retained all four explicit refutations, both contested claims, both unseen
two-passage minimal groups, and both model-free completeness refusals.

The
[registration](claim-verification-guarded-refutation-v2-registration.json) was
fixed before execution. It binds PRME revision `3f9540e`, exact runner and
implementation hashes, the 20 unchanged v1 cases, the immutable
`cross-encoder/nli-deberta-v3-base` revision, explicit corroboration policy,
thresholds, group bounds, and all machine gates. The
[machine-readable result](claim-verification-guarded-refutation-v2-results.json)
retains every evaluated probability, evidence identity, limitation, digest and
gate verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe `supported` decisions | 0 | 0 | Pass |
| Expected-insufficient cases called `refuted` | 0 | 0 | Pass |
| Correct supported cases | >=7 | 7 | Pass |
| Correct two-passage minimal groups | >=2 | 2 | Pass |
| Correct explicit refutations | >=4 | 4 | Pass |
| Correct insufficient cases | >=4 | 4 | Pass |
| Correct contested cases | >=2 | 2 | Pass |
| Incomplete cases with no model call | >=2 | 2 | Pass |

Observed statuses were seven `supported`, four `refuted`, five `insufficient`,
two `contested`, and two `incomplete`.

## Causal result

The model probabilities were byte-for-byte identical to v1 for all 20 cases.
Only the two targeted decisions changed:

- “I want to deploy after the tests pass” retained its 0.9709 contradiction
  score but changed from `refuted` to `insufficient` with
  `uncorroborated_model_contradiction`.
- A migration plan that did not state completion retained its 0.9948
  contradiction score and received the same guarded result.

The four passages with explicit negation, correction language or incompatible
values remained `refuted`. Both supporting-plus-refuting evidence sets remained
`contested`. This confirms that the deterministic guard changed decision
semantics without hiding the local model's raw output.

## Remaining miss

The only mismatch is unchanged from v1: the model scores “The team decided to
use PostgreSQL” at 0.0304 entailment for “The team approved PostgreSQL.” The
0.80 support threshold remains unchanged. The registration deliberately
required seven supported cases rather than tuning a general threshold or adding
an equivalence rule after observing this example.

## Evidence boundary

This is a regression confirmation on the fully observed v1 development cohort.
The guard was designed after the two v1 false refutations were known, and its
unit behavior was tested before registration. Passing establishes that the
implementation fixes that named failure mode and retains the registered v1
capabilities on these cases. It does not measure held-out accuracy, calibration,
implicit-contradiction recall, long-context verification, answer quality,
domain transfer or comparative product leadership.

The next verification study should freeze a broader untouched claim set with
more numerical relations, implicit contradictions, temporal scope, negated
claims, distractors and three-passage chains. Refutation should remain opt-in
until that study measures the precision/recall cost of the conservative guard.
