# Bounded graph walk, same output (2026-11-12)

Task 21, prme-manager. Branch `bounded-graph-walk-2026-11`, rebased on `origin/main` at `81e45546` (0.17.0a7 plus
address-first memory, #561). The engine change is in this branch; this file reports what was measured.

## Answer

**The graph candidate stage now walks the graph once from all seeds, and `retrieve()` gives the same output.**

- At 20,500 stated facts one `retrieve()` takes **574 ms** (median of 15 reads; p95 661 ms), against
  **18,965 ms** on `origin/main` measured the same way (P3 measured 18,519 ms). Bar (b), under 1 s, is met.
- Every read on every measured pack gives the same response, ranked ids, packed ids, context text and stored
  receipt as on `origin/main`: 460 of 460 reads on six packs, and 37 of 37 probe receipts on the three probe packs.
- On the 10 LoCoMo `ingest()` packs the median read goes from 372 to 658 ms on `origin/main` to
  193 to 432 ms (paired, same questions, same machine, order swapped every other pack): about half on every pack,
  and the slowest p95 goes from 1,655 ms to 600 ms.
- No setting changes, no default changes, no stored memory is rewritten, and no `DERIVATION_VERSION` bump: the
  change is in how the graph stage reads, not in what any source derives.

## What changed

P3 (`research/read-scaling-2026-11/REPORT.md`, branch `research-read-scaling-2026-11`, `52af09e5`) found that at
20,500 stated facts 99% of a read was `_generate_graph_candidates`: a question with 44 seed entities ran one 3-hop
walk per seed through the place hubs, made about 500,000 rows into `MemoryNode` records, and kept 150.

Two cycles were run (the brief allowed two).

**Cycle 1: one walk from all seeds.**

- `walk_from_seeds` on DuckDB and PostgreSQL (`src/prme/storage/seed_walk.py`, the same SQL on both but for how
  each reads JSON and times). One recursive query starts from every seed at once and returns each reached node
  once, as a small `WalkedNode` row (id, type, owner, scope, confidence, three times, and metadata for entities and
  summaries only) with its fewest hops.
- A seed's own per-seed walk leaves the seed out, so a seed is in the result only when another seed reaches it.
  A walk from all seeds reaches every seed at 0 hops, so the seeds' depths are found by meeting in the middle on
  the seeds' one-hop sets: 1 when another seed's step reaches it, 2 when two seeds' steps reach the same node, 3
  when a step links a node one hop from it to a node one hop from another seed.
- Each step keeps the old rules: an edge either way, never an alias proposal no one accepted, never onto a node
  another user owns (#438); a node that no longer exists is passed, as `get_neighborhood_with_depth` passes it.
  Only the returned nodes must be the user's, current, and valid at `valid_at` when one is given.
- `_generate_graph_candidates` runs its filters (user, unindexed summary, scope, time window) on those rows,
  sorts and caps as before, and only then reads full records for the kept candidates (`get_nodes`, at most
  `packing.graph_max_candidates`).
- `_seed_reach` still gets every reached node with its fewest hops and the entity metadata it reads to find
  owner entities, so its owner rule and its `SEED_REACH_MAX_NODES` = 5,000 limit act exactly as before
  (prme-manager's trap note). P4's finding that the limit already turns the seed count off on large chat stores
  is a separate follow-up and is unchanged.
- Ties: graph candidates with the same confidence and creation time are now ordered by node id after those two,
  so the order no longer depends on the order a walk returned rows in. No measured pack has such a tie (every
  current node has a distinct `(confidence, created_at)` in all six packs), so this changed no output here.
- `get_edges` on DuckDB writes canonical UUID ids into the SQL as literals instead of binding each one.
  `_uuid_literals` writes a value only when it is exactly `str(UUID(value))` (36 lowercase hexadecimal digits and
  hyphens), so the text cannot carry SQL; anything else keeps the bound parameters.
- A store without `walk_from_seeds`, such as the privacy read view (`AdmittedGraphView` forwards no method it
  does not know), keeps the per-seed loop. That is the one capability check (`_walks_once` in `candidates.py`).

Cycle 1 gave 1,010 ms at 20,500 (median, load 2.3): not under 1 s. Timing each part over 10 reads at 20,500
(`scripts/stage_one.py`, load about 7) put the rest in `_seed_reach` (574 ms, of which `get_edges` 444 ms),
`get_nodes` (182 ms, two calls), vector search (306 ms, in parallel) and the walk itself (139 ms).
`scripts/edges_cost.py` split the seed reach edge read: 255 to 279 ms of SQL and 111 to 210 ms building about
45,000 `MemoryEdge` records, where only the two ends of each edge are used.

**Cycle 2: smaller reads after the walk.**

- `walk_links` on DuckDB and PostgreSQL returns the links `_seed_reach` steps along as two ids each: the edges
  `get_edges(node_ids=...)` returns, less an edge from a node to itself and an alias proposal no one accepted,
  which `_seed_reach` dropped anyway. `scripts/links_proto.py` checked the SQL shape on five 20,500 reads:
  the same link set as the old Python filter, 216 to 224 ms against about 450 ms. An `unnest` or join shape was
  ten times slower on DuckDB 1.4.4 (2.3 to 2.5 s) and was not used.
- DuckDB's `get_nodes` writes canonical ids as literals too: 900 ids took 57 to 62 ms bound and 10 to 12 ms as
  literals (`scripts/nodes_proto.py`), with the same rows.

## Bar (a): same outputs

Before is `origin/main` at `81e45546` in a separate worktree, after is this branch, both with `uv sync --all-extras`.
Every pack was copied before it was opened, because a read writes a receipt into the pack; the saved packs were
never opened. Providers were blocked in every run, and the probes ran from the extraction cache only.

`scripts/same_outputs.py` reads each pack and records, per question, the normalized response (durations dropped,
request id replaced), the ranked node ids, the packed ids and the SHA-256 of the context text, then every stored
retrieval receipt (request id replaced, `source_files_sha256` masked, durations dropped). `scripts/compare_outputs.py`
compares the two sides read by read.

| Pack | Reads | Ranked | Packed | Context | Response | Receipts | Receipt fields |
|---|---|---|---|---|---|---|---|
| Stated facts, 400 (P2 `m4/current-a/400`) | 25 | 25 | 25 | 25 | 25 | 25/25 | 25 |
| Stated facts, 4,000 (P2 `m4/current-a/4000`) | 25 | 25 | 25 | 25 | 25 | 25/25 | 25 |
| Stated facts, 20,500 (P2 `m3-pack`) | 25 | 25 | 25 | 25 | 25 | 25/25 | 25 |
| LoCoMo conv-26 (`mid-tier-v1`, `main-0.17.0a6-locomo`) | 152 | 152 | 152 | 152 | 152 | 152/152 | 152 |
| LoCoMo conv-30 | 81 | 81 | 81 | 81 | 81 | 81/81 | 81 |
| LoCoMo conv-41 | 152 | 152 | 152 | 152 | 152 | 152/152 | 152 |

Each column counts reads that are identical on both sides. The table is the cycle 2 result
(`bounded-graph-walk/same-outputs-cycle2.json`); cycle 1 gave the same counts (`same-outputs-cycle1.json`).

- Stated packs: all 25 questions of P2's `m4_compare.questions`, at the pack's reference time, with the
  opportunistic organizer off.
- LoCoMo packs: the conversation's questions from the harness (`question_rows("locomo")`) under `gate_config`,
  at the manifest's reference time and user.
- The brief said `origin/main` could not open the P2 packs and asked for a scratch cherry-pick onto
  `address-first-memory`. After #561 merged, `origin/main` opens them, so both sides ran plain `origin/main`
  code and no cherry-pick was used.

**Probes.** The defect, chat and member probe builds of today (`data/chat-probe-v1/<conversation>/rc-M`) were copied
and measured on both sides (`chat_probe measure`, cache only; `scripts/probes_samepack.sh`). Their last 7, 18 and
12 stored receipts are identical once the request id and source hashes are masked, and so are the logged fields:
7/7, 18/18 and 12/12 (`probe-receipts-cycle2.txt`). The probe reports are the same apart from times. The raw
receipts differ only by those masked values.

## Bar (b): speed

Machine: Apple M5 Pro, 48 GB, macOS 26, Python 3.11.6, DuckDB 1.4.4. Other sessions ran at the same time; the
1-minute load average at each run's start is in the tables. The two sides of a pair ran one after the other.

**Stated facts** (`scripts/speed_stated.py`): one warm-up read, then P2's five questions three times over
(15 reads), each pack a scratch copy, organizer off. The graph column is `_generate_graph_candidates`, wall time.

| Stored | `origin/main` median / p95 | Graph | This branch median / p95 | Graph | P3 (median) |
|---|---|---|---|---|---|
| 400 | 377 / 443 ms | 224 ms | 205 / 230 ms | 93 ms | 372 ms |
| 4,000 | 1,353 / 1,457 ms | 1,191 ms | 278 / 307 ms | 173 ms | 1,340 ms |
| 20,500 | 18,965 / 20,133 ms | 18,772 ms | 574 / 661 ms | 429 ms | 18,519 ms |

Load at start: `origin/main` 5.5, this branch 3.2. Cycle 1 alone gave 244, 396 and 1,010 ms (median, load 2.3;
`speed-stated-walk-cycle1.json`).

**LoCoMo `ingest()` packs** (P4's `chat_read_cost.py`, branch `research-chat-read-cost-2026-11`, `b00d043c`,
unchanged): per pack one warm-up read and 20 timed reads of the conversation's own questions, at the pack's
reference time, on scratch copies of `/tmp/p4/packs/locomo` (the same `main-0.17.0a6-locomo` builds). Both sides
ran the same questions; `scripts/chat_pairs.sh` ran `origin/main` first on even packs and this branch first on odd
ones. P4's walk columns time `_get_neighborhood_with_depth_sync`, which this branch no longer calls, so only the
read and graph stage times are compared.

| Pack | `origin/main` median / p95 | Graph | This branch median / p95 | Graph | P4 base run 2 median |
|---|---|---|---|---|---|
| conv-26 | 372 / 485 ms | 214 ms | 209 / 255 ms | 88 ms | 313 ms |
| conv-30 | 424 / 704 ms | 300 ms | 193 / 234 ms | 76 ms | 380 ms |
| conv-41 | 550 / 708 ms | 354 ms | 275 / 316 ms | 126 ms | 434 ms |
| conv-42 | 658 / 1,232 ms | 432 ms | 432 / 600 ms | 201 ms | 426 ms |
| conv-43 | 504 / 728 ms | 328 ms | 243 / 295 ms | 110 ms | 462 ms |
| conv-44 | 548 / 659 ms | 377 ms | 260 / 388 ms | 115 ms | 472 ms |
| conv-47 | 471 / 969 ms | 297 ms | 249 / 282 ms | 111 ms | 424 ms |
| conv-48 | 491 / 729 ms | 338 ms | 240 / 333 ms | 112 ms | 432 ms |
| conv-49 | 396 / 643 ms | 235 ms | 211 / 260 ms | 96 ms | 338 ms |
| conv-50 | 495 / 1,655 ms | 327 ms | 315 / 456 ms | 137 ms | 378 ms |

- 20 timed reads per side per pack, the same 20 questions on both sides (`locomo-pairs.json`, built by
  `scripts/locomo_pairs.py`).
- The median of the per-pack ratio (this branch over `origin/main`) is 0.51. The graph stage's share of a read goes
  from 60 to 70% to 41 to 46%.
- The 1-minute load average was 6.6 to 14.1 during these runs, higher than in P4's runs, so `origin/main` here is
  slower than P4's base run 2 (last column). The pairs ran one right after the other, so both sides saw about the
  same load.
- Both sides report commit `81e45546`: this branch's side ran from the working tree of this change on top of it,
  before it was committed.

## Bar (c): tests

All runs with providers blocked (`OPENAI_API_KEY=`, `ANTHROPIC_API_KEY=sk-ant-blocked`, both base URLs at
`127.0.0.1:9`, `PRME_EXTRACTION_API_KEY=`).

- New tests: `tests/test_seed_walk.py` builds random graphs (hubs, cycles, another user's nodes, unaccepted
  alias proposals, retired nodes, validity windows, a dangling edge) and checks that `walk_from_seeds` returns
  the nodes of the per-seed walks at the same fewest hops for 1, 2, 5 and 12 seeds, 1 to 3 hops, with and
  without `valid_at`; that `walk_links` gives the links of the edges `get_edges` gives; and that the candidate
  stage gives the same candidates, records, proximities and seed reach counts with one walk as with the per-seed
  loop, at caps of 8 and 150, with scope and time filters. It also checks a seed reached only from another seed,
  no step onto another user's node, and that a store without the walk keeps one walk per seed.
- `tests/test_pg_seed_walk.py` runs the same walk, link and candidate checks on PostgreSQL. It is skipped
  without `PRME_TEST_DATABASE_URL`. **It was run here on a local PostgreSQL 16.11 database and passed**, and CI
  runs it on Python 3.11 to 3.13.
- Graph, determinism and receipt set (15 files: the two above, `test_neighborhood_depth`,
  `test_graph_neighborhood_tenancy`, `test_graph_seed_count`, `test_graph_fusion`, `test_owner_scoped_graph_reads`,
  `test_mention_links`, `test_structured_assertions`, `test_determinism_rebuild`,
  `test_retrieval_filter_forwarding`, `test_product_candidates`, `test_default_structure_options`,
  `test_hosted_profile`, `test_packing_receipt_compatibility`), with the local PostgreSQL database: 348 passed,
  6 skipped (the 6 are one DuckDB-only case in `test_graph_fusion.py:146`).
- Privacy tests (`tests/test_privacy_*.py`, cycle 1 code): 434 passed, 172 skipped, 2 xfailed. Cycle 2 does not
  touch the privacy read path: the privacy view has no `walk_from_seeds`, so it keeps `get_edges`.
- Full suite on the final revision (`pytest tests/ -n auto --dist worksteal`, with the local PostgreSQL database
  through its Unix socket): 9,105 passed, 230 skipped, 128 xfailed, 16 failed. None of the 16 comes from this change.
  15 are PostgreSQL privacy tests (`test_privacy_restore`, `_migration`, `_export`, `_portability_cli`,
  `_format_compatibility`, `_quality_sets`) that need a TCP URL. Run again, 12 passed with
  `postgresql://127.0.0.1:5432/<database>`, and the other 3 (restore tests, which give the native PostgreSQL tools a
  user and a password) passed with `postgresql://<user>:<password>@127.0.0.1:5432/<database>`. The 16th,
  `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`, fails on `origin/main` too,
  because both run from a git worktree, not a plain clone. `tests/test_lexical_shutdown.py`, run alone as CI runs it:
  10 passed. `ruff check`: clean.
- One flaky result was seen before the rebase:
  `tests/test_graph_neighborhood_tenancy.py::test_neighborhood_route_reads_a_protected_store_through_the_admitted_view[duckdb]`
  failed once and passed on rerun and in every later run. It reads through the privacy view, which this change
  leaves on the per-seed loop.

## Limits

- Times are wall times on a shared laptop with other sessions running; medians are less sensitive to that than
  p95. The stated-fact sides ran minutes apart, not interleaved.
- Bar (a) covers 6 packs and 3 probe packs. The random graph tests cover the cases those packs lack (another
  user's nodes, unaccepted aliases, validity windows, dangling edges, retired nodes).
- PostgreSQL speed was not measured. PostgreSQL equivalence was checked locally and rests on CI after that.
- Seed reach on large chat stores: P4 found the 5,000 node limit already turns `scoring.rrf_graph_seed_count` off
  on large chat stores. This change keeps that behavior exactly; changing it is a separate follow-up.

## Files

- `bounded-graph-walk/same-outputs-cycle1.json`, `same-outputs-cycle2.json`: bar (a) counts per pack.
- `bounded-graph-walk/probe-receipts-cycle2.txt`: probe receipt comparison.
- `bounded-graph-walk/speed-stated-*.json`: stated-fact timings with every read's seconds.
- `bounded-graph-walk/locomo-pairs.json`: LoCoMo pair timings.
- `bounded-graph-walk/scripts/`: the measurement scripts as run. They name local paths (`/tmp/task21`,
  `~/Sites/...`); the per-read outputs (several GB of contexts and receipts) were not kept in the repository.

## Commands

```sh
# Bar (a), from each worktree root (origin/main and this branch), then compare.
PYTHONPATH=. uv run python benchmarks/results/research/2026-11-12/bounded-graph-walk/scripts/same_outputs.py --side main
PYTHONPATH=. uv run python benchmarks/results/research/2026-11-12/bounded-graph-walk/scripts/same_outputs.py --side walk
uv run python benchmarks/results/research/2026-11-12/bounded-graph-walk/scripts/compare_outputs.py /tmp/task21/a
zsh benchmarks/results/research/2026-11-12/bounded-graph-walk/scripts/probes_samepack.sh <worktree> <side>
uv run python benchmarks/results/research/2026-11-12/bounded-graph-walk/scripts/probe_receipts.py main walk

# Bar (b)
PYTHONPATH=. uv run python benchmarks/results/research/2026-11-12/bounded-graph-walk/scripts/speed_stated.py --label <side>
zsh benchmarks/results/research/2026-11-12/bounded-graph-walk/scripts/chat_pairs.sh

# Bar (c)
OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY= PRME_TEST_DATABASE_URL=<local database> \
  uv run pytest -q tests/test_seed_walk.py tests/test_pg_seed_walk.py ...
```
