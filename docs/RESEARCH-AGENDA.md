# Research Agenda: Toward 98%+ Memory Accuracy

## Failure Analysis (v0.6.0 baseline)

### LME: 93.8% → 98% (need to fix ~20 of 23 failures)

| Root Cause | Count | Examples |
|---|---|---|
| **Aggregation impossible with top-k** | 10 | "How many cuisines?", "Total money spent on workshops?" |
| **Knowledge update staleness** | 6 | Retrieves old value ("Chicago") when newest is "suburbs" |
| **Temporal computation errors** | 3 | Miscounts weeks between two events |
| **Retrieval miss** | 2 | Relevant fact exists but wasn't retrieved |
| **Abstention false negative** | 2 | Should have abstained, didn't |

### LoCoMo: 81.8% → 98% (need to fix ~25 of 27 failures)

| Root Cause | Count | Examples |
|---|---|---|
| **Fact buried in noise** | 8+ | "How many children?" — fact is incidental in a long turn |
| **Similar-but-wrong event** | 5+ | Retrieves a *different* painting project |
| **Entity fact not consolidated** | 5+ | "Where did Caroline move from?" — never extracted to fact |
| **Temporal reasoning** | 3 | Imprecise date inference from conversation context |
| **Inference across signals** | 1 | "Is Caroline religious?" — requires holistic synthesis |

### The Core Problem

The current architecture is **retrieve-and-present**: find relevant text chunks, stuff them into context, and hope the LLM generates the right answer. This hits a ceiling because:

1. **Raw content is noisy** — A 200-word conversation turn might contain one 10-word fact. Token budget fills with noise before critical facts surface.
2. **Top-k can't do exhaustive queries** — "How many X?" requires ALL instances, not the best 10.
3. **No temporal state tracking** — "What is X now?" requires finding the latest value, but retrieval scores by relevance, not recency.
4. **No reconstruction** — Humans don't replay memories verbatim; they reconstruct from schemas + cues. We should too.

---

## Research Theories

### Theory 1: Memory Consolidation Pipeline (MCP)

**Inspiration**: Hippocampal replay during sleep — the brain consolidates episodic memories into semantic knowledge through repeated reactivation.

**Proposal**: Multi-level memory hierarchy with automatic consolidation:

```
Level 0: Raw Events (append-only log)
    ↓ [extraction]
Level 1: Session Distillation (every fact/preference/decision per session)
    ↓ [entity consolidation]  
Level 2: Entity Knowledge Cards (structured, always-current view per entity)
    ↓ [schema abstraction]
Level 3: User Schema (habits, routines, relationships, compact profile)
```

**How it helps**:
- Aggregation queries hit Level 2: "How many cuisines?" → count entity→cuisine edges
- Knowledge update queries hit Level 2: entity card always has the LATEST value
- Single-hop queries hit Level 2: "How many children does Melanie have?" → `Melanie.children.count = 3`
- Multi-hop queries combine Level 2 cards: "What inspired Caroline's painting for the art show?" → look up `Caroline.art_show_painting.inspiration`

**Key innovation**: Level 2 Knowledge Cards are **living documents** — every new event that mentions an entity triggers a card update. The card is never stale.

**Expected impact**: Fixes knowledge_update (6), aggregation (10), single_hop buried-fact (8) = ~24 failures across both benchmarks.

### Theory 2: Reconstructive Retrieval

**Inspiration**: Bartlett's Schema Theory — human memory is reconstructive, not reproductive. We don't replay; we rebuild from schemas + cues + semantic knowledge.

**Proposal**: Replace retrieve-and-present with a multi-phase reconstruction:

```
Phase 1: Query Analysis → What answer SHAPE do I need?
    - count → need exhaustive list + counting
    - entity attribute → need entity card lookup
    - temporal → need timeline + date arithmetic
    - comparison → need two entities' attributes
    
Phase 2: Schema Activation → What knowledge structure fits?
    - Activate the right retrieval strategy per shape
    
Phase 3: Cue Retrieval → Get DISTILLED cues, not raw text
    - Retrieve from Level 2 (entity cards) first
    - Only drill to Level 0 (raw events) for detail/verification
    
Phase 4: Reconstruction → Build the answer from cues + schema
    - LLM reconstructs with structured cues, not raw context
    
Phase 5: Verification → Spot-check against raw events
    - For high-stakes answers, verify key facts against source events
```

**How it helps**:
- Aggregation: Schema says "count" → do exhaustive entity query, not top-k text search
- Knowledge updates: Schema says "current state" → look up entity card, not search events
- Multi-hop: Schema says "chain" → traverse entity graph, not text similarity

**Expected impact**: Fundamentally changes the retrieval→generation interface from "here's text, figure it out" to "here's structured knowledge, reconstruct the answer."

### Theory 3: Exhaustive Retrieval for Aggregation Queries

**The problem**: "How many cuisines have I tried?" with top-k retrieval might find 3 of 4 cuisine mentions. The system confidently answers "3" when the answer is "4". Worse, it might retrieve adjacent events and hallucinate "5".

**Proposal**: When query intent = AGGREGATION:
1. Extract the aggregation target entity/type ("cuisines", "properties viewed", "workshops attended")
2. Do a graph traversal: find ALL nodes connected to the target via relevant edges
3. Return the complete set to the LLM, not a relevance-ranked subset
4. Use Level 2 entity cards which already have pre-aggregated counts

**Key insight**: Aggregation queries don't need the "best" results; they need ALL results. This is a fundamentally different retrieval mode.

### Theory 4: Temporal State Machine

**The problem**: "Where does Rachel live now?" requires finding the LATEST mention of Rachel's location, not the highest-scoring one. If Rachel moved 3 times, the system might return the most semantically similar mention (the one with the best embedding match), which could be any of the three.

**Proposal**: For each entity attribute that changes over time, maintain a **state timeline**:
```
Rachel.location: [
    {value: "New York", valid_from: 2023-01-01, valid_to: 2023-06-15},
    {value: "Chicago", valid_from: 2023-06-15, valid_to: 2023-09-01},
    {value: "suburbs", valid_from: 2023-09-01, valid_to: null},  ← CURRENT
]
```

Queries about "current" state simply look up `valid_to = null`. No retrieval needed.

**Key insight**: This already exists partially in the graph store (valid_from/valid_to on edges). But it's not being leveraged for retrieval. The retrieval pipeline should check entity state timelines BEFORE doing embedding search.

### Theory 5: Context Compression via Fact Distillation

**The problem**: A 200-word conversation turn like "Hey, I went to the store today and picked up some groceries. Oh, I also redeemed that $5 coupon on coffee creamer at Target. The cashier was so nice..." contains one retrievable fact: "Redeemed $5 coupon on coffee creamer at Target."

**Proposal**: At ingestion time, extract facts into compressed form:
- Raw: 200 words → Distilled: 10 words per fact
- Store both, but retrieve distilled facts by default
- This means a 4000-token budget fits ~400 facts instead of ~20 raw events
- 20x more knowledge per token

**How it helps**:
- single_hop "Where did Caroline move from?" → "Caroline moved from Sweden" is retrievable
- Context packing: 400 facts > 20 raw events for information density
- Aggregation: scan 400 distilled facts for all cuisine mentions in one pass

### Theory 6: Contrastive Memory Encoding

**The problem**: "tennis" and "table tennis" have embedding similarity ~0.85. The system can't distinguish them via vector search. Similarly, "vintage cameras" vs "vintage films", "San Francisco" vs "Sacramento".

**Proposal**: Store contrastive features alongside each fact:
- When a new fact is similar to an existing one (cosine > 0.8), compute and store what DISTINGUISHES them
- At retrieval time, verify the retrieved fact matches the query's distinguishing features
- This is "elaborative encoding" from cognitive psychology

---

## Implementation Roadmap

### Phase A: Memory Consolidation (highest impact)
1. Enhance ingestion to always extract distilled facts (not just when organizer runs)
2. Build Entity Knowledge Cards (Level 2) — auto-updating structured summaries
3. Build aggregation indexes on entity cards

### Phase B: Reconstructive Retrieval
4. Implement answer-shape classification in query analysis
5. Build schema-driven retrieval strategies (entity lookup, graph traversal, timeline query)
6. Replace context-stuffing with structured cue packing

### Phase C: Temporal State Machine  
7. Implement entity attribute timelines with valid_from/valid_to
8. Add "current state" fast-path in retrieval for knowledge-update queries

### Phase D: Context Compression
9. Dual storage: raw events + distilled facts with cross-references
10. Fact-first context packing (facts fill budget before raw events)

### Phase E: Contrastive Encoding
11. Near-duplicate detection at ingestion
12. Contrastive feature storage and verification at retrieval

---

## Success Criteria

- LME ≥ 98% (470 queries, gpt-5-mini)
- LoCoMo ≥ 98% (152 queries, gpt-5-mini)  
- No regression on synthetic benchmarks
- Retrieval latency < 500ms p95
