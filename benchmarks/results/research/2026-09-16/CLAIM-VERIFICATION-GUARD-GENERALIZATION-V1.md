# Claim verification guard generalization assay

The registered 44-case previously unexecuted assay matched **38/44** labels and
failed two of eight gates. It produced zero plain `supported` decisions on
unsafe cases, but three expected-insufficient negative distractors became
`refuted`, and a fourth became `contested`. The failed development result is
preserved before changing the implementation.

The
[registration](claim-verification-guard-generalization-v1-registration.json)
was fixed before any case was executed against an NLI model. It binds PRME
revision `984c87d`, exact runner and implementation hashes, all 44 typed cases,
the immutable `cross-encoder/nli-deberta-v3-base` revision, thresholds, policies,
group bounds, evidence order and machine gates. The
[machine-readable result](claim-verification-guard-generalization-v1-results.json)
retains every score, basis, limitation, identity, digest and verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe `supported` decisions | 0 | 0 | Pass |
| Expected-insufficient cases called `refuted` | 0 | 3 | **Fail** |
| Correct supported cases | >=11 | 12 | Pass |
| Correct two-passage minimal groups | >=3 | 6 | Pass |
| Correct explicit refutations | >=5 | 6 | Pass |
| Correct insufficient cases | >=14 | 12 | **Fail** |
| Correct contested cases | >=3 | 4 | Pass |
| Incomplete cases with no model call | >=4 | 4 | Pass |

Observed statuses were 12 `supported`, nine `refuted`, 14 `insufficient`, five
`contested`, and four `incomplete`.

## Safety failure

The default refutation guard accepted any explicit negation cue once the NLI
contradiction threshold was met. That signal was too broad:

- “Nadia did not attend the Atlas project review” refuted “Nadia leads Atlas.”
- “Priya did not review the mobile application” refuted “Priya owns it.”
- “The release team is not meeting on Friday” refuted “The release is Friday.”
- “The image API does not cache PNG files” produced a deterministic refutation
  alongside model support for “The image API serves PNG files,” yielding a
  false `contested` result.

The last case also shows that counting shared nouns is insufficient. Subject and
object overlap can be high while the predicates differ. Evidence-side negation
must require every non-generic claim token to occur in the negated clause, after
only narrow normalization. A model contradiction plus an unrelated `not` is not
an independent corroboration signal.

## Retained behavior and other misses

All ten completed-action safety cases remained `insufficient`; all six explicit
refutations, all six new two-passage chains in both caller orders, all four real
conflicts and all four completeness refusals were correct. This supports the
speech-act guard and caller-order fix on new wording, but the failed negation
gate blocks promotion.

Two intended modal-support cases were missed. A reported question was blocked
because `?` and “asked whether” were assigned different local modes; that is a
deterministic normalization gap. The typed-unverified paraphrase received no
model entailment and should remain a recorded recall miss rather than trigger a
threshold change.

## Decision and boundary

Replace shared-token negation matching with a claim-token subset rule and use
that same relation-alignment requirement before accepting evidence-side
negation as model-contradiction corroboration. Treat reported questions as
preserving question speech act. Re-register the unchanged cohort after the
causal fix; do not relabel the four distractors or lower thresholds.

All cases were new to model execution, but they were authored after the guard
designs were known. This is development generalization evidence from one pinned
model, not an independent external holdout. It does not measure retrieval,
long-context verification, calibration, domain transfer, answer quality or
comparative product leadership.
