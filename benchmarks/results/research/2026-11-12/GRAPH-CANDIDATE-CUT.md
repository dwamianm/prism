# Graph candidates in one query, same output (2026-11-12)

Task 24, prme-manager. Branch `graph-candidate-cut-2026-11`, cut from `main` at `d95f5c41` (#564 merged, so the
`read_profile=robot` side applies). The engine change is in the branch; this file reports what was measured.
The scripts and the compact results are in `graph-candidate-cut/`.

## Answer

**The graph candidate stage now reads its candidates in one store query, and `retrieve()` gives the same output.**

- Every read measured gives the same ranked ids with scores, packed ids, context text, tokens, candidate counts and
  stored receipt (request id and `source_files_sha256` masked) as `main` at `d95f5c41`: 120 of 120 reads on two
  packs under three settings, 37 of 37 probe receipts, and the offline evidence gate with every first-table row
  +0.0 and 0 wins, 0 losses.
- On the m3 pack (20,500 stated facts) a read's median goes from **405.3 ms** on `main` to **271.7 ms** (p95 527.5 to
  395.4 ms), and the median paired saving is **109.6 ms** (bar: at least 80 ms). The 12 of 20 questions with graph
  candidates save 125.4 ms at the median; the 8 with none save 0.2 ms. The flat pack is not slower: median paired
  saving +0.5 ms (p50 59.6 to 58.8 ms).
- No setting changes, no default changes, no stored memory rewritten, no `DERIVATION_VERSION` bump: the change is in
  how the graph stage reads, not in anything a source derives.

## What changed

P8 (`research/graph-read-cost-2026-11/REPORT.md`, branch `research-graph-read-cost-2026-11`, `b3e40925`) found that
on the m3 pack (20,500 stated facts) a question with 25 seeds reaches about 38,000 nodes, and the stage made each one
into a `WalkedNode` in Python to filter, sort and keep 150. The new store method `walk_candidates`
(`src/prme/storage/seed_walk.py`, on DuckDB and PostgreSQL) runs the same walk (`seed_walk_sql`) and does that work
in the same SQL:

- It leaves out an unindexed hierarchical summary with the condition `unindexed_summary_sql` writes, but only when the
  summary's publication kind is unset or hierarchical. `is_unindexed_summary` reads the kind alone when it is set,
  so the stage kept a summary of another kind with the old hierarchical fields, and so does the query
  (`test_the_query_leaves_out_the_summaries_the_stage_leaves_out`, 9 metadata shapes).
- It keeps only the requested scopes, and, except for an entity or a preference, a validity window that does not end
  at or before `time_from` and does not start after `time_to`.
- It orders by the stage's key (confidence, then creation time, newest first, then id) and marks the first
  `packing.graph_max_candidates`. Ids sort as bytes, as Python compares strings: DuckDB VARCHAR order is byte order,
  and PostgreSQL sorts them with `COLLATE "C"`.
- It returns five narrow columns per reached node: id, fewest hops, place in the order (or NULL), whether it is an
  entity and, for an entity, its metadata. The stage reads full records only for the kept ids and gives
  `_seed_reach` each reached node's type and an entity's metadata, which is all it reads.

A store without the method keeps the earlier path, unchanged (`_walked_candidates`). That includes the privacy read
view (`AdmittedGraphView`), so a protected read is unchanged. A time bound without a time zone also keeps the earlier
path: there it raised `TypeError`, which the candidate gather records as a failed graph path, and it still does.
The API and MCP surfaces pass times with a time zone. Seeds, record reads, `_seed_reach` and the privacy fences are
unchanged. One query and one store method per dialect; no store shape needed a special case.

## Bar 1: same output

Each side runs in its own process from its own tree (`prme.__file__` checked), on its own fresh copy of the pack,
through `gate_config` (current defaults, environment ignored, organizer off) with `--set`. 20 questions (P8's),
reference time 2026-11-05 UTC, user `robot-1`. `graph-candidate-cut/same-output/`.

| Pack | Setting | Ranked | Packed | Context | Masked stored receipt | Log fields | Replay | Raw receipt |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| m3 (20,500 stated facts) | `memory_mode=address_first` (default) | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| m3 | `memory_mode=classic` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| m3 | `read_profile=robot` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| n20000-s1 (flat) | `memory_mode=address_first` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| n20000-s1 | `memory_mode=classic` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |
| n20000-s1 | `read_profile=robot` | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/20 |

The raw receipt differs only in `source_files_sha256`, whose `candidates` entry hashes `candidates.py`; it is the only
source entry that changes. Tokens used and candidate counts per path are 20/20 on every row. The flat pack's questions
name no entity in it, so its graph path has no seeds and the change does not run there.

Probe receipts (`probes.sh`, `probe_receipts.py`): each probe's cached pack (`data/chat-probe-v1/<probe>/rc-M`) was
copied once per side and measured with `chat_probe measure`, no model call. Masked stored receipts and log fields are
identical on `defects-v1` 7/7, `chat-v1` 18/18 and `member-v1` 12/12 (`graph-candidate-cut/probe-receipts.txt`).
The probe scores are the same on both sides: `defects-v1` 7/7 in the top 3, `chat-v1` 15/18 in the top 3 and 16/18
in the top 5, `member-v1` 11/12 in the top 3 and 12/12 in the top 5.

Offline evidence gate (`gates.sh`, current defaults, LongMemEval-S controls regenerated 2026-10-14): every row of the
first table on LoCoMo (1,536 questions with evidence, 1,540 for projected accuracy) and LongMemEval-S (470 and 500)
is +0.0 points, interval +0.0 to +0.0, 0 wins, 0 losses; no question lost or gained all its evidence among the
candidates (`graph-candidate-cut/gate/compare-main-vs-change.md`).

## Bar 2: speed

`pairs.py --rounds 3 --set '{}'` (current defaults): both sides read the same 20 questions in turns, three rounds, 60
pairs, the order flipped each pair, one reader process per side kept open, each on its own fresh pack copy. The
1-minute load average was recorded before every pair (`graph-candidate-cut/speed/`). The interval is a bootstrap of
the median over the 60 pairs.

| Pack | Load (min / median / max) | `main` p50 / p95 | Change p50 / p95 | Median paired saving | 95% interval | Change faster |
|---|---|---|---|---:|---|---:|
| m3 | 5.7 / 6.6 / 8.2 | 405.3 / 527.5 ms | 271.7 / 395.4 ms | 109.6 ms | 34.8 to 122.8 ms | 48 of 60 |
| n20000-s1 (flat) | 4.7 / 5.0 / 5.4 | 59.6 / 85.5 ms | 58.8 / 85.3 ms | 0.5 ms | 0.1 to 0.7 ms | 42 of 60 |

The m3 interval is wide because 8 of the 20 questions find no seed in the pack, so their graph path does nothing on
either side (median saving 0.2 ms); the 12 with graph candidates (150 each, the cap) save 125.4 ms at the median.
The bar is met: the m3 median paired saving is at least 80 ms, and the flat pack is not slower.

The bar 1 runs (one round each, load about 10) gave the same picture: m3 savings of 134.2 ms (`address_first`),
131.1 ms (`classic`) and 4.3 ms (`read_profile=robot`, which keeps 40 graph candidates where the defaults keep 150), and flat savings of
1.0, -1.4 and 0.0 ms.

## Bar 3: PostgreSQL

The query is the same SQL in the PostgreSQL dialect (`seed_candidates_sql("postgres", ...)`): JSON read with `->>`,
times passed as `timestamptz`, seed ids as `uuid[]`, ids ordered with `COLLATE "C"`. Local server Postgres.app 16.

- `tests/test_pg_*.py` (11 files): 98 passed. `tests/test_pg_seed_walk.py` checks the store query against the
  per-seed walks and the earlier one walk, under every filter case, on PostgreSQL.
- A small pack on PostgreSQL (`pg_pack.py`: 10 entities, 48 claims, edges between them, built once through
  `MemoryEngine` with `extract=False`), read with 8 entity-named questions from each tree: the ranked ids, scores,
  graph proximities and paths are the same on 8 of 8 questions, with 29, 46, 47, 52, 48, 0, 0 and 38 graph
  candidates. The change side's store calls were counted: `walk_candidates` 8 times, no per-seed walk
  (`graph-candidate-cut/pg/`).

## Bar 4: privacy

The privacy and tenancy tests pass on the change, with PostgreSQL on: `test_privacy_fences.py`,
`test_privacy_admission.py`, `test_graph_neighborhood_tenancy.py`, `test_privacy_erasure.py`,
`test_privacy_format_compatibility.py`, `test_privacy_erasure_native_faults.py`, with `test_seed_walk.py` and
`test_pg_seed_walk.py`: 182 passed, 34 skipped (the one skip reason left is "explicit isolated historical
environment required"). The privacy read view has no `walk_candidates`, so a protected read takes the earlier path
unchanged.

Two notes on the test runs. In a broad run of 1,272 tests under 8 workers (graph, privacy, retrieval, rank fusion,
read profile, summaries, receipt compatibility and determinism) 5 privacy tests failed with `PrivacyDenied` from the
local analyzer's start (`privacy_capability_unavailable`); the same 5 pass when run again on the change, and their
files pass on both trees. With `PRME_TEST_DATABASE_URL` written as `postgresql:///db?host=/tmp`,
`test_existing_namespace_runtime_login_without_ddl` fails the same way on `main` (`invalid literal for int() with
base 10: 'None'`, no port in the address); with `postgresql://localhost:5432/db` it passes on the change.

## Bar 5: determinism and replay

Determinism and receipt compatibility tests pass (in the broad run above: 948 passed, the 5 failures being the
analyzer starts). The receipt's `source_files_sha256` changes: its `candidates` entry goes from `06fe5afb...` to
`1f309b34...`. Receipts still replay (`replay_cross.py`, m3, current defaults, 20 questions): receipts made on `main`
replay to the ids `main` returned under `main` 20/20 and under the change 20/20, and receipts made on the change
replay under the change 20/20 and under `main` 20/20 (`graph-candidate-cut/replay/cross.jsonl`).

## Bar 6: simple

One query (`seed_candidates_sql`, which wraps the existing walk) and one store method per dialect
(`walk_candidates` on `DuckPGQGraphStore` and `PgGraphStore`). The only branch in the stage is "the store has the
method and the time bounds have a time zone", else the earlier path. No store shape needed a special case.

## Full suite

`uv run pytest -n 8` on the change, with PostgreSQL on (`postgresql://localhost:5432/prme_task24`) and every model
provider blocked: 9,146 passed, 230 skipped, 128 xfailed, 4 failed, in 21 minutes 25 seconds. None of the 4 comes
from the change:

- `test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself` expects the checkout at
  `~/Sites/prism`; a worktree is somewhere else.
- 3 tests in `test_privacy_restore.py` (`test_ER08_archive_clear_retained_copies[postgres]`,
  `test_bound_postgres_restore_current_catalog_and_recovery_lease`,
  `test_same_lineage_restore_applies_current_ledger_before_recovery[postgres]`) read `PGUSER` and `PGPASSWORD` from
  the address and raise `TypeError` when it names no user and no password. They fail the same way on `main` at
  `d95f5c41`. With `postgresql://dwamianm:unused@localhost:5432/prme_task24` the file passes, 13 of 13, on both
  trees.

## What was not done

- No model call, no paid provider, no answer run: the change cannot change a context, so none applies.
- No mid tier: the offline gate and the probes show identical contexts.
- The 10 LoCoMo `ingest()` packs were not timed for this change.
