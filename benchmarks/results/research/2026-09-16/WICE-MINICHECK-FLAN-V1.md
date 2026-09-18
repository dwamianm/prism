# WiCE MiniCheck Flan-T5 grounding trial v1

**Result: failed.** The published strongest sub-billion-parameter MiniCheck
variant passed recall, balanced accuracy, and both false-support gates, but
failed the fixed 90% precision requirement on partially supported claims.

## Bound protocol

- Runner revision: `c3048fd5a570bb3919ddd6bcc8ef0078fd1199d0`
- Registration commit: `6c067904554db18ba108dd2b9ffa8893e40f8456`
- Registration SHA-256:
  `8b76a1b7550dff033da3db61e37b5a57e8082e9436b25b8697a1ba513e6dbf77`
- Model: `lytang/MiniCheck-Flan-T5-Large`
- Verified safetensors conversion revision:
  `5294ece54f6e7a4445bf86cd20250df6e1ce9548`
- License: MIT
- Safetensors SHA-256:
  `d38bd7f79e74ba25dbeff988dfff32dd758a74ef389b746b982e984646d3d47c`
- Published inference: `predict: ` prefix, document/EOS/claim input, one decoder
  step, unsupported token 3, supported token 209, strict probability > 0.5
- Cohort: all 358 WiCE oracle-retrieval claim test cases

The runner loaded safetensors with remote code disabled and emitted no source
text. Local MPS inference took 103.89 seconds for 1,070 oracle variants after
model resolution.

## Result

| Metric | Flan-T5 | DeBERTa | Generic NLI raw | PRME localized |
|---|---:|---:|---:|---:|
| Accuracy | 84.92% | 81.84% | 77.37% | 75.42% |
| Balanced accuracy | 81.52% | 75.01% | 67.48% | 63.04% |
| Supported precision | 76.92% | 77.78% | 73.02% | 73.91% |
| Supported recall | 72.73% | 57.27% | 41.82% | 30.91% |
| Supported F1 | 74.77% | 65.97% | 53.18% | 43.59% |
| False-support rate | 9.68% | 7.26% | 6.85% | 4.84% |
| Fully unsupported false-support rate | 0% | 0% | not separately gated | 0% |

The confusion matrix was 80 true positives, 24 false positives, 224 true
negatives, and 30 false negatives. All 24 false positives were partially
supported compound claims; none of the 32 fully unsupported claims passed.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Claims evaluated | at least 358 | 358 | pass |
| Supported precision | at least 90% | 76.92% | **fail** |
| Supported recall | at least 60% | 72.73% | pass |
| Balanced accuracy | at least 70% | 81.52% | pass |
| False-support rate | at most 10% | 9.68% | pass |
| Fully unsupported false-support rate | at most 10% | 0% | pass |

## Frozen-score diagnosis

No threshold over the saved probabilities satisfies the precision and recall
gates together. Among thresholds retaining at least 60% recall, the best
precision is 79.35%. Reaching at least 90% precision reduces recall to 2.73%.
The larger model improves support detection but still treats a compound claim as
supported when only part is established.

WiCE's human subclaim annotations form an almost exact architectural control:
requiring every gold subclaim to be fully supported reproduces 357/358 parent
labels. The next preregistered experiment should apply the unchanged Flan model
to those subclaims and require all subclaims to pass. This measures the value of
atomic claim verification without conflating it with learned decomposition.

## Artifact integrity

- Result file SHA-256:
  `d32e471c193a65296f6edab9ffb4af7232ff0de619031321605a24410ddbeb6c`
- Canonical result SHA-256:
  `32878ba1b5cbe80e24e4aaac3520d7e80ad39a471f19ee108261757981532b0a`
