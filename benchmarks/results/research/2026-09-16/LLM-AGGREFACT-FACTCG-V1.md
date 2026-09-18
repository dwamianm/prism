# LLM-AggreFact FactCG trial v1

**Result: failed calibration; test remained sealed.** The pinned FactCG model
could not simultaneously reach the registered 90% supported precision and 60%
supported recall requirements on the balanced 1,100-claim development cohort.
The runner therefore made no predictions on the separately bound test cohort.

## Bound protocol

- Dataset: LLM-AggreFact revision
  `981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4`
- Development and test cohorts: 50 supported and 50 unsupported claims from
  each of 11 datasets, selected independently by a registered hash order
- Verifier: `yaxili96/FactCG-DeBERTa-v3-Large` revision
  `0430e3509dbd28d2dff7a117c0eae25359ff3e80` (MIT)
- Source handling: the official 550-word-token sentence chunking strategy;
  maximum support probability across chunks
- Threshold policy: maximize development recall, then precision, then prefer
  the lower threshold, subject to at least 90% precision and 60% recall
- Test unlock: development must pass both calibration requirements

The MPS run scored 2,057 document/claim chunk pairs in 371.24 seconds. Results
contain identifiers, labels, scores and counts without document or claim text.

## Development result

No threshold met both calibration requirements.

| Operating point | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|
| Fixed threshold 0.5 | 78.83% | 63.64% | 73.27% | 17.09% |
| Best precision with recall at least 60% | 79.44% | 61.82% | 72.91% | 16.00% |
| Best recall with precision at least 90% | 90.29% | 28.73% | 62.82% | 3.09% |
| Maximum balanced accuracy | 75.90% | 76.73% | 76.18% | 24.36% |

At the fixed 0.5 threshold, the confusion matrix was 350 true positives, 94
false positives, 456 true negatives and 200 false negatives. Requiring 90%
precision reduced true positives to 158 and recall to 28.73%. This is a wide
precision/recall separation rather than a near miss.

## Decision

Do not integrate FactCG as PRME's provider verifier. Preserve it as a strong,
small evidence ranker candidate: its continuous score and document chunking can
select compact evidence for a separate verifier, but its binary decision does
not satisfy PRME's safety and utility requirements by itself.

The 1,100-claim test cohort remains untouched and can still confirm a separately
registered evidence-local cascade. Any such development must bind its model,
prompts, evidence selection and gates before inference and must pass development
before the test is unlocked.

## Artifact integrity

- Registration SHA-256:
  `505e04451f1e7d4ca29e40fe5592f6e9c669b4d74ed632fa1676b5c5f77df517`
- Result file SHA-256:
  `fd29b06dee6f35002031ee3fa17e5cf3e554e3c20c0331e70754bb409b2b9122`
- Canonical result SHA-256:
  `e3906ee0b0fa5966d535c814fc48fef5b685ffb8bee9093b154fa9acd0f661b7`
