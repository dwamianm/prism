# PRME Research Scan: SOTA Memory Systems & Techniques, 2025–2026 (2026-06-11)

Context: PRME at 94.7% LME / 89.8% LoCoMo with gpt-5-mini; target 98% without a stronger answering model. Blockers: (1) LLM counting/aggregation imprecision, (2) semantic retrieval gap on tangential mentions.

## Calibration: where PRME actually stands

PRME is already at or near the top of the credible field. The only systems reporting higher LME with a comparable answering model are Mastra OM (94.87% with gpt-5-mini) and Chronos High (95.6%, but with Claude Opus 4.6 — model-inflated). Higher claims (Supermemory 99%, OMEGA 95.4%) carry caveats below. The benchmark-number landscape is polluted: the [Mem0 vs Zep dispute](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/) ([Mem0's counter](https://github.com/getzep/zep-papers/issues/5)) showed the same system swinging 58%→84% on LoCoMo depending on adversarial-category inclusion, judge prompts, and configuration. Treat all vendor numbers as incomparable unless answering model + judge + category exclusions are disclosed.

## TIER 1 — Directly addresses both blockers

### 1. Chronos: structured event calendar + agentic tool-calling retrieval (highest relevance)
- **Source:** [arXiv 2603.16862](https://arxiv.org/abs/2603.16862) (March 2026)
- **Technique:** At ingestion, decompose dialogue into subject-verb-object event tuples with resolved datetime ranges and entity aliases, indexed in a structured "event calendar" plus a "turn calendar." At query time, a ReAct loop with four tools: `search_turns`/`search_events` (vector) and `grep_turns`/`grep_events` (exact keyword), with datetime-range constraints. Counting questions become temporal filters over the structured event index — computational, not LLM-counted.
- **Results:** 92.6% LongMemEval-S with GPT-4o (Chronos Low), 95.6% with Opus 4.6 (Chronos High); +7.67% over prior SOTA. **Ablation: the events calendar is the single largest contributor.** Per-category: KU 96.2%, TR 90.2%, MS 91.7%.
- **Assessment:** Independent validation of *both* of PRME's planned directions in one paper — entity/event extraction at ingestion (blocker 2) AND structured/computational queries for aggregation (blocker 1). Critical delta from what PRME tried: aggregation prompting failed because the LLM still counts; Chronos has the *retrieval layer* return a filtered, enumerated event list. PRME already has the substrate (DuckDB event store + graph) — exposing `grep_events`-style exact-match + time-window tools to an agentic answer loop is a natural fit. Reproducible-looking (real ablations, per-category numbers, multiple models). **Highest-priority read.**

### 2. Tool-augmented / agentic retrieval over memory (blocker 1 + LoCoMo retrieval gaps)
- **Sources:** [TA-Mem, arXiv 2603.09297](https://arxiv.org/abs/2603.09297) (2026) — agent autonomously selects between key-based lookup and similarity retrieval over a multi-indexed memory DB, evaluated on LoCoMo with claimed significant gains; [Supermemory ASMR](https://supermemory.ai/blog/we-broke-the-frontier-in-agent-memory-introducing-99-sota-memory-system/) — ~99% LongMemEval via parallel LLM reader agents (no vector DB), but **explicitly experimental, not their production engine (production: ~85%)**, uses agent swarms with Gemini, cost/latency not comparable. Flag: hype-adjacent but the direction is real.
- **Assessment:** The convergent 2026 finding is that single-shot top-k retrieval is the ceiling; systems breaking through let the answering model *iterate*: search, inspect, re-query with different terms, constrain by time. Directly attacks PRME's "tangential mention" failures. The wider-pools failure doesn't contradict this: wider pools add noise in one shot; iterative search adds *targeted* recall. Cost: more answering-model calls per question (still gpt-5-mini, within the constraint).

### 3. Failure-mode evidence supporting computational aggregation
- **Sources:** [EMem baseline paper, arXiv 2511.17208](https://arxiv.org/pdf/2511.17208); analysis of 118 LongMemEval errors found ~74.6% reasoning failures vs 25.4% retrieval failures (exact figure unverified — directional claim consistent across sources). Also [Dust on LLM counting limits](https://docs.dust.tt/docs/understanding-llm-limitations-counting-and-parsing-structured-data), and [OLLA, arXiv 2603.08443](https://arxiv.org/abs/2603.08443) (SQL-style COUNT over LLM-extracted structure, 1% error bound).
- **Assessment:** Confirms PRME's diagnosis. The fix pattern in the literature is uniformly "extract structure once at write time, aggregate in code" — never "prompt harder." With DuckDB underneath, PRME can do genuine SQL `COUNT(DISTINCT event)` over extracted event tuples; the open design question is dedup of repeated mentions of the same real-world event, which Chronos handles via entity-alias resolution at ingestion.

## TIER 2 — Validates/refines entity-attribute extraction (blocker 2)

### 4. Hindsight (TEMPR): narrative fact extraction + 4-channel retrieval
- **Source:** [arXiv 2512.12818](https://arxiv.org/html/2512.12818v1) (Dec 2025)
- **Technique:** At ingestion: coarse-grained chunking (2–5 self-contained facts/conversation), coreference resolution, temporal normalization to absolute ranges, typed entity extraction + canonical entity resolution (string similarity + co-occurrence + temporal proximity). Retrieval: 4 parallel channels (vector, BM25, graph spreading-activation, temporal) fused via RRF, then cross-encoder rerank. Four memory networks (world/experience/opinion/observation).
- **Results:** LoCoMo 89.61% with OSS-120B (claimed prior best 75.78%); LongMemEval 89.0% (OSS-120B), 91.4% (Gemini-3).
- **Assessment:** Strong open-model results — beats PRME's LoCoMo score with a weaker answering model, which matters given the no-stronger-model constraint. The load-bearing pieces for blocker 2 are *coreference resolution + canonical entity resolution at write time* — that's what makes tangential mentions findable. Caution: their cross-encoder rerank resembles PRME's failed neural reranker; the difference is reranking over *extracted facts*, not raw turns.

### 5. Mastra Observational Memory: the gpt-5-mini apples-to-apples datapoint
- **Source:** [Mastra research](https://mastra.ai/research/observational-memory) (late 2025/2026)
- **Technique:** No per-query retrieval at all. An Observer agent converts messages into dense *dated observations*; a Reflector periodically condenses/restructures; the full observation log sits statically in the answering model's context. Two-level bullets, temporal anchoring, 3–6× compression.
- **Results:** **94.87% LongMemEval with gpt-5-mini** — PRME's exact answering model, 0.2pp above PRME. 95.5% on temporal reasoning. They refuse to report LoCoMo, calling its scoring unreliable.
- **Assessment:** The most honest comparable out there, and it says something uncomfortable: a retrieval-free dated-observation log matches PRME's whole hybrid pipeline at LME scale. Transferable: (a) make observations exhaustive and always-in-context for the question's relevant entities rather than retrieved; (b) the Reflector is the only memory-consolidation design with benchmark-relevant evidence — maps onto PRME's organizer. Limits: works because LongMemEval-S histories compress into one context; doesn't scale to true long-horizon memory, PRME's actual product thesis.

### 6. Mem0's 2026 multi-signal retrieval (entity matching as a third signal)
- **Source:** [Mem0 State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026) (April 2026); [Mem0 paper](https://arxiv.org/pdf/2504.19413)
- **Technique:** Single-pass hierarchical extraction (agent-generated facts weighted equally with user statements) + three parallel retrieval signals — semantic, BM25, **entity matching** — with normalized score fusion. They *dropped* their graph store in favor of built-in entity linking.
- **Results (self-reported, treat skeptically):** LoCoMo 92.5, LME 94.4 at ~7K tokens/query; biggest gains temporal (+29.6) and multi-hop (+23.1).
- **Assessment:** Entity-match as an explicit fused retrieval signal (not just expansion, which PRME has in Stage 2.5) is a cheap, deterministic experiment — fits the determinism constraint better than learned rerankers. Answering model undisclosed; absolute numbers are marketing, the *direction* (entity signal beats graph traversal for these benchmarks) is corroborated by Chronos and Hindsight.

### 7. Other extraction-at-write-time corroboration
- [Memori](https://memorilabs.ai/blog/memori-locomo-paper-results/) ([paper](https://arxiv.org/html/2603.19935)): SQL-native memory, semantic triples + session summaries; 81.95% LoCoMo at 1,294 tokens/query — proof the structured-store approach is token-efficient, not more accurate.
- [ProMem, arXiv 2601.04463](https://arxiv.org/abs/2601.04463): *iterative self-questioning* during extraction to recover facts missed in a single pass — relevant because tangential-mention failures are exactly single-pass extraction misses. Scores modest; the mechanism is the takeaway.
- [EverMemOS, arXiv 2601.02163](https://arxiv.org/abs/2601.02163): MemCells → MemScenes → agentic retrieval; 92.3% LoCoMo / 82-83% LME, biggest gains on knowledge-update (+15.5pp) — PRME's knowledge-update handling already works. Lower priority.
- [HippoRAG 2](https://www.emergentmind.com/topics/hipporag-2) (Feb 2025): PPR over KG with passage nodes; +7 F1 on associative/multi-hop QA. Designed for document corpora, not conversational memory; no LoCoMo/LME numbers. Watch, don't adopt.

## TIER 3 — Consolidation / organizer (weak benchmark evidence)

- **[Letta sleep-time compute](https://www.letta.com/blog/sleep-time-compute)** (Apr 2025): evidence is about **token efficiency at equal accuracy** (5× test-time reduction), *not* accuracy gains. Relevant to the organizer philosophically; do not expect benchmark movement.
- Mastra's Reflector (above) is the strongest evidence that periodic reorganization of extracted facts contributes to top-tier scores — but unablated.
- MemoryBank-style forgetting curves: no 2025–2026 evidence that forgetting mechanics move LoCoMo/LME accuracy. Skip for the 98% push.
- **Late-interaction (ColBERT) for conversational memory: no direct evidence found.** Nobody credits late interaction for memory-benchmark gains. Given PRME's embedding-swap and reranker failures, deprioritize.

## Bottom line for PRME's 98% push

1. **Blocker 1 (counting):** Build Chronos-style structured event tuples (SVO + resolved datetime + canonical entity) in the existing DuckDB layer, and answer aggregation questions from a code-side temporal/entity filter that returns an enumerated, deduped list (or the count itself) to gpt-5-mini. Strongest evidence in the field.
2. **Blocker 2 (semantic gap):** Canonical entity resolution + coreference at ingestion (Hindsight's recipe) so tangential mentions index under the entity, plus entity-match as an explicit fused retrieval signal (Mem0 2026). If insufficient, add a bounded iterative-retrieval loop (Chronos/TA-Mem ReAct pattern) — 2 extra gpt-5-mini calls on low-confidence questions, within the no-stronger-model constraint.
3. **Reality check:** With gpt-5-mini, the honest comparable ceiling demonstrated so far is ~95% on LME (Mastra). 98% likely requires the structured/computational path — nobody has published 98% with a mini-class answering model; if PRME gets there via computational aggregation, that's a genuinely novel result.

## Sources

[ByteRover](https://www.byterover.dev/blog/benchmark-ai-agent-memory) · [Zep rebuttal](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/) · [Mem0 correction issue](https://github.com/getzep/zep-papers/issues/5) · [Mastra OM](https://mastra.ai/research/observational-memory) · [OMEGA](https://omegamax.co/benchmarks) (95.4% LME but GPT-4.1 answering model — not comparable) · [Chronos](https://arxiv.org/abs/2603.16862) · [Hindsight](https://arxiv.org/html/2512.12818v1) · [TA-Mem](https://arxiv.org/abs/2603.09297) · [Supermemory ASMR](https://supermemory.ai/blog/we-broke-the-frontier-in-agent-memory-introducing-99-sota-memory-system/) · [Mem0 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026) · [Memori](https://arxiv.org/html/2603.19935) · [ProMem](https://arxiv.org/abs/2601.04463) · [EverMemOS](https://arxiv.org/abs/2601.02163) · [EMem baseline](https://arxiv.org/pdf/2511.17208) · [OLLA](https://arxiv.org/abs/2603.08443) · [Letta sleep-time compute](https://www.letta.com/blog/sleep-time-compute) · [HippoRAG](https://github.com/osu-nlp-group/hipporag) · [LongMemEval](https://arxiv.org/abs/2410.10813)
