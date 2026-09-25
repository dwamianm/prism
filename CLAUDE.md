# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PRME (Portable Relational Memory Engine) is a local-first, embeddable memory substrate for LLM-powered systems. It combines event sourcing, graph-based relational modeling, hybrid retrieval, and organizer-driven memory reorganization (opportunistic and on-demand; see Organizer). The system is implemented (current release v0.11.0); design specs live in `docs/`.

## Architecture

Four storage layers behind a unified retrieval API. The default backend is local DuckDB-based; a PostgreSQL backend (`src/prme/storage/pg/`) is also wired and selected when `database_url` is set.

- **Event Store (DuckDB)** — Append-only immutable event log. All derived structures must be rebuildable from this log.
- **Graph Store (DuckDB)** — Typed nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) and typed edges with temporal validity windows (`valid_from`/`valid_to`), confidence scores, and provenance references. Implemented in `src/prme/storage/duckpgq_graph.py` using DuckDB tables with recursive CTEs for traversal (DuckPGQ SQL/PGQ is not available on the supported DuckDB build, so recursive CTEs are the only code path — there is no Kùzu dependency).
- **Vector Index (USearch HNSW)** — Approximate nearest neighbor search via the `usearch` library (`src/prme/storage/vector_index.py`), persisted to `vectors.usearch`, with versioned embeddings (model name, version, dimension tracked per embedding).
- **Lexical Index (Tantivy)** — BM25 full-text search via `tantivy-py` (`src/prme/storage/lexical_index.py`), persisted to a `lexical_index/` directory, over event content, facts, and summaries.

## Key Design Constraints

- **Append-only**: Events must never be overwritten or deleted except by policy-based archival. Conflicting assertions must not silently overwrite prior ones — use supersedence. Note: store-time supersedence is gated behind `enable_store_supersedence` (default `False`, `src/prme/config.py`); with the default, `store()` does not supersede and conflict resolution relies on retrieval-time `[LATEST]`/recency markers in the context formatter rather than graph-level supersedence edges. Predicate matching in the supersedence detector is exact-match plus three hardcoded equivalence classes (`src/prme/ingestion/supersedence.py`); paraphrased predicates are not currently superseded.
- **Deterministic**: Given identical event logs and config, retrieval results must be reproducible. Scoring weights must be configurable and versioned.
- **Portable artifact**: The memory pack — `memory.duckdb` (event store + graph tables), `vectors.usearch` (USearch index), `lexical_index/` (Tantivy directory), and `manifest.json` (encryption/version metadata) — must be copyable, encryptable, and rebuildable. Derived indexes can be regenerated from the durable graph with `prme rebuild`.

## Hybrid Retrieval Pipeline

Query → intent classification + entity extraction + time detection → candidate generation (graph neighborhood, stable facts, vector similarity, lexical, recent high-salience) → deterministic re-ranking → context packing into memory bundles (entity snapshots, stable facts, recent decisions, active tasks, provenance refs).

## Memory Object Lifecycle

Objects progress through: Tentative → Stable → Superseded → Archived. Each object carries: id, type, scope (personal/project/org), confidence, salience, validity window, evidence references, and supersedence pointer.

## Organizer

The organizer (`src/prme/organizer/`, RFC-0015) provides maintenance jobs that handle: salience/confidence recalculation, promotion/demotion of assertions, summarization, deduplication/entity alias resolution, policy-based archival with TTL enforcement, tombstone and index compaction sweeps, and snapshot generation. `ALL_JOBS` currently registers twelve jobs (some are full implementations, some stubs pending future RFCs).

Despite the name "scheduled," there is **no built-in cron or daemon scheduler**. Jobs run in two ways: (1) an opportunistic in-process pass triggered during retrieve/ingest, gated by a cooldown (`opportunistic_cooldown`, default 3600s) and a per-pass time budget (`opportunistic_budget_ms`, default 200ms); and (2) explicit invocation via `prme organize` (optionally `--user-id`, `--jobs`, and `--budget-ms`). Continuous scheduling, if needed, must be driven by an external cron/timer calling `prme organize`.

**Multi-tenant stores must pass a user scope.** `organize(user_id=...)` confines every job to that user's nodes. An unscoped run is correct for a single-tenant store and is still the default, but `deduplicate`, `alias_resolve`, and `consolidate` compare nodes to each other, so a shared store should be maintained as a loop over tenants. `feedback_apply` is the exception: scoring weights are engine-global, so it ignores the scope and reports `"scope": "global"`.

## RFCs

Design specifications live in `docs/` as numbered RFCs (RFC-0000 through RFC-0015). See `docs/INDEX.md` for the full listing. Key RFCs include:

- **RFC-0000** — Suite overview
- **RFC-0001** — Core data model
- **RFC-0002** — Event store
- **RFC-0003** — Epistemic state model
- **RFC-0005** — Hybrid retrieval pipeline
- **RFC-0014** — Portability, sync, and federation
- **RFC-0015** — Self-organizing memory (organizer execution model)

Always consult the relevant RFC before implementing or modifying a subsystem.

## Configuration Surface

Config is defined as Pydantic models in `src/prme/config.py` and `src/prme/retrieval/config.py` (loaded from `PRME_`-prefixed env vars, `.env`, or direct args). The surface is large (roughly 100 fields across both files). Several parameter defaults are explicitly tagged `[HYPOTHESIS]` in their descriptions — these are reasoned but not yet benchmark-validated and may change. Treat `[HYPOTHESIS]` knobs as provisional and prefer not to depend on their exact values. Notable defaults to be aware of: `enable_store_supersedence=False`, `enable_surprise_gating=False`, and `enable_reranker=False` (the cross-encoder reranker has not improved benchmark scores in practice).

## Storage Backends

- **DuckDB (default)** — local-first; no `database_url` set.
- **PostgreSQL** — used when `database_url` is set; implemented in `src/prme/storage/pg/`. Its test suite (`tests/test_pg_*.py`) is skipped unless `PRME_TEST_DATABASE_URL` points at a live database, so those tests are skipped locally without a database. CI runs them against a live PostgreSQL service on Python 3.11–3.13.

## MVP Phases (delivered)

The originally planned MVP scope is implemented as of v0.9.0:

1. Event store, graph schema, vector search, hybrid retrieval
2. Organizer jobs, stable fact promotion, snapshot generation, supersedence handling (store-time supersedence opt-in; see Key Design Constraints)
3. Encryption, CLI tooling, evaluation harness, deterministic rebuild validation (`prme rebuild`)

## Browser Automation

Use `agent-browser` for web automation. Run `agent-browser --help` for all commands.

Core workflow:

1. `agent-browser open <url>` - Navigate to page
2. `agent-browser snapshot -i` - Get interactive elements with refs (@e1, @e2)
3. `agent-browser click @e1` / `fill @e2 "text"` - Interact using refs
4. Re-snapshot after page changes

## Github

- Do not make any claude attributions to git commits

## Epic #77 work rules (label `audit-2026-09`)

These rules apply to every issue in epic #77, including unattended ticket-loop runs. They exist because pull requests in this repository can merge automatically once CI passes. The evidence behind the epic is `memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md`.

- Keep production defaults unchanged unless the default-change rule below is met. Put new behavior behind an explicit configuration option first.
- Never start a run against a paid API (OpenAI, Anthropic or any provider billed per request or per token), and never spend API credit. Local tests, the offline evidence gate and answer runs through Ollama are fine.
- Answer runs use the DeepSeek track from #117: `deepseek-v4.1-flash:cloud` as both reader and judge through the local Ollama server at `http://127.0.0.1:11434/v1` (`--provider ollama` in `benchmarks/integrations/gpt54_baselines.py`). The `:cloud` tag is served by Ollama's hosted service, so prompts leave the machine; LoCoMo and LongMemEval-S are public. DeepSeek scores are a separate track. The GPT-5.4 numbers stay the published reference and are never compared directly with DeepSeek numbers.
- Default changes have the owner's standing approval (2026-09-23) when a DeepSeek paired answer run at the 4K budget on both LoCoMo and LongMemEval-S, over the same questions, shows (a) a gain in overall accuracy on at least one benchmark whose paired 95% interval excludes zero, and (b) no loss on the other benchmark whose paired 95% interval excludes zero. Repeated DeepSeek runs on identical contexts do not give identical answers, so the rule also needs these (#118, revised in #129):
  - **Interleaved pairs.** Each variant run gets its own fresh run of the defaults, answered in the same session and interleaved question by question (variant, defaults, variant, defaults), so drift and the time of day affect both sides equally. `run-pair` answers the two together against a recorded defaults baseline, and `compare` pairs a variant only with the defaults run answered alongside it and refuses any other pairing of a variant.
  - **Intervals.** LoCoMo intervals resample the 10 conversations, not single questions. LongMemEval-S intervals resample questions, because each question has its own history. The rule reads `compare`'s `interval_95`. A bootstrap over only 10 conversations covers less than 95%, so for LoCoMo `interval_95` spans both the conversation-level interval (`interval_95_conversations`) and the question-level one (`interval_95_questions`): a LoCoMo difference excludes zero only when both do.
  - **Confirmation run.** A variant that passes must pass the same test a second time before the default flips, with a whole new pair: a fresh variant run and a fresh defaults run (running `run-pair` again after a complete pair starts the next one). The confirmation is the first fresh pair with the same settings and context text, alongside the same baseline, that completes: if it fails, the variant fails. A pair that a final failure stops publishes no result and counts as neither a pass nor a fail; `run-pair` gives it up on record and starts the next pair. Under the DeepSeek track's amended failure policy (#132), a truncated reader answer or a garbled verdict is asked once more and then scored incorrect as `truncated` or `verdict_unresolved`, so neither stops a pair. A complete pair that `compare` refuses because more than 1% of either side's questions ended that way is invalid, and it also counts as neither a pass nor a fail. The pull request lists every arm prepared with those settings or that context text and every pair it was answered in, given up or not (#130).
  - **Which pairs count (#143).** A confirmation counts only when it was answered alongside the same baseline as the first pair and has the same variant identity (#130): the same settings that differ from the defaults and the same hash of the variant's context text on every question. If either differs, `compare` refuses it as a confirmation, and `run-pair` answers no pair whose count is already decided as neither. The two may read contexts prepared at different commits only because both of those match, which makes the contexts identical. **A new baseline starts every count again:** after a default flip, every variant is counted from zero against the new baseline, and earlier pairs stay in the listed history without counting toward a decision. `compare` warns about them, and separately when an earlier baseline prepared the same defaults' text, since those pairs then ran the same test. **A real fix is a new variant:** a code change that alters a variant's context text gives it a new identity, with its own first pair and confirmation, and `compare` still lists the earlier attempts with the same settings so a reviewer can see them. Other settings on the same context text stay in the same count, so a setting that retrieval never reads cannot buy a variant another first pair. For pairs started before #130, which recorded no settings, the context text decides alone.
  - **Model identity and server version.** Both sides of a pair, a variant's first pair and its confirmation, and the A/A check the test relies on all use the same Ollama model identity and answer settings. `compare` refuses a mismatch between the two sides of a pair, and a variant's pair that no recorded A/A check covers (below); between a variant's first pair and its confirmation, `verdict` refuses a different model identity or answer settings (#144). Every run records the live Ollama server version when it starts, resumes and finishes, and `compare` refuses a pair whose server version changed during either run. The identity is Ollama's local manifest digest and the hosted weights are not pinned, so a matching identity does not prove the same weights.
  - **A/A check.** Before the revised test is used, one interleaved A/A pair (the defaults against themselves, `run-pair prme --benchmark <benchmark> --provider ollama --baseline <baseline>`) is answered on both benchmarks and recorded in `BENCHMARKS.md` and the A/A record (below) for the conditions it was measured under: model identity, Ollama server version, context budget, failure policy and answer settings; measure it again when any of them changes. The test can be used when the A/A interval includes zero on both benchmarks. If either excludes zero, the test still applies with one more condition: a variant's gain must also be larger than the largest absolute A/A difference measured so far on that benchmark, the #118 sequential repeat included (all recorded in `BENCHMARKS.md`). For each set of those conditions, the A/A check is the first A/A pair on each benchmark that completes and that `compare` accepts, as with the confirmation run: it is never drawn again to replace a result. Any later complete A/A pair is recorded in `BENCHMARKS.md` too, and if one excludes zero, the extra condition applies.
  - **A/A record (#137).** `run-pair` adds every A/A pair it publishes to the tracked A/A record, `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-aa-checks.jsonl`: the model identity, Ollama server version, context budget, failure policy and answer settings it was answered under, its interval, and whether it is the first accepted A/A pair for those conditions. `compare` refuses a variant's pair unless the record holds an A/A check on both benchmarks under the same five conditions, and it names that check and every other A/A pair under them; `run-pair` answers no variant pair that `compare` would refuse this way. The record must list exactly the A/A pairs the track's run logs show complete, and each line must match its published results, so none can be left out or moved to other conditions. Commit each new line with its pair's published results. **A new Ollama server version means a new A/A pair on both benchmarks before any further variant pair counts**, and so does a new model identity, failure policy, answer settings or context budget. The Ollama app updates itself, so turn off its automatic updates on the answering machine while variant pairs are in progress where possible.
  - **Verdict (#144).** Whether a variant passed is recorded, not worked out by hand. The `compare` command records every pair it accepts as a variant's first pair or confirmation, or as an A/A pair: a `compared` event in the pair's run log, and a line in the tracked verdict record, `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-pair-verdicts.jsonl`, with the benchmark, the role, the baseline, the variant identity, the difference, `interval_95`, whether it excludes zero, the A/A check it relied on, compare's warnings and the published results' digests. Compare the published results on the machine that answered the pair, and commit each new line with its pair's published results, like the A/A record (its A/A lines are for reference; the margin reads the A/A record). `verdict prme --variant <name> --provider ollama` reads those lines for the variant on both benchmarks, checks them against the published results, and prints `pass`, `fail` or `incomplete` with the numbers behind it. It applies this rule: the first pair and the confirmation must each pass, a failed first pair or a failed confirmation fails the variant, a pair `compare` refused was never recorded and counts as neither, and a new baseline starts the count again. Where an accepted A/A pair under a pair's conditions excluded zero, on either benchmark, that pair's gain must also be larger than the largest absolute A/A difference recorded on its benchmark, the #118 sequential repeat included, and the output says which case applied. On the machine that answered the pairs it also checks the record against the track's run logs, repeats compare's current warnings, and reports `checked_against_run_logs: true`.
  - **Current state (2026-09-24).** The sequential repeat from #118 did not hold (LongMemEval-S +2.2 points, 95% interval +0.4 to +4.2), so #129 revised the test as above. The interleaved A/A check of the revised test then held on both benchmarks, measured with model identity `e04da138`, Ollama server version 0.34.3 and the amended failure policy (#132): LongMemEval-S -0.2 points (95% interval -1.4 to +1.0) and LoCoMo -0.39 points (-1.30 to +0.52), both including zero. The test can be used without the extra margin for that model identity, server version, answer settings and failure policy only. Both checks are in the A/A record, and `compare` refuses a variant pair answered under any other conditions until a new A/A pair under them is complete and accepted on both benchmarks (#137). The pairs tried earlier that day under the registered retry policy each stopped at a final failure and were given up on record; `BENCHMARKS.md` records them and the passing pairs. After the default change in #177 (2026-09-25), the DeepSeek defaults baseline is `prme@335ee82b` (LoCoMo 1237/1540, LongMemEval-S 431/500), and every variant counts from zero against it. The existing A/A checks still apply, because the model identity, Ollama server version, context budget, failure policy and answer settings did not change.

  The pull request that flips a default must cite the `verdict` output for the variant, which must read `pass` with `checked_against_run_logs: true`, and commit the verdict record's lines for its pairs (#144). It must also include the before and after numbers of both sides of every pair, the intervals, each pair's model identity and server versions, the A/A check each pair relied on (`compare`'s `aa_check`), and the receipt paths. After a flip, record a new DeepSeek baseline for the new defaults (`prepare prme` and `run prme` on `main`) and pair later variants with it.
- Once the evidence gate from #78 exists, run it for retrieval, packing and representation changes, and put its before and after numbers in the pull request.
- If any acceptance criterion is still open (for example an answer run that has not been done yet), write `Part of #N` instead of `Fixes #N` in the pull request, and list the remaining criteria there, so the issue stays open.
- Keep receipts replayable and determinism tests passing.
