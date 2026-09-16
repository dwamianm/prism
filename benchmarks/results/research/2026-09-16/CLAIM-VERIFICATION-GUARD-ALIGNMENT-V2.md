# Claim verification relation-alignment confirmation

The registered same-cohort confirmation passed **all eight targeted gates** and
matched **43/44** authored labels. It produced zero unsafe supports, zero false
refutations among expected-insufficient cases, correct decisions for all 16
insufficient cases, all six explicit refutations, all six two-passage chains,
all four conflicts and all four model-free completeness boundaries.

The
[registration](claim-verification-guard-alignment-v2-registration.json) was
fixed before execution. It binds PRME revision `3445f6f`, exact runner and
implementation hashes, the unchanged 44-case cohort, the immutable
`cross-encoder/nli-deberta-v3-base` revision, guarded policies, thresholds,
group bounds, evidence order and all machine gates. The
[machine-readable result](claim-verification-guard-alignment-v2-results.json)
retains every probability, decision basis, limitation, identity, digest and
verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe `supported` decisions | 0 | 0 | Pass |
| Expected-insufficient cases called `refuted` | 0 | 0 | Pass |
| Correct supported cases | >=13 | 13 | Pass |
| Correct two-passage minimal groups | >=6 | 6 | Pass |
| Correct explicit refutations | >=6 | 6 | Pass |
| Correct insufficient cases | >=16 | 16 | Pass |
| Correct contested cases | >=4 | 4 | Pass |
| Incomplete cases with no model call | >=4 | 4 | Pass |

Observed statuses were 13 `supported`, six `refuted`, 17 `insufficient`, four
`contested`, and four `incomplete`.

## Causal result

Every NLI probability was byte-for-byte identical to the failed v1 run. Exactly
five statuses changed:

- The reported question changed from guarded `insufficient` to `supported`
  after “asked whether” and a direct `?` were mapped to the same question mode.
- The attendance, review and meeting distractors changed from `refuted` to
  `insufficient`; their high raw contradiction scores remain visible with
  `uncorroborated_model_contradiction`.
- The cache distractor changed from false `contested` to `insufficient`; its
  0.8805 raw entailment remains visible with
  `uncorroborated_model_entailment`.

All six true explicit refutations remained correct. The distinguishing rule is
relation alignment: every normalized non-generic claim token must occur in the
negated clause, and a two-token proposition requires the exact token set.
Evidence-side negation that does not meet that rule cannot corroborate a model
contradiction or support a positive claim.

## Remaining miss

The only mismatch is the predeclared typed-unverified paraphrase. The model
assigned 0.0008 entailment and 0.9715 neutral to the qualified claim. The
epistemic guard correctly permits qualified unverified evidence, but it cannot
turn a neutral model score into support. No threshold or equivalence rule was
changed for this one observed example.

## Evidence boundary

This confirmation reused the fully observed failed cohort, and the changes were
designed against its five actionable decisions. Passing is causal regression
evidence, not untouched generalization. The preceding v1 cases were new to model
execution and still document the failure that prompted this fix. Both runs use
short authored claims and one pinned local NLI model; they do not establish
calibration, long-context verification, retrieval quality, domain transfer,
answer quality or product leadership.
