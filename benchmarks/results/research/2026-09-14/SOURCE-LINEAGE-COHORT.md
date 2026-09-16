# Extracted source-lineage development cohort

This preregistered diagnostic evaluates one outcome-independent LongMemEval
development question from each of the six represented categories. The selection
rule chose the shortest question in each category and broke ties by question ID,
for 6 questions and 80 source turns. The registration fixes the dataset hash,
question IDs, budgets, model digest, and evaluation protocol before the cohort
results were known.

The raw control and final extracted run both executed from clean commit
`3de853fc567297f11ffb34059ff938bdb634947f`. The extracted profile used the
already installed `prme-qwen3.5:9b-8k` Ollama model, digest
`1805492e55c52f46b37bbf5f4f5a8db62081b6b7c4b97c05b569d7871c7c95d8`,
with temperature zero and reasoning disabled. Both arms completed all six
questions with zero evaluation errors.

## Reliability repair

The first registered extracted run at clean commit `1bc91b1` completed four of
six questions. Two source turns repeatedly returned useful claims beside one
malformed fact whose object was null. Response-level validation rejected each
complete extraction after the bounded retries.

Commit `7143a43` changed admission to validate facts and relationships
independently. Valid siblings, grounded entities, and the append-only source
event now survive a malformed proposal. The repaired cohort completed 6/6.

That repaired run initially exposed an evaluator accounting error: the product
already queued a durable raw NOTE for every source event, but the evaluator built
its source map before draining those deferred writes. Commit `3de853f` makes the
extracted protocol finish raw-source processing before retrieval and reports raw
and derived coverage separately. A source with an intentionally empty semantic
extraction is now measured through the same durable NOTE path used by the public
engine.

## Source coverage

The corrected extracted run materialized one raw NOTE for every source turn:
80/80 sources, with no source missing a retrievable node. Semantic extraction
created at least one derived node for 34/80 sources and produced 276 total nodes.
Assistant turns containing only generic advice correctly contributed raw notes
without being promoted to durable semantic claims; the two-turn
single-session-assistant case remained retrievable with recall@5 of 1.0.

| Category | Source turns | Raw source notes | Sources with derived nodes | Total nodes |
|---|---:|---:|---:|---:|
| Temporal reasoning | 12 | 12 | 6 | 42 |
| Knowledge update | 22 | 22 | 11 | 97 |
| Single-session preference | 14 | 14 | 3 | 33 |
| Single-session user | 10 | 10 | 4 | 41 |
| Single-session assistant | 2 | 2 | 0 | 2 |
| Multi-session | 20 | 20 | 10 | 61 |
| **Total** | **80** | **80** | **34** | **276** |

## Retrieval results

All PRME evidence sources fit at 2,048, 4,096, and 8,192 tokens in both arms.
Semantic nodes improved the vector and source-level RRF ranks. The product's
composite PRME ranker regressed on this small cohort because one earlier source
for the knowledge-update question moved from rank 4 to rank 7; its newer answer
source remained rank 2 and both sources remained in the 2,048-token context.
This is evidence for improving source diversity and multi-evidence ranking, not
for promoting extraction as an unconditional ranking win.

| Method | Raw MRR | Extracted MRR | Raw recall@5 | Extracted recall@5 | Raw NDCG@5 | Extracted NDCG@5 | Extracted packed recall at 2K |
|---|---:|---:|---:|---:|---:|---:|---:|
| PRME | 0.917 | 0.833 | 1.000 | 0.917 | 0.893 | 0.840 | 1.000 |
| BM25 | 1.000 | 0.917 | 0.917 | 0.917 | 0.915 | 0.854 | 1.000 |
| Vector | 0.644 | 0.778 | 0.917 | 0.917 | 0.674 | 0.788 | 1.000 |
| RRF | 0.806 | 1.000 | 0.917 | 0.917 | 0.766 | 0.936 | 1.000 |

For RRF, the paired MRR delta was +0.194 (2 wins, 0 losses, 4 ties; bootstrap
95% interval 0.000 to 0.417) and the NDCG@5 delta was +0.170 (3 wins, 0 losses,
3 ties; interval 0.025 to 0.329). With only six development questions, these
intervals describe this cohort and do not establish general or competitive
superiority.

The cohort was reused after its two initial failures were inspected, so the
successful rerun is a regression and repair check rather than held-out
confirmation. It measures retrieval of labelled evidence, not answer accuracy,
abstention quality, long-horizon consolidation, or performance against other
memory products. The next decision should be based on a separately frozen,
larger answer-level comparison and a source-diversity change tested without
tuning on these six cases.

After these clean artifacts were generated, commit `e502c31` removed a remaining
false-positive modality warning for polite requests such as “could you help.” A
live canary on the exact affected cohort turn retained the asserted fact while
unit cases continued to reject real “could” and “might” hypotheticals. That
follow-up is not included in the metrics below.

## Artifacts

- [registration](source-lineage-cohort-registration.json)
- [initial failure](source-lineage-cohort-initial-failure.json)
- [raw control](source-lineage-cohort-raw.json) — SHA-256
  `226e90f4dbdcf54193e32abecbb73f2349c90c740a0160ceba2fe323327d3b51`
- [extracted run](source-lineage-cohort-extracted.json) — SHA-256
  `b07f8a0ba2213bd027c788326959a9ce88fa4b5cd8c8c7cb023736e15dec6624`
- [paired comparison](source-lineage-cohort-comparison.json) — SHA-256
  `c97aea8ee8e68dba27e0e102d4d65cc5cc1a9a258fdc904fef623c127bc976d0`

