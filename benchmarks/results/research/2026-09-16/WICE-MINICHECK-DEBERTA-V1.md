# WiCE MiniCheck DeBERTa grounding trial v1

**Result: failed.** The purpose-trained grounding model materially outperformed
the generic NLI verifier on WiCE, but missed the fixed precision and recall
gates. It passed balanced accuracy and both false-support safety gates.

## Bound protocol

- Runner revision: `c4aae8e78211f6c539d4432919f4916578f0a267`
- Registration commit: `fc38fd8b35b06d14fcfbf8b8cf14718888b92cda`
- Registration SHA-256:
  `42cc0ad5b7d300f7f37dfb853077d3aa465d9078c3ce5c45252e5308260733b1`
- Model: `lytang/MiniCheck-DeBERTa-v3-Large`
- Model revision: `60c4e0825ae044a6193ba811c5712c37548636a0`
- License: MIT
- Safetensors SHA-256:
  `8528d6a1399182054c3d2918f58bb67785de50ffde97f5ecdb215eabfb73297d`
- Published decision rule: support probability strictly greater than 0.5
- Input: document, tokenizer EOS, claim; maximum length 2,048 tokens
- Cohort: all 358 WiCE oracle-retrieval claim test cases

The runner loaded safetensors with remote code disabled, validated two output
labels and the exact weight digest, and emitted no source text. Local inference
on MPS took 93.34 seconds for all 1,070 oracle variants.

## Result

| Metric | MiniCheck DeBERTa | Generic NLI raw | PRME localized |
|---|---:|---:|---:|
| Accuracy | 81.84% | 77.37% | 75.42% |
| Balanced accuracy | 75.01% | 67.48% | 63.04% |
| Supported precision | 77.78% | 73.02% | 73.91% |
| Supported recall | 57.27% | 41.82% | 30.91% |
| Supported F1 | 65.97% | 53.18% | 43.59% |
| False-support rate | 7.26% | 6.85% | 4.84% |
| Fully unsupported false-support rate | 0% | not separately gated | 0% |

The confusion matrix was 63 true positives, 18 false positives, 230 true
negatives, and 47 false negatives. All false positives were partially supported
claims; none of the 32 fully unsupported claims passed.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Claims evaluated | at least 358 | 358 | pass |
| Supported precision | at least 90% | 77.78% | **fail** |
| Supported recall | at least 60% | 57.27% | **fail** |
| Balanced accuracy | at least 70% | 75.01% | pass |
| False-support rate | at most 10% | 7.26% | pass |
| Fully unsupported false-support rate | at most 10% | 0% | pass |

## Frozen-score diagnosis

No threshold over the saved probabilities satisfies both failed gates. Among
thresholds retaining at least 60% recall, the best precision is 79.07%. Among
thresholds reaching at least 90% precision, the best recall is 30.0%. Intersecting
MiniCheck with the generic NLI signal raises precision above 90% but reduces
recall below 28%; union rules raise recall above 69% but keep precision below
74%. This is not a threshold-only defect.

The next capacity test is the authors' MIT-licensed MiniCheck Flan-T5 Large,
which is purpose-trained for the same binary task and was published as their
strongest sub-billion-parameter verifier. Keep the DeBERTa result rejected as a
standalone PRME support model.

## Artifact integrity

- Result file SHA-256:
  `63e69ee46b88428502dd333c72b21730f374b1864b9140d98b924d5b610ada6b`
- Canonical result SHA-256:
  `70f289a80bbe392b7432205ebb17331c20d2e90859bdb7be8ca4f08f5be918d8`
