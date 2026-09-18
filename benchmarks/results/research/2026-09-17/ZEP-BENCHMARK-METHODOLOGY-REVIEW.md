# Zep benchmark methodology review and experiment handoff

**Status:** external-methodology review, not a PRME benchmark result  
**Reviewed:** 2026-09-17  
**Primary audience:** the process selecting PRME's next registered experiment

## Decision summary

Zep's current results are credible as results for a strong, tuned, end-to-end
hosted stack. They do not establish that Zep's underlying memory representation
is more accurate than PRME's, and they are not comparable to PRME's current
LongMemEval-V2 result.

The most useful follow-up is a matched LongMemEval-S baseline followed by a
composition-only ablation. Do not interrupt or reinterpret an experiment that
is already registered or running. In particular, the active Jev confirmation
must finish under its frozen protocol before this note influences later work.

If the next experiment is about conversational retrieval, the recommended order
is:

1. Reproduce a full 500-question PRME baseline on the cleaned LongMemEval-S
   dataset with a frozen reader, judge, prompt, dataset digest and context
   budget.
2. Compare the existing PRME pipeline with an explicit multi-scope composition
   using facts, entities, raw episodes, profiles/summaries and only already
   available derived records.
3. Add a cross-encoder arm only if the first comparison localizes losses to
   ordering rather than missing candidates. PRME's prior reranker evidence does
   not justify enabling it by default.
4. Consider a new observation/pattern representation only after the
   composition-only result shows that existing records cannot supply the
   required evidence.

Do not adopt automatic newest-wins supersedence to chase the benchmark. Zep's
temporal invalidation policy aligns with LongMemEval knowledge-update questions,
but it conflicts with PRME's requirement that recency alone is not proof of
truth.

## What Zep reports

Zep's current research page reports:

| Benchmark | Accuracy | Retrieval p50 / p95 | Median returned context |
|---|---:|---:|---:|
| LongMemEval | 451/500, 90.2% | 104 / 162 ms | 4,408 tokens |
| LoCoMo | 1,459/1,540, 94.7% | 87 / 155 ms | 5,760 tokens |
| LoCoMo auto search | 86.5% | 115 / 173 ms | 2,680 tokens |

The headline pipeline uses `gpt-5.4` with medium reasoning as the reader and
`gpt-5.4` with chain-of-thought grading as the judge. Its client-composed
retrieval depth is 20 edges, 10 entity nodes, 10 raw episodes, 5 thread
summaries and 5 observations, followed by cross-encoder reranking. Zep calls
five searches in parallel and composes the resulting context at the client.

Sources:

- [Current Zep benchmark page](https://www.getzep.com/research/)
- [Zep comparison with Mem0](https://www.getzep.com/mem0-alternative/)
- [Zep retrieval documentation](https://help.getzep.com/retrieving-context)
- [Zep observation documentation](https://help.getzep.com/observations)
- [Konig architecture](https://www.getzep.com/platform/agent-knowledge-graph/)

## How the stack produces the result

The operative pipeline is more than a graph lookup:

```text
messages and business data
  -> LLM entity/fact extraction and resolution
  -> raw episodes + temporal facts + entity summaries + thread summaries
  -> derived cross-entity observations
  -> five parallel semantic/lexical/graph/pattern searches
  -> cross-encoder reranking
  -> token-bounded context
  -> GPT-5.4 answer generation and GPT-5.4 judgment
```

The principal contributors are:

1. **Query cost is moved to ingestion.** Entity resolution, fact extraction,
   contradiction comparison, summaries and observations are prepared before
   the reported retrieval call. The published retrieval latency does not
   include this work, answer generation or judging.
2. **The same source has multiple retrieval surfaces.** Facts provide compact
   assertions, entities provide accumulated summaries, episodes recover raw
   wording, thread summaries provide broad coverage, and observations capture
   multi-record patterns.
3. **The retrieval recipe is a high-recall ensemble.** The listed scope limits
   permit up to 50 heterogeneous results before reranking. This is not
   equivalent to a single top-k vector query.
4. **Temporal invalidation matches the task.** Zep's 2025 paper says new facts
   can invalidate temporally overlapping contradictions and that the
   transactional policy prioritizes new information. This directly reduces
   distractors for knowledge-update questions.
5. **A frontier reader does material reasoning work.** It can filter a larger,
   noisier evidence packet and answer multi-hop, temporal and open-domain
   questions that a smaller reader may miss.
6. **Serving is optimized independently of memory quality.** Konig keeps hot
   graphs in RAM as adjacency lists and CSR matrices with vector and BM25
   indexes beside them. The latency claim is for this hosted, hot retrieval
   path, not for total ingestion-to-answer time.

The original Zep paper documents a materially lower LongMemEval-S score:
71.2% with `gpt-4o`, versus today's 90.2% with `gpt-5.4`. The earlier run used
about 1.6K context tokens; the current page reports a 4.4K median. Because the
reader, context budget and retrieval pipeline all changed, the public evidence
does not allocate the roughly 19-point increase among those causes.

Source: [Zep temporal knowledge graph paper](https://arxiv.org/abs/2501.13956)

## What the headline does and does not demonstrate

Zep's current comparison page reports LongMemEval accuracy of 90.2% for Zep and
90.4% for Mem0 at top 50 under the same reader and judge. On that comparison,
Zep's differentiator is the claimed 24x lower p50 retrieval latency and 35%
smaller median context, not higher answer accuracy.

The LoCoMo auto-search arm is also informative. It falls from 94.7% to 86.5%
while reducing median context from 5,760 to 2,680 tokens. Retrieval method and
budget both change, so this is not a causal ablation, but it shows that the
headline depends materially on client-side multi-scope composition and a larger
evidence packet.

Treat the current figures as product-stack measurements rather than isolated
memory-quality measurements. Reader capability, judge behavior, ingestion
models, retrieval depth, context packing and storage architecture all
contribute.

## Comparability with PRME

Zep's LongMemEval number is for LongMemEval-S v1: 500 conversational-memory
questions and histories around 115K tokens in the small variant. PRME's current
80/149 result is for the web portion of LongMemEval-V2 Small, which uses noisy,
multimodal web-agent trajectories and a shared history of about 25M tokens. The
full V2 benchmark covers 451 questions, two domains and histories up to 115M
tokens.

The current reader stacks also differ: Zep uses hosted `gpt-5.4` with medium
reasoning; PRME's registered V2 result uses a local Qwen 9B reader. Therefore
90.2% versus 53.69% is not a valid system comparison.

Sources:

- [Official LongMemEval repository](https://github.com/xiaowu0162/LongMemEval)
- [Official LongMemEval-V2 repository](https://github.com/xiaowu0162/LongMemEval-V2)
- [PRME research agenda](../../../../docs/RESEARCH-AGENDA.md)

## Audit limitations in the current Zep publication

The current page lists high-level settings but does not link the current run's
per-question answers, retrieved evidence, prompts, dataset checksum, registration
or replayable runner. Zep's public `zep-papers` repository contains the older
2025 study rather than an artifact package for the current 2026 figures. A
third-party reproduction would need at least the exact dataset revision, system
and answer prompts, context serializer, token counter, judge prompt and raw
outputs.

The displayed LoCoMo totals also do not reconcile:

- aggregate: 1,459 correct out of 1,540;
- displayed category correct counts: 646 + 311 + 304 + 175 = 1,436;
- displayed category denominators: 670 + 325 + 323 + 221 = 1,539.

This may be a stale table or publication error. Until corrected artifacts are
available, the 94.7% LoCoMo result should not be treated as independently
audited.

The latency boundary needs equal care. Zep labels the number retrieval latency
and describes hot in-memory graphs. A matched comparison must separately report
warm and cold retrieval and state whether query embedding, network time, context
serialization, ingestion and answer generation are included.

## Proposed matched experiment

### Question

Does explicit multi-scope composition improve PRME's evidence recall and
end-to-end accuracy on conversational memory when the dataset, reader, judge and
budget are held fixed?

### Frozen cohort and stack

- All 500 questions from one checksum-pinned `longmemeval_s_cleaned.json`.
- One frozen answer model and model revision for every arm.
- Two scores over the same saved answers when affordable:
  - the official LongMemEval prompt with its pinned official judge;
  - a separately labeled GPT-5.4 diagnostic matching Zep's disclosed judge
    family.
- One fixed answer prompt and one fixed context-token ceiling.
- Ingestion never receives question text, answers, evidence-session IDs or
  question-type labels.
- Every arm starts from the same immutable ingested PRME artifact.

### Arms

1. **Current PRME:** unchanged retrieval and balanced packing.
2. **Multi-scope composition:** reserve independently configurable quotas for
   facts, entities, raw events/episodes and generated profiles/summaries, then
   apply deterministic deduplication and the existing packer.
3. **Multi-scope plus reranker:** only if arm 2 improves candidate recall but
   loses ordering or answer quality. Freeze the reranker before evaluating this
   arm.

Do not add a new pattern-extraction representation in this experiment. That
would combine ingestion and retrieval changes and prevent causal attribution.

### Required measurements

- complete 500-question answer accuracy and per-category accuracy;
- official session-level and turn-level source recall where labels support it;
- recall of all required evidence, not only recall of any evidence;
- paired wins, losses and a question-bootstrap interval;
- median, mean and p95 packed tokens;
- number of candidates retrieved and packed from each scope;
- warm and cold retrieval p50/p95, with inclusion boundaries stated;
- ingestion wall time, model calls and token use reported separately;
- invalid/failed answers retained in the denominator;
- per-question retrieval receipts, contexts, answers and judge decisions;
- hashes for dataset, code revision, configuration, prompts and result files.

### Decision gates

Promote multi-scope composition only if it:

- improves the preregistered primary accuracy metric with a positive paired
  interval;
- does not reduce complete-evidence recall or any protected update/temporal
  category beyond a frozen tolerance;
- stays within the frozen context budget;
- preserves provenance and current conflict semantics;
- passes a separate untouched confirmation or remains explicitly experimental.

Do not promote a reranker based solely on answer-score movement. It must improve
ordering on fixed candidate sets without losing source coverage, and its benefit
must survive the confirmation cohort.

## Recommendation to the active experiment process

Finish the current registered work unchanged. Afterwards, select this only if
the next research objective is conversational-memory retrieval or a direct Zep
comparison. The smallest decision-quality next step is the matched current-PRME
LongMemEval-S baseline; it provides the missing denominator before any feature
work. If that baseline is already competitive under the same GPT-5.4 stack,
there is no justification for adding observations or changing supersedence. If
it is not, use source-recall and packing receipts to choose between composition,
ordering and representation work rather than copying the entire Zep design.
