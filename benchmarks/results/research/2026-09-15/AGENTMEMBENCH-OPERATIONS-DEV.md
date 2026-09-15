# AgentMemBench operational evidence

PRME's current-state correction removed the stale-result failure reproduced by
AgentMemBench's rapid-conflict workload. On the same 100 deterministic pairs,
the frozen pre-change revision returned the new fact first 20 times; revision
`72ba49c` returned it first 100 times.

A subsequent official-size operational run at revision `2a22039` confirmed
250/250 new-fact-first conflict results and completed the full archive workload
that exposed a USearch 2.23 deletion stall. The corrected USearch 2.26.2 build
completed all 200 archive cases and the remaining concurrency and scale phases.

## Protocol

- AgentMemBench revision:
  `186c9a54edd47aae42d8b6990520f8e902b60303`
- MemDialogue file SHA-256:
  `33632710ae6495b95724df455ff6f9947d231ee68ebc0ef10eb8291fd55ca2a6`
- Seed: `2027`
- Baseline PRME revision:
  `6172d8e46f97a5ef7031f7328ef342c8283bea77`
- Candidate PRME revision:
  `72ba49c9fe16f67daab4670e972cdb07a01d8b52`
- Official-size confirmation revision:
  `2a220393f74d686e33d114f4c8c992194f816cd4`
- Conflict workload: 100 pairs across location, role, preference, status, and
  numeric update templates; top 1 inspected from a requested top 3.
- Extended candidate-only checks: 20 users with three facts each, 20 archival
  cases, 40 concurrent writes at 1/4/8 workers, and 100 stored scale records
  with 100 reads.
- Official-size confirmation: 250 conflict pairs, 100 users with five facts
  each, 200 archival cases, 200 concurrent writes at 1/4/8/16 workers, and
  scale cohorts of 100 and 1,000 records with 100 and 200 reads respectively.

Both runs were preregistered before execution. Verification matched the exact
source hashes, parameters, clean runtime revision, retained pack generations,
and aggregate operation counts. The checked-in artifacts contain hashes and
aggregates only:

- [baseline verification](agentmembench-prme-conflict-baseline-dev100-verification.json)
- [candidate verification](agentmembench-prme-operations-dev-verification.json)
- [official-size candidate verification](agentmembench-prme-operations-official-verification.json)

## Results

| Check | Baseline | Candidate |
|---|---:|---:|
| New fact returned first | 20/100 | 100/100 |
| Stale fact returned alone | 80/100 | 0/100 |
| Both versions in top result | 0/100 | 0/100 |
| Cross-user leak rate | — | 0/20 |
| Visible before archival | — | 20/20 |
| Absent from retrieval after archival | — | 20/20 |
| Concurrent write/materialization success, 1/4/8 workers | — | 40/40 each |
| Recall@3 at 100 stored records | — | 100/100 |

The official-size confirmation produced:

| Check | Result |
|---|---:|
| New fact returned first | 250/250 |
| Stale fact returned alone | 0/250 |
| Both versions in top result | 0/250 |
| Cross-user leak rate | 0/100 |
| Visible before archival | 200/200 |
| Absent from retrieval after archival | 200/200 |
| Concurrent write/materialization success, 1/4/8/16 workers | 200/200 each |
| Recall@3 at 100 stored records | 100/100 |
| Recall@3 at 1,000 stored records | 200/200 |

The conflict improvement is 80 percentage points on this exact synthetic
development workload. The new scorer boosts only the newest candidate that
explicitly presents itself as an update, records the applied coefficient for
receipt replay, and retains the relevance floor. The signal does not prove the
new assertion is true and does not create graph supersedence; applications can
use explicit corrections when they possess authoritative evidence.

Concurrent writes remained correct at every tested worker count. The larger run
held roughly 12.8 operations per second at 1, 4, 8, and 16 workers, while
per-call latency rose with contention. This indicates serialized local write
capacity on this machine, not horizontal scaling. The scale checks use one
record per user and therefore test scoped lookup as the overall pack grows;
they do not measure dense single-tenant large-corpus retrieval.

AgentMemBench names its deletion result `audited_deletion_rate`. PRME's adapter
implements this operation through owner-scoped archival. The 200/200 official
result means the memory was visible before archival and absent from ordinary
retrieval after it; the immutable source event was not physically erased.

No LLM or judge was used for these operational phases. The raw upstream result
is retained outside the repository and bound by SHA-256 in each verification
artifact. These results support this bounded retrieval correction and the
adapter contracts. They do not establish a universal product ranking or a
controlled latency comparison with another memory system.
