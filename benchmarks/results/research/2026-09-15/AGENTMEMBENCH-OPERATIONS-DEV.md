# AgentMemBench operational development diagnostic

PRME's current-state correction removed the stale-result failure reproduced by
AgentMemBench's rapid-conflict workload. On the same 100 deterministic pairs,
the frozen pre-change revision returned the new fact first 20 times; revision
`72ba49c` returned it first 100 times.

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
- Conflict workload: 100 pairs across location, role, preference, status, and
  numeric update templates; top 1 inspected from a requested top 3.
- Extended candidate-only checks: 20 users with three facts each, 20 archival
  cases, 40 concurrent writes at 1/4/8 workers, and 100 stored scale records
  with 100 reads.

Both runs were preregistered before execution. Verification matched the exact
source hashes, parameters, clean runtime revision, retained pack generations,
and aggregate operation counts. The checked-in artifacts contain hashes and
aggregates only:

- [baseline verification](agentmembench-prme-conflict-baseline-dev100-verification.json)
- [candidate verification](agentmembench-prme-operations-dev-verification.json)

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

The conflict improvement is 80 percentage points on this exact synthetic
development workload. The new scorer boosts only the newest candidate that
explicitly presents itself as an update, records the applied coefficient for
receipt replay, and retains the relevance floor. The signal does not prove the
new assertion is true and does not create graph supersedence; applications can
use explicit corrections when they possess authoritative evidence.

Concurrent writes remained correct at every tested worker count. Throughput was
roughly 12 operations per second at 1, 4, and 8 workers, while per-call latency
rose with contention. This indicates serialized local write capacity on this
machine, not horizontal scaling. The scale check covered only 100 records with
one record per user and therefore tests scoped lookup rather than large-corpus
index behavior.

AgentMemBench names its deletion result `audited_deletion_rate`. PRME's adapter
implements this operation through owner-scoped archival. The 20/20 result means
the memory was visible before archival and absent from ordinary retrieval after
it; the immutable source event was not physically erased.

No LLM or judge was used for these operational phases. The raw upstream result
is retained outside the repository and bound by SHA-256 in each verification
artifact. These results support this bounded retrieval correction and the
adapter contracts. They do not establish a universal product ranking or a
controlled latency comparison with another memory system.

