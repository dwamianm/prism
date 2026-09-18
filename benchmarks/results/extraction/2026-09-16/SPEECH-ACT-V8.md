# Speech-act v8 development assay

The preregistered 14-case, 15-target development assay completed against
`deepseek-v4.1-flash:cloud` at the registered digest and failed all-zero gates.
The run preserved 13/15 expected targets and passed 11/14 cases. All outputs
used extraction policy `speech_act_v2` and materialization policy
`speech_act_v8`.

One unsafe relationship survived. For “I'm trying to set up ESLint v8.39 with
the Airbnb style guide,” the extractor emitted the safe
`trying_to_use_with` fact and an `ESLint used_with Airbnb style guide`
relationship. The v8 validator scoped the attempt check to a literal `I` or
`we` claim subject, so it did not reject a completed relationship between two
objects inside that same attempted-action clause.

Two qualified targets were lost rather than falsified. The model retained
entities and accurate summaries for “I've tried to install CUDA 12.4...” and
“I would like to use Redis if...,” but returned no fact or relationship claim
for either target. The safety filter therefore needs a utility check as well as
a false-assertion check.

This is an authored development contrast set and one hosted model profile. It
does not estimate held-out accuracy or establish competitive quality. The
failed run is retained because it identified two distinct changes to test:
clause-level validation for component relationships, and stronger extraction
instructions that require useful attempt and intention claims.

Artifacts:

- `speech-act-v8-registration.json` — prompt, schema, implementation, cases,
  model digest, controls, and zero-tolerance gates fixed before execution.
- `speech-act-v8-results.json` — complete raw extractions, materialized nodes,
  per-target scoring, and process outcome.
