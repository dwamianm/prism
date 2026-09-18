# Jev Candidate Pipeline Confirmation V1

## Decision

The frozen pipeline was rejected. Candidate routing and Jev response validity
passed, and the pipeline substantially improved recall over exact-name matching,
but the original Jev proposal threshold did not preserve enough precision on the
hard candidate-generated distribution.

No graph mutation or automatic merge was authorized by the trial.

## Results

| Measure | Exact-name baseline | Candidate + Jev | Gate |
| --- | ---: | ---: | ---: |
| Precision | 100.00% | 73.76% | >= 90% |
| Recall | 6.22% | 77.20% | >= 50% |
| F1 | 11.71% | 75.44% | — |
| Accuracy | 91.17% | 95.27% | — |
| False-positive rate | 0.00% | 2.86% | <= 5% |

The candidate generator routed 189 of 193 positive pairs (97.93%) while reducing
the 56,376,996-pair catalog cross product by 99.8644%. Jev returned a valid
response for every one of the 1,706 routed labeled pairs. Median request latency
was 181 ms and p95 was 327 ms.

The end-to-end confusion matrix was 149 true positives, 53 false positives,
1,803 true negatives, and 44 false negatives. Precision was the only failed
preregistered gate.

## Development finding

After the rejection was fixed, an offline search over the typed responses found
an explainable calibration candidate:

```text
link_state.score >= 1.6
and same_name.noul >= 0.8
and compatible_price.noul >= 0.5
```

Applied to this development evidence, that rule produced 99 true positives and
8 false positives: 92.52% precision and 51.30% end-to-end recall. These are
development figures. The rule must be frozen and tested on a split whose Jev
outputs have not been observed before it can be integrated.

## Artifact integrity

- Registration SHA-256: `39754304091d22c9ea878800bad1f68d7cc62940067b73e5717a2945c7c3e2a2`
- Result file SHA-256: `39a3f331b40de77f8946e930fceb764cfd67f82297664c6337f7f042d0fda124`
- Canonical result SHA-256: `c31911412c0958d897ce4f9dafbd70c3a63764315b2d1c1f0c008d5a20f3bc44`

## Limits

Walmart–Amazon is one structured retail dataset. The candidate-generated class
distribution is harder than the earlier balanced pair sample. The rejected
threshold remains unsuitable for automatic use, and even a confirmed calibrated
rule can only create reviewable, unverified alias proposals.
