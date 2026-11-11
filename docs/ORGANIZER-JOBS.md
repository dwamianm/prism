# Organizer jobs: scheduled-run readiness on PostgreSQL

Part of dwamianm/prism#518, under dwamianm/prism-monorepo#645.

The hosted service plans to run one `organize()` pass for each scope on its own.
This page records, for each registered job, what it changes, whether it is safe
in such a scheduled run, what the user-scoping and information-loss tests check,
and what the job costs on PostgreSQL at 240, 1,000 and 10,000 nodes.

`organize()` runs the jobs in `DEFAULT_JOBS` in registry order with one shared
budget. Every job except `index_compaction` reads at most 500 nodes per pass
(100 for `deduplicate` and `alias_resolve`), and `index_compaction` bounds its
stale query to 500 rows. A pass is bounded maintenance, not a complete corpus
scan. The processed counts in the timing table show that bound. With
`OrganizerConfig.incremental_enabled`, `deduplicate` and `alias_resolve` page
their changed records instead; the full-scope `alias_resolve` still examines
only the newest 100 active entities, so it cannot re-evaluate an older scope in
one pass.

## Jobs and scheduled-run status

| Job | Scheduled | What it changes | Status |
|---|---|---|---|
| `promote` | Yes | Tentative nodes older than `promotion_age_days` with enough evidence become stable | Safe |
| `decay_sweep` | Yes | Archives nodes whose virtual salience or confidence falls below the configured thresholds | Safe |
| `archive` | Yes | Archives nodes below `force_archive_salience_threshold` | Safe |
| `deduplicate` | Yes | Merges equivalent copies into one canonical node, transfers evidence and edges, records a journal | Safe |
| `alias_resolve` | Yes | Merges known name variants, or publishes an unverified alias proposal link | Safe |
| `summarize` | Yes | Publishes extractive daily, weekly and monthly SUMMARY nodes; they stay out of the default lexical and vector indexes and are read through `include_node_types` | Safe |
| `feedback_apply` | No | Retunes engine-global scoring weights from pending feedback | Excluded; weights are engine-global, and a scoped call raises before any work |
| `tombstone_sweep` | Yes | Archives nodes past their TTL and records the policy transition | Safe |
| `snapshot_generation` | Yes | Reads an entity and its owned neighborhood and builds an ephemeral snapshot | Safe; no graph write |
| `consolidate` | Yes | Publishes an extractive summary and retires only fully represented, unchanged, old, low-confidence sources | Safe |
| `index_compaction` | Yes | Evicts the stored vector and any `lexical_documents` row for non-active nodes; a node's generated `content_tsv` is not cleared | Safe on both backends; the generated lexical column is dwamianm/prism#521 |

The tests for these statements are `tests/test_organizer_job_safety.py` and
`tests/test_organizer_privacy_scoping.py`. Both run on DuckDB and, when
`PRME_TEST_DATABASE_URL` points at a live server, on PostgreSQL.

## Timing on PostgreSQL

The timing harness is `benchmarks/diagnostics/organizer_timing.py`. It creates
no roles or databases and never starts a server: point it at a temporary
PostgreSQL with `--database-url` or `PRME_TEST_DATABASE_URL`.

For each size it builds one owner's store and times every default job on its
own with the full budget, so one job's cost does not decide whether the next one
runs. It then adds ten records and times the same jobs again. The store holds
exactly the requested number of graph nodes for that owner: notes (including
TTL-expired copies), entities, facts, 40 exact duplicate nodes, 40 alias
entities, 5 consolidation candidates and 100 aged tentative facts. Jobs run in
registry order, so a later job sees the state an earlier one left. Both sizes
share one database, so the physical store holds both owners; every job is
owner-scoped, and the table reports the per-owner work. The harness uses the
store's configured embedding provider; this run used the default local
provider.

| | |
|---|---|
| Machine | MacBook Pro (Mac17,8), Apple M5 Pro, macOS 26.6.1, arm64; Python 3.11.6 |
| PostgreSQL | PostgreSQL 16.11 (Postgres.app), temporary server on 127.0.0.1:55432 |
| Harness | `benchmarks/diagnostics/organizer_timing.py` |
| Budget | 120,000 ms per job |
| Recorded at | 2026-11-09T22:26:32.311856+00:00 |
| Raw result | `benchmarks/results/research/2026-11-09/ORGANIZER-JOBS-518.json` |

| Nodes | Job | Full ms | Full processed | Incremental ms | Incremental processed |
|---|---|---|---|---|---|
| 240 | promote | 1517.11 | 100 | 3.6 | 0 |
| 240 | decay_sweep | 13.29 | 36 | 7.62 | 50 |
| 240 | archive | 11.81 | 36 | 5.94 | 50 |
| 240 | deduplicate | 2369.71 | 411 | 5076.33 | 233 |
| 240 | alias_resolve | 223.56 | 9 | 254.11 | 8 |
| 240 | summarize | 352.69 | 2 | 157.24 | 2 |
| 240 | tombstone_sweep | 16.13 | 36 | 5.45 | 45 |
| 240 | snapshot_generation | 40.36 | 13 | 57.17 | 13 |
| 240 | consolidate | 1636.9 | 9 | 1356.81 | 12 |
| 240 | index_compaction | 1.0 | 0 | 0.76 | 0 |
| 1000 | promote | 1502.48 | 100 | 8.9 | 0 |
| 1000 | decay_sweep | 20.81 | 320 | 26.86 | 379 |
| 1000 | archive | 24.36 | 320 | 26.88 | 379 |
| 1000 | deduplicate | 3508.31 | 412 | 10076.31 | 57 |
| 1000 | alias_resolve | 983.72 | 24 | 724.1 | 23 |
| 1000 | summarize | 487.53 | 2 | 192.82 | 2 |
| 1000 | tombstone_sweep | 143.25 | 377 | 15.96 | 328 |
| 1000 | snapshot_generation | 377.47 | 51 | 322.04 | 51 |
| 1000 | consolidate | 12181.95 | 147 | 11935.37 | 144 |
| 1000 | index_compaction | 1.73 | 0 | 1.57 | 0 |
| 10000 | promote | 262.21 | 100 | 7.06 | 0 |
| 10000 | decay_sweep | 16.49 | 5 | 17.31 | 97 |
| 10000 | archive | 15.78 | 5 | 19.04 | 97 |
| 10000 | deduplicate | 2153.1 | 493 | 4331.99 | 55 |
| 10000 | alias_resolve | 4201.32 | 510 | 4005.49 | 510 |
| 10000 | summarize | 323.38 | 2 | 255.17 | 2 |
| 10000 | tombstone_sweep | 17.08 | 5 | 17.32 | 15 |
| 10000 | snapshot_generation | 1739.47 | 500 | 1701.15 | 500 |
| 10000 | consolidate | 8298.63 | 240 | 5726.0 | 249 |
| 10000 | index_compaction | 2.76 | 0 | 2.57 | 0 |

`nodes_modified` is not shown; the raw result holds it for every job and size.
Each value is one sample, not an average. `processed` is the job's own
`nodes_processed`; for `deduplicate` and `alias_resolve` it counts candidate
pairs, not examined nodes.

The harness gives each job its own `organize()` call and the full budget. A real
scheduled pass shares one budget across the registry, and the CLI default is
5,000 ms. At 10,000 nodes the measured work sums to about 17.8 s, so with the
default budget the later jobs in a shared pass are partial or skipped. Size a
scheduled budget from that sum. The `index_compaction` row is 0 processed at
both sizes because the seeded store has no stale index entry; its eviction path
is covered by the unit test, not by this table.

The 10,000-node row shows the window bound directly. `decay_sweep`, `archive`
and `tombstone_sweep` examine the newest 500 active nodes, and in this corpus
the newest 500 are almost all entities and facts that those jobs skip, so only
5 notes are processed. The pass is cheap, but it does not reach the expired
notes stored earlier. Nothing is lost; the coverage gap is the first follow-up
below.

## Deduplicate and the time budget

The hosted team measured `deduplicate` at 240 memories with the production
embedding model at 20 to 26 seconds, and the total barely moved when the
`organize()` budget changed. The job has two phases. `find_duplicates` gets half
the job budget (`src/prme/organizer/jobs.py`) and checks it between nodes
(`src/prme/organizer/deduplication.py`), but each `index.search` embeds the node
and searches in one call that cannot be interrupted, so one search can
overshoot. `merge_duplicates` then gets no budget at all: it revalidates and
merges every candidate `find_duplicates` returned, one graph transaction each.
That second phase is what makes the total independent of `budget_ms`.

Nothing is lost when a run is cut short: every merge is committed on its own
and the merged node is superseded, so the next run re-finds only what is still
active. The per-scope watermark and resumable stop added in dwamianm/prism#517 (and
extended to `alias_resolve` in dwamianm/prism#523) let these jobs record where
they stopped and continue on the next run when
`OrganizerConfig.incremental_enabled` is on. A page whose search or merge
failed does not advance that watermark, and a node that fails three runs in a
row is recorded as skipped and passed, so one bad record cannot pin the scope
(dwamianm/prism#545). When the failure is a merge or resolution that raised,
the node recorded and skipped is the page node of the earliest failing pair,
not the page head, so a pair that fails on every run costs only its own node
(dwamianm/prism#546). This audit records the behavior and does not change the
merge policy.

The local timing table above adds a 240-node row so one provider measures all
three sizes. Its absolute `deduplicate` time is lower than the hosted number
because the harness uses the local default embedding provider, not the
production model.

## What the scoping tests check

`tests/test_organizer_job_safety.py` fills one owner and a second owner with the
same kinds of records, runs one job scoped to the first owner, and compares the
second owner's nodes, edges, events and index entries before and after. Every
node, edge and event must be byte for byte unchanged, and the owner's vector or
lexical entry must still be present. The same check runs on DuckDB and
PostgreSQL.

`tests/test_organizer_privacy_scoping.py` repeats the full default pass over two
owners that both hold records under their own privacy policy in one protected
store. The run is scoped to the first owner; the second owner's admitted nodes,
edges and events must be unchanged, and the first owner's records must actually
change. This covers the protected store on the DuckDB directory layout and on
PostgreSQL.

## What the no-loss tests check

For each job and each probe conversation (`member-v1`, `chat-v1`, `defects-v1`),
the test stores every turn as a note, adds one candidate for every mutating job,
and snapshots the owner's graph. After the job it checks that:

- every event that existed still exists;
- every node still exists, with the same content and node type;
- every evidence reference on every node survives;
- every edge still exists with the same provenance, validity and metadata;
- a node that stays active keeps its vector or lexical entry;
- a superseded node names a successor that exists, or a SUPERSEDES edge targets
  it, and a summary successor lists the retired node among its sources.

The jobs that change state (`promote`, `summarize`, the retiring jobs and
`index_compaction`) must also report at least one modification, so the test
cannot pass by doing nothing. `snapshot_generation` is checked by the snapshots
it generates, because its snapshots are read-only views.

## What the promotion tests check

`tests/test_organizer_job_safety.py` creates a tentative node older than
`promotion_age_days` with evidence, then promotes it three ways:

- `organize(user_id=..., jobs=["promote"])`, the explicit job;
- `MaintenanceRunner.maybe_run(user_id=...)`, the automatic pass;
- `retrieve()` followed by `MaintenanceRunner.drain()`, the scheduled request
  path that runs during retrieve and ingest.

Every path must leave the node stable on DuckDB and on PostgreSQL.

## Index compaction on PostgreSQL

Before this change `index_compaction` returned immediately on PostgreSQL,
although the job contract says it reconciles the indexes on every backend. It now
queries `nodes` for non-active nodes that still hold an `embedding` or a
`lexical_documents` row and clears both, with the stale query bounded to 500
rows. A `lexical_documents` row with no node is left alone: the PostgreSQL
lexical index also writes there before the graph row exists, so that row is not
evidence of a retired node. A test archives a node with an injected eviction
failure and checks that the next `index_compaction` pass removes the stale
vector and lexical row, with a scoped and an unscoped case.

A retired node's text is also in `nodes.content_tsv`, which this pass does not
clear. The lexical search does not filter lifecycle state yet, so a retired node
can still consume a lexical candidate slot even though hydration drops it. That
is dwamianm/prism#521. A store with `enable_multi_model_embeddings` keeps
further vectors in `node_embeddings`; that per-model index is not reconciled by
this pass and is dwamianm/prism#520.

## Excluded and follow-up work

- `feedback_apply` stays out of `DEFAULT_JOBS`. Its signals and scoring weights
  are engine-global, so a per-scope scheduled run cannot tune them without
  changing every tenant's ranking. A scoped call raises before it reads or
  consumes any signal; an unscoped operator can run it explicitly.
- The archival jobs (`decay_sweep`, `archive`, `tombstone_sweep`) read the
  newest 500 active nodes per pass. At 10,000 nodes a node far outside that
  window waits for a later pass even if its TTL has expired. The graph keeps the
  node, so nothing is lost, but the scheduled coverage is partial. Pushing the
  relevant cutoff into SQL changes which nodes each pass examines; it is
  dwamianm/prism#519 rather than changed here.
- A store with `enable_multi_model_embeddings` keeps vectors in
  `node_embeddings`. The pass selects on `nodes.embedding` while the multi-model
  index deletes `node_embeddings` rows only, so a stale node can be selected on
  every pass. That is dwamianm/prism#520; the measured table is the default
  single-model path.
- A retired PostgreSQL node's `nodes.content_tsv` is not cleared and the lexical
  search does not filter lifecycle state, so the retired text can still consume
  a lexical candidate slot. Hydration drops it, so it does not surface.
  dwamianm/prism#521 tracks adding the active-state predicate to that search.
