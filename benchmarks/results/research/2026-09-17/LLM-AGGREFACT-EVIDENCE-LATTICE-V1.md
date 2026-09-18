# LLM-AggreFact whole-claim evidence-lattice diagnostic

**Result: reversing the ranked evidence group produced a small post hoc gain,
but evidence-subset search failed the verification gate.** The paired ordered
control reached 75.90% balanced accuracy. Taking the lower score across both
evidence orders reached 76.25%, while the minimal-subset projections fell to
56.81%–72.69%. No policy reached 90% precision at 60% recall. This development
diagnostic is closed without a learned policy or test-cohort access.

## Motivation and protocol

The preceding
[source-token partition diagnostic](LLM-AGGREFACT-ATOMIC-PARTITION-V2.md)
showed that provider-authored claim fragments were structurally reliable but did
not improve classification. This follow-up preserved every original claim and
instead localized the evidence. It adapts the bottom-up evidence-group idea from
[Li et al., *Minimal Evidence Group Identification for Claim Verification*
(TrustNLP 2025)](https://aclanthology.org/2025.trustnlp-main.8/), while retaining
the already pinned FactCG model and ranked evidence.

For each of the 310 typed-reference v2 cases that had already been observed, the
harness scored the whole claim against:

- each of the two registered ranked evidence segments;
- both segments in ranked order; and
- both segments in reverse order.

It then evaluated six fixed score projections. `max_subset` is the direct
minimal-evidence analogue: the maximum score over either single segment or an
evidence pair. `order_robust_group` takes the lower score across the two pair
orders. No provider was called, no claim was split, and no source text was
emitted. The external test cohort was not accepted by the CLI and remained
sealed.

The ranked-order group reproduced the previously saved unsplit control with a
maximum absolute difference of `1.1324882507324219e-06`. This lies inside the
explicit `2e-5` MPS tolerance and equals the largest drift observed between the
two earlier independent control runs.

## Results

All thresholds below maximize balanced accuracy post hoc on the same 150
supported and 160 unsupported development claims.

| Projection | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|
| Ordered group control | 79.23% | 68.67% | 75.90% | 16.88% |
| Order-robust group | 78.95% | 70.00% | 76.25% | 17.50% |
| Maximum single segment | 53.18% | 78.00% | 56.81% | 64.38% |
| Maximum evidence subset | 75.00% | 66.00% | 72.69% | 20.62% |
| Order-robust maximum subset | 70.29% | 64.67% | 69.52% | 25.62% |
| Single/group consensus | 61.80% | 73.33% | 65.42% | 42.50% |

At a recall floor of 60%, order-robust grouping reached the highest precision of
the lattice policies at 82.88% precision and 61.33% recall. At a precision floor
of 90%, it reached 25.33% recall. The ordered control reached 81.51% precision
at 64.67% recall and 8.00% recall above 90% precision. No fixed projection
passed the joint target.

The ranked-evidence stage took 111.67 seconds on MPS. FactCG processed 8,698
expanded evidence/claim pairs in 333.46 seconds. This cost and the weak observed
frontier do not justify fitting a learned lattice policy to this exposed cohort.

## Decision

Reject evidence-subset maximization with the current binary FactCG scorer.
Individual segments frequently look spuriously supportive, so selecting the
highest-scoring subset sharply increases false support. Preserve the small
evidence-order result as a diagnostic, not a product policy: its 0.35-point
balanced-accuracy gain is post hoc and its false-support rate is higher.

The next capacity trial should keep the whole claim and full evidence, but use a
newer specialized open factual-consistency model that was trained for RAG
hallucination detection. Model selection must be based on license, immutable
weights and a registered, disjoint development cohort before external-test
access. No larger general-purpose decomposer is warranted.

## Artifact integrity

The source-free result is
[`llm-aggrefact-evidence-lattice-observed.json`](llm-aggrefact-evidence-lattice-observed.json).
Its file SHA-256 is
`005ba211e5babf5ac981a5a13aa6b1765143899669b8a1034cdfab8d70421370`,
and its canonical result SHA-256 is
`c786f6e9d17bd841ed8cc8fc4754cd700880b6315c4943b2df6b17f87f332951`.
It binds the control artifact and runners, contains every probability and policy
metric, states `development_only: true` and `test_accessed: false`, and contains
no claim, evidence or provider-response text.
