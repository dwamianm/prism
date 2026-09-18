# Broad claim verification safety confirmation

The registered same-cohort confirmation passed **all eight targeted gates** and
matched **42/48** authored labels. It produced zero unsafe support decisions and
zero false refutations among expected-insufficient cases. It retained all 16
previously correct support decisions, all six explicit refutations, four
minimal groups, four conflicts, 12 insufficient cases and four model-free
completeness refusals required by the protocol.

The
[registration](claim-verification-broad-safety-v2-registration.json) was fixed
before execution. It binds PRME revision `32a4c2f`, exact runner and
implementation hashes, the 48 unchanged broad-v1 cases, the immutable
`cross-encoder/nli-deberta-v3-base` revision, both guarded decision policies,
thresholds, group bounds and all machine gates. The
[machine-readable result](claim-verification-broad-safety-v2-results.json)
retains every score, typed identity, limitation, decision basis, digest and gate
verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe `supported` decisions | 0 | 0 | Pass |
| Expected-insufficient cases called `refuted` | 0 | 0 | Pass |
| Correct supported cases | >=16 | 16 | Pass |
| Correct two-passage minimal groups | >=4 | 4 | Pass |
| Correct explicit refutations | >=6 | 6 | Pass |
| Correct insufficient cases | >=12 | 12 | Pass |
| Correct contested cases | >=4 | 4 | Pass |
| Incomplete cases with no model call | >=4 | 4 | Pass |

Observed statuses were 16 `supported`, six `refuted`, 18 `insufficient`, four
`contested`, and four `incomplete`.

## Causal result

All NLI group probabilities were byte-for-byte identical to broad v1. Exactly
two status decisions changed:

- “I want to enable passkeys after the security review” retained 0.9819 model
  entailment for “The user enabled passkeys,” but changed from `supported` to
  `insufficient` with `uncorroborated_model_entailment`.
- The ownership correction retained 0.0317 model contradiction, but “Ravi no
  longer owns ingestion” matched the near-exact proposition “Ravi owns the
  ingestion pipeline.” The result changed from `supported` to `contested`, and
  `refuting_basis` records `explicit_negation_overlap` rather than claiming a
  model contradiction.

No threshold, case, expected label or raw model output changed. Matching desire,
attempt and recommendation claims remained supported, which checks that the
entailment guard preserves speech acts instead of discarding them wholesale.

## Remaining misses

The six unchanged mismatches are deliberate evidence about capability limits:

- All four implicit contradictions remain `insufficient`. Their NLI
  contradiction probabilities are above 0.9995, but the implementation does
  not treat raw contradiction as proof without a typed or deterministic basis.
- Two of six new two-passage chains remain `insufficient`. One pair reaches only
  0.7428 entailment; the other is scored as contradiction and safely blocked.

This confirmation supports the two named safety mechanisms. It does not support
broad implicit-refutation recall or general relation composition.

## Evidence boundary

The complete broad-v1 result was known before this registration, and the two
guards were developed against its failures. Passing establishes a same-input
causal regression result, not held-out generalization. These are short authored
claims evaluated by one pinned local NLI model. The assay does not measure
retrieval recall, long contexts, answer decomposition, calibration, domain
transfer or comparative product quality.

Keep verification opt-in until a new untouched cohort measures speech-act
precision, typed epistemic handling, explicit-negation false positives, implicit
contradiction recall and multi-passage composition together.
