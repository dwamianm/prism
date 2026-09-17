# Jev generic product-alignment development v1

**Result: passed development.** Pinned `jev-1.13.0` improved candidate-pair
classification over normalized exact-name matching on a fresh balanced sample
of 400 DeepMatcher Amazon-Google training pairs. This result unlocks the
registered held-out confirmation. It does not authorize a product integration
or an automatic identity merge.

| Method | Precision | Recall | Accuracy | False-positive rate |
|---|---:|---:|---:|---:|
| Exact normalized name | 72.73% | 4.00% | 51.25% | 1.50% |
| Jev score >= 1.5 | 94.38% | 75.50% | 85.50% | 4.50% |

Jev recovered 151 of 200 true matches with nine false proposals. The exact-name
baseline recovered eight true matches with three false matches. The Jev route
passed every preregistered completeness, precision, recall, false-positive,
relative-gain and latency gate.

All 400 responses were valid and none required a retry. The run used 203,520
input tokens and 28,400 output tokens. Median request latency was 0.170 seconds
and p95 was 0.301 seconds with six concurrent requests.

The intended product boundary is an optional advisor over caller-supplied
software-product pairs. A positive score can recommend an unverified proposal;
it can never merge identities. Candidate generation and other entity domains
remain unevaluated.

## Artifact integrity

- Registration file SHA-256: `f34654aefc3a230a2ccaa91c1af88f718000bb1a80a6dee0ce5d1007c8169aef`
- Result file SHA-256: `b17ab494bdab772656611338c9e780722012f2fc491a0ec921245023d9f9c8ff`
- Canonical result SHA-256: `df3b1b2bfedda036f7244c71bd36aadadcf017099ae2b2104db03e805ee5a789`

The committed result contains pair identities, labels, typed probabilities,
decisions, token use and latency. It contains no product text or credential.
