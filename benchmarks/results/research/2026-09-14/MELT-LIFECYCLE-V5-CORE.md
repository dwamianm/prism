# MELT lifecycle-v5 core held-out result

Date: 2026-09-14  
Status: complete registered execution

PRME passed all 20 checkpoints in the pinned MELT `lifecycle-v5-core`
held-out profile. The run used five preregistered seeds and four cases per seed:
write quality, correction, contradiction, and maintenance. The upstream MELT
report loader accepted the final report, and PRME's independent validator found
no registration, source, protocol, system, run, case, or checkpoint errors.

| Measurement | Result |
|---|---:|
| Runs | 5/5 |
| Case checkpoints | 20/20 |
| Recall@12 | 1.000 |
| NDCG@12 | 1.000 |
| Lifecycle pass rate | 1.000 |
| Run standard deviation for each metric | 0.000 |

Every axis scored 1.000 on recall@12, NDCG@12, and lifecycle pass rate in all
five runs. PRME made no model calls during ingest, structured memory writes, or
queries. It used FastEmbed `BAAI/bge-small-en-v1.5` and the MELT B2 contract.

## Reproducibility

The protocol was registered before PRME execution and bound to PRME commit
`aa1f41c86d2389056ad4fa9be9c7972bc7591cf4`, MELT commit
`47c819f417b0d81a57f781ec54d8a5cf84e0c833`, the canonical fixture hash,
materialized run hash, launcher, bridge, upstream runner, report loader, suite
manifest, seeds, configuration, and expected coverage.

The final summary SHA-256 is
`b63eddcdf38f2e246335c10668fada4eea9d83e5ad7cbf7215563fb6588da85e`.
The execution-manifest SHA-256 is
`0e730e2f8952bbafedabaedba629172d0542165726088ccf8041fce0301c7144`.
The machine-readable result records the remaining configuration, envelope, and
per-run report hashes.

MELT emitted `retrieval bypass: top_k is greater than or equal to session_count
(12 >= 1)`. The final score profile mandates `top_k=12`, while each core case
has one session. The warning is preserved in the result rather than suppressed.

## Claim boundary

This is independent deterministic evidence for PRME's correction,
contradiction, maintenance, temporal-boundary adapter, structured-write, and
retrieval lifecycle behavior on MELT's four B2 core cases. It does not measure
generated-answer quality, B3 scoped memory, autonomous memory creation, or a
head-to-head comparison with every competing memory system. The registered
LongMemEval and BEAM workflows cover separate answer and long-context retrieval
questions.

## Artifacts

- [registration](melt-lifecycle-v5-core-v1-registration.json)
- [machine-readable result](melt-lifecycle-v5-core-v1-results.json)
