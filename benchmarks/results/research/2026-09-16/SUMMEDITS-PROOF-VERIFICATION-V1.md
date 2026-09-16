# SummEdits proof-carrying verification trial v1

**Result: failed.** A large reasoning model combined with free-form verbatim
proof quotations was conservative, but neither safe nor useful enough for
integration. It failed precision, recall, balanced-accuracy, and quote-integrity
gates.

## Bound protocol

- Runner revision: `9bca7aef6573f2bb38297fbabc32d95fde805f2a`
- Registration commit: `bd29bd4a8e26b02361998af7ce7f6ba7668a5bfb`
- Registration SHA-256:
  `48007aa4692c36f452872b0c9eb28e31594e81373c7271edef03a308f134be01`
- Dataset: Salesforce factualNLG revision
  `b95184aba5f3bd2a3eb36fb1897c9b6394ca0207`
- Cohort: 100 evaluation cases, balanced between supported and unsupported,
  with ten cases from each SummEdits domain and no more than three variants
  from any of the 40 source documents
- Model: `mistral-large-3:675b-cloud`, Ollama manifest
  `951502e79e7f517bb49c298b597959501406b965420feacfa9ccfbce2e40d35c`
- Acceptance: every generated atom must be supported, cover every summary
  token through exact summary quotations, and contain exact document evidence
  quotations

One inspected prototype was excluded before registration. The first invocation
completed 96 cases and four calls exhausted the single schema retry. A resumed
invocation evaluated only those four incomplete cases with the same registered
inputs. The 100 accepted responses consumed 1,574.09 aggregate provider-call
seconds under four-call concurrency. The final invocation's 7.58-second runtime
field does not represent the earlier resumable work.

## Result

| Metric | Observed |
|---|---:|
| Accuracy | 52.00% |
| Balanced accuracy | 52.00% |
| Supported precision | 75.00% |
| Supported recall | 6.00% |
| Supported F1 | 11.11% |
| False-support rate | 2.00% |
| Complete quote integrity | 23.00% |

The confusion matrix was 3 true positives, 1 false positive, 49 true
negatives, and 47 false negatives. Only four cases passed the complete proof
contract.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Cases evaluated | at least 100 | 100 | pass |
| Supported precision | at least 90% | 75.00% | **fail** |
| Supported recall | at least 60% | 6.00% | **fail** |
| Balanced accuracy | at least 75% | 52.00% | **fail** |
| False-support rate | at most 10% | 2.00% | pass |
| Quote integrity | at least 98% | 23.00% | **fail** |

## Diagnosis

The deterministic proof checks worked: paraphrased or invented quotations and
incomplete summary coverage could not become support. The interface given to
the model was the failure. The model frequently copied neither summary nor
document text exactly. More fundamentally, presenting the document during
decomposition caused it to add document facts that the summary never asserted;
those extra atoms then became `uncertain` because they were absent from the
summary. Ignoring quotation checks would still yield only 80.00% precision,
48.00% recall, and 68.00% balanced accuracy.

The next development protocol should separate decomposition from verification.
The decomposer must see only numbered summary segments. A second call should
verify the resulting fixed atom IDs against numbered document segments. Code
can then resolve citations without asking a model to reproduce source bytes,
reject omitted or invented IDs, and prevent document facts from contaminating
claim decomposition.

## Artifact integrity

- Result file SHA-256:
  `a055ad26f8b88e320962b9336e1d7125c91931009303f995dedf2898978cc786`
- Canonical result SHA-256:
  `fbcd2fa43b606d9e578f8027849a2b22f30bf9931f1bdddc4e84efc419860fde`
