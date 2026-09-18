# LLM-AggreFact evidence-local cascade v2

**Result: failed development; test remained sealed.** FactCG-ranked local
evidence plus a pinned Mistral verifier improved recall, but it could not meet
the registered precision and recall requirements together. The run also exposed
seven structured-output failures that remained fail-closed.

## Bound protocol

- Cohort: the same balanced 1,100-claim development cohort and independently
  selected 1,100-claim test cohort bound by the FactCG v1 trial
- Ranker: pinned FactCG DeBERTa, selecting the two highest-scoring 550-word-token
  source chunks before any provider call
- Verifier: `mistral-large-3:675b-cloud`, manifest
  `951502e79e7f517bb49c298b597959501406b965420feacfa9ccfbce2e40d35c`
- Interface: one fixed claim, numbered local evidence segments, and a strict
  supported/unsupported/uncertain schema with source IDs
- Acceptance: a valid supported verdict plus a globally calibrated FactCG score
- Test unlock: at least 90% precision, 60% recall and 98% reference integrity
  on development

The initial invocation produced 1,065 valid verdicts and 35 schema failures.
Unchanged resumptions reduced the missing set to seven. Because malformed
outputs never became verdicts, all failures remained rejected and the result
contains no document, claim, explanation or quotation text.

## Development result

The registered run did not complete because seven of 1,100 cases never produced
the required schema. A disclosed post-hoc diagnostic treated those seven as
`uncertain` with failed reference integrity. This cannot turn the registered run
into a pass; it establishes that further schema retries cannot change the model
selection decision.

| Operating point | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|
| Mistral verifier only | 73.70% | 85.09% | 77.36% | 30.36% |
| Best precision with recall at least 60% | 85.12% | 63.45% | 76.18% | 11.09% |
| Best recall with precision at least 90% | 90.13% | 36.55% | 66.27% | 4.00% |

Reference integrity under the conservative diagnostic was 98.91%. Verdicts
comprised 636 supported, 366 unsupported, 91 uncertain and seven schema
failures. The safety/utility separation remains wide even if every malformed
response is rejected.

## Decision

Do not integrate this provider cascade. Evidence localization improved recall
over direct FactCG classification, but ranker/verifier intersection still did
not distinguish false support well enough. More retries, another threshold or
accepting malformed output cannot close the observed gap.

The external test cohort remains untouched. Further verification work should
change the information available to the decision, such as explicit
claim-to-evidence alignment or deterministic relation extraction, rather than
adding another general-model vote.

## Artifact integrity

- Registration SHA-256:
  `5d49bd92abe3d4f8284647b0b8348b552ff853b5621200481299e6cc60f75188`
- Partial result file SHA-256:
  `8fb646e3cd507a6e32474209f45b80f7269ca31b5258cd86546fb1b47df5cbfc`
- Canonical partial result SHA-256:
  `71fc4cecc691d08159ab690bb9cdf7a1abeb134c2f8a39caf49a4b8bb8371232`
