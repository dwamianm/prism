# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PRME (Portable Relational Memory Engine) is a local-first, embeddable memory substrate for LLM-powered systems. It combines event sourcing, graph-based relational modeling, hybrid retrieval, and organizer-driven memory reorganization (opportunistic and on-demand; see Organizer). The system is implemented (current release v0.15.0); design specs live in `docs/`.

## Architecture

Four storage layers behind a unified retrieval API. The default backend is local DuckDB-based; a PostgreSQL backend (`src/prme/storage/pg/`) is also wired and selected when `database_url` is set. A local pack is a directory of files by default; an opt-in single-file pack (`pack_path` / `PRME_PACK_PATH`, or a `MemoryClient` path ending in `.prme`; issue #247) keeps the whole pack in one DuckDB file, with the USearch snapshot in a `vector_snapshot` table and the Tantivy index held in memory, rebuilt from the graph on open and shared by every engine in the process that opens the pack, so the "persisted to" locations below describe directory packs.

- **Event Store (DuckDB)** — Append-only immutable event log. All derived structures must be rebuildable from this log.
- **Graph Store (DuckDB)** — Typed nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) and typed edges with temporal validity windows (`valid_from`/`valid_to`), confidence scores, and provenance references. Implemented in `src/prme/storage/duckpgq_graph.py` using DuckDB tables with recursive CTEs for traversal (DuckPGQ SQL/PGQ is not available on the supported DuckDB build, so recursive CTEs are the only code path — there is no Kùzu dependency).
- **Vector Index (USearch HNSW)** — Approximate nearest neighbor search via the `usearch` library (`src/prme/storage/vector_index.py`), persisted to `vectors.usearch`, with versioned embeddings (model name, version, dimension tracked per embedding).
- **Visual memory (RFC-0018, phase 1)** — `src/prme/vision/` turns a photo or a saved video into observation events: `ffmpeg` samples frames, `keyframes.py` keeps the ones that changed (pixel change plus a heartbeat), a local vision model (Ollama) writes a facts-first description of each, `repeat.py` drops a description that repeats the previous kept frame's, and `observe.py` admits each description through `ingest()` (default, with an extraction retry) or `store()` as an event of role `tool` with the capture clock as `event_time` and the RFC-0018 metadata. Thumbnails live in a `blobs` table of the pack's DuckDB database in both layouts (`src/prme/storage/blobs.py`), created on first use. Everything is behind `VisionConfig` (`vision`, `PRME_VISION_*`), off by default; `prme observe` turns it on for its own run. The spike harness `benchmarks/vision/spike.py` drives this code; local Ollama runs only.
- **Lexical Index (Tantivy)** — BM25 full-text search via `tantivy-py` (`src/prme/storage/lexical_index.py`), persisted to a `lexical_index/` directory, over event content, facts, and summaries. Entity nodes that extraction creates get a vector but no lexical document; the ingestion write path, `rebuild_indexes` (`prme rebuild`) and a single-file pack's open share that rule (`has_lexical_document` and `LEXICAL_DOCUMENT_SQL` in that module), so change it only there. A node that a lifecycle transition or an `ingest()` derivation retires leaves both indexes when it is retired (`src/prme/storage/index_eviction.py`; best effort, `index_compaction` retries), and an in-memory lexical index drops Tantivy's deleted-document markers before its next search, so the indexes a single-file session builds match a rebuild.

## Key Design Constraints

- **Append-only**: Events must never be overwritten or deleted except by policy-based archival. Conflicting assertions must not silently overwrite prior ones; use supersedence. Note: store-time supersedence is gated behind `enable_store_supersedence` (default `False`, `src/prme/config.py`); with the default, `store()` does not supersede and conflict handling relies on retrieval-time recency (under the default rank fusion, the current-state recency boost and event-time tie-break) and chronological `[MOST RECENT]`/`[OLDER]` markers in the context formatter rather than graph-level supersedence edges; recency alone is not treated as proof of truth. Predicate matching in the supersedence detector is exact-match plus three hardcoded equivalence classes (`src/prme/ingestion/supersedence.py`); paraphrased predicates are not currently superseded.
- **Deterministic**: Given identical event logs and config, retrieval results must be reproducible. Scoring weights must be configurable and versioned.
- **Portable artifact**: The memory pack — `memory.duckdb` (event store + graph tables), `vectors.usearch` (USearch index), `lexical_index/` (Tantivy directory), and `manifest.json` (encryption/version metadata) — must be copyable, encryptable, and rebuildable. Derived indexes can be regenerated from the durable graph with `prme rebuild`. That directory layout is the default; the opt-in single-file pack (a `.prme` file) is one DuckDB database holding the same tables plus `vector_snapshot` and a `prme_manifest` row (`src/prme/storage/pack_manifest.py`), uses native DuckDB encryption instead of Fernet (PRME also turns on DuckDB's `temp_file_encryption`, which is off by default), is shrunk with `prme compact` and is created from a directory pack with `prme migrate` (both in `src/prme/storage/pack_tools.py`; RFC-0014, single-file pack amendment). Making the single file the default is a separate decision (issue #247).

## Hybrid Retrieval Pipeline

Query → intent classification + entity extraction + time detection → candidate generation (graph neighborhood, stable facts, vector similarity, lexical, recent high-salience) → deterministic re-ranking → context packing into memory bundles (entity snapshots, stable facts, recent decisions, active tasks, provenance refs).

## Memory Object Lifecycle

Objects progress through: Tentative → Stable → Superseded → Archived. Each object carries: id, type, scope (personal/project/org), confidence, salience, validity window, evidence references, and supersedence pointer.

## Organizer

The organizer (`src/prme/organizer/`, RFC-0015) provides maintenance jobs that handle: salience/confidence recalculation, promotion/demotion of assertions, summarization, deduplication/entity alias resolution, policy-based archival with TTL enforcement, tombstone and index compaction sweeps, and snapshot generation. `ALL_JOBS` currently registers eleven jobs; `DEFAULT_JOBS` is all of them except the legacy engine-global `feedback_apply` tuner.

Despite the name "scheduled," there is **no built-in cron or daemon scheduler**. Jobs run in two ways: (1) an opportunistic in-process pass triggered during retrieve/ingest, gated by a cooldown (`opportunistic_cooldown`, default 3600s) and a per-pass time budget (`opportunistic_budget_ms`, default 200ms); and (2) explicit invocation via `prme organize` (optionally `--user-id`, `--jobs`, and `--budget-ms`). Continuous scheduling, if needed, must be driven by an external cron/timer calling `prme organize`.

**Multi-tenant stores must pass a user scope.** `organize(user_id=...)` confines every job to that user's nodes. An unscoped run is correct for a single-tenant store and is still the default, but `deduplicate`, `alias_resolve`, and `consolidate` compare nodes to each other, so a shared store should be maintained as a loop over tenants. `feedback_apply` is the exception: scoring weights are engine-global, so a scoped request that includes it raises `ValueError` before any work, and an explicit unscoped operator run reports `"scope": "global"`. Under rank fusion, the default scoring, it reports `not_applicable` and keeps its signals.

## RFCs

Design specifications live in `docs/` as numbered RFCs (RFC-0000 through RFC-0018). See `docs/INDEX.md` for the full listing. Key RFCs include:

- **RFC-0000** — Suite overview
- **RFC-0001** — Core data model
- **RFC-0002** — Event store
- **RFC-0003** — Epistemic state model
- **RFC-0005** — Hybrid retrieval pipeline
- **RFC-0014** — Portability, sync, and federation
- **RFC-0015** — Self-organizing memory (organizer execution model)

Always consult the relevant RFC before implementing or modifying a subsystem.

## Configuration Surface

Config is defined as Pydantic models in `src/prme/config.py` and `src/prme/retrieval/config.py` (loaded from `PRME_`-prefixed env vars, `.env`, or direct args). The surface is large (roughly 100 fields across both files). Several parameter defaults are explicitly tagged `[HYPOTHESIS]` in their descriptions; these are reasoned but not yet benchmark-validated and may change. Treat `[HYPOTHESIS]` knobs as provisional and prefer not to depend on their exact values. Notable defaults to be aware of: the retrieval defaults changed after v0.12.0 (#177, #187) to rank fusion (`scoring.fusion="rrf"`, `rrf_k=60`) with `scoring.rrf_recency_boost=0.25` and `scoring.rrf_tie_break="event_time"`, the `reader` context format, `balanced` multi-path ordering and `packing.session_context_rank_fusion_score_decay=0.6`. They live in `PRMEConfig`; `ScoringWeights()` and `PackingConfig()` built in code keep their field defaults (weighted; `auditable`, `balanced` and no rank fusion session decay). `docs/PACKING.md` ("Default retrieval settings") lists the environment variables that restore the v0.12.0 defaults. Since 2026-10-06, `enable_claim_sentence_text=True` (a claim's text is its own sentences), `enable_speaker_references=True` (a named speaker's I, me and my bind to the speaker's entity) and `packing.fold_repeated_text=True` (each source text packed once in the reader format) are defaults too, after they passed the default-change rule together on the DeepSeek track over `ingest()` packs; default retrieval receipts are version 22. Since 2026-10-16, `extraction_batch_target_tokens=2048` (#254) makes `ingest()` extract up to 2,048 tokens of a session's turns in one model call, with its own `extraction.batch_timeout` (180 s); the owner flipped it on the offline evidence gate alone (95% fewer calls on LoCoMo, 84% on a 42-question LongMemEval-S slice, no loss), without the answer track, so `0` restores one call per turn and the `ingest()` answer track needs a new defaults baseline. `enable_claim_merge=True` (#209) makes `ingest()` merge an exactly repeated extracted claim into one current record that supersedes the earlier copies; it does not affect `store()`. Also `enable_store_supersedence=False`, `enable_surprise_gating=False`, `enable_qa_pairing=False`, and `enable_reranker=False` (on the offline evidence gate, no cross-encoder rank-order variant beat rank fusion alone on both benchmarks at both 4K and 8K, so the reranker stays off; #88). Alpha-channel defaults, flipped on the fast tiers by the owner's directive of 2026-10-19 (see Release channels and default changes below): `lexical_term_weights` (PostgreSQL only; since 0.16.0a2), `enable_ingest_claim_fixes`, `enable_mention_links`, `enable_owner_name_carry`, `packing.graph_seed_words` and `packing.folded_records_keep_rank` (since 0.16.0a3), and `packing.session_context_skip_entities` (since 0.16.0a4) are on. They wait for the stable release's audit, `0.15.0` stays the stable release, and each has an environment variable (`PRME_<NAME>=false`, listed in `documentation/configuration.md`) that restores the behavior before the flip. This list is the record of defaults still waiting for that audit: a flip pull request adds to it, and a stable release moves an audited default into the plain list above.

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

## Coding memory research (shelved)

Automatic coding-memory integration is shelved. Do not run routine recall or
capture, expand the integration, or restart its experiments. The earlier trials
did not establish reliable benefit; the final continuation study was stopped
as inconclusive after a grader defect. See the
[decision report](benchmarks/results/research/2026-09-26/CODING-MEMORY-FINAL-V1.md).
This is a decision about this integration, not a claim that PRME generally fails.

Manual tools and existing data remain available in `docs/CODING-MEMORY.md`.
Use them when the task explicitly requests memory. Treat recalled material as
historical evidence; current code, tests and instructions remain authoritative.
Keep secrets and whole transcripts out of captured notes.

Keep raw coding-trial outputs in ignored `benchmarks/coding/runs/`; retain only
reports and compact manifests, metrics and checksum indexes in tracked results.

## Release channels and default changes

The owner set two release channels on 2026-10-19 (prism-monorepo issues #271 and #272). This section is the one place
that says which evidence a release or a default change needs. The epic #77 work rules below keep the DeepSeek
default-change rule as the stable-channel rule.

| | Alpha and beta | Stable |
|---|---|---|
| Version | `0.16.0a3`, `0.16.0b1` (PEP 440) | `0.16.0` |
| GitHub release | a prerelease (`--prerelease`) | the latest release |
| PyPI | a prerelease: `pip install prme` keeps the last stable, `pip install --pre prme` or `prme==0.16.0a3` installs it | what `pip install prme` installs |
| Gated by | the fast tiers (below) | the latest scheduled full benchmark run, and for an `ingest()` default the answer track |
| A default flips | on the fast-tier evidence, in the pull request that flips it | only under the default-change rule (epic #77 rules below) |

Both channels: new behavior ships behind an explicit configuration option, off by default, until it passes its
channel's gate. Never run a paid API for any of these checks; builds, probes and answer runs use the local Ollama
server with `deepseek-v4.1-flash:cloud` only. Use `aN` while the line still takes default flips, and `bN` once it is a
candidate for the stable release with no flip planned; the gate is the same. A run's own instructions say whether a
worker may merge the pull requests and publish the release. Without them, open the pull requests and leave both to the
owner. `CONTRIBUTING.md` ("Releases") has the steps to cut and publish a release.

### Alpha and beta gate: the fast tiers

The fast tiers are a regression screen over small fixtures and one recorded extraction. They are not accuracy
estimates. Run each tier with the option off (the current defaults on `main`, no `--set`) and on (the same tree with the
change's options set by `--set <key>=<value>`, once per option), each run under its own label. Compare two runs only
when both used the same model identity and Ollama server version. Times were measured on 2026-10-19 on a laptop with
Ollama 0.40.6; a run whose extraction request is unchanged replays the extraction cache in seconds. `BENCHMARKS.md` has
each tier's inputs and results ("Fast gate for ingest changes", "Fast cycle", "Corrections probe", "Offline evidence
gate", "Mid tier").

| Tier | Checks | Time |
|---|---|---|
| Defect probes | the 14-turn chat `defects-v1`, 7 probes: does the answer rank in the top 3, and is the owner named | 22 s, 14 model calls per build |
| Chat probe | the 54-turn chat `chat-v1`, 18 probes, and the structure of what `ingest()` stored | 83 s, 54 calls per build |
| Corrections probe | what a correction stores and returns (`corrections-v1`) | about 30 s |
| Fast cycle | 75 memories and 17 questions through `store()`, then three scripted chats through `ingest()` | 158 to 211 s cold, 25 to 28 s warm (saved extraction cache) |
| Offline evidence gate | `retrieve()` over the saved LoCoMo and LongMemEval-S packs, no answer model | about 10 minutes per side (10 min 10 s) |
| Mid tier | all of LoCoMo and 12 LongMemEval-S questions, built through `ingest()`, then the gate | 24 to 34 minutes cold, 11 minutes warm |

```sh
# Defect probes (22 s): build the off side, the on side, then compare. The chat probe is the same three commands
# without --conversation (it reads chat-v1).
uv run python -m benchmarks.diagnostics.chat_probe --conversation benchmarks/conversations/defects-v1.json \
  build --label off --model deepseek-v4.1-flash:cloud --cloud
uv run python -m benchmarks.diagnostics.chat_probe --conversation benchmarks/conversations/defects-v1.json \
  build --label on --model deepseek-v4.1-flash:cloud --cloud --set <key>=<value>
uv run python -m benchmarks.diagnostics.chat_probe --conversation benchmarks/conversations/defects-v1.json compare off on

# Corrections probe (about 30 s): run once with and once without --set.
uv run python -m benchmarks.diagnostics.corrections_probe run --model deepseek-v4.1-flash:cloud --cloud \
  --label <label> --set <key>=<value> --replace --output <dir>

# Fast cycle: judged against the committed baseline (the current defaults), so it has no off run. Exits 0 on a pass,
# 1 when a gated measure is worse, 2 when the reports are not comparable.
uv run python -m benchmarks.fast_cycle run --label <label> --out <dir> --set <key>=<value>

# Offline evidence gate (10 min 10 s per side; needs the inputs under "Inputs" in BENCHMARKS.md).
uv run python -m benchmarks.diagnostics.product_packing gate \
  --lme-captures data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline --set <key>=<value> --output <on.json>
uv run python -m benchmarks.diagnostics.product_packing gate \
  --lme-captures data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline --output <off.json>
uv run python -m benchmarks.diagnostics.product_packing gate-compare <off.json> <on.json> --output <comparison.json>

# Mid tier (for a larger change): the baseline is main, built once (cold), then reused (warm).
uv run python -m benchmarks.mid_tier run --label <main-label> --out <dir>
uv run python -m benchmarks.mid_tier run --label <label> --out <dir> --set <key>=<value>   # retrieval or packing only: add --reuse-build <main-label>
uv run python -m benchmarks.mid_tier compare <main dir> <dir> --output <comparison.json>
```

The change passes when every criterion below that applies holds. This is the rule the 0.16.0a3 flip used.

- **Defect probes.** With the options on, the answer ranks in the top 3 for 7 of 7 probes. For a change to who a
  claim is attached to (speaker names, `ingest()` grounding), the user claims on the named owner also exceed the user
  claims on "I" or "We".
- **Chat probe.** On `chat-v1`, the answers ranked in the top 3 and in the top 5 are not below the options-off counts,
  and no measure is worse in its good direction (the directions are listed under "Fast gate for ingest changes" in
  `BENCHMARKS.md`; the counts of extraction calls, claims and entities are not judged).
- **Fast cycle.** `run` exits 0: no gated measure is worse than the committed baseline beyond its tolerance. When the
  change moves measures on purpose, say which, and refresh `benchmarks/fixtures/fast-cycle/baseline-main.json` in the
  same pull request (`--accept <measure>` names a worsening the change means to cause).
- **Offline evidence gate.** No row of `gate-compare`'s first table (all evidence packed, evidence recall, all evidence
  among the candidates, projected accuracy) has a 95% interval entirely below zero, and no question lost all its
  candidates. The gate replays saved `store()` packs, which hold no extracted records, so for an `ingest()` change
  it shows only that plain retrieval is left alone.
- **Corrections probe.** Run it and put its result in the pull request. It is not a pass criterion, because a live
  extraction is one sample: a worse result is recorded as a known weakness in the pull request and the release notes,
  as the pull request for 0.16.0a3 did. A change made for corrections must show its fix on it.
- **Mid tier.** Optional, for a larger change that the small fixtures may not show (a new extraction prompt, batch
  size or model, or a change to the ranking or packing of every query). If you run it, a 95% interval entirely below
  zero fails the change, as in the offline gate. LoCoMo carries the signal; the 12 LongMemEval-S questions only show a
  collapse.

If a measure is slightly worse on a live build, rebuild that side once before judging: a live build is one sample,
while a build that replays the extraction cache repeats exactly. If the rule fails with several options on together,
run each option alone (both probes) and flip only the options that pass alone and still pass together, as the pull
request for 0.16.0a1 did when none passed; the others stay off, with their numbers in the pull request.

A default may flip in the alpha channel on that evidence. The pull request that flips it:

- cites the commit, the commands, the model identity and Ollama version, the off and on numbers of every tier, and the
  owner's directive of 2026-10-19 (ship what passes the fast tiers, audit on the full benchmark at the stable
  release), and records every criterion that was not met, plainly;
- changes the default in `src/prme/config.py` or `src/prme/retrieval/config.py` and in `src/prme/config_audit.py`,
  updates `documentation/configuration.md`, the docs that name the option and the CHANGELOG, keeps a test of the off
  path, and names the environment variable that restores the previous behavior (`PRME_<NAME>=false`);
- says whether memories stored before the upgrade are rewritten, and adds the option to the list of alpha-channel
  defaults in the notable defaults paragraph under Configuration Surface (here and in `AGENTS.md`).

**What the alpha channel does not wait for.** The paired interleaved DeepSeek answer runs on both benchmarks, their A/A
checks, the confirmation pair and the `verdict` record; the DeepSeek `ingest()` track for an `ingest()` default
(batched extraction was flipped in 0.15.0 on the offline gate alone); the full builds and the scheduled full
benchmark run. It still waits for CI and for the fast tiers' pass rule above.

### Stable gate

A stable release needs all of the following. A worker reports their result and cuts a stable release only when the
owner's instruction says so.

1. **No loss on the latest scheduled full benchmark run.** The Mac mini builds the full benchmark twice a week, Monday
   and Thursday at 00:30 local time (prism-monorepo issue #272, `scripts/full-benchmark/`): LoCoMo and LongMemEval-S
   through `ingest()` with the defaults, then the offline evidence gate, compared with the previous stable's full run
   (for 0.15.0, the run at `d810ee55` in `BENCHMARKS.md`). No loss means no 95% interval entirely below zero in that
   comparison (for LoCoMo, both the conversation-level and the question-level interval count, and the run's report
   shows both). The run must be of the release commit, or of a `main` commit with the same `src/` tree; otherwise wait
   for the next run.
2. **The answer track, on demand, for an `ingest()` default.** It is not scheduled. For each `ingest()` default that is
   on (flipped in an alpha or beta, or in the stable release itself), run the DeepSeek answer track over `ingest()`
   packs of the release commit under the default-change rule below, with its A/A checks, interleaved pairs and confirmation (`BENCHMARKS.md`,
   "Answers over `ingest()` packs").
   For a default already on, record the baseline of the new defaults at the release commit and answer the previous
   behavior (the option off, as a named variant) in pairs alongside it, as `prme-per-turn` was for batched extraction
   on 2026-10-18. The audit reads a loss: if the old behavior passes the default-change rule against the new default,
   the default lost and goes back to off, and the owner decides what happens to the release; if its first pair fails,
   the default stays.
3. **A default that never went on in an alpha or beta** may flip in the stable release only under the default-change
   rule below, in full.

When a stable release is cut, record the audit's numbers in `BENCHMARKS.md`, move each audited default from the
alpha-channel list into the plain list of notable defaults, and drop the alpha wording.

## Epic #77 work rules (label `audit-2026-09`)

These rules apply to every issue in epic #77, including unattended ticket-loop runs. They exist because pull requests in this repository can merge automatically once CI passes. The evidence behind the epic is `memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md`. The default-change rule below is the stable-channel rule: the alpha channel flips defaults on the fast tiers (Release channels and default changes, above) and does not wait for it, and a stable release audits the defaults that went on in alpha with it.

- Keep production defaults unchanged until the channel's gate is met: the fast tiers in the alpha channel, the default-change rule below in a stable release (Release channels and default changes, above). Put new behavior behind an explicit configuration option first, off by default.
- Never start a run against a paid API (OpenAI, Anthropic or any provider billed per request or per token), and never spend API credit. Local tests, the offline evidence gate and answer runs through Ollama are fine.
- Answer runs use the DeepSeek track from #117: `deepseek-v4.1-flash:cloud` as both reader and judge through the local Ollama server at `http://127.0.0.1:11434/v1` (`--provider ollama` in `benchmarks/integrations/gpt54_baselines.py`). The `:cloud` tag is served by Ollama's hosted service, so prompts leave the machine; LoCoMo and LongMemEval-S are public. DeepSeek scores are a separate track. The GPT-5.4 numbers were the published reference until 2026-10-16, when the public benchmarks page (`documentation/benchmarks.md`) moved to the DeepSeek baselines of the current defaults; they are never compared directly with DeepSeek numbers.
- **The default-change rule (the stable-channel rule).** A default flipped in a stable release has the owner's standing approval (2026-09-23) when a DeepSeek paired answer run at the 4K budget on both LoCoMo and LongMemEval-S, over the same questions, shows (a) a gain in overall accuracy on at least one benchmark whose paired 95% interval excludes zero, and (b) no loss on the other benchmark whose paired 95% interval excludes zero. Repeated DeepSeek runs on identical contexts do not give identical answers, so the rule also needs these (#118, revised in #129):
  - **Interleaved pairs.** Each variant run gets its own fresh run of the defaults, answered in the same session and interleaved question by question (variant, defaults, variant, defaults), so drift and the time of day affect both sides equally. `run-pair` answers the two together against a recorded defaults baseline, and `compare` pairs a variant only with the defaults run answered alongside it and refuses any other pairing of a variant.
  - **Intervals.** LoCoMo intervals resample the 10 conversations, not single questions. LongMemEval-S intervals resample questions, because each question has its own history. The rule reads `compare`'s `interval_95`. A bootstrap over only 10 conversations covers less than 95%, so for LoCoMo `interval_95` spans both the conversation-level interval (`interval_95_conversations`) and the question-level one (`interval_95_questions`): a LoCoMo difference excludes zero only when both do.
  - **Confirmation run.** A variant that passes must pass the same test a second time before the default flips, with a whole new pair: a fresh variant run and a fresh defaults run (running `run-pair` again after a complete pair starts the next one). The confirmation is the first fresh pair with the same settings and context text, alongside the same baseline, that completes: if it fails, the variant fails. A pair that a final failure stops publishes no result and counts as neither a pass nor a fail; `run-pair` gives it up on record and starts the next pair. Under the DeepSeek track's amended failure policy (#132), a truncated reader answer or a garbled verdict is asked once more and then scored incorrect as `truncated` or `verdict_unresolved`, so neither stops a pair. A complete pair that `compare` refuses because more than 1% of either side's questions ended that way is invalid, and it also counts as neither a pass nor a fail. The pull request lists every arm prepared with those settings or that context text and every pair it was answered in, given up or not (#130).
  - **Which pairs count (#143).** A confirmation counts only when it was answered alongside the same baseline as the first pair and has the same variant identity (#130): the same settings that differ from the defaults and the same hash of the variant's context text on every question. If either differs, `compare` refuses it as a confirmation, and `run-pair` answers no pair whose count is already decided as neither. The two may read contexts prepared at different commits only because both of those match, which makes the contexts identical. **A new baseline starts every count again:** after a default flip, every variant is counted from zero against the new baseline, and earlier pairs stay in the listed history without counting toward a decision. `compare` warns about them, and separately when an earlier baseline prepared the same defaults' text, since those pairs then ran the same test. **A real fix is a new variant:** a code change that alters a variant's context text gives it a new identity, with its own first pair and confirmation, and `compare` still lists the earlier attempts with the same settings so a reviewer can see them. Other settings on the same context text stay in the same count, so a setting that retrieval never reads cannot buy a variant another first pair. For pairs started before #130, which recorded no settings, the context text decides alone.
  - **Model identity and server version.** Both sides of a pair, a variant's first pair and its confirmation, and the A/A check the test relies on all use the same Ollama model identity and answer settings. `compare` refuses a mismatch between the two sides of a pair, and a variant's pair that no recorded A/A check covers (below); between a variant's first pair and its confirmation, `verdict` refuses a different model identity or answer settings (#144). Every run records the live Ollama server version when it starts, resumes and finishes, and `compare` refuses a pair whose server version changed during either run. The identity is Ollama's local manifest digest and the hosted weights are not pinned, so a matching identity does not prove the same weights.
  - **A/A check.** Before the revised test is used, one interleaved A/A pair (the defaults against themselves, `run-pair prme --benchmark <benchmark> --provider ollama --baseline <baseline>`) is answered on both benchmarks and recorded in `BENCHMARKS.md` and the A/A record (below) for the conditions it was measured under: model identity, Ollama server version, context budget, failure policy and answer settings; measure it again when any of them changes. The test can be used when the A/A interval includes zero on both benchmarks. If either excludes zero, the test still applies with one more condition: a variant's gain must also be larger than the largest absolute A/A difference measured so far on that benchmark, the #118 sequential repeat included (all recorded in `BENCHMARKS.md`). For each set of those conditions, the A/A check is the first A/A pair on each benchmark that completes and that `compare` accepts, as with the confirmation run: it is never drawn again to replace a result. Any later complete A/A pair is recorded in `BENCHMARKS.md` too, and if one excludes zero, the extra condition applies.
  - **A/A record (#137).** `run-pair` adds every A/A pair it publishes to the tracked A/A record, `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-aa-checks.jsonl`: the model identity, Ollama server version, context budget, failure policy and answer settings it was answered under, its interval, and whether it is the first accepted A/A pair for those conditions. `compare` refuses a variant's pair unless the record holds an A/A check on both benchmarks under the same five conditions, and it names that check and every other A/A pair under them; `run-pair` answers no variant pair that `compare` would refuse this way. The record must list exactly the A/A pairs the track's run logs show complete, and each line must match its published results, so none can be left out or moved to other conditions. Commit each new line with its pair's published results. **A new Ollama server version means a new A/A pair on both benchmarks before any further variant pair counts**, and so does a new model identity, failure policy, answer settings or context budget. The Ollama app updates itself, so turn off its automatic updates on the answering machine while variant pairs are in progress where possible.
  - **Verdict (#144).** Whether a variant passed is recorded, not worked out by hand. The `compare` command records every pair it accepts as a variant's first pair or confirmation, or as an A/A pair: a `compared` event in the pair's run log, and a line in the tracked verdict record, `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-pair-verdicts.jsonl`, with the benchmark, the role, the baseline, the variant identity, the difference, `interval_95`, whether it excludes zero, the A/A check it relied on, compare's warnings and the published results' digests. Compare the published results on the machine that answered the pair, and commit each new line with its pair's published results, like the A/A record (its A/A lines are for reference; the margin reads the A/A record). `verdict prme --variant <name> --provider ollama` reads those lines for the variant on both benchmarks, checks them against the published results, and prints `pass`, `fail` or `incomplete` with the numbers behind it. It applies this rule: the first pair and the confirmation must each pass, a failed first pair or a failed confirmation fails the variant, a pair `compare` refused was never recorded and counts as neither, and a new baseline starts the count again. Where an accepted A/A pair under a pair's conditions excluded zero, on either benchmark, that pair's gain must also be larger than the largest absolute A/A difference recorded on its benchmark, the #118 sequential repeat included, and the output says which case applied. On the machine that answered the pairs it also checks the record against the track's run logs, repeats compare's current warnings, and reports `checked_against_run_logs: true`.
  - **Current state (2026-09-24).** The sequential repeat from #118 did not hold (LongMemEval-S +2.2 points, 95% interval +0.4 to +4.2), so #129 revised the test as above. The interleaved A/A check of the revised test then held on both benchmarks, measured with model identity `e04da138`, Ollama server version 0.34.3 and the amended failure policy (#132): LongMemEval-S -0.2 points (95% interval -1.4 to +1.0) and LoCoMo -0.39 points (-1.30 to +0.52), both including zero. The test can be used without the extra margin for that model identity, server version, answer settings and failure policy only. Both checks are in the A/A record, and `compare` refuses a variant pair answered under any other conditions until a new A/A pair under them is complete and accepted on both benchmarks (#137). The pairs tried earlier that day under the registered retry policy each stopped at a final failure and were given up on record; `BENCHMARKS.md` records them and the passing pairs. After the default changes in #177 and #187 (2026-09-25), the DeepSeek defaults baseline is `prme@d811e3ed` (LoCoMo 1250/1540, LongMemEval-S 453/500), and every variant counts from zero against it. The existing A/A checks still apply, because the model identity, Ollama server version, context budget, failure policy and answer settings did not change.

  - **Options that change `ingest()`.** The saved packs were built with `store()`, which never extracts, so an option that changes extraction, the graph or how extracted records are packed is tested on the track over packs built through `ingest()` (`--contexts ingest`; `BENCHMARKS.md`, "Answers over `ingest()` packs"). The same rule applies there, with that track's own baseline, A/A checks and records: `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-ingest-aa-checks.jsonl` and `ollama-deepseek-v4.1-flash-cloud-ingest-pair-verdicts.jsonl`.

  The pull request that flips a default under this rule must cite the `verdict` output for the variant, which must read `pass` with `checked_against_run_logs: true`, and commit the verdict record's lines for its pairs (#144). It must also include the before and after numbers of both sides of every pair, the intervals, each pair's model identity and server versions, the A/A check each pair relied on (`compare`'s `aa_check`), and the receipt paths. After a flip, record a new DeepSeek baseline for the new defaults (`prepare prme` and `run prme` on `main`; `prepare` takes `--lme-captures` on a machine without the 2026-09-22 LongMemEval-S control captures) and pair later variants with it.
- Run the offline evidence gate from #78 for retrieval, packing and representation changes in every channel, and put its before and after numbers in the pull request.
- If any acceptance criterion is still open (for example an answer run that has not been done yet), write `Part of #N` instead of `Fixes #N` in the pull request, and list the remaining criteria there, so the issue stays open.
- Keep receipts replayable and determinism tests passing.
