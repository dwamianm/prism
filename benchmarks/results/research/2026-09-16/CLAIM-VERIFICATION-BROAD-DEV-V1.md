# Broad guarded claim verification development assay

The registered 48-case assay matched **40/48** authored labels and failed its
overall safety gate. Seven of eight registered gates passed, but the verifier
produced **two unsafe `supported` decisions** where the protocol required zero.
The result is preserved as a failed development assay.

The
[registration](claim-verification-broad-dev-v1-registration.json) was fixed
before any case was executed against an NLI model. It binds PRME revision
`d695ba7`, exact runner and implementation hashes, all typed passages, the
immutable `cross-encoder/nli-deberta-v3-base` revision, the guarded refutation
policy, thresholds, group bounds and machine gates. The
[machine-readable result](claim-verification-broad-dev-v1-results.json) retains
every score, selected identity, limitation, digest and verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe `supported` decisions | 0 | 2 | **Fail** |
| Expected-insufficient cases called `refuted` | 0 | 0 | Pass |
| Correct supported cases | >=14 | 16 | Pass |
| Correct two-passage minimal groups | >=4 | 4 | Pass |
| Correct refuted cases | >=6 | 6 | Pass |
| Correct insufficient cases | >=10 | 11 | Pass |
| Correct contested cases | >=3 | 3 | Pass |
| Incomplete cases with no model call | >=4 | 4 | Pass |

Observed statuses were 18 `supported`, six `refuted`, 17 `insufficient`, three
`contested`, and four `incomplete`.

## Safety failures

The model assigned 0.9819 entailment to “I want to enable passkeys after the
security review” for the claim “The user enabled passkeys.” Preserving a desire
during ingestion is insufficient if answer-time entailment collapses that desire
into a completed action. Support needs the same speech-act boundary already
required of extraction.

The second unsafe support occurred in a conflict. “Ravi owns the ingestion
pipeline” scored 0.9970 entailment, while “Ravi no longer owns ingestion; Tessa
owns it” received only 0.0317 contradiction. The final result was `supported`
instead of `contested`. An explicit, lexically aligned negation needs a narrow
deterministic conflict path when the general NLI model treats the correction as
neutral. Any such path must expose its non-model decision basis rather than
inventing a probability.

## Conservative tradeoffs

All six refutations with explicit negation, correction language or incompatible
values were correct. All four deliberately included implicit contradictions
were returned as `insufficient`, with raw contradiction probabilities from
0.9996 to 0.9999 and `uncorroborated_model_contradiction` limitations. This is
the expected recall cost of requiring deterministic corroboration. A future
system can add a richer typed contradiction resolver, but raw NLI contradiction
alone remains rejected by the v1 safety evidence.

The verifier found four of six previously unexecuted two-passage chains. The
Orion pair reached only 0.7428 entailment. The Cedar pair was incorrectly scored
0.9692 contradiction, which the guard safely reduced to `insufficient`. These
misses show that bounded grouping does not make a three-label NLI model a
reliable relation composer.

## Decision

Keep the feature opt-in. Add a deterministic support guard that blocks model
entailment when the evidence expresses a desire, attempt, plan, advice,
question, uncertainty or condition that the claim does not preserve. Retain the
raw score and report the blocked basis.

Add a conservative explicit-negation fallback only for near-exact propositions,
with a separately reported deterministic basis. It should recover the ownership
conflict without accepting arbitrary lexical alternatives as refutations. Do
not lower model thresholds or broaden the concrete-value rule to recover the
four intentionally implicit contradictions.

## Evidence boundary

These are short authored development probes. They were not executed before
registration, but they were created after the earlier 20-case outcomes and are
not an independent external holdout. The assay uses one local model and does not
measure retrieval recall, long contexts, answer decomposition, calibration,
domain transfer or comparative product quality.
