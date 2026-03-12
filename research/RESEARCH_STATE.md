# PRME-X Research State

## Status: PHASE 2 COMPLETE

## Phase 1 Grade: A (92.8%) — COMPLETE
- Precision@1: 83.3% (10/12)
- Recall@5: 100% (12/12)
- Exclusion rate: 100% (12/12)
- MRR: 0.892
- Supersedence accuracy: 100% (10/10)

## Phase 2: Narrative Rewriter — COMPLETE

### What's Done
- `research/narrative/document.py` — NarrativeDocument model (sections, entries, audit trail)
- `research/narrative/rewriter.py` — NarrativeRewriter with RuleBasedGenerator
- `research/tests/test_narrative.py` — 32 tests (all passing)
- Full changing_facts scenario runs through rewriter successfully
- Auto-generates settled facts that integrate with Phase 1 retrieval
- Case preservation fixed: `_find_current_subject` checks narrative document first
- Redundant deprecation skipping: deprecations after migrations are no-ops
- Benchmark comparison: baseline vs rewriter retrieval quality measured
- 101/101 total research tests passing

### What's Working
1. **NarrativeDocument**: Structured living doc with topic sections, history, render
2. **RuleBasedGenerator**: Regex-based settled fact generation (no LLM needed)
3. **NarrativeRewriter.ingest()**: Wraps VSAMemory.store(), detects migrations,
   auto-generates settled facts, maintains narrative document
4. **Full scenario integration**: Rewriter handles database migration, editor switches
   (including switch-back), API migration, infrastructure migration
5. **Retrieval integration**: Auto-generated settled facts work with Phase 1 ranking
6. **Case preservation**: Deprecation path uses narrative document for proper-cased subjects
7. **Redundant skip**: Deprecation events after migrations don't create duplicate updates
8. **Benchmark**: Both baseline and rewriter achieve precision@1, but rewriter returns
   clean settled facts instead of noisy transition records

### Benchmark Results: Baseline vs Rewriter
Both achieve 4/4 precision@1, but the quality of the top result differs:
- **Baseline**: Returns transition records ("MySQL is no longer used", "I switched from VS Code to Neovim")
- **Rewriter**: Returns clean settled facts ("Our database is PostgreSQL with better JSON support", "My primary editor is VS Code")

The rewriter's settled facts provide direct answers without migration noise.
Baseline: 10 memories, Rewriter: 15 memories (5 auto-generated settled facts).

### Remaining Optional Items
1. **LLM backend**: The `SettledFactGenerator` protocol is defined but only
   `RuleBasedGenerator` is implemented. Phase 2b could add an LLM-driven generator
   for higher quality settled facts.
2. **Narrative persistence**: Serialize/deserialize the NarrativeDocument to disk.
3. **Settled fact quality**: The generated text is functional but template-like.
   E.g., "Our database is PostgreSQL with better JSON support and performance."
   An LLM could produce more natural text.

## Phase 1 Checklist
- [x] Core ops: bind, bundle, unbind, similarity, permute
- [x] Codebook: deterministic symbol→vector mapping, stemming, stopwords
- [x] Temporal encoding: absolute (hierarchical) and relative (permutation chain)
- [x] Memory store: store, retrieve, supersede, organize
- [x] Supersedence detection: phrase-based (100% accuracy)
- [x] Ranking precision: topic-word boost + transition penalty
- [x] Test suite: 69 tests (all passing)
- [x] Benchmark: Grade A (92.8%) on changing_facts scenario

## Phase 2 Checklist
- [x] NarrativeDocument model (sections, entries, history, render)
- [x] RuleBasedGenerator (regex + template settled fact generation)
- [x] NarrativeRewriter (event processing, integration with VSAMemory)
- [x] Test suite: 32 tests (all passing)
- [x] Full scenario integration test
- [x] FIX: Case preservation in deprecation path
- [x] FIX: Skip redundant deprecation updates
- [x] BENCHMARK: Compare auto vs manual settled facts
- [ ] OPTIONAL: LLM-based generator backend
- [ ] OPTIONAL: Narrative persistence (serialize/deserialize)

## Key Research Findings
1. Pure VSA needs semantic bridging (tags + stemming)
2. Supersedence requires phrase matching, not word matching
3. Same-day memories shouldn't supersede each other
4. Migration signal detection needs flexible patterns (broad verbs + regex)
5. Content similarity from random atomic vectors is noise (~0.01 magnitude)
6. Topic-word boosting (first content word) is a reliable query intent heuristic
7. Transition records need conditional demotion only when clean alternatives exist
8. **Settled facts are essential** — auto-generating them via narrative rewriting
   is the key insight connecting Phase 1 and Phase 2
9. Rule-based rewriting is sufficient for common patterns (migration, switch, deprecation)
10. **Narrative document as case-preserving cache** — checking the document before
    raw memories solves the tag-lowercase problem without needing case-mapping tables
11. **Redundant event detection** — deprecation events after migrations are information-free;
    detecting and skipping them keeps the narrative clean

## Files
### Phase 1
- `research/vsa/core.py` — Core VSA ops
- `research/vsa/codebook.py` — Symbol→vector mapping with stemming
- `research/vsa/temporal.py` — Temporal encoding
- `research/vsa/memory.py` — VSA memory store (retrieve with ranking fixes)

### Phase 2
- `research/narrative/__init__.py` — Module exports
- `research/narrative/document.py` — NarrativeDocument model
- `research/narrative/rewriter.py` — NarrativeRewriter + RuleBasedGenerator

### Tests
- `research/tests/test_vsa_core.py` — 36 tests
- `research/tests/test_vsa_codebook.py` — 10 tests
- `research/tests/test_vsa_temporal.py` — 8 tests
- `research/tests/test_vsa_memory.py` — 13 tests
- `research/tests/test_vsa_benchmark.py` — 2 benchmark tests (Grade A)
- `research/tests/test_narrative.py` — 32 tests (Phase 2)
