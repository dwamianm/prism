# Jev claim-verification development trial v1

**Result: rejected.** Pinned `jev-1.13.0` materially improved over prior verifier trials on a fresh 400-claim development cohort, but every frozen decision arm missed the registered 90% supported-precision boundary. No Jev product integration or external test access follows from this run.

## Bound protocol

- Dataset: LLM-AggreFact development revision `981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4`
- Cohort: 400 claims, balanced 25 supported and 25 unsupported cases across each of eight source datasets
- Exclusions: 3,038 claim identities found in prior committed PRME LLM-AggreFact results
- Provider: TypeSafe AI, pinned model `jev-1.13.0`
- Input: complete source document and claim; three independent typed questions in one request
- Frozen arms: top Choice label, direct Noul above 0.5, and agreement among Choice plus direct and inverse Nouls
- Gates: at least 90% supported precision, 60% recall, 75% balanced accuracy, and at most 10% false-support rate
- Source and API credentials are absent from committed artifacts

## Results

| Arm | Precision | Recall | Balanced accuracy | False-support rate | Decision |
|---|---:|---:|---:|---:|---|
| `choice_top` | 85.80% | 72.50% | 80.25% | 12.00% | reject |
| `direct_noul` | 88.51% | 65.50% | 78.50% | 8.50% | reject |
| `conservative_agreement` | 88.81% | 63.50% | 77.75% | 8.00% | reject |

All 400 responses were structurally valid, resolved to the pinned model, and completed without retry. The run used 533,591 input tokens and 37,597 output tokens. Median request latency was 0.188 seconds and maximum latency was 0.625 seconds with six concurrent requests.

The direct Noul arm came closest: 131 true positives, 17 false positives, 183 true negatives, and 69 false negatives. It passed recall, balanced-accuracy, and false-support gates but reached 88.51% precision. The conservative agreement arm removed one false positive and four true positives, reaching 88.81% precision and 63.50% recall.

A post-run threshold scan found no direct-Noul or three-signal minimum threshold that simultaneously met all registered gates. Threshold calibration alone therefore cannot repair this cohort. Domain results were also uneven: direct Noul balanced accuracy ranged from 54% on ExpertQA to 94% on Reveal, while meeting and media summaries produced the largest false-support rates.

## Comparison and decision

Jev is materially stronger than the rejected DeepSeek full-source certificate trial on its separate fresh cohort: the best Jev arm reached 80.25% balanced accuracy versus 73.50%, and the conservative Jev arm reached 88.81% precision versus 78.31%. Because cohorts differ, this is directional capacity evidence rather than a paired superiority result.

Do not integrate Jev as a global claim verifier from this run. The miss is small in aggregate but concentrated in source domains where false support is costly. Further Jev work should use a different architecture or task rather than tune another threshold on these observed cases. Retrieval reranking and conservative entity resolution remain plausible because they can use Jev as a bounded decision signal without treating its output as truth.

## Artifact integrity

- Registration SHA-256: `f89c46097ada2c502074b4691f82df9dc3acadcfa00b823648e141585505f4e6`
- Result file SHA-256: `7883e2a18836d32927b48681f814e29540dc7018088042bec1a7b212508bc29a`
- Canonical result SHA-256: `fec923126c70e489fd827400b3fb7472f16954154594cc194d57b9a1a9d9ec13`

The result contains identifiers, labels, typed probabilities, decisions, token use, and latency. It contains no claim text, source document text, or credential.
