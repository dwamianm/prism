# SummEdits separated segment verification trial v2

**Result: failed.** Separating summary-only decomposition from fixed-atom
verification repaired the proof interface and passed recall, balanced-accuracy,
and reference-integrity gates. It still accepted ten inconsistent summaries,
failing precision and false-support requirements.

## Bound protocol

- Runner revision: `1fbf086ae31f7330e87895573633c868e3f8eaf4`
- Registration commit: `9d5cb138b0de65ca34777b7e3eebee1a2f1c3f4b`
- Registration SHA-256:
  `5728a079e1bd3daaf2d2b60b80bb805ce2a2a8ac5c4207b663d95b1f2cf35460`
- Dataset, 100-case cohort, model, temperature, seed, concurrency, and quality
  thresholds: unchanged from v1
- Decomposition: the model sees numbered summary segments only; every segment
  must map to at least one atom
- Verification: a separate call sees numbered document segments and the fixed
  atom IDs; every atom must receive one verdict and supported atoms must cite
  valid document segment IDs
- Acceptance: all reference checks pass and every fixed atom is supported

This is a same-cohort causal repair study after observing v1, not an untouched
confirmation. It completed in 272.00 wall seconds with 1,037.98 aggregate
provider-call seconds under four-call concurrency.

## Result

| Metric | V2 segment IDs | V1 copied quotes | Change |
|---|---:|---:|---:|
| Accuracy | 76.00% | 52.00% | +24.00 points |
| Balanced accuracy | 76.00% | 52.00% | +24.00 points |
| Supported precision | 78.26% | 75.00% | +3.26 points |
| Supported recall | 72.00% | 6.00% | +66.00 points |
| Supported F1 | 75.00% | 11.11% | +63.89 points |
| False-support rate | 20.00% | 2.00% | +18.00 points |
| Reference integrity | 99.00% | 23.00% | +76.00 points |

The confusion matrix was 36 true positives, 10 false positives, 40 true
negatives, and 14 false negatives. One otherwise supported case failed the
reference-integrity contract. The ten false supports contained seven entity
modifications, four antonym swaps, and one hallucinated insertion; edit labels
can overlap.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Cases evaluated | at least 100 | 100 | pass |
| Supported precision | at least 90% | 78.26% | **fail** |
| Supported recall | at least 60% | 72.00% | pass |
| Balanced accuracy | at least 75% | 76.00% | pass |
| False-support rate | at most 10% | 20.00% | **fail** |
| Reference integrity | at least 98% | 99.00% | pass |

## Decision

The separation and segment-ID mechanisms are valid architecture improvements:
they removed document-to-decomposition contamination, made references
machine-resolvable, and recovered useful recall. They do not make one verifier
safe. Do not integrate this provider path from the observed cohort.

The remaining errors are concentrated in subtle entity and relation edits. A
bounded next experiment can apply an independent model-family critic only to
the 46 v2 support candidates, using the exact saved atoms and segment IDs. It
must keep the same gates and preserve rejected cases. Passing would still need
untouched confirmation before product promotion.

## Artifact integrity

- Result file SHA-256:
  `adc52ae235ce15f80cb2a25d0c5cd2e2c3d37db5de12cdce62c89a48808f4f15`
- Canonical result SHA-256:
  `8848fe7d40b6b3114d1f7c023e897fe31c6a29596c34cbe1ababe004f242f384`
