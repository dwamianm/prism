# Code Review: perf/hosted-retrieve-cost

## Plan (written before implementation)

Scope: items 1 to 3 of dwamianm/prism-monorepo#150, each output-identical. Items 4 (namespace pool
checkouts) and 5 (a default retrieve limit) are left for a later pull request. The approval gate was
replaced by a self-check: the approach is concrete and bounded and gives the same output for the same
inputs, and nothing in it needs a decision only the owner can make (the thread count keeps today's
default; see item 2).

Profile of the code before the change (3,435 LoCoMo turns, 30 questions, in process):

- PostgreSQL retrieve p50 about 200 ms wall. `importlib.metadata.version("fastembed")` ran 604 times per
  retrieve, almost all of them once for every row `PgVectorIndex.search_by_vector` returned
  (src/prme/storage/pg/vector_index.py, the per-row identity tuple), and dominated the profile.
- `pack_context` p50 57 ms (PostgreSQL inputs, 554 candidates) and 64 ms (DuckDB inputs, 849). Of its
  time, tiktoken `encode` took 46% (about 77 whole-context encodes per call on top of one per candidate)
  and entry rendering 35% (about 3,500 `_render_reader_entry` calls per call, because `_render_sections`
  re-rendered every packed record for each candidate tried).

Approach:

1. Cache the fastembed version for the process (`_fastembed_version`, `lru_cache`, the same pattern as
   `fastembed_model_dimension`), and read the provider identity once per PostgreSQL search, only when the
   search returned rows (as before, an empty search reads nothing).
2. `EmbeddingConfig.threads` / `PRME_EMBEDDING__THREADS` and `FastEmbedProvider(threads=...)`, passed to
   `TextEmbedding(threads=...)` only when set, modeled on `duckdb_threads`. Default `None` everywhere,
   including the HTTP server: the engine's rules put new behavior behind an explicit option, and a
   different default would need the owner's default-change evidence. A deployment that knows its CPU
   limit sets it.
3. `pack_context` counts each record line once. The context is lines joined by newlines; record lines
   start with `[`, `-` or `{` and end with `"`, `]` or `}`, the head ends with `.`, and under the
   pre-tokenizer patterns of all seven tiktoken 0.12 encodings a newline between such lines always ends
   a piece that is the same whether text follows or not, so the count of the whole context is the sum of
   the lines' counts. `_ContextTokens` caches each tried line's two counts (with and without its newline)
   and sums them per proposed context; any other encoding, or a line or head of another shape, falls back
   to rendering and counting the whole context, which is the old algorithm. The context text is rendered
   once at the end from the packed sections.

Alternatives considered, with the evidence that ruled them out:

- Cache entry renders but keep counting the whole context per candidate: removes the 35% but keeps the
  46% (the whole-context encode is O(context) per try; 1.09 ms for an 18 KB context on this machine).
- Use an upper bound on the count to skip whole counts: not exact. `tokens_used` feeds the next
  candidate's preflight and the receipt, so it must be the exact count, and BPE counts are not provably
  subadditive under concatenation.
- A running O(1) sum instead of summing cached counts per try: more state to keep right across folds
  and session-window insertions; summing cached integers costs about 1 ms per call.
- `functools.cached_property` for the version: makes `model_version` assignable on the instance, which
  it is not today.
- A server-only default of one thread: a production default change under the engine's rules, and
  identical vectors across thread counts were measured on one machine only.

Shared state the change touches: two process-wide `lru_cache`s (the fastembed version; the per-encoding
line-counting check). Nothing persisted changes: vectors, version strings, manifests, receipts and the
recorded engine configuration keep their bytes (receipts' `source_files_sha256` hashes of the two changed
modules change, as they do for any edit to those files).

## Review Scope

- Base: origin/main `1e392f72134c6ca97d537c8bf4643b063d3740fb`.
- Reviewed snapshot (review-plan): `d2f270f0dc3871ac0039a73a4cc886f8cd15e948721d6f5703a6bffe25b29c3b`
  at head `8e2864de`. Delta re-review snapshot `b62e0cbb583b91a6d5467c1d33fe00e5d497f4e4edf5b612825a4f039e9e810a`
  at head `a294a4af`. The final commit after that changes only comments, docstrings, the CHANGELOG
  timing figures and this file.
- Tier M. Predicates: NEW_SYMBOLS true; DATA, INFRA, SECURITY and PERSISTED_FIELD false. Semantic risk
  check: DATA was considered and not raised, because the PostgreSQL query, what it reads and every
  stored value are unchanged (only the identity comparison after the read moved out of the loop), and
  no field is persisted. Nothing touches authentication, tenancy or deployment wiring.
- Roster: core reviewer (Sonnet; checklists 1, 3, 4 and 5 merged, with the security and wiring screen)
  and adversarial reviewer (Opus; checklist 8, at most 4 scenarios). Checklists 2, 6 and 7 not
  applicable at this tier. The core reviewer re-reviewed the fix delta.

## Files Changed

- src/prme/storage/embedding.py: `_fastembed_version`; `FastEmbedProvider(threads=...)`; the factory passes `threads`.
- src/prme/storage/pg/vector_index.py: provider identity read once per search.
- src/prme/config.py: `EmbeddingConfig.threads` (left out of dumps while unset).
- src/prme/retrieval/tokenization.py: `counts_lines_independently`, the checked patterns, probe lines, `LINE_STARTS`, `LINE_ENDS`.
- src/prme/retrieval/packing.py: `_ContextTokens`; `pack_context` uses it; `_context_head`, `_section_line` and `_shows_speaker` shared with `_render_sections` and `reader_shows_speaker`.
- tests/test_packing_line_counts.py and tests/fixtures/packing/line-count-digests.json (new).
- tests/test_embedding_initialization.py, tests/test_embedding_configuration.py, tests/test_pg_vector_index.py, tests/test_reader_context.py.
- CHANGELOG.md, docs/PACKING.md, docs/LOCAL-RESOURCES.md, docs/INTEGRATION.md, documentation/configuration.md.

## Approach Summary

See the plan. The output-identity proof for packing has three parts: the committed digests, written by
the code before this branch, for 260 cases (scenarios built with the helpers of the existing packing
tests, plus a varied set with hostile text, folds, session windows, guidance, notices, five encodings and
the monotonic compact packer); a per-case comparison of line counting with whole-context counting; and
the offline evidence gate before and after (below).

## Must Fix

- M1 (core): `EmbeddingConfig.threads: None` changed the nested `embedding` dict in every configuration
  dump, so `benchmarks/integrations/gpt54_baselines.py` `_check_defaults_build`, which compares top-level
  keys, would refuse an `ingest()` build recorded before this change as a defaults baseline. Confirmed
  against the recorded builds in `data/extracted-packs-v1`. Resolved: `exclude_if=lambda value: value is
  None`, the pattern src/prme/retrieval/config.py uses for unset options. The gate's recorded
  `engine_config` is byte-identical to the code before the branch; a test covers it.

## Should Fix (all resolved)

- S1: the line-shape and head guards had no test. Added tests with record lines prefixed by a newline or
  `//` (cl100k_base and o200k_base) and a header ending in a space; all five fail against a copy of
  packing.py with the guards removed. Added a case with a notice longer than the budget.
- S2: the speaker predicate and the section line were stated twice. Now `_shows_speaker` and
  `_section_line`, used by both the renderer and the line counts.
- S3: the embedding paragraph in docs/LOCAL-RESOURCES.md split the DuckDB section. Moved after it.

## Consider (resolved or recorded)

- C1 (two dicts with the same keys for full entries and costs): left; no behavior risk.
- C2 (parameter name): `measure(..., entry_cost)`.
- C3 (golden digests are a new test pattern): kept. The module docstring gives the regeneration command
  (`uv run python -m tests.test_packing_line_counts --write`), which lists the changed cases.
- C4 (doc claims about thread spinning and affinity, and vectors across thread counts): removed the
  unsourced claims; the docs say the thread count is not recorded with a vector and that bit-identical
  vectors were measured on one machine.
- C5: the standalone `PRME_EMBEDDING_THREADS` form is tested; the duplicated import hook in a test is left.
- Delta C-a: a build recorded with `threads` set is refused as a defaults baseline; deliberate, now
  stated in the test. Delta C-b: comment grammar and a docstring, fixed.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Secrets or private data in code, tests or docs | PASS | None. The repository is public; the docs and changelog describe engine behavior only. |
| Input validation | PASS | `threads` rejects bools, non-integers and values below 1 in both the config and the provider. |
| Tenant isolation | PASS | The PostgreSQL query, including its `user_id` filter, is unchanged. |
| New I/O | PASS | No new network or file access; one private tiktoken attribute is read with a default. |

## Pattern Consistency Assessment

`threads` matches `duckdb_threads` field for field (optional positive integer, bool-rejecting validator,
description, docs in docs/LOCAL-RESOURCES.md and documentation/configuration.md, CHANGELOG entry, tests
for default, environment and invalid values). Its validation in `FastEmbedProvider` matches `dimension`.
`exclude_if` for an unset option follows src/prme/retrieval/config.py. The version cache follows
`fastembed_model_dimension`. The logger follows the `logging.getLogger(__name__)` convention.

## Redundancy Check

No existing helper counted lines or cached the version. `_context_head`, `_section_line` and
`_shows_speaker` are extractions shared by the renderer and the counter. No new dependency.

## Wiring Findings

`threads` reaches the provider through `create_embedding_provider`, which `MemoryEngine.open` (both
backends) and `MemoryWorkspace` use, so `PRMEConfig`, the environment, workspaces and the HTTP server
get it. A caller-owned provider takes its own `threads` argument, as documented.

## Data Safety and Read/Write Proof

Not applicable: no schema, key, migration or persisted field. Stored vectors and version strings are
unchanged; the default configuration dump is unchanged (M1); receipts are unchanged apart from the
recorded hashes of the two changed modules.

## Break Scenarios (adversarial)

Pre-mortem headline (adversarial reviewer): "No plausible incident. The nearest is 'Retrieve latency
quietly regresses after a tiktoken bump turns off packing's line counts' (latency only, output unchanged)."

| # | Scenario | Label | Likelihood / impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | A tiktoken release keeps the pattern strings but applies them differently, so summed line counts come out below the real count | Newly introduced | L / M, silent | Fix now | The diff made it possible and the fix is contained: `counts_lines_independently` also checks every pair of five probe lines (one of each shape) once per encoding per process, and falls back to whole counting if any pair differs. Tested with a fake tokenizer. |
| 2 | A tiktoken pattern edit turns line counting off with no sign except latency | Newly introduced | L to M / L (output unchanged), quiet | Fix now | One warning is logged per encoding per process when packing falls back; the engine's tests also assert that every encoding tiktoken ships is counted line by line, so a dependency bump in this repository fails loudly. |

Attacked hardest and held (adversarial reviewer): 32,376 base-versus-new bundles with hostile notices
and guidance, three encodings and budgets from 0 to 2,590 gave the same aggregate digest; patched
renderers with hostile lines (1,080 bundles) matched the base; speaker-header switching, folding, the
single final render and reserved levels keep the cached line counts in step. Scale: 5,000 candidates at
a 128K budget pack in 0.98 s.

## Follow-ups Raised

None. Both adversarial scenarios were fixed here. The test crash noted under Tests is the existing open
issue #268, not a new finding.

## Evidence gate (offline)

LongMemEval-S over the regenerated controls (`--lme-captures`) and LoCoMo over the `ingest()` packs
`ingest-baseline-locomo-deepseek-2026-10-15` (`--packs`), with `--capture-dir`; the 2026-09-22 LoCoMo
packs the default gate reads are no longer on this machine. "Before" imported the `src/prme` tree of
e7e9764c (identical to main's) in place of the branch, so its reports name the worktree's commit at the
time; "after" is a294a4af.

| Benchmark | Questions | Records per context | Memory text | All evidence packed | Projected | Retrieval p50 / p95 before | after |
|---|---:|---:|---:|---:|---:|---:|---:|
| LongMemEval-S | 500 | 39.9 | 82.6% | 445/470 | 91.4% | 0.186 / 0.257 s | 0.150 / 0.227 s |
| LoCoMo (ingest packs) | 1,540 | 96.2 | 95.2% | 1,253/1,536 | 75.9% | 0.371 / 0.563 s | 0.258 / 0.341 s |

Every question's row is identical apart from its timing and the digest of its capture file; every
rendered context is identical (2,040 of 2,040); every receipt is identical apart from its request ID and
the two `source_files_sha256` entries for packing.py and tokenization.py; the per-benchmark summaries are
identical. The gate numbers match the owner's earlier report for the same LoCoMo packs.

## Measurements

In process, 3,435 LoCoMo turns under one user, 30 questions after a warm-up, three alternating rounds,
18-core machine: PostgreSQL retrieve p50 202 ms to 88 ms (CPU 207 ms to 101 ms), `pack_context` 57 ms to
14 ms; DuckDB retrieve 267 ms to 214 ms (CPU 322 ms to 269 ms), `pack_context` 64 ms to 23 ms. Every
question's context digest, token count, record count and result count were identical in all six runs per
backend. Captured real inputs repacked under 17 settings and two notice variants (2,040 bundles) were
byte-identical between the two implementations. fastembed with one thread: CPU per embedding 4.9 ms
against 42.5 ms by default, same wall time, bit-identical vectors on 300 texts.

## Tests

On the final tree (commit a294a4af plus the comment, docstring and changelog edits of the final commit):

- `pytest tests/ -q` without a database: 5,425 passed, 1,034 skipped, 1 failed. The failure is
  `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`, which expects a
  plain clone and fails the same way on main's code in a git worktree; CI runs in a plain clone.
- The same suite with `PRME_TEST_DATABASE_URL` (local PostgreSQL 17 with pgvector): two full runs
  crashed with a segmentation fault in the teardown of
  `tests/test_ingested_corrections.py::test_mcp_owner_declaration_is_strict_and_persisted[duckdb]`, a
  DuckDB extraction read still running while the test's event loop closes. That is the open issue #268,
  which reproduces on unmodified main (4 of 8 runs of that file on 2026-10-15) and depends on process
  state; this branch does not touch that code. A full run with `--ignore=tests/test_packing_line_counts.py`
  (whose tests need no database and passed in the run above) passed: 6,074 passed, 100 skipped, and the
  same worktree-only failure. Main's full run from an export of the tree did not crash either.
- `python -m scripts.run_simulations` with the current defaults and with the previous retrieval
  defaults: 74 of 74 checkpoints each.
- `ruff check src/ tests/`, the strict mypy check of `tests/typing/public_api.py`, and mypy on the four
  changed source modules: clean.
- New tests fail on the code before the branch where they should: the PostgreSQL identity test (6 reads
  instead of 1), the thread and version tests, and the line guard tests against packing with the guards
  removed. A one-token error in the line sum fails 27 tests, including the digest test.

## Resolution Status

| Finding | Status |
|---|---|
| M1 config dump | Resolved (a294a4af) |
| S1 guard tests | Resolved (a294a4af) |
| S2 shared helpers | Resolved (a294a4af) |
| S3 docs order | Resolved (a294a4af) |
| C1, C3 | Recorded, not changed |
| C2, C4, C5 | Resolved (a294a4af; C5 helper duplication left) |
| Delta C-a, C-b | Resolved (final commit) |
| Adversarial 1, 2 | Fix now, resolved (a294a4af) |
