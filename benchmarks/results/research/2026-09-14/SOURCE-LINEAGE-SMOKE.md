# Extracted source-lineage smoke

This clean end-to-end smoke evaluates one seeded LongMemEval oracle question
after ingesting all 36 source turns through the public synchronous `ingest()`
path. Both reports were declared before model calls, ran from clean commit
`64b417f67ee3cfb04343e44477584f6bc733dc34`, and completed with zero errors.

The raw control materialized one note for each of the 36 turns in 3.629 seconds.
The extracted profile used the already installed `prme-qwen3.5:9b-8k` Ollama
model with reasoning disabled and materialized 101 derived nodes from 14
information-bearing turns in 328.678 seconds: 60 entities, 11 facts, 27
preferences, and 3 decisions. Turns without admitted durable claims correctly
remain in the append-only event log even when they do not produce graph nodes.

The question requires evidence from two different sessions. Extracted PRME
retrieval ranked both sources in the first five results and retained both in the
packed context at 2,048, 4,096, and 8,192-token budgets.

| Method | Raw MRR | Extracted MRR | Raw NDCG@5 | Extracted NDCG@5 | Extracted recall@5 |
|---|---:|---:|---:|---:|---:|
| PRME | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| BM25 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Vector | 0.333 | 1.000 | 0.571 | 1.000 | 1.000 |
| RRF | 1.000 | 1.000 | 0.920 | 1.000 | 1.000 |

This is a pipeline and provenance diagnostic, not a product-quality or
leadership benchmark. With one temporal-reasoning question, it cannot estimate
general retrieval quality, answer accuracy, abstention, or statistical
uncertainty. The later [multi-category cohort](SOURCE-LINEAGE-COHORT.md) also
found that this smoke counted derived nodes before the evaluator drained and
mapped the product's deferred raw NOTE path. Its derived-node counts remain
valid; use the cohort's 80/80 result for corrected durable source coverage.

Artifacts:

- [raw control](source-lineage-smoke-raw.json) — SHA-256
  `be24c550db9f8651ae4d081923f6e5203c8edce888020a9dc0f4d772ce270678`
- [extracted run](source-lineage-smoke-extracted.json) — SHA-256
  `f3a08097714b5493d71926862b25435287b17148d1e118eb59399b8b7e834177`
- [comparison](source-lineage-smoke-comparison.json) — SHA-256
  `28e7c7971e3d6d913fd77ad4bcde5cf378bc36fd38eaa467b3002a44fab76e18`
