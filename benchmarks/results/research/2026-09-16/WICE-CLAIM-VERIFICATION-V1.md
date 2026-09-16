# WiCE external claim-verification assay v1

**Result: failed.** The guarded verifier passed both false-support safety gates
but failed precision, recall, and balanced-accuracy gates on the complete WiCE
oracle-retrieval claim test set.

## Bound protocol

- PRME verifier revision: `af0c0a21e1f977e60f0f56497520977b81c0105f`
- Registration commit: `ebec9bc399cdce2f6bd11f20b315c677f62fed6a`
- Registration SHA-256:
  `dc8750ed180c35282e2130c022a9d781bbac3a8b05dd5c39ce1edaf347464f50`
- WiCE revision: `ddeb6c183665e2a20c5f03c5aa07f03888b9870f`
- WiCE oracle claim test SHA-256:
  `234bb8c2ca4511ca078e49e07c895a2db61a581d03237e7e1ecb7c48c8510a3e`
- Dataset: 1,070 registered oracle chunks for 358 distinct claims; 110
  `supported`, 216 `partially_supported`, and 32 `not_supported`
- Positive class: `supported` only
- Model: `cross-encoder/nli-deberta-v3-base` at revision
  `6c749ce3425cd33b46d187e45b92bbf96ee12ec7`
- Product policy: default guarded entailment and explicit-corroboration
  refutation, threshold 0.80, one oracle chunk per evidence item, no cross-chunk
  combinations

The assay uses WiCE's oracle-retrieval chunks. It measures verification after
perfect retrieval rather than PRME retrieval or the original full-document WiCE
task. The result contains no claim or source text.

## Results

| Metric | Guarded product | Raw 0.80 entailment |
|---|---:|---:|
| Accuracy | 72.07% | 77.37% |
| Balanced accuracy | 55.05% | 67.48% |
| Supported precision | 85.71% | 73.02% |
| Supported recall | 10.91% | 41.82% |
| Supported F1 | 19.35% | 53.18% |
| False-support rate | 0.81% | 6.85% |

The guarded confusion matrix was 12 true positives, 2 false positives, 246 true
negatives, and 98 false negatives. Both false positives were partially supported
claims. No fully unsupported claim was accepted. Product statuses were 14
`supported`, 5 `refuted`, 2 `contested`, and 337 `insufficient`.

| Registered gate | Required | Observed | Result |
|---|---:|---:|---|
| Claims evaluated | at least 358 | 358 | pass |
| Supported precision | at least 90% | 85.71% | **fail** |
| Supported recall | at least 60% | 10.91% | **fail** |
| Balanced accuracy | at least 70% | 55.05% | **fail** |
| False-support rate | at most 10% | 0.81% | pass |
| Fully unsupported false-support rate | at most 10% | 0% | pass |

## Diagnosis

The same model scores crossed the entailment threshold for 46 supported and 17
partially supported claims. The product guard retained 12 of those true supports
and 2 false supports. It rejected the other 32 true raw supports and made 2 more
contested, while rejecting 15 of 17 raw false supports.

Forty-nine claims reported `uncorroborated_model_entailment`. Forty-seven had a
modality token somewhere in an oracle chunk that was absent from the claim, and
35 contained a negated clause somewhere in the chunk. Because the guard applies
to the complete multi-sentence passage, an unrelated question, intention,
condition, or negation can veto factual support elsewhere in the same passage.
That behavior is safe on short authored assertions but does not generalize to
document evidence.

This failure blocks default promotion. The next experiment must preregister a
localized guard that removes only nonmatching speech-act and negated sentences,
then rechecks entailment on the retained evidence. It must preserve the existing
completed-action refusals and cannot tune thresholds on WiCE v1.

## Artifact integrity

- Result file SHA-256:
  `63648b5f54aa24b5b798e55783a4b2d85e4823c227a6d0cb2c8bce9ad09ac6b1`
- Canonical result payload SHA-256:
  `16fbc58e3a3669ed08b718c461955b4ce891edcdba16dc8dd5f414dea10b89e5`

The result records an absolute local checkout path as nonsemantic execution
metadata. The repository revision, relative dataset path, dataset hash, shape,
and every decision digest are independently bound. A later runner revision
should omit the machine-local path.
