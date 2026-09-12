# PostgreSQL vector eligibility and search mode

PostgreSQL now honors `PRMEConfig.vector_exact_search`, which defaults to `True`.
Exact mode first materializes rows eligible for the requested owner, scope,
lifecycle and validity window. It then orders their cosine distances, with native
UUID ordering for ties. Undefined cosine distances from zero-norm vectors are
excluded. Eligibility preserves the existing ENTITY/PREFERENCE validity exemptions.

The earlier PostgreSQL implementation ignored this option and allowed the
planner to use HNSW. Its comments incorrectly claimed SQL filters applied before
approximate candidate selection. In fact, pgvector applies filters after the
approximate index scan. A filtered query can therefore return few or no rows even
when matching rows exist. pgvector documents this behavior and its iterative-scan
and partitioning alternatives in its [filtering guide](https://github.com/pgvector/pgvector#filtering).

Five adversarial native-query tests reproduced zero results despite three eligible
neighbors when closer vectors were excluded by owner, scope, expiration, future
validity or archival. Their captured plans used HNSW. The exact path returns the
eligible neighbors and its plan cannot select HNSW before applying those filters.

```python
from prme import PRMEConfig

exact = PRMEConfig(vector_exact_search=True)   # Both local and PostgreSQL backends.
approximate = PRMEConfig(vector_exact_search=False)
```

`False` allows PostgreSQL's planner to use approximate search; it does not force
an HNSW plan. It can trade recall and repeatability for lower query cost. It can
also under-return after filters. Measure recall and latency with the actual owner
and scope distribution before choosing that mode. Exact cost grows with the
eligible corpus, so the default change may increase cost for large, broad queries.
PRME does not silently switch back to approximate mode based on corpus size.

The [authored cost probe](../benchmarks/results/recovery/2026-09-12/PG-VECTOR-SEARCH.md)
records both selective and broad queries, ordinary planner choices and an index-
construction failure. Its synthetic timings are guidance for measuring tradeoffs,
not production guarantees or a universal switch threshold.

New retrieval receipts report the configured mode in
`execution.features.vector_search.exact`. An absent or null observation in an
older/custom receipt is unknown, not proof of exact search. Existing receipt
canonical bytes and checksums are preserved. This field records the selected
policy, not a captured database execution plan.

Exact owner/scope filtering does not implement PostgreSQL named projects or
database RLS. A namespace design still needs separate tables/indexes or another
mandatory partition boundary. [Local workspaces](WORKSPACES.md) already use
physical packs; hosted PostgreSQL project routing remains separate work.
