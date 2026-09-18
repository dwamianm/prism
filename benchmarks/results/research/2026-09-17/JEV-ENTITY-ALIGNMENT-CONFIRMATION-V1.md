# Jev entity-alignment confirmation v1

**Result: automatic merge use rejected.** The frozen Jev route raised held-out match recall from 14.29% to 64.29% and accuracy from 86.81% to 93.96%, but one false merge reduced precision to 94.74%, below the registered 98% safety gate.

## Frozen confirmation

- Cohort: all 182 previously unscored validation and test candidate pairs from DeepMatcher Structured BeerAdvocate-RateBeer
- Labels: 28 matches and 154 nonmatches
- Model: pinned TypeSafe `jev-1.13.0`
- Prompt and typed questions: byte-identical to the passing development protocol
- Decision: merge candidate only when `link_state.score >= 1.5`
- No threshold, prompt, or arm selection changed after development
- No graph mutation occurred

## Results

| Method | True matches | False merges | Merge precision | Merge recall | Accuracy |
|---|---:|---:|---:|---:|---:|
| Current PRME conservative name match | 4/28 | 0/154 | 100.00% | 14.29% | 86.81% |
| Frozen Jev score route | 18/28 | 1/154 | 94.74% | 64.29% | 93.96% |

The route passed completeness, recall, false-merge-count, and recall-gain gates. It failed only the 98% precision gate. All 182 responses were valid and resolved to the pinned model; none required retry. The run used 93,325 input tokens and 12,922 output tokens with 0.180-second median request latency.

## Decision

Do not allow Jev to merge graph identities. The single false positive is small operationally but violates the chosen mutation boundary because a wrong merge transfers every fact and edge. Jev remains promising for ranking or filtering unverified proposals, where a false positive cannot collapse identity. That safer use needs a task-specific comparison and confirmation before integration.

The confirmation uses held-out splits from the same product dataset, and TypeSafe's public cookbook described this dataset for Jev 1.12. It therefore establishes behavior for this pinned service and protocol, not cross-domain generalization.

## Artifact integrity

- Registration SHA-256: `a1b99beac25cbc2273191080fb03ba981e9a4a1a27f7fa43b24381c412c19c18`
- Result file SHA-256: `e75d826382ed3a40b01278b1d0099ae5455fa2a93777c630cedf75602ef95e2a`
- Canonical result SHA-256: `8555ac1149e4c3e6108a70348c1be4e67cdef4d251d367fd12e11f9f3eaed7e9`

The result contains pair identifiers, labels, scores, decisions, token use, and latency. It contains no entity field text or credential.
