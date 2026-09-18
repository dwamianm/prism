# PRME competitive landscape snapshot — 2026-09-17

**Status:** Point-in-time product assessment

**Assessment date:** 2026-09-17

**PRME release:** v0.11.0

**PRME source revision:** `ec498a188c45f72a234d60b78c4ae7a7ab75fca6`

**Source branch:** `feat/memory-reliability-quality`

This document records how PRME compared with the agent-memory market on the
assessment date. It is not a permanent ranking. Product behavior, hosted
services, documentation, benchmark methodology, and adoption signals can all
change after this date.

When revisiting the evaluation, create a new dated snapshot instead of editing
the conclusions or time-sensitive measurements in this file. Corrections to
factual errors should remain visible in version control.

## Executive assessment

PRME was a technically deep but commercially early memory engine. Its strongest
position was auditable, correctness-first memory: immutable source events,
explicit epistemic and lifecycle state, evidence-bound corrections and
contradictions, atomic mutation records, deterministic retrieval, and portable
rebuildable storage.

PRME was not yet competitive as the default general-purpose memory product.
Mem0, Zep/Graphiti, Cognee, Supermemory, and Letta had materially larger
communities, stronger product surfaces, hosted offerings, and more favorable
published retrieval claims. PRME's own evidence did not establish cross-product
superiority, and several evaluations exposed retrieval, abstention, temporal,
and answerability gaps.

The credible position was therefore:

> The auditable, local-first temporal memory database for high-trust AI
> systems.

This was narrower than the generic "memory layer" category, but it aligned with
PRME's implemented differentiation and evidence.

## Scope and comparison method

The comparison separates competitors by product category:

- **Direct competitors:** Mem0, Zep/Graphiti, Cognee, and Supermemory. These
  products offered a persistent memory or context layer with extraction,
  retrieval, and storage.
- **Adjacent competitors:** Letta and LangMem. Letta was primarily a stateful
  agent runtime; LangMem was primarily memory-management tooling integrated
  with LangGraph storage.
- **Substitutes:** custom PostgreSQL/pgvector, vector-database, GraphRAG, or
  application-specific memory stacks. These could reproduce subsets of PRME at
  the cost of assembling their own lifecycle, provenance, and recovery rules.

The assessment used four evidence classes:

1. Current PRME source, documentation, and registered result artifacts.
2. Competitor documentation and public repositories maintained by the vendor or
   project.
3. GitHub repository metadata as an adoption and support-surface indicator.
4. Explicit inference where products did not publish comparable evidence.

Vendor benchmark claims are reported as claims, not independent validation.
Different answer models, judges, dataset variants, context budgets, retrieval
depths, and metrics make the headline numbers non-comparable unless a matched
harness proves otherwise.

## Summary scorecard

The ratings below are qualitative product judgments, not benchmark scores.

| Dimension | PRME position on 2026-09-17 | Assessment |
|---|---|---|
| Auditability and provenance | Documented strength | Potentially category-leading contract; not independently validated |
| Explicit corrections and conflicts | Strong | More explicit than the common update/delete or automatic-consolidation model |
| Determinism and reproducibility | Strong | Versioned scoring, replayable receipts, fixed policy boundaries, and rebuildable indexes |
| Embedded and local deployment | Strong | Competitive, but no longer unique |
| Current claim-state queries | Strong | Exact structured assertion-state and aggregation paths were differentiated |
| Historical temporal queries | Material gap | `knowledge_at` was not exact historical replay; Graphiti had the clearer bi-temporal story |
| Retrieval quality evidence | Behind | Useful internal results, but no matched cross-product leadership result |
| Context efficiency | Mixed | Strong results in some small matched tasks; weak tradeoff in the held-out LongMemEval-V2 answer run |
| Developer product experience | Adequate | Python, REST, MCP, CLI, LangChain, and LlamaIndex existed; no dashboard or TypeScript SDK |
| Hosted and enterprise product | Behind | No managed service, trust center, hosted governance layer, or published SLA |
| Ecosystem and adoption | Far behind | Alpha project with a very small public community relative to direct competitors |

## PRME differentiation

The graph/vector/lexical combination was not itself a defensible moat. By the
assessment date, most direct competitors had some combination of semantic,
keyword, entity, or graph retrieval.

PRME's differentiated combination was:

- An append-only event store with derived state intended to be rebuildable.
- A typed relational graph with provenance, validity, confidence, lifecycle,
  supersedence, and contradiction state.
- Atomic and checksummed records for covered lifecycle, correction,
  reinforcement, organizer, expiration, and ingestion operations.
- Evidence membership checks and owner/scope boundaries for public mutations.
- Deterministic hybrid ranking and versioned retrieval receipts capable of
  replaying the returned-candidate ranking.
- Exact structured assertion-state, assertion aggregation, and quantity
  aggregation paths that do not pretend semantic top-k retrieval is complete.
- A copyable local pack with optional PostgreSQL deployment and rebuildable
  external indexes.
- Explicit disclosure when temporal or aggregation coverage is not exhaustive.

No reviewed competitor documented this complete combination. This supports a
differentiation claim about the contract, not a universal quality or performance
claim.

## Direct competitors

### Mem0

Mem0 had the strongest drop-in product position: Python and TypeScript clients,
library, authenticated self-hosted server, dashboard, managed platform, broad
integrations, and a much larger community.

Its April 2026 memory architecture also narrowed PRME's architectural lead. Mem0
described ADD-only extraction, retained prior facts, a SQL history log, entity
linking, BM25, semantic retrieval, and fused ranking. This meant that
"append-only plus hybrid retrieval" was no longer a useful standalone PRME
differentiator.

Mem0's official pages were not internally consistent on headline benchmark
values at capture time:

- The evaluation documentation reported 91.6 on LoCoMo and 93.4 on
  LongMemEval.
- The GitHub README reported 92.5 on LoCoMo and 94.4 on LongMemEval.
- Both sources stated that the results came from the managed platform and
  included proprietary optimizations unavailable in the open-source SDK.

Consequently, those results could not be used as an OSS comparison without a
matched rerun.

PRME's counter-position was explicit truth management: typed epistemic states,
evidence-bound corrections, contradiction resolution, deterministic receipts,
exact structured state queries, and a more complete durable mutation record.

### Zep and Graphiti

Graphiti was PRME's closest architectural competitor. It documented episodic
provenance, custom entity and edge types, hybrid semantic/BM25/graph search,
incremental fact invalidation, and a bi-temporal model supporting point-in-time
queries. Zep added a managed enterprise Context Lake, governance, and a
sub-200-millisecond service claim.

Graphiti therefore had the stronger temporal-query story. PRME retained source
and mutation history but did not reconstruct an exact prior graph/index state:
`knowledge_at` filtered candidates from current state and explicitly returned
`exact_snapshot=False`.

PRME's counter-position was a simpler embedded artifact, more explicit
epistemic and correction semantics, deterministic scoring evidence, exact
assertion operations, and less mandatory graph-database infrastructure.

### Cognee

Cognee combined relational, vector, and graph storage with a broad document
ingestion and knowledge-graph pipeline. Its 2026 product material described
embedded SQLite, LanceDB, and Ladybug defaults, a managed cloud, a lightweight
Rust core, and a growing connector surface. This directly reduced the novelty
of PRME's embedded graph-vector-relational deployment story.

Cognee had the stronger ingestion-platform, connector, hosted-product, and
community position. PRME had the stronger documented contract for explicit
claim lifecycle, corrections, contradictions, provenance, deterministic
retrieval, and recovery from partially completed durable derivations.

Cognee's statements about one million monthly pipelines and adoption by more
than 70 companies were vendor claims and were not independently verified in
this assessment.

### Supermemory

Supermemory offered a managed memory and context product, connectors,
multimodal ingestion, automatically maintained static and dynamic user profiles,
and a self-hosted local binary using the same API as its platform.

Its repository claimed 95% LongMemEval Recall@15 with approximately 720 context
tokens. Recall@15 is a retrieval metric and was not comparable with PRME's
end-to-end answer accuracy. The underlying hosted and local model stacks also
differed.

Supermemory's local single-directory deployment weakened portability as a
standalone PRME moat. PRME's counter-position was a more explicit, inspectable,
and auditable truth model with deterministic storage and retrieval contracts.

## Adjacent competitors

### Letta

Letta was better categorized as a complete stateful-agent runtime. Its core
model used persistent editable memory blocks, out-of-context archival/recall
memory, perpetual message history, compaction, shared blocks, tools, schedules,
and agent-controlled context management.

Letta was the stronger choice when the desired product was an agent that managed
its own memory and context. PRME was the stronger fit when an application needed
to own memory semantics and prevent an agent from silently rewriting the
authoritative record. The products could also complement each other.

### LangMem

LangMem provided storage-independent memory managers and prompt optimizers plus
stateful integration with the LangGraph store. It explicitly modeled semantic,
episodic, and procedural memory and supported both hot-path and background
formation.

LangMem was the lower-friction choice for an existing LangGraph application and
had a stronger procedural-memory story. PRME provided a substantially deeper
storage, graph, correction, provenance, lifecycle, recovery, and audit layer.

## Quantitative evidence snapshot

### PRME evidence

The current evidence supported memory utility and several bounded efficiency
claims, but not market leadership:

| Evaluation | Recorded result | Boundary |
|---|---:|---|
| LongMemEval-V2 web-small held-out answer comparison | 80/149 (53.69%) versus 10/149 without memory | One local Qwen 9B reader; mean 43,195 PRME memory-context tokens; no competing memory system |
| LongMemEval-V2 same-cohort compact 4K development run | 49/149 | Material quality loss after an 83.05% mean context reduction |
| BEAM 100K extracted-memory development run | 13/20 (65.0%); mean rubric score 0.56750 | One tuned conversation; abstention and event ordering were 0/2 |
| MemoryAgentBench Banking77 development slice | 20/20 versus BM25 17/20 | 20 questions; 90.52% fewer retrieved-context tokens |
| MemoryAgentBench EventQA episode-routing development slice | 19/20 versus BM25 20/20 | 20 questions; opt-in policy; not a leadership result |
| Held-out evidence retrieval at 2,048 tokens | 85.27% support recall across 381 questions | Paired comparisons did not establish an advantage over vector/RRF baselines |
| Cited-answerability trials | Failed every preregistered gate | Experimental API; not automatic retrieval behavior |

These results came from different harnesses and measured different layers.
Candidate retrieval, context packing, and end-to-end answering must remain
separate in future comparisons.

### Competitor headline claims

The following table records what official project sources claimed on the
assessment date. It is not a leaderboard.

| Product | Headline claim captured | Important boundary |
|---|---|---|
| Mem0 | 91.6 or 92.5 LoCoMo; 93.4 or 94.4 LongMemEval; 64.1 BEAM 1M; under 7,000 mean tokens | Official pages disagreed; managed platform included proprietary optimizations |
| Graphiti/Zep | 94.7 LoCoMo and 90.2 LongMemEval; 155/162 ms retrieval; 5,760/4,408 context tokens | Vendor-authored methodology and service/model configuration |
| Supermemory | 95% LongMemEval Recall@15 and approximately 720 tokens | Retrieval recall, not end-to-end answer accuracy |
| Cognee | Production and adoption claims rather than a directly comparable current benchmark in the reviewed sources | No matched PRME comparison was identified |

The only defensible competitive quality conclusion was that PRME lacked a
same-harness, same-reader, same-budget cross-product result.

## Public adoption snapshot

GitHub metadata was captured through the public API on 2026-09-17. Stars and
forks measure attention and community surface, not correctness or product
quality, and the values will change after this snapshot.

| Repository | Stars | Forks |
|---|---:|---:|
| `dwamianm/prism` | 2 | 0 |
| `mem0ai/mem0` | 65,503 | 7,681 |
| `getzep/graphiti` | 30,964 | 3,146 |
| `topoteretes/cognee` | 30,778 | 3,048 |
| `supermemoryai/supermemory` | 29,955 | 2,623 |
| `letta-ai/letta` | 24,776 | 2,624 |
| `langchain-ai/langmem` | 1,670 | 189 |

PRME was also explicitly classified as Alpha in `pyproject.toml`. Its extensive
test and recovery work reduced technical risk, but did not substitute for
external deployments, independent evaluations, integrations, maintainers, or a
support ecosystem.

## Competitive implications

### What PRME could credibly claim

- Local-first and embeddable with an optional PostgreSQL path.
- Append-only source retention and rebuildable derived indexes.
- Explicit, auditable handling of uncertainty, corrections, contradictions,
  conditions, reinforcement, and lifecycle changes.
- Deterministic retrieval and versioned ranking evidence.
- Exact structured state and aggregation operations alongside honest
  non-exhaustive semantic retrieval.
- Portable storage with clear compatibility and recovery boundaries.

### What PRME could not credibly claim

- Best overall memory engine.
- Superior cross-product retrieval or answer quality.
- Exact historical state replay.
- Production-proven enterprise scale.
- Unique local, graph, hybrid, append-only, or portable architecture in
  isolation.
- Automatic answerability or reliable abstention.

### Highest-value next evidence

1. Run Mem0 OSS, Graphiti, Cognee, Supermemory, BM25, vector, RRF, and PRME
   through one frozen dataset, reader, judge, token budget, and failure policy.
2. Report retrieval, packing, answering, ingestion cost, query latency, and
   build time separately.
3. Complete exact bi-temporal replay or retain the narrower current-state claim.
4. Validate one production deployment where auditability changes a real
   operational or compliance outcome.
5. Expose the existing durability work through an operator-oriented inspection
   surface: provenance, timelines, pending work, recovery, and index health.
6. Improve project naming and search discovery. `PRME` and the `prism`
   repository competed with multiple unrelated projects using "PRISM" for AI
   memory.

## Sources captured

All external sources were accessed on 2026-09-17 unless otherwise noted.

### PRME

- [README retrieval results and product contract](../README.md#retrieval-quality-and-evaluation)
- [Benchmark measurement contract](../BENCHMARKS.md)
- [Roadmap and current evidence](../ROADMAP.md)
- [`pyproject.toml` package status and integrations](../pyproject.toml)
- [LongMemEval-V2 held-out answer report](../benchmarks/results/research/2026-09-14/LONGMEMEVAL-V2-WEB-UNSEEN-DETERMINISTIC-V1.md)
- [LongMemEval-V2 compact 4K development report](../benchmarks/results/research/2026-09-14/LONGMEMEVAL-V2-WEB-COMPACT4K-DEVELOPMENT.md)
- [BEAM 100K extracted-memory report](../benchmarks/results/research/2026-09-15/BEAM-100K-EXTRACTED-SCORED.md)
- [Held-out evidence-retrieval report](../benchmarks/results/evidence/2026-09-12/heldout/README.md)
- [GitHub repository metadata](https://api.github.com/repos/dwamianm/prism)

### Mem0

- [Memory evaluation](https://docs.mem0.ai/core-concepts/memory-evaluation)
- [Self-hosted setup](https://docs.mem0.ai/open-source/setup)
- [Open-source graph memory](https://docs.mem0.ai/open-source/features/graph-memory)
- [Repository](https://github.com/mem0ai/mem0)
- [GitHub repository metadata](https://api.github.com/repos/mem0ai/mem0)

### Zep and Graphiti

- [Graphiti overview](https://help.getzep.com/graphiti/getting-started/overview)
- [Adding episodes and provenance](https://help.getzep.com/graphiti/core-concepts/adding-episodes)
- [Custom entity and edge types](https://help.getzep.com/graphiti/core-concepts/custom-entity-and-edge-types)
- [Zep versus Graphiti](https://help.getzep.com/zep-vs-graphiti)
- [Repository](https://github.com/getzep/graphiti)
- [GitHub repository metadata](https://api.github.com/repos/getzep/graphiti)

### Cognee

- [How Cognee builds AI memory](https://www.cognee.ai/how-cognee-builds-ai-memory)
- [Cognee 1.0 announcement](https://www.cognee.ai/cognee-1-0-announcement)
- [Documentation introduction](https://docs.cognee.ai/reference/colab_notebooks)
- [Repository](https://github.com/topoteretes/cognee)
- [GitHub repository metadata](https://api.github.com/repos/topoteretes/cognee)

### Supermemory

- [Self-hosting quickstart](https://github.com/supermemoryai/supermemory/blob/main/apps/docs/self-hosting/quickstart.mdx)
- [User profiles](https://supermemory.ai/docs/concepts/user-profiles)
- [Repository and benchmark claims](https://github.com/supermemoryai/supermemory)
- [MemoryBench harness](https://github.com/supermemoryai/memorybench)
- [GitHub repository metadata](https://api.github.com/repos/supermemoryai/supermemory)

### Letta

- [Letta documentation](https://docs.letta.com/)
- [Memory blocks](https://docs.letta.com/tutorials/attaching-detaching-blocks/)
- [Python SDK memory model](https://docs.letta.com/api/python)
- [Repository](https://github.com/letta-ai/letta)
- [GitHub repository metadata](https://api.github.com/repos/letta-ai/letta)

### LangMem

- [Conceptual guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
- [Documentation home](https://langchain-ai.github.io/langmem/)
- [Repository](https://github.com/langchain-ai/langmem)
- [GitHub repository metadata](https://api.github.com/repos/langchain-ai/langmem)

## Revision policy

This file is an immutable dated assessment except for factual corrections. A
future review should create `COMPETITIVE-LANDSCAPE-YYYY-MM-DD.md`, record the new
PRME commit and release, rerun the public metadata capture, and explain changes
relative to this snapshot.
