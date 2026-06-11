# PRME v0.9.0 — Full Audit Report (2026-06-11)

Four parallel deep audits: security, efficiency, accuracy, user value, plus a 2025–2026 research scan. Detailed per-dimension reports live alongside this file:

- [audit-security.md](audit-security.md)
- [audit-efficiency.md](audit-efficiency.md)
- [audit-accuracy-value.md](audit-accuracy-value.md)
- [audit-research-scan.md](audit-research-scan.md)

The engine's core engineering is better than typical for this stage — but one finding reframes everything else.

## The headline finding: benchmark scores measure the harness, not the product

The benchmark query path adds machinery that doesn't exist in any user-facing path (`benchmarks/longmemeval.py:889-904`, `benchmarks/locomo.py:914-938`):

- **LLM query reformulation** — 3 alternate queries per question, merged. The product's `query_analysis.py` is rule-based by design, so a real user gets 1 query, not 4–8.
- **LoCoMo entity fan-out** — proper-noun extraction with entity+keyword combo queries, harness-only.
- **Dataset annotation ingestion** — the LoCoMo harness stores the dataset's own `observation` field as nodes (`locomo.py:855-876`). The +3pp "observations" win was borrowed from dataset annotations, not produced by the system.
- **Test-mirroring prompt examples** — `llm_judge.py:41-48` contains worked examples that are near-copies of specific LoCoMo questions (the Sweden/grandma one, the "3 kids" one). Violates the project's own no-benchmark-hacks standard.
- **`ingest()` is never benchmarked** — all benchmark ingestion uses raw `store()`. The flagship LLM extraction pipeline is unvalidated by the headline numbers.

So 94.7%/89.8% are upper bounds the user can't reach out of the box. Cheapest honest fix = highest-value product improvement: **move multi-query reformulation into `retrieval/pipeline.py` as an opt-in product feature** (one small LLM call), neutralize the prompt examples, drop observation ingestion, re-baseline.

## Second finding: 98% is not currently measurable

- Run-to-run flip noise is 5–8 questions (±1.1–1.7pp) — same magnitude as the remaining headroom. A single run cannot distinguish 96.5% from 98% (98% on LME allows ≤9 failures out of 470).
- The same model generates and judges (leniency bias); `score >= 0.5` counts the rubric's "partially correct" band as fully correct; API errors score 0.0.
- The judge never sees context, so 3–5 suspect gold labels are permanently unwinnable.

**Fix:** pin a separate stronger judge model, cache verdicts per (question, answer), score by 3-run majority. Days of work; gates everything else.

## Security — fine for a laptop, not for any deployment

Storage and crypto layers are solid (zero SQL injection across both backends, Fernet + PBKDF2 600k iterations, disciplined parameterization).

| Severity | Finding | Where |
|---|---|---|
| High | REST API has **zero auth** and binds `0.0.0.0` by default | `api/server.py:17`, `api/routes.py` |
| High | CORS `allow_origins=["*"]` + credentials — any website can read/poison the localhost memory store | `api/app.py:59-65` |
| High | `user_id` is caller-asserted; node-ID operations have no ownership check | `engine.py:1333`, `mcp/server.py` |
| Medium | Stored memory enters LLM context verbatim and undelimited — persistent prompt injection replayed on every retrieval; attackers can forge `[LATEST]`/`COMPUTED:` markers | `context_formatter.py` |
| Medium | Encryption fails open: `close()` swallows errors leaving the pack plaintext; crash mid-session leaves everything decrypted | `engine.py:1864-71`, `encryption.py` |

The three Highs are small mechanical fixes (default `127.0.0.1`, bearer-token dependency, pinned origins, ownership checks).

## Efficiency — hot path design is right, the DB access patterns aren't

Retrieval hot path: **0 LLM calls, 1 embedding call**. The cost is round-trips and scans. Top 5 by impact-per-effort:

1. **Batch `get_nodes(ids)`** — candidates resolve up to ~1000 node IDs one sequential await at a time (`retrieval/candidates.py:505-508`), plus ~450 in the aggregation scan and ~60 in Stage 2.5. Dominant per-query latency.
2. **Invert `_fetch_allowed_keys`** (`vector_index.py:92-141`) — every vector search loads every vector-metadata row for the user. O(N) → O(k) with `WHERE vector_key IN (matches)`.
3. **Ingestion is O(N²) disk I/O** — full HNSW index rewritten on every insert (`vector_index.py:190`); tantivy commits once per document with a fresh writer (`lexical_index.py:54-74`). Debounce the save; long-lived writer with batched commits.
4. **Cross-scope hint pass re-runs all four backends** (`pipeline.py:424-465`) — doubles per-query work under scope filters.
5. **Push filters into SQL** — pinned candidates hydrate 500 nodes/scope to filter in Python; session expansion hydrates 2,000.

Correctness bugs found in perf code: **auto-promote never reaches older eligible nodes** (orders `created_at DESC`, no cursor — `maintenance.py:124-127`); pinned nodes older than newest 500 silently unretrievable; lexical/vector indexes have no working delete (superseded content accumulates forever). ~20–30% token redundancy in formatted bundles (profile preamble duplicates body; only the aggregation format dedups).

## Accuracy & claims reality check

**Holds up (verified):** temporal validity enforced at retrieval; epistemic confidence enters the composite score; scope isolation works; default config IS the benchmark config; 3-line `MemoryClient` with zero required external services; `retrieve()` costs 1 local embed.

**Doesn't hold up:**
- **Supersedence off by default in the benchmarked path** — `enable_store_supersedence=False`; all knowledge-update wins came from `[LATEST]` retrieval markers. CLAUDE.md's supersedence constraint unmet by default. Oscillation detection dormant behind it.
- **Determinism claim violated** — unseeded HNSW construction; two rebuilds from the same log can differ. Fix: `exact=True` at current scale.
- **No `rebuild()` exists** — rebuildability is aspirational. `prme rebuild` also fixes index corruption, embedding migration, and the no-delete index problem.
- **CLAUDE.md describes a different system** — says Kùzu, `vectors.bin`, FTS5; reality is DuckDB recursive CTEs, usearch, tantivy. No scheduler behind the "Scheduled Organizer."
- **14 config fields shipped tagged `[HYPOTHESIS]`** — untested guesses in the public API. Prune or move to `experimental` before v1.0.

**Failure taxonomy (latest runs):** of 24 LME failures, ~22 are LLM reasoning (counting, off-by-one date intervals, wrong-fact selection), ~2 hard retrieval misses. Third systematic class: **off-by-one interval arithmetic** ("N vs N+1 days") — deterministic date math in the context formatter could eliminate it.

## Research: the field just validated the roadmap

- **Chronos (arXiv 2603.16862, Mar 2026)** = issues #27 + #29 in one paper: SVO event tuples with resolved datetimes and entity aliases at ingestion, queried via temporal/exact-match tools so counting questions return an enumerated filtered list. Ablation: the event calendar is the **single largest contributor** to 95.6% LME. PRME already has the substrate.
- **Hindsight (arXiv 2512.12818)** — blocker-2 recipe: coreference + canonical entity resolution at write time. Beats PRME's LoCoMo score (89.6%) with a weaker open model.
- **Mem0 2026** — dropped their graph store for entity-match as an explicit third fused retrieval signal — deterministic, cheap, fits constraints better than failed rerankers.
- **Calibration:** Mastra's 94.87% LME with gpt-5-mini is the honest published ceiling. **Nobody has published 98% with a mini-class model.** Convergent literature answer for breaking through: iterative/agentic retrieval, not wider single-shot pools. Skip: ColBERT/late-interaction, forgetting curves, sleep-time compute (token efficiency only).

## Recommended order of attack

1. **Fix measurement** (judge pinning, cached verdicts, 3-run majority) — without this, no result below can be trusted.
2. **Move harness features into the product** (multi-query reformulation in `pipeline.py`, neutralize judge prompt examples, drop observation ingestion); re-baseline honestly.
3. **Three High security fixes + flip `enable_store_supersedence` default** — small and mechanical.
4. **Top-3 perf fixes** (batch `get_nodes`, inverted key filter, debounced index writes) — makes benchmark iteration itself faster.
5. **Build the Chronos-style structured event layer** (#27 + #29 together); land `exact=True` + `prme rebuild` along the way.

98% with gpt-5-mini via computational aggregation after an honest re-baseline would be a genuinely novel, publishable result.
