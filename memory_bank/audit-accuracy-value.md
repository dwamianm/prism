# PRME Accuracy + Value Audit (v0.9.0, 2026-06-11)

## PART A — ACCURACY

### A1. The benchmark scores measure the harness, not the product [VERIFIED — highest-impact finding]

The benchmark query path layers machinery on top of `engine.retrieve()` that does not exist in any user-facing path:

- **LLM query reformulation** — 3 alternate queries per question, results merged: `benchmarks/longmemeval.py:889-904`, `benchmarks/locomo.py:914-918`. The product's `query_analysis.py` is rule-based by design ("no blocking LLM calls per RFC-0005 S3", `src/prme/retrieval/query_analysis.py:6`), so a user calling `client.retrieve()` gets one query, not 4-8.
- **Entity fan-out (LoCoMo only)** — proper-noun extraction plus entity+keyword combo queries appended to the alt-query list: `benchmarks/locomo.py:920-938`.
- **Dataset annotation ingestion (LoCoMo only)** — the harness stores LoCoMo's own `observation` field (dataset-provided distilled facts) as `[Observation]` nodes: `benchmarks/locomo.py:855-876`. Real users have no observation annotations; PRME's own extraction never produced these. The +3pp credited to "observations" is borrowed from the dataset, not the system.
- **None of the benchmarks exercise `ingest()`** — all ingestion is raw `store()` of turns (`longmemeval.py:870`, `locomo.py:486`). The LLM extraction pipeline is unvalidated by the headline numbers.

**Impact:** 94.7%/89.8% are upper bounds the user cannot reach out of the box. **Action:** wire multi-query reformulation into `retrieval/pipeline.py` as an opt-in mode (1 small LLM call), or report a "product-path" score alongside the harness score. Remove observation ingestion from LoCoMo runs or report it as a separate condition.

### A2. Generation prompt contains test-mirroring examples [VERIFIED]

`benchmarks/llm_judge.py:41-48` (GENERATION_SYSTEM_PROMPT) includes worked examples that are near-literal copies of specific LoCoMo questions: *"grandma is in Sweden" + "moved from their home country" → moved from Sweden* (the Caroline/Sweden question, which still fails anyway); *"3 kids" from "son", "daughter", "youngest child"* (the Melanie children question); *"single parent" + "applied to adoption" = single*. Conflicts with the project's own no-benchmark-hacks standard. **Action:** replace with domain-neutral examples and re-run; treat any score delta as previously inflated.

### A3. Failure taxonomy (latest runs: `lme_v23_agg.json` 94.47% 446/470; `locomo_v24_multihop.json` 88.16% 140/152)

- **LME (24 wrong):** ~22 are LLM reasoning failures (counting undercounts, off-by-one date intervals, week/day unit confusion, wrong-fact selection under contradiction), only ~2 are hard retrieval misses (explicit "I don't know"), and 3-5 are plausibly judge/gold-label issues (e.g., citrus count expected 3, model lists 5 valid fruits; "Vegan" counted as cuisine in gold).
- **LoCoMo (12 wrong):** ~10 reasoning/selection failures, 2 hard retrieval misses. Recurrent patterns: wrong "most recent" fact picked (Melanie horse vs sunset), hedging ("at least 2" vs 3), binary instead of nuanced inference.
- **Stability:** only 16/24 LME and 7/12 LoCoMo failures persist v22→v23/v24; 8 and 5 respectively are new flips. That is 30-42% noise in the failure set.

Confirms the two known blockers and adds a third: **off-by-one interval arithmetic** is a systematic, promptable failure class (multiple "N vs N+1 days" misses) that deterministic date math in the context formatter (it already computes "days ago") could eliminate.

### A4. Judge calibration and the measurability of 98% [VERIFIED]

- **Self-judging:** the same `llm_config` (one provider/model) both generates and judges (`benchmarks/__main__.py:95-115`). Known leniency bias toward same-family outputs.
- **Threshold semantics:** `is_correct = score >= 0.5` (`longmemeval.py:961`, `locomo.py:978`) while the rubric defines 0.4-0.6 as "partially correct" (`llm_judge.py:92`). Half the partial-credit band counts as fully correct.
- **Failure = wrong:** any judge/generation API exception returns 0.0 (`llm_judge.py:291, 337`) — infrastructure errors scored as incorrect answers.
- **Judge is context-blind:** it sees only question/expected/generated (`llm_judge.py:318-328`), so it can never detect a bad gold label; the 3-5 suspect golds are permanently unwinnable.
- **Abstention correctness is itself an LLM call:** `score = 1.0 if should_abstain else 0.0` (`longmemeval.py:923-926`).
- **Measurability:** 98% on LME allows ≤9 failures out of 470. Observed run-to-run flip count is 5-8 questions (±1.1-1.7pp); project memory itself attributes ~8 LME failures to noise. **A single run cannot distinguish 96.5% from 98%.** Action: pin a separate, stronger judge model, cache judge verdicts per (question, answer) pair, score via 3-run majority before claiming movement toward #33. Note `--retry-failed` exists (`benchmarks/__main__.py:104-113`); if retried passes are ever hand-merged into old reports, judge noise ratchets scores upward — the saved v23/v24 files appear to be full runs, but add a process guardrail (SUSPECTED risk, not observed).

### A5. Supersedence: off by default in the path that was benchmarked [VERIFIED]

- `ingest()` always runs `detect_and_supersede()` (`src/prme/ingestion/pipeline.py:407-415`), but `store()` only does so when `enable_store_supersedence=True` — **default False** (`src/prme/config.py:242-250`, `engine.py:645-656`). Benchmarks use `store()`, so all knowledge-update wins came from retrieval-time `[LATEST]` markers in `context_formatter.py`, not supersedence. The CLAUDE.md constraint "conflicting assertions must use supersedence" is unmet in the default configuration.
- Matching is exact predicate match plus 3 hardcoded equivalence classes (`works_at`/`lives_in`/`role`) — `src/prme/ingestion/supersedence.py:31-72`. Paraphrased facts ("employed by" vs "works at") will not supersede; both stay active. No timestamp-comparison bugs found.
- Oscillation detection (`engine.py:947-958`) runs only inside the supersedence branch — **effectively dormant by default**.

**Action:** flip `enable_store_supersedence` default to True (or document loudly), add embedding-similarity predicate matching as fallback.

### A6. Temporal validity: actually enforced [VERIFIED — claim holds]

`valid_from`/`valid_to` filtered at retrieval for graph candidates (`retrieval/candidates.py:147-153`) and via DuckDB join for vector candidates (`storage/vector_index.py:117-129`). ENTITY and PREFERENCE node types exempt by design (`candidates.py:123`). Not vestigial.

### A7. Epistemic confidence: not vestigial [VERIFIED — claim holds]

Confidence matrix (`epistemic/matrix.py`) applied at ingestion (`ingestion/pipeline.py:354-357`), stored on nodes, enters ranking as `w_confidence * effective_confidence` in the 6-term composite score whose weights are validated to sum to 1.0 (`retrieval/scoring.py:372`, `retrieval/config.py:100-115`).

### A8. Determinism claim violated at the vector layer [VERIFIED construction; SUSPECTED magnitude]

`usearch.Index(ndim=…, metric="cos", dtype="f32")` with **no seed and no `exact=True`** (`storage/vector_index.py:58-64`, search at :269). HNSW graph construction is order/thread-dependent, so two rebuilds from the same event log can return different ANN neighbor sets — contradicting "identical log + config → identical retrieval". Everything downstream is clean: stable sort tiebreaker `(-composite, -path, node_id)` (`scoring.py:578-583`), rule-based query analysis, candidate dedup sorted before return. **Action:** at current corpus sizes (<100k vectors), pass `exact=True` to `search()` — brute-force cosine is fast at this scale and makes the claim true; otherwise document the exception.

---

## PART B — VALUE TO THE USER

### B1. Integration story is genuinely good [VERIFIED]

Target user: engineers adding persistent memory to LLM agents. Minimal path is 3 lines via `MemoryClient` (`src/prme/client.py:58-312`), **zero external services required**: default embedding is local fastembed bge-small-en-v1.5 (`config.py:44-63`). Surfaces: CLI (15+ commands), FastAPI (`api/routes.py`), MCP server (`mcp/server.py`), LangChain retriever/history (`integrations/langchain.py:55-130`), LlamaIndex (`integrations/llamaindex.py`).

**Cost per operation (user's bill):**

| Op | Embedding calls | LLM calls |
|---|---|---|
| `store()` | 1 (local, free by default) | 0 |
| `ingest()` | 1 + 1/extracted fact | 1 (gpt-4o-mini default; falls back to store() if unconfigured) |
| `ingest_fast()` | 1 | 0 |
| `retrieve()` | 1 | 0 |
| `organize()` / `consolidate_knowledge()` | ~0 | 0 |

Strong cost story — but per A1, accuracy at this cost is below the headline numbers; the benchmarked configuration adds ~1 LLM call + 3-8 retrievals per query.

### B2. Feature reality vs claims

- **Portable pack: documentation is wrong** [VERIFIED]. Actual artifacts: `memory.duckdb` (events+graph in one file), `vectors.usearch`, `lexical_index/` (Tantivy), `manifest.json` only when encryption is on. CLAUDE.md still claims Kùzu, `vectors.bin`, `hnsw.idx`, FTS5. Kùzu was replaced by DuckDB recursive CTEs (`storage/duckpgq_graph.py`); no Kùzu imports remain. Action: update CLAUDE.md/RFC references.
- **Deterministic rebuild: aspirational** [VERIFIED]. Event log is append-only and the materialization queue can be drained, but there is **no public `rebuild()`/replay** that regenerates graph+vector+lexical from `events.duckdb`; MVP Phase 3 "deterministic rebuild validation" has no harness. Combined with A8, portability is the least-delivered of the three core claims. Action: implement `prme rebuild` — also the cleanest fix for index corruption and embedding-model migration.
- **Encryption: real, opt-in** [VERIFIED]. PBKDF2-HMAC-SHA256 (600k iter) + Fernet, encrypt-on-close/decrypt-on-open, manifest metadata.
- **Scope isolation: implemented** [VERIFIED]. Six scopes, filtered in candidate queries (`retrieval/candidates.py:419-440`) with cross-scope hints (`retrieval/pipeline.py:420-440`).
- **"Scheduled Organizer": there is no scheduler** [VERIFIED]. All 11 jobs are callable (CLI `prme organize`, `client.organize()`, HTTP, MCP) plus an opportunistic in-process pass during retrieve/ingest (cooldown 3600s, 200ms budget, `organizer/maintenance.py`), but nothing is cron/daemon-scheduled. CLAUDE.md's "Periodic jobs" oversells. Action: document, or ship `prme organize --daemon`.

### B3. Dead/dormant subsystems [VERIFIED reachability via imports]

Little fully-dead code, but several features are wired yet **dormant under defaults** — shipping untested by every benchmark run:
- `enable_store_supersedence` (False), `enable_surprise_gating` (False), `reinforce_similarity_threshold` (None), `enable_reranker` (False — known to hurt), `namespace_weights` (empty), oscillation detection (gated behind supersedence).
- `quality/` (FeedbackTracker, WeightTuner) instantiated in engine (`engine.py:120-121`), run via organizer job (`organizer/jobs.py:327-348`), but produces nothing unless feedback signals are recorded — closed loop with no default producer.
- `benchmarks/epistemic.py` — evaluation-only.
- PostgreSQL backend (`storage/pg/`) — wired but 25 tests permanently skipped; second backend carries real maintenance cost for unclear demand. Action: decide keep-and-test or deprecate.

Recommended deletions/decisions: the reranker code path (empirically harmful), surprise-gating and novelty knobs (4 config fields, never validated, all marked [HYPOTHESIS]).

### B4. Config surface [VERIFIED]

~50 user-facing knobs across `config.py` (431 lines) + `retrieval/config.py`. **14 fields explicitly tagged `[HYPOTHESIS]`** — untested guesses shipped as API. Knobs that demonstrably matter per the project's own ablations: `enable_qa_pairing` (default True — correct), scoring weights, packing budget, embedding model. **The default config IS the benchmark config** — both benchmarks construct `PRMEConfig` with only paths (`longmemeval.py:852-856`), so users get the benchmark-validated engine defaults. Right call; the gap is harness-side (A1), not config-side. Action: prune or move [HYPOTHESIS] knobs to an `experimental` namespace before v1.0 freezes the config API.

---

## Bottom line

1. Core engineering claims mostly hold (temporal filtering, epistemic scoring, scope isolation, encryption, cost story) — better than typical at this stage.
2. Headline accuracy numbers are inflated relative to product reality by benchmark-only reformulation/fan-out, dataset observation ingestion, and a generation prompt containing test-mirroring examples. The single highest-value fix is moving multi-query reformulation into the product.
3. 98% is not currently measurable: single-run judge noise (±1.1-1.7pp, self-judging, threshold-straddling rubric, errors-score-zero) is the same magnitude as the remaining headroom. Fix measurement before chasing the target.
4. The least-true documented claims are determinism (unseeded HNSW) and rebuildability (no rebuild()) — both have cheap fixes (`exact=True` search; an event-replay command).
