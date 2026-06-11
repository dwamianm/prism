# PRME Efficiency/Performance Audit (2026-06-11)

LLM calls in the retrieval hot path: **0** (query analysis is regex/dateparser; reranker disabled). Embedding calls per query: **1** (query embed; cross-scope reuses it via LRU cache). The cost problems are DB round-trip counts, full-corpus scans per query, and per-insert index persistence.

## HIGH severity

**H1. Per-query full scan of all user vectors — `storage/vector_index.py:92-141` (`_fetch_allowed_keys`)**
Every `search()` loads **every** `vector_metadata` row for the user (joined with `nodes`) into a Python dict, then post-filters the HNSW matches against it. Cost: O(total vectors for user) per vector search — and there are multiple vector searches per `store()` (novelty, re-mention, instruction reinforcement, supersedence = up to 4) and per `retrieve()` (primary + cross-scope). At 100k vectors this is a 100k-row fetch + dict build *per call*. Fix: invert the filter — `WHERE vm.vector_key IN (<matched keys>)`, bounded at `k * 3` rows.

**H2. Full HNSW index saved to disk on every insert — `vector_index.py:190` (`self._index.save(...)` inside `index()`)**
Each indexed node rewrites the entire USearch file. O(index size) disk I/O per insert; ingestion becomes O(N²) total write volume. Also: `_conn.execute` (lines 164-184), `_index.add`, and `_index.save` are all **synchronous calls directly on the event loop** — no `to_thread`, bypassing the shared `conn_lock` other components use (event-loop blocking + a thread-safety hole). Fix: save on a debounce/interval/`close()`; wrap DuckDB + USearch ops in `to_thread` under `conn_lock`.

**H3. One tantivy commit + reload per document — `storage/lexical_index.py:54-74` (`_do_index`)**
Each `index()` call creates a fresh 50MB-heap writer, adds one doc, commits, reloads. Per-doc commits are the canonical anti-pattern (segment explosion, merge churn). With Q-A pairing and fact/entity/summary indexing, one ingested message can trigger 4-6 separate commits. Fix: long-lived writer, batch commits (count/time-based), reload on search only (post-index reload is redundant — `_do_search:111` already reloads).

**H4. N+1 sequential `get_node` for candidate resolution — `retrieval/candidates.py:505-508`**
`vector_k=500, lexical_k=500` defaults (`retrieval/config.py:162-167`) mean up to ~1000 unresolved node IDs, resolved **one at a time** in a sequential `await graph_store.get_node(node_id)` loop — each an `asyncio.Lock` acquire + `to_thread` hop + single-row SQL. Same pattern in the aggregation scan (`retrieval/pipeline.py:244`: up to 9 terms × 50 hits = ~450 sequential gets) and Stage 2.5 entity expansion (`pipeline.py:298`: up to 60 more). This is the dominant per-query latency. Fix: batch `get_nodes(ids)` (`WHERE id IN (...)`) on GraphStore — one round trip instead of ~500-1500.

**H5. Graph seed discovery fetches ALL entity nodes per query — `retrieval/candidates.py:84-109`**
`query_nodes(user_id, node_type=ENTITY)` (no name filter) pulls entity nodes then substring-matches names in Python. O(entities in corpus) hydration per query, twice when cross-scope hints run. Fix: push `LOWER(content) LIKE ?` into SQL per extracted entity.

**H6. Hop-incremental neighborhood = redundant recursive CTEs without cycle guard — `candidates.py:127-161` + `duckpgq_graph.py:1188-1219`**
Per seed, three separate recursive CTE executions (hops 1, 2, 3) where the 3-hop query re-traverses everything the 1- and 2-hop queries did. The CTE is `UNION ALL` with **no visited-set/cycle prevention**: it enumerates *paths*, bidirectionally — on a dense graph row count grows ~O(avg_degree^hops) with A→B→A→B oscillation. Fix: single CTE returning `MIN(depth)` per node; dedupe/limit depth-paths inside the CTE.

**H7. Aggregation "exhaustive keyword scan" — `retrieval/pipeline.py:216-253`**
Per aggregation query: up to 5 keyword + 4 bigram lexical searches (each `limit=50`, each a tantivy reload in a thread), executed **sequentially**, plus per-hit N+1 `get_node` (H4). Bounded at 9×50, but ~9 index searches + up to 450 DB round trips per "how many…" query — and the aggregation multiplier simultaneously raises vector/lexical k to 1500/1500 (cap 2000), tripling H1/H4 costs. Fix: `asyncio.gather` the term searches, batch node resolution; consider one OR-query to tantivy.

**H8. Pinned candidates: 500-node hydration per query, filtered in Python — `candidates.py:239-283`**
Every retrieval fetches up to 500 newest active nodes (per scope!) to keep those with `salience == 1.0` or `node_type == TASK`. Runs again in the cross-scope hint pass. Fix: SQL predicate. (Correctness gap: pinned nodes older than the newest 500 are silently missed.)

**H9. Session context expansion fetches 2000 nodes per query — `retrieval/session_context.py:80-83`**
`query_nodes(user_id, limit=2000)` hydrates up to 2000 nodes per retrieval to find adjacent turns for ≤20 sessions. O(corpus) per query once corpus > 2000 (silently wrong beyond 2000, ordering is `created_at DESC`). Fix: `session_id IN (...)` filtering in `query_nodes`.

## MEDIUM severity

**M1. Cross-scope hint pass re-runs the full candidate pipeline — `retrieval/pipeline.py:424-465`**
Despite the "cheapest backends only" comment, it calls `generate_candidates` with all four backends: repeats H5, H6, H8, and H1. Roughly doubles per-query DB work when a scope filter is set. Fix: vector+lexical only, as designed.

**M2. CONTESTED annotation: 2 sequential `get_edges` per contested node + O(n) inner scan — `pipeline.py:373-398`**
Fix: one batched edge query, index `scored` by id once.

**M3. `store()` issues up to 4 vector searches + sequential write-queue hops — `storage/engine.py:506-723`**
Per stored message: event append, node create, vector index, lexical index, novelty search, instruction-reinforcement search (always-on), optional re-mention + supersedence searches; each search's matches checked with sequential per-id `get_node` (engine.py:755, 836, 900). Each search pays H1's full key scan. Q-A pairing (674-721) adds another node create + embed + HNSW save + tantivy commit per paired turn — ~2× index write cost (deliberate, benchmark-validated trade-off, but doubles H2/H3 pain).

**M4. Unbounded concurrent LLM extraction tasks — `ingestion/pipeline.py:167-171, 178-215`**
`ingest()` spawns a background task per message with no semaphore. `ingest_batch` of 1,000 → 1,000 concurrent LLM calls. Fix: `asyncio.Semaphore` (4-8).

**M5. Organizer jobs are full-corpus, restart-from-zero scans — `organizer/jobs.py`**
`_job_decay_sweep` (153), `_job_archive` (266), `_job_tombstone_sweep` (587), `_job_snapshot_generation` (492) all query then loop with one write per node. No cursor/watermark: `_query_nodes_sync` orders `created_at DESC` with LIMIT, so `MaintenanceRunner._auto_promote` (`maintenance.py:124-127`) re-examines the same newest batch every pass and **never reaches older eligible nodes** — perf AND correctness issue. Fix: keyset pagination cursor persisted between runs; batch UPDATEs.

**M6. Dedup/alias/consolidation: one vector search per node — `organizer/deduplication.py:122-135`, `alias_resolution.py:212-227`, `consolidation.py:105-118`**
Each pass re-embeds (cache may evict at 512 entries) and searches per node; each search pays H1 → O(batch × N). Budget-capped, but the budget means less work per run, not cheaper work.

**M7. Token redundancy in formatted context — `retrieval/context_formatter.py:431-497`**
Profile preamble nodes duplicated verbatim in the "Retrieved Memory" body; Q-A pair merged nodes contain full text of both turns while individual turns are also retrievable and session expansion can pull them again. Only the `aggregation` format deduplicates (`_format_aggregation:585-591`); `temporal`/`knowledge_update`/`default` have no dedup — same content can appear 2-3×. Bundles run ~5-10k tokens with ~20-30% redundancy — direct per-query cost. Fix: apply `content_key` dedup to all formats; exclude profile-rendered nodes from the body. Minor: `_format_temporal`/`_format_knowledge_update`/`_format_aggregation` sort `results` in place, mutating the caller's list.

**M8. `retrieve()` results bypass the token budget in the formatter path** — `pack_context` enforces `token_budget=4096` on the bundle, but `format_for_llm(results=response.results[:50])` formats raw scored results, not the packed bundle.

**M9. Lexical index has no working delete — `lexical_index.py:193-210` (stub)**
Superseded/archived/rolled-back nodes stay in tantivy forever; same for vector entries (`write_queue.py:174-179`): orphaned vectors permanently inflate the H1 scan and HNSW size.

**M10. `consolidate_knowledge` creates duplicate profiles on every run — `engine.py:1709-1787`**
No check for an existing SUMMARY profile for the entity; repeated calls append new profile nodes, inflating all indexes. Also O(entities × 5000 nodes) Python substring scan per run.

## LOW severity

- **L1.** `vector_index.search_by_vector:269` — HNSW search runs on the event loop, not `to_thread`. Matters at 1M vectors.
- **L2.** Embedding LRU `maxsize=512` hardcoded (`embedding.py:348`) — too small for organizer passes; make configurable.
- **L3.** `engine.store()` submits vector and lexical indexing as two sequential `write_queue.submit` awaits (`engine.py:594-605`) — gather them.
- **L4.** `_last_session_turn` dict (`engine.py:131`) grows unbounded — slow leak in long-lived servers.
- **L5.** Dedup budget check recomputes `time.monotonic()` per pair (`deduplication.py:107-110`); O(group²) pair generation could be linear.
- **L6.** Per-query retrieval-log INSERT through the global conn lock (`pipeline.py:500-507`) — serialized behind write-queue traffic.

## Already efficient (keep)

- Four candidate backends run truly in parallel (`candidates.py:432-447`).
- No LLM and no per-query re-embedding in the retrieval hot path; query analysis is pure regex/dateparser.
- `CachedEmbeddingProvider` batches misses and preserves order; FastEmbed inference offloaded via `to_thread`.
- DuckDB graph/event stores consistently use `conn_lock` + `to_thread` (vector index is the exception — H2).
- HNSW uses incremental `add()`, never full rebuilds.
- WriteQueue gives clean single-writer serialization with backpressure (`maxsize=1000`).
- Maintenance runner is cooldown- and batch-bounded; organizer jobs are time-budgeted.
- Scoring/ranking/packing are pure in-process CPU with a single shared `now` timestamp.

## Top 5 by impact-per-effort

1. Batch `get_nodes(ids)` API + use in candidates.py/pipeline.py (H4) — removes ~500-1500 round trips/query.
2. Invert `_fetch_allowed_keys` to `WHERE vector_key IN (matches)` (H1) — O(N)→O(k) per vector search.
3. Debounced HNSW save + long-lived tantivy writer with batched commits (H2, H3) — ingestion O(N²)→O(N) I/O.
4. Cross-scope pass: vector+lexical only (M1) — halves per-query work under scope filters.
5. SQL-side pinned filter + session_id filter in `query_nodes` (H8, H9) — removes ~2500 node hydrations/query.
