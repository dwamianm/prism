# Jev retrieval-rerank development trial v1

**Result: rejected.** Pinned `jev-1.13.0` reduced exact-source hit@1 from
80.50% to 71.50% and MRR from 0.8702 to 0.8081 on a fresh 200-case
AgentMemBench development cohort. A post-hoc rank-one answer-support diagnostic
confirmed that this was a real utility regression rather than harmless source
substitution. No confirmation cohort was accessed and no product integration is
authorized by this result.

## Bound protocol

- Dataset: the verified 1,000-case PRME AgentMemBench retrieval result at
  upstream revision `186c9a54edd47aae42d8b6990520f8e902b60303`
- Cohort: 200 deterministically selected cases after excluding the previously
  examined 100-case development result
- Provider: TypeSafe AI, pinned model `jev-1.13.0`
- Input: query and PRME's fixed five retrieved memories
- Questions: five independent typed Nouls asking whether each indexed candidate
  directly supplies the information needed by the query
- Ordering: descending Noul probability with original rank as the stable tie
  breaker
- Candidate generation and candidate membership remained unchanged
- Gates: at least 5 points hit@1 gain, 0.03 MRR gain, ten net top-one gains,
  unchanged recall@5, complete valid responses, preserved candidate sets, and
  at most one second p95 request latency
- Query and memory text, benchmark source data, and API credentials are absent
  from the committed Jev artifacts

## Frozen results

| Metric | PRME order | Jev order | Change |
|---|---:|---:|---:|
| Exact-source hit@1 | 80.50% (161/200) | 71.50% (143/200) | -9.00 points |
| Exact-source MRR | 0.8702 | 0.8081 | -0.0621 |
| Exact-source recall@5 | 96.50% | 96.50% | 0 |

Jev moved nine previously non-top sources to rank one and displaced 27 correct
rank-one sources, for 18 net losses. It passed response validity, candidate-set
preservation, recall@5 noninferiority, and latency gates. It failed every
registered ordering-quality gate.

All 200 Jev responses were valid and none required a retry. The run used
159,176 input tokens and 18,800 output tokens. Median request latency was 0.178
seconds and p95 was 0.384 seconds with six concurrent requests. Jev was fast
enough for an optional reranking stage; speed did not compensate for worse
ordering.

## Post-hoc answer-support diagnostic

The benchmark's headline retrieval measure judges whether any of five memories
supports a reference answer, while the frozen Jev trial used exact source
identity to measure order. To test whether Jev was choosing different but still
answer-bearing rank-one memories, the same pinned local Qwen 35B A3B judge used
by the verified AgentMemBench run evaluated the original and Jev rank-one
candidate independently against the query and reference answer.

| Rank-one candidate | Supported answer | Rate |
|---|---:|---:|
| PRME original | 162/200 | 81.00% |
| Jev | 144/200 | 72.00% |

The paired diagnostic observed eight gains and 26 losses. It was post-hoc and
is diagnostic rather than confirmation evidence, but it agrees closely with the
registered exact-source result.

A selectively applied policy discovered after the run promoted only five cases
and produced three exact-source gains with no exact-source loss. Under the
answer-support judge it produced one gain and one loss. That discrepancy shows
why the favorable post-hoc source metric is insufficient for product use.

## Decision

Do not add Jev retrieval reranking to PRME and do not spend the untouched cohort
on confirmation. The direct reranker failed by a wide margin, and the selective
variant had no net answer-support benefit. Jev's low latency and typed outputs
remain useful capacity findings, but this task does not close a demonstrated
PRME gap.

Combined with the claim-verification rejection and the held-out entity-alignment
precision failure, the current evidence does not support a Jev runtime
dependency in PRME. The worktree remains an evaluation record only.

## Artifact integrity

- Registration SHA-256: `766ab45aa20a085479552b25bba6ed9572afaa8d66cadeea19192f36c57095a7`
- Result file SHA-256: `a4395513bf1fd0b5bf510f5b4abc5079ecf441825a6e05293bad3ab2dd7d8088`
- Canonical result SHA-256: `2cffc404daf39941f094100eae673bc2308d992f226c0451c248706203ef67d6`
- Rank-one diagnostic file SHA-256: `75b08abaf52427416c4aa5a2682604d13e86dcae41ab224a0adbeb990ccb6f2f`
- Rank-one diagnostic canonical SHA-256: `6a8fbe7755182c4f53174e7fe3cfa81becf01bbd9d96e9bc8d83871c03590f10`

The result contains case identifiers, ranks, probabilities, orderings, token
use, and latency. The diagnostic contains case identifiers and boolean verdicts.
Neither contains query text, memory text, benchmark answers, or credentials.
