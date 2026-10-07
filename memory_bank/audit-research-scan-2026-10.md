# PRME Research Scan: Agent Memory, September–October 2026 (2026-10-06)

Context: PRME v0.13.0 stands at 90.6% LongMemEval-S and 81.2% LoCoMo on the
DeepSeek answer track, with the published GPT-5.4 reference at 86.0% and 64.0%
against a 3,996-token budget. Work follows [epic #77](https://github.com/dwamianm/prism/issues/77).
Targets: LoCoMo ≥ 85% and LongMemEval-S ≥ 90.2% at 8K or less.

This scan covers 714 arXiv papers (cs.CL, cs.AI, cs.IR, cs.LG; submitted
2026-08-01 to 2026-10-05) retrieved with 15 queries and cross-checked against
the [2026-06-11 scan](audit-research-scan.md) and every arXiv reference already
in the repository. The newest paper PRME cites anywhere is 2608.29605
(2026-08-30); nothing in this scan appears in the repository today. Vendor
blog numbers are excluded except where they carry a disclosed reader, judge and
budget. Following the calibration rule in `docs/RESEARCH-AGENDA.md`, every score
below is quoted with its conditions and is never compared directly with PRME's.

## Calibration: where the frontier moved

| System | LongMemEval-S | LoCoMo | Reader and budget | Limits |
|---|---:|---:|---|---|
| PRME v0.13.0 (DeepSeek track) | 453/500 (90.6%) | 1,250/1,540 (81.2%) | fixed 2026-09-23 reader, strict judge, 3,996 tokens | no GPT-5.4 run of the current defaults |
| PRME GPT-5.4 reference (2026-09-23) | 430/500 (86.0%) | 985/1,540 (64.0%) | gpt-5.4, strict judge, 3,996 tokens | retrieval defaults before 2026-09-25 |
| [MemStrata](https://arxiv.org/abs/2610.05343) | 475/500 (95.0%) | 1,400/1,540 (90.91%) | local Qwen 3.8 27B Q4_K_M, 24,000-token ceiling, source-aware GPT-5.5 grading | same answers score 92.6% and 78.25% reference-only; all questions development-exposed, no holdout |
| [Mnemon](https://arxiv.org/abs/2609.36059) | 83.8% | 91.7% | gpt-4.1-mini answering, under 4K context | scored inside a public re-evaluation of 14 systems; PRME is absent from that cohort |
| [MERA](https://arxiv.org/abs/2609.37443) | 71.29% | 77.40% | Qwen3-30B evidence and answer model, trained 0.6B planner | measures its own retrieval loop, not a shared protocol |
| [EGMEMORY](https://arxiv.org/abs/2609.23465) | — | 73.6% (dyadic) | prompting and tool use, no policy training | GroupMemBench/EverMemBench are its own benchmarks |
| [Mem++](https://arxiv.org/abs/2610.02002) | second, behind its entity-graph variant | best mean LLM-judge score | gpt-4.1-mini | OrgMemBench gain is 8.0–13.1 points, its own benchmark |

Two observations matter more than any single row. First, the 90–95% band on
these benchmarks is now reached with local or small hosted readers under
budgets from 4K to 24K; the reader is no longer what separates the top of the
field. Second, the two largest reported gains in this window come from the two
things epic #77 has not shipped yet: nonduplicated dated source spans at a
higher budget (MemStrata, #90/#91) and retrieval that re-queries on evidence
already found (MERA, #94). The [audit](AUDIT-2026-09-23-BENCHMARK-GAP.md)
projected about 82% on LoCoMo at 8K from raw turns and named phases 4 and 5 as
the remaining gap. The September–October literature fills both phases with
worked examples.

## Tier 1 — Directly addresses open epic #77 tickets

### 1. MERA: retrieval conditioned on evidence already found (#94, #93)

- **Source:** [arXiv 2609.37443](https://arxiv.org/abs/2609.37443) (2026-09-29)
- **Technique:** separates a globally searchable memory from a
  question-specific evidence state. Verified evidence guides the next retrieval
  round without restricting access to global memory. A lightweight planner is
  trained by reinforcement learning with a reward for queries that recover
  previously missing evidence.
- **Results:** LoCoMo cumulative evidence recall rises from 55.5% to 80.5%
  across rounds; 77.40% answer accuracy on LoCoMo and 71.29% on LongMemEval-S
  with Qwen3-30B, beating a 30B planner without retrieval-grounded training by
  4.10 and 3.96 points. The trained planner is 0.6B parameters.
- **Assessment:** This is the missing evidence for #94, which the roadmap
  currently lists with no projection. The mechanism is what the audit's item 4
  describes — multi-hop questions need 3.1 annotated turns on average, only 16%
  get all their evidence into the context at 4K, and their median rank is 51.
  The delta from PRME's attempts: #87 tried to make ranking matter by shrinking
  candidate lists and lost LongMemEval evidence; MERA instead keeps the full
  store reachable and spends its second round on the *missing* evidence, which
  is the exact shape of PRME's multi-hop loss. Because the planner is trained,
  and PRME's constraint is deterministic replay plus no query-time model calls
  in the evidence gate, the admissible first experiment here is the
  deterministic version: condition a bounded second round on the assertion-state
  and aggregate paths PRME already has. Treat the trained planner as a
  later, separately registered option.

### 2. Write-time dated facts at the *source-span* level (#91, #92, #90)

- **Sources:** [MemStrata](https://arxiv.org/abs/2610.05343) (2026-10-04),
  [EnSIMem](https://arxiv.org/abs/2609.27279) (2026-09-23),
  [PACMI](https://arxiv.org/abs/2610.05732) (2026-10-05)
- **Technique:** MemStrata keeps a retrieval backbone and adds *nonduplicated,
  dated, speaker-attributed source spans*. EnSIMem builds
  `[entity][entity_type][property: value]` index entries that each preserve
  source turns and temporal information, and answers from preserved source
  evidence rather than from lossy summaries. PACMI represents memories and new
  evidence in a provenance graph with typed dependency edges, assigns each
  record to a four-state validity lattice, and propagates validity changes to
  dependent records.
- **Results:** MemStrata: 95.0% LongMemEval-S and 90.91% LoCoMo at a
  24,000-token ceiling with a local 27B reader; a same-reader full-history
  control with about 4.7× the evidence scored 470/500, and keyword-only
  selection at the same budget scored 425, so selection rather than budget is
  doing the work. PACMI leads its own 100-case diagnostic and its premise
  checker reports perfect precision, recall and F1 on the controlled query
  distribution; removing cascading propagation raises final-answer errors from
  3 to 11, a paired difference that does not reach significance.
- **Assessment:** Three independent systems converge on dated, entity-attached,
  nonduplicated spans — the same target as #91/#92 — and one of them reports
  speaker attribution (#84) inside the same representation. MemStrata's
  full-history control is the useful number for #90: at the same reader, adding
  4.7× evidence gained 6 and 9 questions depending on grading, which argues for
  a smaller budget with better selection rather than a larger one. This also
  supports #87's original instinct even though PRME's bounded-limit experiment
  lost LongMemEval evidence: the limit failed when discovery, not ranking, was
  carrying recall. PACMI is the closest published analogue of the #222/#223
  supersedence and contradiction work and it reports that the *cascade* is what
  fixes memory state, not per-claim judgment alone.

### 3. Selection instead of extraction, and where reranking pays (#87, #88, #90)

- **Source:** [When Does Selection Replace Extraction?](https://arxiv.org/abs/2609.34227)
  (2026-09-28; pre-registered, held-out LoCoMo conversations and LongMemEval)
- **Technique:** compares raw turns selected by a single call to a typed
  decision model against an LLM-extraction memory, at matched context, and
  sweeps candidate-set size against reranking gain.
- **Results:** at a tight budget on LoCoMo, raw turns selected by one typed
  decision call are non-inferior to LLM-extraction memory (one-sided 95% bound
  −3.0 points against a −5-point margin), and raw turns cost 3,061× less to
  write. Reranking adds 17.4 points on LoCoMo and 9.1 on LongMemEval when three
  of thirty candidates are kept; at generous budgets it adds 1.5 and 1.1, and
  extraction systems are more accurate. At matched context the typed selector
  matches an LLM reranker (bound −2.0) at a third of the latency. Reranking
  lowers correct abstention.
- **Assessment:** this is the study PRME's #87 and #88 needed. #87 found that
  smaller candidate limits lost LongMemEval-S evidence, and #88 found no
  cross-encoder variant that beat rank fusion on both benchmarks at both
  budgets. The paper supplies the missing variable: reranking's value is a
  function of how aggressively candidates are cut, and it is largest exactly
  where PRME's multi-hop recall is worst. It also warns that reranking degrades
  correct abstention, which matters for #80's fidelity floors. Its non-inferiority
  result for selection over extraction is a direct caution for #91: PRME should
  keep the verbatim turn beside every extracted claim rather than replacing it
  (see the *Fidelity Before Structure* caution below).

### 4. The benchmark PRME should be measured in, and a comparable for its reader (#77, #95, #98)

- **Sources:** [Mnemon](https://arxiv.org/abs/2609.36059) (2026-09-28),
  [Auditing Long-Term Memory Evaluation](https://arxiv.org/abs/2609.38021)
  (2026-09-29), [EvalMem](https://arxiv.org/abs/2609.22231) (2026-09-03)
- **Technique:** Mnemon keeps conversations as raw dated records, has an LLM
  plan searches, a typed decision model judge what the searches return, and
  budgeted rules turn the judgments into a small view for an unchanged
  answering model; a background pass consolidates each record once into topic
  timelines, value histories and standing instructions linked to the records.
  The audit re-judges historical passes and varies the reader. EvalMem splits
  every failure into encoding, retrieval and generation with three parallel
  examiners.
- **Results:** Mnemon scores 91.7% LoCoMo (highest of fourteen) and 83.8%
  LongMemEval-S at under 4K tokens with gpt-4.1-mini, inside a public
  re-evaluation. The audit finds that re-judging the same pass-1 answers changes
  three labels, that reader lanes on fixed packets span 93 to 479, and that
  paired tests between the two strongest lanes establish neither superiority nor
  equivalence. EvalMem attributes 22.1% of LoCoMo defects to retrieval against
  7.7% to encoding and 6.5% to generation, and its recall-first agentic search
  raises recall of present evidence from 70.2% to 95.6%.
- **Assessment:** the audit is independent corroboration of the measurement
  contract in `BENCHMARKS.md` and of #178: judge drift and reader choice can
  move a headline more than the system change under test, and no untouched
  holdout has been evaluated anywhere in this literature. EvalMem's recall-first
  search is a cheap, deterministic diagnostic that separates "the fact was never
  stored" from "the retriever missed it" — the same distinction PRME's #102 and
  the extracted-pack gate work on. Mnemon matters twice: it is the strongest
  external evidence that the four Jev tickets (#220–#223) describe the right
  architecture, and its public 14-system cohort is a place PRME can appear at
  matched reader and budget, which is cheaper and more persuasive than another
  vendor comparison.

### 5. Consolidation decisions are not quality scores (#91, organizer gates)

- **Source:** [The Epistemics of Agent Memory](https://arxiv.org/abs/2609.33013)
  (2026-09-26)
- **Technique:** four phases on the consolidation decision — what to keep,
  compress, abstract into skills and rules, or forget. Phase 3 introduces
  ConsolidationBench, an oracle-by-construction benchmark scoring consolidation
  decisions against a known optimum on three non-circular axes. Phase 4 wraps
  the decision in poison-resistance, reversibility and auditability guarantees
  with a quality gate.
- **Results:** a learned promotion policy reached +22.7% task success at 7×
  compression but exposed a degenerate-forgetting failure and a distribution-shift
  failure. Production retrieval systems retain information yet score zero on
  cross-level transfer. Governance is statistically distinct from the quality
  score (r² = 0.43; identical-quality policies differ threefold in governance).
  In the resolved negative: a two-benchmark study with 2,532 real answer cells
  finds the quality score does not predict real transfer accuracy (pooled
  Spearman ρ = −0.24, n = 12, CI spanning zero).
- **Assessment:** read this before any organizer-level gate becomes acceptance
  evidence. It is the strongest statement in the window that a
  consolidation-quality metric can be internally sound and still predict
  nothing about answers — the failure mode PRME's promotion, decay and
  summarization work would fall into if it were tuned on its own metric. The
  governance half (reversibility, auditability, poison resistance as separate
  guarantees from quality) matches PRME's transactional journal and
  before/after record discipline, and its finding that identical-quality
  policies differ threefold in governance is a usable argument for why those
  records are part of the product rather than overhead.

### 6. Aggregation and list questions (#93, and the aggregation APIs)

- **Sources:** [APDMem](https://arxiv.org/abs/2610.02472) (2026-10-01),
  [JustMem](https://arxiv.org/abs/2609.19877) (2026-09-17)
- **Technique:** APDMem represents history as four progressively detailed
  layers (thematic summaries, personalized key facts, turn-level evidence notes,
  raw messages) and lets a controller read the cheapest layer that can answer,
  drilling deeper for temporal, multi-hop or exact-evidence questions; a note
  synthesizer consolidates facts, orders events and flags contradictions before
  generation. JustMem formulates access along discovery breadth and reading
  fidelity, with LOOKUP, COMPOSE and REPLAY modes chosen per query.
- **Results:** APDMem reports strong long-context reasoning while reading only
  8% of the total conversations. JustMem reports the highest mean accuracy and
  retrieval recall among its compared systems on LoCoMo and LongMemEval-S with
  substantially fewer generative-model tokens for construction and inference.
- **Assessment:** both are per-query adaptivity, the direction #93 (per-entity
  topic cards) and #94 sit in, and both do it *without* adding a model call at
  retrieval time — the controller reads a cheaper representation first. For
  PRME, the concrete proposal is that the packing layer should be able to answer
  "is the summary sufficient, or do I need the source turn" as an explicit,
  replayable decision rather than a fixed representation level, which is
  consistent with #89's four-level tokenization and #80's fidelity floor. Note
  that both systems report accuracy, not evidence coverage, so neither settles
  #90's budget question on its own.

## Tier 2 — Entity identity, maintenance and the Jev tickets (#220–#223)

### 7. Jev-style decisions over raw records: the Mnemon result (#220–#223)

- **Source:** [Mnemon](https://arxiv.org/abs/2609.36059) (2026-09-28)
- **Assessment of the specific design:** a typed decision model doing many small
  independent yes/no judgments (is this record needed; is it still current) in a
  third of a second, with an LLM doing only query planning and answer
  composition, reaches 91.7% LoCoMo at under 4K tokens. PRME's #220 probe
  measured 465 candidate pairs judged for about $0.013 and a third of a second
  per request. The published system is the same division of labour at benchmark
  scale, which is the acceptance evidence the four Jev tickets currently lack.
  The caution from PRME's own probe still stands and is not contradicted: a
  typed model may propose, not decide identity, and answers must be stored and
  replayed rather than re-requested.

### 8. Topical links and where graph traversal helps — and does not (#221)

- **Sources:** [Entity-Memory Graph Retrieval](https://arxiv.org/abs/2608.27925)
  (2026-08-28), [TAGGRAPH](https://arxiv.org/abs/2609.38353) (2026-09-29),
  [EngramRAG](https://arxiv.org/abs/2609.32049) (2026-09-25)
- **Results:** Entity-Memory graph retrieval — verbatim Memory nodes linked
  through shared Entities plus directed chronological edges, with entity gating,
  semantic fusion, one-hop chronological recovery and dense backfill — raises
  official LoCoMo evidence recall@25 from 79.75% to 84.48%, but *no matched
  cutoff supports an overall final-answer F1 difference*, and recall is
  sensitive to the embedding artifact. TAGGRAPH finds that on LongMemEval-S
  plain BM25 reaches 0.867 MRR against 0.844 for its strongest graph
  configuration, and that extraction-quality and vocabulary normalization
  dominate. EngramRAG's usage-modulated Personalized PageRank plus a SUPERSEDES
  DAG reports Recall@5 gains on all 1,982 LoCoMo QA pairs and reduces
  split-brain hallucinations from 70.0% to 0.0% in controlled mutation tests.
- **Assessment:** this is the most useful result in the window for #221's
  acceptance criteria. PRME's probe already showed that Jev links close the
  entity-isolation gap (entities with no edge fall from 49% to 24%); the
  published precedent says the same design buys evidence coverage, not answers,
  and that a strong lexical baseline can still beat it. #221 should therefore
  keep its evidence-gate-first acceptance criterion exactly as written and not
  expect an answer-level gain from traversal alone. EngramRAG's use of the
  supersedence DAG to *suppress obsolete state at retrieval time* is the more
  promising half for #222/#223.

### 9. Supersedence and contradiction between stored claims (#222, #223)

- **Sources:** [PACMI](https://arxiv.org/abs/2610.05732) (2026-10-05),
  [When Evidence Changes](https://arxiv.org/abs/2610.03902) (2026-10-02),
  [Temporal relation work already in PRME](https://github.com/dwamianm/prism/issues/91)
- **Results:** PACMI's four-state validity lattice with typed dependency edges
  leads its own benchmark, and the paired difference from the strongest baseline
  is significant under an exact McNemar test; the cascade improves final-answer
  accuracy mostly through memory-state correctness (errors 11 → 3 without
  significance). The evidence-revision study compares repair against re-reading
  and finds that on short records local repair uses 5–10× fewer revision tokens
  than rebuilding, yet *every* memory pipeline costs at least twice full
  re-reading, and source-filtered re-reading stays cheapest; none of its four
  confirmatory tests reached significance.
- **Assessment:** two papers, one conclusion each, both directly usable. First,
  supersedence is worth doing for *state correctness* and stale-premise
  detection, and that is where it should be measured — PRME already reports
  assertion state and contradiction links, and PACMI's node/context/answer
  separation is the right reporting shape for #222/#223. Second, the
  cost comparison is a caution for the organizer's whole premise at small
  scale: on short histories, keeping memory is more expensive than re-reading
  filtered source. PRME's product thesis is large histories, so this does not
  refute the organizer, but it does say that any organizer-cost claim must be
  measured against a source-filtered re-reading control, which PRME's
  benchmark harness does not currently include (#95 adds full-context and
  plain-RAG arms, not this one).

### 10. Entity structure and the storage substrate (#93, DuckDB path)

- **Sources:** [EnSIMem](https://arxiv.org/abs/2609.27279) (2026-09-23),
  [Graph Memory for LLM Agents: At What Cost?](https://arxiv.org/abs/2609.23315)
  (2026-09-20), [PolyMemDB](https://arxiv.org/abs/2608.25577) (2026-08-26)
- **Results:** the systems paper benchmarks a 1.02M-node, 5.34M-row property
  graph across eight engines including DuckPGQ on a twenty-query workload. No
  system is categorically fastest: a native graph engine wins narrow bounded
  neighborhoods, the columnar engine wins shapes that scan or join a large
  fraction of the graph, and DuckPGQ is measurably slower *purely due to query
  plan choice*. Bulk-ingest throughput varies three orders of magnitude across
  engines (5.0k–4.3M rows/s) and dominates total cost below roughly 10^5 queries
  per data refresh.
- **Assessment:** PRME's DuckPGQ graph uses recursive CTEs precisely because
  SQL/PGQ is unavailable on the supported DuckDB build, so the plan-choice
  warning applies directly. The useful, low-cost action is to add the
  neighborhood, bounded-path, set-intersection and anti-join shapes from this
  workload to PRME's own performance checks so that a graph query-plan
  regression is caught as a test rather than as a retrieval-latency surprise.
  The ingest-throughput finding is the same story as PRME's batched-ingestion
  work: for a memory pack that refreshes far more often than it is queried, the
  write path is the cost centre.

## Tier 3 — Caution: results that argue against planned work

These are the papers to read before committing to phases 4 and 5.

1. **Fidelity Before Structure** ([2601.00821v4](https://arxiv.org/abs/2601.00821)):
   within one fixed retrieval-rerank-reasoning pipeline, swapping only the
   stored representation shows verbatim chunks beating LLM-extracted typed
   artifacts by 15.9 points on LoCoMo (43.9% vs 28.0%) and 22.0 points on
   LongMemEval-S (67.4% vs 45.4%); a 1-hop semantic graph does not recover the
   gap, and accuracy tracks how much source text survives in the store. *Adding*
   artifacts alongside chunks preserves accuracy; substituting them forfeits it.
   Direct consequence for #91/#92: write-time facts must augment the verbatim
   turn, never replace it, and #80's fidelity floor is the mechanism that
   enforces this.
2. **TAGGRAPH** ([2609.38353](https://arxiv.org/abs/2609.38353)): on
   LongMemEval-S, BM25 (0.867 MRR) beats the strongest graph configuration
   (0.844) and a raw-input external reference (0.880). Missing extraction tags
   are common among top-five misses. Graph work should be justified per
   configuration, never assumed.
3. **The Epistemics of Agent Memory** ([2609.33013](https://arxiv.org/abs/2609.33013)):
   a consolidation-quality score did not predict real transfer accuracy
   (ρ = −0.24, CI spanning zero). Do not promote a consolidation metric to an
   acceptance gate.
4. **Entity-Memory Graph Retrieval** ([2608.27925](https://arxiv.org/abs/2608.27925)):
   an 84.5% evidence-recall improvement with no supported answer-level gain.
   Evidence coverage is a gate, not a result (#78 already says this).
5. **When Evidence Changes** ([2610.03902](https://arxiv.org/abs/2610.03902)):
   on short records every memory pipeline costs at least twice source-filtered
   re-reading; memory only wins on long histories and often through truncated
   extraction. Any efficiency claim needs that control.
6. **Fidelity/abstention interaction** ([2609.34227](https://arxiv.org/abs/2609.34227)):
   reranking lowers correct abstention, so ranking changes must be reported
   beside abstention behaviour.

## Tier 4 — Security, isolation and portability (product surface, not scores)

- [MemLeak](https://arxiv.org/abs/2610.04195) (NeurIPS-PALM 2026): in a shared
  vector store, ordinary cosine-similarity retrieval leaks other users' memories
  without any exploit — 70–100% incidental leakage under pooled same-team
  retrieval, 90–100% top-k placement for adversarially crafted memories with
  score lifts of +0.416 to +0.511, and end-to-end response contamination of
  5.00/5 (4.67/5 with Claude Sonnet 4.5), with contaminated responses often
  scoring as *more* helpful than clean ones. Of three mitigations, only hard
  post-retrieval ownership gating restored the clean baseline at roughly 1.4 ms
  per query. This is the strongest external argument for PRME's owner-scoped
  retrieval and for keeping the isolation claims in `docs/WORKSPACES.md`
  narrow; it also suggests an adversarial test PRME does not have: verify that
  a scoped retrieval cannot surface a semantically adjacent foreign memory even
  when the store is shared.
- [Does Your Agent's Memory Survive a Model Upgrade?](https://arxiv.org/abs/2609.05339):
  fixed-schema structures transfer after a writer swap (+0.0004 ± 0.0020);
  compressed natural-language notes shift asymmetrically by +9.91 or −13.28
  points; partial 50/50 embedding migrations capture only 4.96 of an 11.90-point
  full re-embedding gain; 80% of the notes deficit is construction loss.
  Relevant to `prme rebuild` and to model-version metadata on embeddings.
- [Memory Canonicalization](https://arxiv.org/abs/2610.05124): cross-model
  drift, with an honestly reported negative — the emotional-consistency
  improvement does not survive multiple-comparison correction and no factual
  drift comparison reached significance. Useful as a template for how to report
  a small pilot, not as a result.
- [Authority Before Utility](https://arxiv.org/abs/2609.37474) and
  [Audience-Bound Persistent Memory](https://arxiv.org/abs/2609.36373): memory
  permission and audience as first-class lifecycle attributes, which is the
  direction `docs/ENTITY-IDENTITY.md` and the scoped-workspace work already point
  at.

## Bottom line for epic #77

1. **#94 is no longer speculative.** MERA's numbers (LoCoMo evidence recall
   55.5% → 80.5%, multi-round) plus the audit's multi-hop diagnosis make
   bounded second-hop retrieval the highest-expected-value unstarted phase.
   Register it on the offline evidence gate first and start with the
   deterministic form — a second round conditioned on already-found evidence and
   the existing assertion/aggregate paths — before any trained planner.
2. **#87/#88 should be re-opened as one experiment.** The pre-registered study
   shows the candidate-limit question and the reranking question are the same
   question, with reranking's gain concentrated where PRME's recall is weakest
   (3-of-30 kept) and shrinking to 1.5/1.1 points at generous budgets. One
   paired run at two limits, reporting abstention beside accuracy, settles both.
3. **#91/#92 must augment, not replace.** Both the negative-result paper and the
   selection-over-extraction study say verbatim source survives; MemStrata and
   EnSIMem show the winning shape is nonduplicated dated speaker-attributed
   spans *beside* the turn, with source-based answering. Keep #80's fidelity
   floor as the mechanism, and expect the gain to be in selection at a fixed
   budget rather than in a larger context (#90).
4. **Do not promote a consolidation metric.** Consolidation decisions belong in
   governance and lifecycle reporting, where PRME is already strong, and the
   only external evidence on a quality-versus-transfer split is negative.
5. **Report beside abstention and cost.** Reranking lowers correct abstention;
   memory costs at least twice source-filtered re-reading on short histories;
   DolphinBench ([2609.24971](https://arxiv.org/abs/2609.24971)) now requires
   cost and latency alongside accuracy. PRME's published contract should add
   both controls before the epic's exit numbers are claimed.

## Sources

[MERA 2609.37443](https://arxiv.org/abs/2609.37443) ·
[MemStrata 2610.05343](https://arxiv.org/abs/2610.05343) ·
[Mnemon 2609.36059](https://arxiv.org/abs/2609.36059) ·
[Selection vs Extraction 2609.34227](https://arxiv.org/abs/2609.34227) ·
[Auditing Long-Term Memory Evaluation 2609.38021](https://arxiv.org/abs/2609.38021) ·
[EvalMem 2609.22231](https://arxiv.org/abs/2609.22231) ·
[Epistemics of Agent Memory 2609.33013](https://arxiv.org/abs/2609.33013) ·
[APDMem 2610.02472](https://arxiv.org/abs/2610.02472) ·
[JustMem 2609.19877](https://arxiv.org/abs/2609.19877) ·
[EnSIMem 2609.27279](https://arxiv.org/abs/2609.27279) ·
[PACMI 2610.05732](https://arxiv.org/abs/2610.05732) ·
[When Evidence Changes 2610.03902](https://arxiv.org/abs/2610.03902) ·
[Entity-Memory Graph Retrieval 2608.27925](https://arxiv.org/abs/2608.27925) ·
[TAGGRAPH 2609.38353](https://arxiv.org/abs/2609.38353) ·
[EngramRAG 2609.32049](https://arxiv.org/abs/2609.32049) ·
[Graph Memory At What Cost 2609.23315](https://arxiv.org/abs/2609.23315) ·
[Fidelity Before Structure 2601.00821](https://arxiv.org/abs/2601.00821) ·
[MemLeak 2610.04195](https://arxiv.org/abs/2610.04195) ·
[Memory Portability 2609.05339](https://arxiv.org/abs/2609.05339) ·
[Memory Canonicalization 2610.05124](https://arxiv.org/abs/2610.05124) ·
[Evidence Sufficiency 2609.32269](https://arxiv.org/abs/2609.32269) ·
[EGMEMORY 2609.23465](https://arxiv.org/abs/2609.23465) ·
[Mem++ 2610.02002](https://arxiv.org/abs/2610.02002) ·
[MemFit 2610.00872](https://arxiv.org/abs/2610.00872) ·
[DolphinBench 2609.24971](https://arxiv.org/abs/2609.24971) ·
[Authority Before Utility 2609.37474](https://arxiv.org/abs/2609.37474) ·
[Audience-Bound Memory 2609.36373](https://arxiv.org/abs/2609.36373) ·
[Restore-Counterfactual Eviction Audit 2609.08279](https://arxiv.org/abs/2609.08279) ·
[LOCOMO-CONV 2609.03467](https://arxiv.org/abs/2609.03467) ·
[PolyMemDB 2608.25577](https://arxiv.org/abs/2608.25577)
