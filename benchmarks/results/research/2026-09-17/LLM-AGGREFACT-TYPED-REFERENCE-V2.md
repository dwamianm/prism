# LLM-AggreFact source-bound typed-reference trial v2

**Result: rejected during registered development; test remained sealed.** The
source-bound representation substantially reduced invalid references compared
with the preceding copied-substring prototype, but the run crossed its frozen
futility boundary after 310 of 1,100 development cases. Even perfect results on
every remaining case could have reached only 97.73% reference integrity, below
the registered 98% requirement.

## Registered protocol

- Dataset: LLM-AggreFact revision
  `981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4`; development parquet SHA-256
  `ca9a5bda9561bbc6f7327ef81ab41727fa10e96f96e9dc74ef32ba3af3c1d8a8`.
- Cohort: 1,100 development claims, balanced at 550 claims per label and
  deterministically selected after excluding all identities observed by the
  FactCG v1 and typed-alignment v1 trials. The selected-identity SHA-256 was
  `84b3b16cf4310db8f6a8ad0cdea103926f8f1cad4c4bc34971cb181b9c7e3c33`.
- Evidence ranker: pinned `yaxili96/FactCG-DeBERTa-v3-Large`, selecting the two
  strongest source chunks before provider verification.
- Provider: Ollama cloud alias `deepseek-v4.1-flash:cloud`, manifest digest
  `e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`.
  The remote model alias was not a pinned weight artifact.
- Representation: the provider could return only displayed claim-token and
  evidence-segment identifiers. The runner reconstructed claim and evidence
  text from the authoritative dataset; generated text could not become source
  evidence.
- Recovery: three source-reference validation attempts per case, with prior
  identifier-only JSON and machine error codes returned for repair. All attempts
  failed closed.
- Frozen integrity gate: at least 98% across all 1,100 development cases. The
  runner stopped only after more than 22 failures made that gate mathematically
  impossible.
- The runner accepted no test path. The external test split was not accessed.

Registration file SHA-256:
`e38e4ccc7e3820972428d3e058b0b07bdf89a7728eecff9e3fef2c257e2ff2ec`.

## Observed result

| Measure | Result |
|---|---:|
| Registered development cases | 1,100 |
| Cases observed before futility stop | 310 |
| Source-valid cases | 285 |
| Reference integrity | 91.94% |
| Schema-successful cases | 293 |
| Schema success | 94.52% |
| Valid references among schema-successful cases | 97.27% |
| Provider/schema timeouts | 17 (5.48%) |
| Reference-contract failures | 8 (2.58%) |
| First-attempt completions | 191 (61.61%) |
| Second-attempt completions | 74 (23.87%) |
| Third-attempt completions | 45 (14.52%) |
| Maximum possible final integrity after stop | 97.73% |

All 17 provider/schema failures were `TimeoutError`s. Their median accumulated
case time was 549.88 seconds, and the longest used the complete 720-second
three-attempt allowance. The eight source-reference failures had a 308.62-second
median, showing that repeated unconstrained repair was also expensive when the
model could not satisfy the typed coverage/alignment contract.

The evidence ranker scored 2,085 chunk pairs in 321.67 seconds on MPS. Provider
alignment took 6,356.23 wall-clock seconds, with 37,579.84 summed request
seconds across six concurrent calls. The run stopped before atomic FactCG
scoring and calibration, so it produced no precision, recall, balanced-accuracy
or false-support result. Inferring classification quality from the partial
alignment decisions would violate the registered protocol.

The canonical result SHA-256 is
`ce9c9126e70060644d99903af01b3658b9d2a42f3115b8591a0db11515ee3cae`;
the result file SHA-256 is
`7920890f553426bcb5062c6119ef7587e8c66e71143c7848d71aaca5f2d07c64`.

## Decision

Do not integrate this verifier or open the sealed test split. Identifier-only
source binding is the correct provenance direction: it improved observed
integrity from 50.00% in the 48-case copied-substring prototype to 91.94% here
and eliminated generated quotations as evidence. It did not meet the reliability
contract required for a provider verifier.

The next design must make provider execution a durable, independently retryable
job rather than treating three long calls as one in-process case. It should use
grammar-native constrained output or deterministic identifier selection, retain
each provider response and request identity, separate transport exhaustion from
semantic rejection, and prove restart-safe retry behavior before consuming more
disjoint external development data. Any new registered cohort must exclude all
310 identities observed here. Increasing timeouts or weakening the 98% gate
post hoc is not an acceptable remedy.
