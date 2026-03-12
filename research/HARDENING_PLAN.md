# PRME Hardening Plan: From "Architecturally Ambitious" to "Proven"

*Created: 2026-03-12*

---

## Guiding Principle

Nothing matters without benchmark scores. The architecture is strong but unproven. Phase 1 establishes credibility. Phases 2-4 make it production-ready.

---

## Phase 1: Benchmark Scores (THE Priority)

### 1.1 Run Synthetic Benchmarks and Baseline (S)
- **Action:** Run `python -m benchmarks all --json benchmarks/results/baseline.json`
- **What exists:** LoCoMo, LongMemEval, Epistemic benchmark classes with synthetic data generators (300+ turn conversations, 40+ queries)
- **No code changes needed** — pure execution and analysis
- **Expected runtime:** 5-15 minutes with local FastEmbed
- **Dependency:** None

### 1.2 Diagnose and Fix Benchmark Failures (M)
- **Most likely failure:** `enable_store_supersedence` is probably False in benchmark config. One-line fix in `benchmarks/runner.py` `_create_engine()`.
- **Temporal reasoning** will be the weakest category (it is for every system). Supersedence chains through `engine.store()` must work for temporal queries to pass.
- **Scoring weight tuning:** The 8-input composite formula has many knobs. Benchmark results will reveal which need adjustment.
- **Abstention threshold:** `ABSTENTION_SCORE_THRESHOLD = 0.3` in LongMemEval needs validation.
- **Key file:** `benchmarks/runner.py` line 41-57
- **Dependency:** 1.1

### 1.3 Download and Run Real Datasets (M)
- **Action:** Run `python scripts/download_benchmarks.py --all` for real LoCoMo/LongMemEval data
- **Gap:** LongMemEval loader only logs when real data exists, doesn't evaluate — needs extension
- **Evaluation method:** Report both keyword-match accuracy (reproducible) and optionally LLM-judged accuracy (comparable to published numbers)
- **Dependency:** 1.2

### 1.4 Publish Results (S)
- Add benchmark scores to README comparison table
- Create `BENCHMARKS.md` with methodology and reproduceability instructions
- Add CI step to run synthetic benchmarks (fast, no external deps)
- **Dependency:** 1.2, 1.3

---

## Phase 2: Production Hardening (Parallel with Phase 1.2+)

### 2.1 Vector Index Delete Method (S)
- **Problem:** No way to remove orphaned vector entries after materialization rollback. Code explicitly acknowledges: `"rollback.orphaned_indexes"`.
- **Solution:** Add `async def delete(self, node_id: str)` to `VectorIndex`. USearch has `Index.remove(key)`. Look up vector_key from metadata table, call remove, delete metadata row.
- **Also:** Update `WriteTracker.rollback()` to call both `vector_index.delete()` and `lexical_index.delete_by_node_id()`.
- **Files:** `vector_index.py`, `pg/vector_index.py`, `write_queue.py`
- **Dependency:** None

### 2.2 Adaptive Vector Overfetch for Multi-Tenant (M)
- **Problem:** `_OVERFETCH_FACTOR = 3` is hardcoded. In 100+ user tenants, relevant results pushed out.
- **Solution (simple):** Make overfetch factor configurable. If post-filter yields < k results, retry with 2x factor, up to a configurable `max_overfetch_factor` cap.
- **Files:** `vector_index.py`, `config.py`
- **Dependency:** None

### 2.3 Write Queue Per-Backend Parallelism (L) — *Defer to Phase 4 if needed*
- **Problem:** Single-consumer queue serializes ALL writes across all backends.
- **Solution:** Categorized queues — one for DuckDB (remains serialized, single-writer constraint), separate consumers for USearch and Tantivy operations.
- **Route by label prefix** (already present: `"store.event:"`, `"store.vector:"`, `"store.lexical:"`).
- **Note:** PostgreSQL already uses `NoOpWriteQueue` with `asyncpg.Pool` — no change needed for PG.
- **Files:** `write_queue.py`, `engine.py`
- **Dependency:** None, but largest item — defer if benchmarks are priority

---

## Phase 3: Resilience and Correctness (After Phase 1 baseline)

### 3.1 Rule-Based Fallback Extraction (M)
- **Problem:** LLM down = events orphaned. After 3 retries (~215s), silent failure.
- **Solution:** Create `RuleBasedExtractionProvider` implementing existing `ExtractionProvider` Protocol.
  - Regex patterns: capitalized words as entities, "X is Y" / "X uses Y" as facts
  - Lower confidence (0.3-0.5) for rule-based extractions
  - dateparser already available for temporal refs
- **Wrap in `FallbackExtractionProvider`:** tries LLM first, falls back on failure.
- **Files:** New file + `extraction.py` factory function
- **Dependency:** None

### 3.2 CONTESTED State Resolution (M)
- **Problem:** Contradicting facts both become CONTESTED forever. No resolution mechanism.
- **Solution:** Add `conflict_resolve` organizer job:
  1. Compare evidence count, recency, confidence between CONTESTED pair
  2. If clear winner (more evidence + more recent), promote to STABLE, supersede loser
  3. After configurable timeout (30 days), resolve by recency
  4. Optional: `resolve_conflict(node_a, node_b, winner)` API for manual resolution
- **Files:** `jobs.py` + new resolution module
- **Dependency:** None

### 3.3 Embedding Model Migration Job (M)
- **Problem:** Model switch requires manual export/re-embed. No migration path.
- **Solution:** Add `reembed` organizer job:
  1. Query metadata for entries where model/version differs from current
  2. Fetch content, re-embed, delete old entry, insert new
  3. Process in batches with budget constraint
- **Files:** `vector_index.py`, `jobs.py`
- **Dependency:** 2.1 (needs vector delete)

---

## Phase 4: Determinism & Performance

### 4.1 Cross-Rebuild Determinism Test (S)
- **Problem:** 8-float scoring with `math.exp()` transcendentals, rounded to 10 decimals. Never verified across full rebuild.
- **Solution:** New `test_determinism.py` — ingest fixed dataset, retrieve, record scores. Rebuild from event log, retrieve again, assert identical rankings.
- **Dependency:** None — can run immediately

### 4.2 Write Queue Parallelism (L) — *If deferred from Phase 2*
- See 2.3 above

---

## Execution Schedule

```
WEEK 1:  [1.1 Run Baselines] + [2.1 Vector Delete] + [4.1 Determinism Test]
              |
              v
WEEK 2:  [1.2 Diagnose & Fix] + [2.2 Adaptive Overfetch] + [3.2 CONTESTED Resolution]
              |
              v
WEEK 3:  [1.3 Real Datasets] + [3.1 Fallback Extraction] + [3.3 Re-embedding Job]
              |
              v
WEEK 4:  [1.4 Publish Results] + [2.3 Write Queue Parallel (if needed)]
```

### Parallelism Map

Items that can run simultaneously:
- **Week 1:** 1.1 + 2.1 + 4.1 (all independent)
- **Week 2:** 1.2 + 2.2 + 3.2 (all independent)
- **Week 3:** 1.3 + 3.1 + 3.3 (3.3 needs 2.1 done)

---

## Size Summary

| Item | Size | Impact | Risk |
|------|------|--------|------|
| 1.1 Run Baselines | S | CRITICAL | Low — infrastructure exists |
| 1.2 Diagnose & Fix | M | CRITICAL | Medium — may reveal deep scoring issues |
| 1.3 Real Datasets | M | HIGH | Medium — format mismatch possible |
| 1.4 Publish Results | S | HIGH | Low |
| 2.1 Vector Delete | S | HIGH | Low — USearch API supports it |
| 2.2 Adaptive Overfetch | M | MEDIUM | Low |
| 2.3 Write Queue Parallel | L | MEDIUM | High — concurrency is hard |
| 3.1 Fallback Extraction | M | HIGH | Low — Protocol pattern exists |
| 3.2 CONTESTED Resolution | M | MEDIUM | Low |
| 3.3 Re-embedding Job | M | MEDIUM | Low — needs 2.1 first |
| 4.1 Determinism Test | S | MEDIUM | Low — may reveal a bug |

---

## Key Risks

1. **Benchmark scores are bad (<50%).** If scoring weights need major tuning, item 1.2 grows from M to L. The 8-input composite has many knobs.
2. **Supersedence through `engine.store()` doesn't work.** If `ContentContradictionDetector` has low recall, temporal queries fail systematically. This is the most likely failure mode.
3. **Real dataset format mismatch.** LoCoMo/LongMemEval download URLs or JSON structure may differ from what the loaders expect.

---

## Success Criteria

| Metric | Target | Stretch |
|--------|--------|---------|
| LoCoMo (synthetic) | >60% | >75% |
| LongMemEval (synthetic) | >60% | >75% |
| LoCoMo (real dataset) | >65% | >80% |
| Temporal reasoning | >50% | >70% |
| Knowledge updates | >70% | >85% |
| All existing tests pass | 841/841 | +50 new tests |
| Vector delete + rollback cleanup | Working | Tested under failure |
| Fallback extraction | Working | <5s latency |
| CONTESTED resolution | Working | 30-day auto-resolve |
