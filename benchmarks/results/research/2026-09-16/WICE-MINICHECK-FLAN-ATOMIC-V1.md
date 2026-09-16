# WiCE MiniCheck Flan-T5 atomic grounding trial v1

**Result: failed.** WiCE's human decomposition almost perfectly reconstructs
the parent labels, but the unchanged MiniCheck verifier still accepted too many
unsupported atomic subclaims to meet the fixed 90% precision requirement.

## Bound protocol

- Runner revision: `304fc8b4bbe9dc523f074aa3639f1d3adc704a51`
- Registration commit: `f8d82c0c357449cd2381a37b8cfd91210d57b9f1`
- Registration SHA-256:
  `dcc838c9e0f678c3d2db07997d1db3d3f6d91669a0c6208ce6303145846c0020`
- Model: `lytang/MiniCheck-Flan-T5-Large`
- Verified safetensors revision:
  `5294ece54f6e7a4445bf86cd20250df6e1ce9548`
- Safetensors SHA-256:
  `d38bd7f79e74ba25dbeff988dfff32dd758a74ef389b746b982e984646d3d47c`
- Cohort: all 358 WiCE oracle-retrieval claim test cases, reconstructed
  from all 958 human-annotated subclaims
- Decision: score each subclaim with the published inference protocol and
  strict probability above 0.5, then accept its parent only when every
  subclaim passes

The runner used the pinned clean WiCE checkout, validated both dataset files
and shapes, loaded safetensors with remote code disabled, and emitted no source
text. Local MPS inference took 258.89 seconds.

## Result

| Metric | Atomic MiniCheck | Whole-claim MiniCheck | Gold decomposition |
|---|---:|---:|---:|
| Accuracy | 83.24% | 84.92% | 99.72% |
| Balanced accuracy | 78.54% | 81.52% | 99.80% |
| Supported precision | 76.04% | 76.92% | 99.10% |
| Supported recall | 66.36% | 72.73% | 100% |
| Supported F1 | 70.87% | 74.77% | 99.55% |
| False-support rate | 9.27% | 9.68% | 0.40% |
| Fully unsupported false-support rate | 0% | 0% | 0% |

The model-level parent confusion matrix was 73 true positives, 23 false
positives, 225 true negatives, and 37 false negatives. All 23 false-positive
parents were partially supported; none of the 32 fully unsupported parents
passed. At subclaim level, the model accepted 39/165 partially supported and
18/265 unsupported subclaims, while rejecting 84/528 supported subclaims.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Claims evaluated | at least 358 | 358 | pass |
| Supported precision | at least 90% | 76.04% | **fail** |
| Supported recall | at least 60% | 66.36% | pass |
| Balanced accuracy | at least 70% | 78.54% | pass |
| False-support rate | at most 10% | 9.27% | pass |
| Fully unsupported false-support rate | at most 10% | 0% | pass |

## Diagnosis

The gold all-subclaim rule reproduced 357/358 parent labels. This establishes
that atomic decomposition is a useful architecture boundary on this cohort.
Applying the current verifier at that boundary did not improve whole-claim
quality: its false accepts and false rejects at subclaim level compounded across
parents.

No threshold over the frozen parent minimum probabilities satisfies precision
and recall together. Among thresholds retaining at least 60% recall, the best
precision is 81.93%. Reaching at least 90% precision reduces recall to 0.91%.
Do not tune this model or integrate it as a product default from this observed
cohort. The next verifier trial needs a materially different reasoning signal
and an independently bound cohort; decomposition alone is not the missing
classifier.

## Artifact integrity

- Result file SHA-256:
  `ee0d72a3100c0008965223c32d4b55f5e7e3e4e92a20b833e52243c04c659215`
- Canonical result SHA-256:
  `eec008f1b66c3a8ad64491938062a43518a3bfedf5f08c23a2002b30b588fb2e`
