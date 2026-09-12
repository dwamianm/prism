# PostgreSQL filtered vector search evidence

`55cf453` makes PostgreSQL honor `vector_exact_search=True`. Exact mode materializes
eligible scored rows before top-k ordering, with native UUID ordering for ties.
It excludes undefined cosine distances and records the configured mode in new
retrieval receipts. `False` permits the planner to choose approximate search.

The full frozen regression passed **2,852 tests, with 81 skips in 403.86 seconds**,
native exit 0, including live PostgreSQL, research and examples. The
[machine-readable record](pg-vector-search-55cf453.json) retains commit identities,
raw artifact/log hashes, failures and individual validation limits.

This supersedes the earlier PostgreSQL-only exception to the exact-search
configuration and corrects comments that treated WHERE clauses as pre-filtered
HNSW search. pgvector's [primary documentation](https://github.com/pgvector/pgvector#filtering)
explains that approximate filtering occurs after the index scan and can reduce
the number of returned eligible rows.

## Reproduction and contracts

Five tests populated 512 closer ineligible vectors and three eligible vectors,
then made HNSW the applicable nearest-neighbor index. On the preceding package,
every owner/scope/expired/future/archived case returned zero eligible rows for
`k=2`. Captured plans identify `idx_nodes_embedding_hnsw`. A separate tie test also
failed. These are adversarial selected-index tests, not evidence that the planner
always chooses HNSW or that every production query previously failed.

The nine final PostgreSQL contracts pass, including eligible recall, stable ties,
undefined cosine, explicit approximate behavior, engine configuration and receipt
mode reporting. An intermediate source regression passed **72 tests with one
skip** before the final native-UUID tie-order cast. The installed final Python
3.13 package passed the same selection in 14.09 seconds.
All 121 installed package Python files match `55cf453`. Existing receipt formats
retain their canonical bytes and checksums.

An initial expanded-test command named a nonexistent test file and exited 4
without running tests. The corrected selection and its result are retained
separately. Type checking with untyped bodies passed after excluding missing
third-party import types; asyncpg does not provide stubs in this environment.

## Ordinary-planner cost probe

The [registered protocol](PG-VECTOR-MODE-PROTOCOL.md) uses 384-coordinate authored
vectors with variation in only two dimensions, one owner, and a rare scope with
three eligible rows. It keeps ordinary node filter indexes and query planner
settings. After two warmups per arm, it measures 20 serial queries per arm,
alternating mode order and retaining IDs, timings and plans.

The initial 1,000-row run completed. The initial 10,000-row run failed during
index creation with `DiskFullError` when the test server could not allocate a
roughly 64-MB shared-memory segment. No query results came from that failed run.
Both sizes were then rerun with serial index construction, set only inside its
build transaction. The table uses only those completed follow-ups; the original
failure and smaller initial result remain in the record.

Server: PostgreSQL 16.15 and pgvector 0.8.6. HNSW `ef_search=40`, iterative scans
off, and sequential/index/bitmap scans enabled. Values below are median elapsed
milliseconds per call, including driver overhead, on this host.

| Other-scope rows | Query | Exact | Approximate allowed | Returned rows and observed plan |
|---:|---|---:|---:|---|
| 1,000 | Rare scope | 0.737 | 0.733 | Both returned 3; ordinary filter index, no HNSW. |
| 1,000 | All scopes | 1.225 | 1.031 | Both returned 10; sequential scans. |
| 10,000 | Rare scope | 0.781 | 0.717 | Both returned 3; ordinary filter index, no HNSW. |
| 10,000 | All scopes | 7.455 | 0.901 | Both returned 10; exact scanned eligible rows, approximate used HNSW. |

The normal-planner selective queries did not reproduce starvation: PostgreSQL
chose a complete filtered plan for this corpus. Exact mode makes that completeness
independent of whether the planner would otherwise choose HNSW. Broad-query cost
increased materially at 10,000 rows; the option and its tradeoff remain explicit.
No universal corpus-size threshold or automatic mode switch was selected.

These sparse synthetic, warm, serial measurements are not production latency,
general ANN recall, real-embedding QA quality or a competitive speed claim. Other
tests ran on the same host. Exact search does not implement PostgreSQL named
project routing, hosted grants or database RLS; those remain separate work.
