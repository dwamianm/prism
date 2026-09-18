# LLM-AggreFact full-source support certificate trial v1

**Result: failed structural and semantic gates; external test remained sealed.**
DeepSeek 4.1 Flash produced 782 valid proof-carrying certificates for 800 fresh
development claims. The 97.75% validity rate missed the registered 98% boundary
by two cases. More decisively, the fixed all-atoms-supported rule reached 78.31%
precision, 65.00% recall, 73.50% balanced accuracy and an 18.00% false-support
rate. Provider verification is not added to PRME.

## Motivation and protocol

The preceding decomposition experiments proved that native tool calls could
return complete source-token partitions, but passing those atoms to FactCG did
not improve classification. The registered HHEM cascade also underperformed its
FactCG ranker. This trial instead asked the fast routed provider to issue the
semantic verdict itself while code enforced a source-bound certificate.

The design follows the fine-grained, all-subclaims-supported direction described
by [HalluTree](https://aclanthology.org/2025.newsum-main.9/) while deliberately
testing a cheaper single-call representation. It is not a reproduction of
HalluTree's dual extractive/inferential pipeline or natural programs.

- Dataset: LLM-AggreFact development revision
  `981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4`
- Fresh cohort: 50 supported and 50 unsupported claims from each of eight
  datasets, excluding every identity in both earlier 1,100-claim cohorts
- RAGTruth excluded to reserve detector-training contamination; AggreFact-CNN
  and WiCE excluded because their fresh minority-label groups had fewer than 50
  rows
- Provider: Ollama cloud route `deepseek-v4.1-flash:cloud`, manifest
  `e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`,
  remote alias `deepseek-v4.1-flash`, 763B FP8 as reported by Ollama
- Evidence: every segment from the complete source document; no FactCG ranker,
  truncation, provider-generated quotation or copied claim text
- Certificate: ordered source claim-token IDs, `supported`/`contradicted`/`unknown`
  status, direct/composed support kind and ordered evidence IDs for every atom
- Deterministic validation: complete substantive-token coverage, known and
  ordered IDs, no duplicate atoms, at least two substantive tokens per atom,
  exactly one citation for direct support, at least two for composed support,
  and evidence for every supported or contradicted atom
- Decision: supported only when every atom in a valid certificate is supported;
  invalid output becomes a safe unsupported abstention

The cohort contains 2,707,452 source characters and 30,098 evidence segments.
The largest document is 132,226 characters, the largest rendered request is
146,685 characters, and the largest claim is 64 source tokens. These fit inside
the route's reported 1,048,576-token context without input truncation.

## Results

| Metric | Result | Registered gate |
|---|---:|---:|
| Valid certificates | 782/800 (97.75%) | at least 784/800 (98%) |
| Supported precision | 78.31% | at least 90% |
| Supported recall | 65.00% | at least 60% |
| Balanced accuracy | 73.50% | at least 75% |
| False-support rate | 18.00% | at most 10% |

The confusion matrix was 260 true positives, 72 false positives, 328 true
negatives and 140 false negatives. The accepted certificates contained 1,026
supported, 717 unknown and 63 contradicted atoms. Invalid certificates were
counted as unsupported, so their safety behavior is included in the metrics.

Performance varied sharply by source domain:

| Dataset | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|
| AggreFact-XSum | 83.87% | 52.00% | 71.00% | 10.00% |
| ClaimVerify | 80.39% | 82.00% | 81.00% | 20.00% |
| ExpertQA | 57.14% | 32.00% | 54.00% | 24.00% |
| FactCheck-GPT | 88.89% | 32.00% | 64.00% | 4.00% |
| LFQA | 95.35% | 82.00% | 89.00% | 4.00% |
| Reveal | 95.92% | 94.00% | 95.00% | 4.00% |
| TofuEval-MediaS | 62.50% | 70.00% | 64.00% | 42.00% |
| TofuEval-MeetB | 67.86% | 76.00% | 70.00% | 36.00% |

The protocol is strong on LFQA and Reveal but unsafe on media and meeting
summaries. Those opposing domain results rule out one global enablement policy.

## Runtime and integrity

The durable runner completed in 875.01 seconds with four concurrent calls. It
made 930 successful provider calls with no transport failure. Median call time
was 3.17 seconds and the maximum was 18.91 seconds. Of 800 cases, 693 used one
semantic attempt, 84 used two and 23 used all three; 18 of the final group
abstained after exhausting validation repairs.

The most common detected defects were schema-invalid arguments (43 attempts),
single-substantive-token atoms, direct support with multiple citations, omitted
claim tokens and out-of-order IDs. The repair loop corrected most defects, which
shows that source-bound certificates are operationally viable even though this
verifier did not meet the quality boundary.

## Decision

Reject the single-call DeepSeek support certificate as a product verifier. Its
full-source design removes the slow FactCG evidence stage and exposes useful
partial-support structure, but it does not reduce false support enough for a
memory correctness boundary. A schema-only confirmation is unwarranted because
semantic quality misses three gates independently of the two-case integrity miss.

Keep the native tool and certificate machinery as benchmark evidence. Do not
tune domain-specific thresholds on this observed cohort or open an external test
set. Further claim-verifier work needs a materially different semantic method
and fresh data; product work should now return to higher-impact memory-system
gaps rather than adding another static judge cascade.

## Artifact integrity

- Registration file SHA-256:
  `e0133d20b5cb5f0e198bbbf70d2b0871bad92903ffcbd504c778e27a6e68fc69`
- Result file SHA-256:
  `bb716a4b927132a7547a09019f53b3b1f28fc3c77610c14b15d7898f89082380`
- Canonical result SHA-256:
  `6ae23209eea5132c9b1277827678819d45f59c7611de91d0ac2cbb6af4be7695`

The source-free result is
[`llm-aggrefact-support-certificate-v1-result.json`](llm-aggrefact-support-certificate-v1-result.json).
It binds the registration, cohort, provider manifest, protocol and input bounds;
contains all 800 labels, decisions and structural counts; states
`development_only: true` and `test_accessed: false`; and contains no document,
claim, evidence or provider-response text. The private durable state retains
checksummed provider responses for restart and audit.
