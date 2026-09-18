# Jev generic product-alignment confirmation v1

**Result: optional product-pair advisor confirmed.** The unchanged pinned
`jev-1.13.0` protocol passed every preregistered gate on 400 previously untouched
DeepMatcher Amazon-Google validation/test pairs. It supports an optional advisor
that recommends unverified alias proposals. It does not authorize automatic
identity merges.

| Method | Precision | Recall | Accuracy | False-positive rate |
|---|---:|---:|---:|---:|
| Exact normalized name | 83.33% | 2.50% | 51.00% | 0.50% |
| Jev score >= 1.5 | 96.30% | 78.00% | 87.50% | 3.00% |

Jev recovered 156 of 200 true matches with six false proposals. The exact-name
baseline recovered five true matches with one false match. Relative to the
baseline, Jev added 75.5 recall points and 36.5 accuracy points while exceeding
the registered 90% proposal-precision boundary.

The development result was directionally consistent: 94.38% precision, 75.50%
recall and 85.50% accuracy on 400 balanced training pairs. The confirmation
used a different selection seed and only validation/test pairs. No prompt,
threshold, model, or decision rule changed between the two runs.

All 400 confirmation responses were valid and none required a retry. The run
used 203,648 input tokens and 28,400 output tokens. Median request latency was
0.178 seconds and p95 was 0.330 seconds with six concurrent requests.

## Product boundary

The confirmed capability accepts two caller-supplied software-product records
with name, manufacturer and price fields. It returns the complete typed score
distribution, field-level Nouls, model and protocol identity, input hashes and a
deterministic assessment hash. `proposal_recommended` is true only at the frozen
score threshold of 1.5. `automatic_merge_authorized` is always false.

Candidate generation was not tested. The result does not establish performance
for people, organizations, places, or arbitrary entity metadata. The external
service receives the three supplied product fields, so use remains explicit and
opt-in.

## Artifact integrity

- Registration file SHA-256: `ecc8dc33261a2e8f315e670a2899189df93f5d64bcf5414728f5b8a97bd71504`
- Result file SHA-256: `d9d5117376ff27f0e069961f9fd60eabb670a427493224bbc848020130397534`
- Canonical result SHA-256: `0b539eca119efe2051fdee320a9268eebb85d08298ac95137afb0f33b2b5b51d`

The committed result contains pair identities, labels, typed probabilities,
decisions, token use and latency. It contains no product text or credential.
