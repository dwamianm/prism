# WiCE localized claim-verification confirmation v2

**Result: failed.** Localized passage guards preserved the complete authored
safety contract and materially improved external supported recall, but the WiCE
confirmation still failed precision, recall, and balanced-accuracy gates.

## Bound protocol

- Implementation and runner revision:
  `cb09c76b5ad615d79e64ff0e41ed247ad6668c90`
- Registration commit: `a93224ec241933a4b2eb94771fef150fbdec62d0`
- WiCE registration SHA-256:
  `b2c7f6f9729463575ff1d03db13086a7dd35b5c694b17f7b482d73219848edab`
- Authored registration SHA-256:
  `198de6972f541eff1b660230bb9e203e1a6c30a68bad3b562dcab4e8c01f500b`
- Dataset, labels, model revision, 0.80 threshold, and every WiCE v1 gate were
  unchanged
- WiCE scope: all 358 human-annotated oracle-retrieval claim test cases
- Authored scope: the same fully observed 44-case guard cohort

## Authored safety regression

The authored run matched 43/44 expected statuses and passed all eight registered
gates: zero unsafe supports, zero false refutations, 13 supported, 6 minimal
groups, 6 refuted, 16 insufficient, 4 contested, and 4 completeness boundaries
without model calls. The existing typed-unverified paraphrase remained the sole
miss. Localized guarding did not weaken the prior completed-action, relation, or
conflict behavior.

## External result

| Metric | Passage-wide v1 | Localized v2 | Raw 0.80 scores |
|---|---:|---:|---:|
| Accuracy | 72.07% | 75.42% | 77.37% |
| Balanced accuracy | 55.05% | 63.04% | 67.48% |
| Supported precision | 85.71% | 73.91% | 73.02% |
| Supported recall | 10.91% | 30.91% | 41.82% |
| Supported F1 | 19.35% | 43.59% | 53.18% |
| False-support rate | 0.81% | 4.84% | 6.85% |
| Fully unsupported false-support rate | 0% | 0% | not separately gated |

Localization changed 32 claim decisions, all from `insufficient` to `supported`:
22 fully supported claims and 10 partially supported claims. It executed 82
localized rechecks and selected `localized_model_entailment` for 33 of the 46
accepted claims. Product statuses became 46 `supported`, 305 `insufficient`, 5
`refuted`, and 2 `contested`.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Claims evaluated | at least 358 | 358 | pass |
| Supported precision | at least 90% | 73.91% | **fail** |
| Supported recall | at least 60% | 30.91% | **fail** |
| Balanced accuracy | at least 70% | 63.04% | **fail** |
| False-support rate | at most 10% | 4.84% | pass |
| Fully unsupported false-support rate | at most 10% | 0% | pass |

## Decision

Keep claim verification opt-in. Localized guarding fixes the passage-scoping
defect without regressing authored safety, but the generic NLI model remains an
inadequate document-grounding classifier. Its raw scores recover only 46/110
supported claims and accept 17 partial claims. The next registered experiment
must test a purpose-trained, permissively licensed grounding model at a pinned
revision. Do not lower thresholds or tune another rule on the observed WiCE
cases.

## Artifact integrity

- Authored result file SHA-256:
  `044627ca9bd5be54e23f27ab0f9650c58cc2cbeba3eefc1bcdcbe8bffd142987`
- Authored canonical result SHA-256:
  `c7b90be07d86106738e51b44e5deccf2f993dc7b6e4ffa58d8e9ddeac546b3a8`
- WiCE result file SHA-256:
  `98308910fb57c4a4568489c4834a31fc5d50c0fea8eeb60724fb3c09fdce2a24`
- WiCE canonical result SHA-256:
  `e7c4feacf2f40d77c26c2af4b1338a42a9dcc1f534e2c3ee3b98dc9617ddd140`
