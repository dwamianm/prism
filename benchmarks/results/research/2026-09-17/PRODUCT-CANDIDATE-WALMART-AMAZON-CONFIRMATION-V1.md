# Walmart–Amazon Product Candidate Confirmation V1

## Decision

The deterministic `product_tfidf_candidates_v1` ranker passed every preregistered
gate on the untouched DeepMatcher Structured Walmart–Amazon test split. It is
confirmed as a broad-recall routing stage for the separately evaluated Jev
product-pair advisor.

This result does not measure Jev classification quality and does not authorize
automatic entity merging.

## Results

| Measure | Result | Gate |
| --- | ---: | ---: |
| Positive labeled pairs routed | 193 / 193 | Recall >= 95% |
| Positive-pair recall | 100.00% | Pass |
| Candidate pairs | 76,456 | <= 123,140 |
| Full cross product | 56,376,996 | — |
| Cross-product reduction | 99.8644% | >= 99% |
| Mean candidates per entity | 3.1044 | — |
| Deterministic replay | Yes | Required |

The generator also routed 1,484 of 1,856 labeled negative pairs. That number is
expected for a recall-oriented router and is why Jev must be evaluated on this
harder candidate-generated distribution before the stages are treated as a
confirmed pipeline.

## Frozen protocol

- Dataset: DeepMatcher Structured Walmart–Amazon
- Split: `test`
- Candidate policy: `product_tfidf_candidates_v1`
- Product fields: title, brand, and price
- Catalog constraint: Walmart-to-Amazon pairs only
- Selection: union of each endpoint's top 5 candidates
- Minimum cosine score: 0.1
- External model calls: none
- Graph mutation: none

## Artifact integrity

- Registration SHA-256: `54ff980358aa9ea37953e838141cb9a7ab89b2d24cde3307c0d08dc5eee653c6`
- Result file SHA-256: `a79400806b44c5ed5291b5b2da2479f8b1f295c6ba445c550907a0c047b8ec9d`
- Canonical result SHA-256: `fdbfe3a6a3c5bcdac1aecf214b30701552dc8607ab67784a1d80a67b585c0bcb`

## Limits

Walmart–Amazon is one structured retail dataset. Candidate recall is not
proposal precision, identity accuracy, or evidence for automatic merging. The
next registered trial evaluates candidate routing and Jev together on the
previously unused validation split.
