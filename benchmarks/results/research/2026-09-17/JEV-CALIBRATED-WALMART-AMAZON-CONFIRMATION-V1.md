# Calibrated Jev Candidate Pipeline Confirmation V1

## Decision

The development-calibrated pipeline was rejected on the untouched Jev test
outputs. It missed both the preregistered 90% proposal-precision gate and the
50% proposal-recall gate. Candidate generation, response validity, latency,
false-positive rate, recall gain, and catalog-reduction gates passed.

This result does not alter the earlier confirmation of Jev as an advisor for a
caller-selected product pair. It rejects automatic bulk composition of the
candidate generator and Jev proposal rule. No graph mutation or automatic merge
was authorized.

## Frozen rule

The rule was selected from the completed validation-split development result
and committed before any test-split Jev calls:

```text
link_state.score >= 1.6
and same_name.noul >= 0.8
and compatible_price.noul >= 0.5
```

It produced 92.52% precision and 51.30% end-to-end recall on development.

## Confirmation results

| Measure | Exact-name baseline | Calibrated pipeline | Gate |
| --- | ---: | ---: | ---: |
| Precision | 100.00% | 88.17% | >= 90% |
| Recall | 4.66% | 42.49% | >= 50% |
| F1 | 8.91% | 57.34% | — |
| Accuracy | 91.02% | 94.05% | — |
| False-positive rate | 0.00% | 0.59% | <= 1% |

The end-to-end confusion matrix was 82 true positives, 11 false positives,
1,845 true negatives, and 111 false negatives. The deterministic candidate
stage routed all 193 positive pairs and reduced the 56,376,996-pair catalog
cross product by 99.8644%. Jev returned valid responses for all 1,677 routed
labeled pairs. Median request latency was 197 ms and p95 was 361 ms.

## Product boundary

PRME may use the independently confirmed components at their demonstrated
boundaries:

- deterministic candidate generation can reduce which pairs an application
  inspects;
- Jev can advise on an explicitly selected pair and retain its typed evidence;
- positive advice can become an inert, durable alias proposal; and
- an owner-scoped reviewer can accept or reject that proposal explicitly.

PRME must not turn the full candidate set into proposals automatically based on
either tested Jev rule. Automatic identity merging remains forbidden.

## Artifact integrity

- Registration SHA-256: `6ecb42874e0c3d5adb920be87032d434608cf46f36441efd9fe7dd1f38a15e54`
- Result file SHA-256: `a422f74c24b777d9e4b0f76a3b4ad0e7632629a6e76006d573547e60fbff5898`
- Canonical result SHA-256: `b4fd7eaf19098c53b48d14b93ff9ce69bce452325d876b7d6e659d2e147951ef`

## Limits

The result covers one structured retail-product dataset and one pinned external
model. The test split's candidate-routing outcome had been measured previously,
but its Jev outputs were not observed before this rule was registered. These
results do not establish performance for people, organizations, places, or
arbitrary memory entities.
