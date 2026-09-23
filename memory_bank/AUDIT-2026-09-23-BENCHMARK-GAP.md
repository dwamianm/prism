# Benchmark gap audit: why PRME trails Zep and Hindsight (2026-09-23)

**Scope:** the September research agenda and the completed GPT-5.4 run
(LongMemEval-S 430/500, 86.0%; LoCoMo 985/1,540, 64.0%; 3,996-token context).

**Tracking:** epic #77, with the work split into issues #78 to #98.

**Method:** offline analysis of the saved run archive `data/gpt54-comparison-v1/`
(every context, receipt, candidate list, score trace, reader answer and judge
verdict), plus code reading at `main` (`dba1e659`). No model calls were made and
no money was spent. The scripts in `memory_bank/audit-2026-09-23/` reproduce the
numbers below. The first reads the saved receipts (about 75 seconds). The other
two replay every LoCoMo retrieval on copies of the saved packs and record each
candidate's own scores (about 5 minutes, local embedding only); the RRF rows use
them.

```bash
PYTHONPATH=. .venv/bin/python memory_bank/audit-2026-09-23/offline_evidence_audit.py
PYTHONPATH=. .venv/bin/python memory_bank/audit-2026-09-23/replay_locomo_candidates.py
PYTHONPATH=. .venv/bin/python memory_bank/audit-2026-09-23/exact_ranking_projection.py
```

This document was checked against the code and the saved data by independent
reviewers before the tickets were filed. Corrections are marked in place.

Projections in this document are planning estimates, not answer scores. They
must be confirmed by a real reader and judge run.

## The short answer

1. **The reader sees too little.** Each packed record is a JSON object with a
   UUID, two ISO timestamps and six labels that never change. In LoCoMo, 71% of
   the 3,996-token context is this envelope and 29% is conversation text. Only
   about 25 turns fit, out of about 555 candidate turns per question.
2. **The ranker cannot find list evidence.** LoCoMo "multi-hop" questions are
   mostly list questions ("What has Melanie painted?"). They need 3.1 turns on
   average, spread across sessions, and those turns rarely share words with the
   question. Their median rank is 51. Only 16% of multi-hop questions get all
   their evidence into the context, so multi-hop scores 28%.
3. **The benchmark path does not use the architecture.** `store()` creates no
   entities or edges, so the graph channel returns 0 candidates. Five of the
   seven scoring signals are constant. What actually runs is vector search plus
   BM25 over raw turns, then a JSON printer.
4. **The leaders change the representation at write time and rerank.** Zep and
   Hindsight turn conversations into short, dated facts linked to entities, fuse
   channels with RRF, and rerank with a cross-encoder. Zep packs facts and
   summaries into about 5.8K tokens. Hindsight packs facts plus raw chunks into
   about 36K to 44K tokens, roughly ten times PRME's budget.
5. **Part of the gap is measurement.** PRME's LoCoMo judge requires every list
   item. Hindsight's benchmark judge is told to "be generous", and Zep's judge
   prompt is not published. About 86 of the 202 PRME multi-hop misses are
   partial answers that a lenient judge would likely accept. An external audit
   found 6.4% of LoCoMo gold answers wrong, which caps a true score near 93.6%;
   Zep's 94.7% is above that cap.

## 1. Evidence from the saved run

### When the evidence is in the context, the reader is right

| LoCoMo category | All annotated evidence packed | Some evidence missing |
|---|---:|---:|
| single-hop | 600/657 = 91.3% | 30/183 = 16.4% |
| temporal | 216/252 = 85.7% | 10/68 = 14.7% |
| multi-hop | 37/45 = 82.2% | 42/233 = 18.0% |
| open-domain | 22/29 = 75.9% | 19/60 = 31.7% |

Of 555 wrong LoCoMo answers, 443 lacked annotated evidence and about 130 were
explicit "I don't know" answers (regex count over the answer endings).
LongMemEval shows the same pattern: 385/403 (95.5%) correct with all evidence
packed versus 21/67 (31.3%) with evidence missing. The earlier label-assisted
packing diagnostic reached 473/500 on LongMemEval. The reader is not the
bottleneck; context selection is.

### Multi-hop accuracy falls with each extra turn needed

| Annotated turns needed | Questions | All packed | Accuracy |
|---|---:|---:|---:|
| 1 | 6 | 33% | 50.0% |
| 2 | 135 | 24% | 36.3% |
| 3 | 56 | 14% | 26.8% |
| 4 | 45 | 4% | 22.2% |
| 5 or more | 40 | 2% | 7.5% |

### "Returned 99.5% of evidence" is not a retrieval result

`vector_k=500` and `lexical_k=500` together return nearly every turn. The
candidate pool averages 555 turns (369 to 689), which is 93% of a conversation's
turns on average, and the whole conversation for 301 of the 1,540 questions.
Discovery is almost trivially complete, so the ranking decides everything.

| Category | Median evidence rank | In top 25 | Beyond rank 150 |
|---|---:|---:|---:|
| single-hop | 3 | 80.0% | 3.2% |
| temporal | 3 | 83.4% | 2.7% |
| multi-hop | 51 | 38.4% | 26.1% |
| open-domain | 71 | 36.7% | 34.7% |

### The context envelope

| | LoCoMo | LongMemEval |
|---|---:|---:|
| Mean context tokens | 3,951 | 3,965 |
| Mean memory text tokens | 1,145 (29%) | 1,277 (32%) |
| Records per context | 25.2 | 23.9 |
| Envelope tokens per record | about 111 | about 112 |

Every record carries `id` (UUID, about 33 tokens), `type`, `scope`, `epistemic`,
`memory_lifecycle`, `representation`, `event_time`, `valid_from`, `valid_to`,
`text` and `source_type`. For raw turns, everything except `text` is constant or
redundant: the text already begins with "(7:55 pm on 9 June, 2023) Caroline:".
`valid_from` is the 2026 ingestion clock, not the conversation date, which is
misleading on temporal questions. 109 LongMemEval-S records and 337 LoCoMo
records were packed as REFERENCE or KEY_VALUE fallbacks. They contain no memory
text at all, yet each still costs 122 to 161 tokens of envelope.

This renderer became the benchmark context on 2026-09-12 (`2209224f`, "enforce
token budgets on faithful rendered memory context"). Before that, adapters used
`format_for_llm()` with up to 50 plain lines. The compact variant added on
2026-09-14 (`dff46a4c`) keeps all ten metadata fields and still costs about 89
tokens per 30-token turn.

For comparison, Zep's reader sees lines like "Emily is experiencing issues with
logging in. (2024-11-14 02:13:19+00:00 - present)", and Hindsight's sees a type,
a time range and participants followed by the quoted source turns (section 5).

### Offline re-pack projections

For each question, take the saved candidate list, render each record as one
plain line ("- (date) Speaker: text"), and pack greedily to the same budget.
Validation: re-simulating the current JSON format reproduces the real packed
sets (mean Jaccard 0.83) and projects 63.3% against the real 64.0%.

| LoCoMo variant | Records | Multi-hop with all evidence | Projected accuracy |
|---|---:|---:|---:|
| Current: JSON, balanced order, 4K | 25 | 16% | 64.0% (real) |
| Plain lines, current order, 4K | 75 | 37% | about 73% |
| Plain lines, balanced order, 4K | 103 | 19% | about 67% |
| Plain lines, RRF order, 4K | 78 | 51% | about 78% |
| Plain lines, current order, 8K | 151 | 57% | about 80% |
| Plain lines, RRF order, 8K | 157 | 71% | about 82% |
| Plain lines, current order, 16K | 311 | 80% | about 84% |
| Plain lines, RRF order, 16K | 315 | 89% | about 86% |

"RRF order" here means RRF of each candidate's own semantic and BM25 ranks,
followed by the product's session expansion. These rows come from an exact
replay of all 1,540 LoCoMo retrievals (`replay_locomo_candidates.py` and
`exact_ranking_projection.py` in `memory_bank/audit-2026-09-23/`); every
replayed context matched its saved SHA-256. Without session expansion, RRF
projects about 77%, 81% and 85%. (Corrected on 2026-09-23: an earlier draft
rebuilt RRF from receipt traces, which copy a trigger's scores onto
session-expansion neighbors, and reported 77%, 82% and 86%.)

Two lessons follow. The format is the largest single lever. And the `balanced`
length penalty only looked good because the fixed JSON envelope hid length
differences; with plain lines it fills the context with short filler turns
("Cool! What did it look like?"). Packing order must be retuned after any format
change.

LongMemEval turns are long, so format matters less there and budget matters
more. Plain lines in score order project about 87% at 4K, 90% at 8K, 92% at 16K
and 94% at 32K.

These projections cannot reach Zep's claimed 94.7% on LoCoMo. Raw turns hit a
ceiling on multi-hop because the evidence is scattered and badly ranked. Closing
the rest of the gap needs the structural changes in section 6.

### Ranking signals that do nothing

From the recorded score traces:

- `graph_proximity` is always 0 (no entities exist).
- `salience` is always 0.5, `epistemic_weight` always 0.9, `node_type_boost`
  always 1.15, `temporal_affinity` always 0.
- `recency_factor` uses ingestion time (`scoring.py:385-391`), so it is 1.0 for
  89% of candidates and changes only on the few current-state queries.
- `confidence` takes two values: 0.8 and 0.6, set by speaker. The adapter
  sorts the two speakers' names and maps the first to "user" and the second to
  "assistant" (`run_gpt54_comparison.py:204-206`), and `infer_source_type`
  marks assistant text as `system_inferred` (`epistemic/inference.py:77-78`).
  The reader also sees `"source_type":"system_inferred"` on everything the
  "assistant" person says.
  Questions whose evidence came only from the "assistant" speaker had all
  evidence packed 63.8% of the time and scored 62.7%, against 70.0% and 68.1%
  for the "user" speaker.

Only semantic similarity and normalized BM25 vary. A plain reciprocal rank
fusion (RRF, k=60) of those two ranks, measured by exact replay, beats the
seven-weight composite. Followed by the product's session expansion, it loses
no category:

| All evidence inside top N | single-hop @25 / @75 | temporal @25 / @75 | multi-hop @25 / @75 | open-domain @25 / @75 |
|---|---|---|---|---|
| Current composite | 80.0% / 88.9% | 82.6% / 92.2% | 20.6% / 39.4% | 39.1% / 52.2% |
| RRF(semantic, BM25) alone | 84.1% / 93.7% | 82.2% / 89.4% | 33.7% / 52.1% | 44.6% / 54.3% |
| RRF, then session expansion | 89.8% / 97.1% | 82.9% / 92.2% | 28.0% / 49.6% | 39.1% / 55.4% |

Other defects found on the same path:

- `_classify_intent` returns ENTITY_LOOKUP before it checks TEMPORAL whenever
  the question contains a capitalized word (`query_analysis.py:137-145`).
  Almost every LoCoMo question names a person, so the temporal boost never
  applies: `temporal_affinity` was 0 for every candidate of all 1,540 LoCoMo
  questions, including the 321 temporal ones.
- Session expansion creates new neighbors with `path_count=1`
  (`session_context.py:158-159`) and never raises the `path_count` of existing
  ones, which leaves single-path neighbors in packing tier 4 behind hundreds of
  tier-3 turns. Across all 1,540 LoCoMo questions, none of the 250,393
  single-path candidates was packed (exact replay).
- Speaker names begin every stored turn, so a BM25 query with a name matches
  every turn by that person.
- Packing dominates retrieval time. `pack_context` tokenizes every candidate
  (about 555 per LoCoMo question) at full fidelity, and tokenizes each one that
  does not fit again at up to three lower levels; a preflight limits
  whole-context renders to about one per packed record. A verification replay
  of 60 LoCoMo questions measured packing at about 0.08 seconds of a
  0.135-second median retrieval. The recorded 16-second retrieval for
  `conv-26-q0011` did not reproduce (about 0.18 seconds on replay).
  (Corrected on 2026-09-23: an earlier draft said the whole context was
  re-rendered for every candidate.)

### How strict the judge is

The LoCoMo judge prompt says "When the reference lists required items, all are
required." A rough lexical check of the 202 wrong multi-hop answers found about
71 list answers with some but not all reference items and about 15 single
answers that overlap the reference. The counts depend on the matching rule. Example: "Melanie has painted sunsets, a sunflower still
life, and likely a horse painting" was judged wrong against "Horse, sunset,
sunrise". Mem0-style LoCoMo judges, including the one in Hindsight's benchmark
harness, tell the grader to "be generous" and accept answers that touch the same
topic. This is a comparability issue, not a product bug. Keep the strict judge
as the primary number.

A useful bound follows from the table at the top of this section. If every
question had all its annotated evidence packed, the same reader and strict judge
would score about 87.5% on LoCoMo and about 94.6% on LongMemEval (the latter
matches the 473/500 label-assisted diagnostic). These are upper estimates,
because questions whose evidence already ranked high are easier than average.
So PRME can pass Zep's 90.2% LongMemEval claim through retrieval and packing
alone. Zep's 94.7% LoCoMo claim sits above PRME's raw-turn perfect-evidence
ceiling under this judge. Cleaner extracted facts can raise that ceiling
somewhat, but a like-for-like LoCoMo comparison still needs the same judge.

## 2. Why multi-hop is so bad

LoCoMo multi-hop questions ask for a list built from several conversations:
"What activities does Melanie do?", "What recipes has Nate made?", "Who has Maria
met while volunteering?". The answer is spread over 2 to 6 turns in different
sessions: 95% of multi-hop questions have evidence in more than one session
(2.7 sessions on average), against 0.2% of single-hop questions. Each turn talks
about one item ("I just discovered I can make coconut milk ice cream") and does
not contain the words in the question ("recipes").
The failure chain:

1. Semantic and lexical similarity to the question is weak for each item, so
   the needed turns rank around 51st and often below 150th.
2. Only about 25 turns fit because of the JSON envelope.
3. The graph channel that was designed for this (entity to facts) is empty,
   because the benchmark stores raw turns and extracts no entities.
4. No entity summary or topic aggregation exists to answer "all X about Y" in
   a few tokens.
5. The judge requires every list item, so a partial list scores zero.

Competitors attack steps 1 to 4: dated facts and entity links at write time, a
cross-encoder reranker at read time, and more room in the context. Context size
alone goes a long way: Hindsight's plain chunk-search baseline reached 85.5% on
true multi-hop with about 22K tokens (section 5).

## 3. Is the architecture too complex?

Yes, but the complexity is in the wrong place.

- Product: 54,895 lines in `src/prme`, 126 configuration fields, 20 labeled
  retrieval blocks (6 switched off by default).
- Measurement: 75,982 lines of benchmark and diagnostic code, more than the
  product itself. Tests: 71,654 lines.
- September alone: 1,036 commits (402 `bench:`, 64 `research:`, 62 `feat:`) and
  173 dated research reports.

The parts that decide accuracy are thin or missing: write-time facts, entity
aggregation, rank fusion, a working reranker and a reader-oriented context. The
parts that are rich (epistemic state, lifecycle, supersedence, durable
publication, receipts, audit fields) never change which evidence the reader
sees, except where they are printed into the prompt and push evidence out.

The fix is not to delete the audit features. It is to keep them in the data
model and receipts, and stop spending the reader's budget on them.

## 4. Audit of the experimental agenda

### What worked

- Honest re-baselining. The v0.9 numbers (LongMemEval 94.7%, LoCoMo 89.8%) used
  a 152-question LoCoMo sample, dataset-supplied observations, QA pairing,
  copied answer examples and a lenient same-model judge. The September runs
  removed all of that. 64.0% is the first trustworthy full LoCoMo number.
- Complete cohorts, a fixed reader and judge, retained failures.
- Evidence-retention diagnostics that separate discovery from packing. They
  pointed at the right layer.

### What went wrong

1. **A proxy metric hid the bottleneck.** The evidence evaluator uses its own
   compact whole-turn packer. The Hindsight comparison measured 96.35% source
   recall with that packer and 74.85% in PRME's actual bundle. That 21-point gap
   came from the product packer: the JSON envelope plus the ordering policy
   (density at the time). The report called it "a substantial PRME packing
   failure", but the envelope share was never isolated.
2. **The format tests could not see the effect.** The compact, grouped and
   metadata-factoring trials kept every field and only reshaped it. They ran on
   LongMemEval only (long turns, smaller overhead share), with a local reader and
   119 questions, and lost 3 to 4 answers. Five identical full controls varied
   from 429 to 437 of 500, so that loss is inside the noise. "Reject compact
   renderers" then became a standing rule in `GOALS.md`.
3. **Most effort tuned packing order inside a 25-record window.** Density,
   score, balanced, one-head, quarter-length, composition, marginal, session
   penalties, grouping, factoring and episode composition all compete for the
   same 25 slots. Any ranking gain below rank 25 was invisible.
4. **Recall-expansion experiments could not work.** The pool already contains
   nearly the whole history: 93% of a LoCoMo conversation and 99.7% of a
   LongMemEval-S history on average. Query reformulation and entity fan-out can
   add almost no evidence, and the current merge drops alternate-query signals
   for candidates already found. The LongMemEval-S reformulation study changed 2
   contexts and added 5.5 seconds of median latency. The reranker trials failed
   for a score-scale bug, then tied.
5. **Small cohorts and changing instruments.** Most studies used 20 to 149
   questions, one of five readers (Qwen 9B, Qwen 35B, Gemma, DeepSeek, GPT-5.4)
   and custom judges. Most intervals include zero, so few decisions were
   possible.
6. **Side quests.** About 15 claim-verification trials (all failed their gates),
   Jev product matching (Walmart-Amazon, beer), MemoryArena travel planning, and
   durability and publication work. These matter for trust but cannot move
   LoCoMo or LongMemEval.
7. **Process cost.** Preregistration, checksum chains and fail-closed verifiers
   were applied to every development probe. Each experiment became slow, and
   cheap iteration became impossible.
8. **Missing baselines.**
   - Full context: a LoCoMo conversation averages 20.6K tokens (maximum 24.1K)
     as one "Speaker: text" line per turn under a date header for each session.
     GPT-5.4 can read all of it. This is the most important reference and it
     has never been run.
   - Plain vector or BM25 RAG with a plain format at the same budget.
   - PRME with `ingest()` (LLM fact extraction) on full LoCoMo and LongMemEval.
     The April plateau notes already named this as the unlock.
   - Competitors with their own full pipelines. The Hindsight comparison used
     raw profiles, a shared local embedding model and local readers, which
     removes Hindsight's main advantage.

## 5. What Zep and Hindsight do differently

Collected from vendor pages, papers and repositories on 2026-09-23. Vendor
numbers are self-reported.

### Headline conditions are not PRME's conditions

| | PRME (2026-09-23) | Zep (research page, 2026) | Hindsight (AMB, March 2026) |
|---|---|---|---|
| LoCoMo | 64.0% | 94.7% | 92.0% |
| LongMemEval-S | 86.0% | 90.2% | 94.6% |
| Answer model | GPT-5.4 medium | GPT-5.4 medium | Gemini 3.1 Pro preview |
| Judge | GPT-5.4, all list items required | GPT-5.4 "chain-of-thought grading", prompt not published | Gemini 2.5 Flash Lite, "be generous" wording |
| Context per question | 3,996-token ceiling | median 5,760 (LoCoMo), 4,408 (LongMemEval) | mean 36,235 (LoCoMo), 43,625 (LongMemEval) |
| Write-time extraction | none in the benchmark path | entities, fact edges, summaries | narrative facts with time ranges |

Sources: <https://www.getzep.com/research/>,
<https://github.com/vectorize-io/agent-memory-benchmark>,
<https://hindsight.vectorize.io/blog/2026/03/23/agent-memory-benchmark>.

Hindsight's own harness also ran a plain chunk-search baseline (dense plus BM42
with RRF over 512-token chunks, same answer model and judge, about 22K to 23K
tokens). It scored 79.1% on LoCoMo and 85.5% on true multi-hop, against
Hindsight's 86.9%. **With a large enough context, even plain chunk search
handles LoCoMo multi-hop.** Hindsight's lead over that baseline came mostly from
temporal questions, because the baseline's chunks had no session dates.

Two external audits also bound the LoCoMo numbers
(<https://github.com/dial481/locomo-audit>,
<https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer>):
99 of 1,540 gold answers (6.4%) are wrong, which caps a perfect score near
93.6%, and a "be generous" judge accepted 62.8% of deliberately vague wrong
answers. Zep's 94.7% is above that cap. Category labels are also mixed up in
several vendor tables: LoCoMo category 1 is multi-hop, 3 is open-domain and 4 is
single-hop.

### Zep (Graphiti)

- **Write time:** entity extraction that reads the current message plus the
  previous four; LLM entity resolution; facts stored as typed edges with valid
  and invalid times; contradictions close the old edge instead of deleting it;
  entity summaries; communities. Zep Cloud adds observations, thread summaries
  and a user summary. At least two LLM calls per message (entities, then
  facts), one more per new fact, plus batched summary updates.
  (<https://arxiv.org/html/2501.13956v1>, <https://help.getzep.com/observations>)
- **Query time:** cosine, BM25 and breadth-first graph search, fused with RRF
  and reranked (cross-encoder in the benchmark). No LLM at query time. The
  benchmark ran five searches in parallel: 20 edges, 10 nodes, 10 episodes, 5
  thread summaries and 5 observations.
- **Context:** facts with date ranges, for example "Emily is experiencing issues
  with logging in. (2024-11-14 02:13:19+00:00 - present)", plus entity
  summaries, and optionally episodes, observations and thread summaries.
  (<https://help.getzep.com/retrieving-context>)
- **Low-budget mode:** "auto search" scores 86.5% on LoCoMo with a median 2,680
  tokens.

### Hindsight (Vectorize)

- **Write time:** one LLM call per chunk of up to 3,000 characters. It extracts
  narrative facts, each with entities, participants, an occurred start and end,
  a mentioned-at time and causal links, and keeps the raw chunk. A background
  job consolidates facts into observations. Facts are never invalidated.
  (<https://hindsight.vectorize.io/blog/2026/07/13/inside-retain-agent-memory>)
- **Query time:** four channels in parallel (semantic, BM25, graph expansion
  over entity and causal links, temporal window), RRF (k=60), top 300 kept,
  cross-encoder rerank, small recency and temporal boosts, then pack. No LLM at
  recall. An optional reflect loop runs up to 10 steps.
  (<https://hindsight.vectorize.io/developer/retrieval>)
- **Context in the benchmark:** Markdown memories with type, time range and
  participants, followed by the quoted raw turns. Raw chunks are about 88% of
  the LoCoMo context. Observations and reflect were off in the published runs.
- **Reranker effect:** on one LoCoMo conversation, retrieval hit@5 rose from
  0.297 with RRF alone to 0.903 with a MiniLM cross-encoder.
  (<https://github.com/vectorize-io/hindsight-benchmarks>)

### What this means for PRME

- Both leaders use RRF plus a cross-encoder reranker. PRME uses a weighted sum
  of mostly constant signals, and its reranker trials were run inside the
  25-record JSON window where a better ranking could not show.
- Both leaders show time on every memory in the reader's own terms (occurred or
  valid dates). PRME shows the ingestion clock next to the conversation date.
- Both leaders extract facts at write time and keep links to the raw source.
  PRME's benchmark path stores raw turns only.
- Hindsight's numbers come with about ten times PRME's context. Zep's come with
  about 1.4 times PRME's context, but filled with dense facts and summaries.
- The PRME team's Hindsight comparison ran Hindsight in raw mode (no extraction,
  no consolidation) with a shared local embedding model and local readers. That
  removed Hindsight's main features, so it does not show how PRME compares with
  the real product.

## 6. Recommendations, in order

Each step names its expected effect. Numbers are projections from section 1
unless marked otherwise.

**Step 0. Fast inner loop (1 day).** Promote the offline evidence metric in
`memory_bank/audit-2026-09-23/` into a benchmark tool that runs over all 2,040
saved questions in minutes with no model cost. Gate every retrieval change on
it first. Spend reader and judge money only on changes that move it.

**Step 1. Reader-facing context (1 to 2 days).** Add a reader format: one line
per record, for example `[2023-06-09] Caroline: ...`, with short `m1` style
references only when citations are requested. Keep the full audit envelope in
`MemoryBundle` and the receipt. Never pack REFERENCE or KEY_VALUE fallbacks into
reader text. Show `event_time`, never the ingestion `valid_from`. Retune ordering
for the new format (score or RRF, not balanced). Expected LoCoMo: about 73% at
4K. Overturns the `GOALS.md` rule against compact renderers, which was based on
a different test.

**Step 2. Ranking (2 to 4 days).** Use RRF of semantic and BM25 ranks as the
default fusion. Remove constant signals from the default formula. Compute
recency from `event_time` against `reference_time`. Check temporal intent
before entity lookup. Treat every named conversation participant as a
first-party speaker instead of "assistant". Index the speaker as a field, not
as leading text. Cap real candidate generation (for example 100 per channel)
so recall features can matter. Then retry a cross-encoder reranker over the
fused top 100 to 300, as Zep and Hindsight do, using rank order rather than
score mixing. Expected LoCoMo: about 78% at 4K before the reranker.

**Step 3. Budget policy (1 day plus one run).** Report 4K for comparability and
evaluate 8K as the product default. Zep reports median contexts of 4,408
(LongMemEval) and 5,760 (LoCoMo) tokens of facts and summaries; Hindsight's
published runs average 36K to 44K tokens. Raw turns need more room than facts.
Always publish the budget next to the score. Expected LoCoMo: about 82% at 8K
with steps 1 and 2.

**Step 4. Write-time facts (2 to 3 weeks). This is the structural fix.**
- Extract short, self-contained facts from each session window. Use the
  preceding turns as context, resolve "I", "she" and "they" to names, and turn
  relative dates into absolute dates. Today the extractor receives one message
  and its role only (`ingestion/pipeline.py:449-451`, `extraction.py:1113-1140`),
  with no preceding turns, and the grounding check requires every subject and
  object to appear in that one message. Relative dates are resolved into
  metadata only (`pipeline.py:586`). The stored fact text is the verbatim
  source passage (`pipeline.py:593-596`), so today's extracted facts are no
  denser than the turns they come from. All of this blocks the step.
- In each LoCoMo conversation, the speaker whose name sorts second is mapped to
  `assistant`, so `ingest()` would apply the assistant prompt to that person's
  turns, which admits only commitments, completed actions and attributed state.
  Fix speaker roles first.
- Store each fact as a node linked to its source turn and to entity nodes. The
  graph channel becomes live and PRME keeps its provenance advantage.
- Retrieve facts first. Attach the source turn only for top facts or on
  request.
- Keep epistemic and lifecycle fields on the node, not in the prompt.

**Step 5. Entity observations for multi-hop (1 to 2 weeks after step 4).**
Maintain short per-entity, per-topic observation cards in the organizer, for
example "Melanie, art: painted a horse (2023-08), a sunset (2023-07), a sunrise
(2023-05)". Include a card only when the query targets that entity and topic.
Build it on the existing consolidation and entity-snapshot path, as the triage
on issue #27 asks, and keep it opt-in until measured. The v0.9 knowledge-card
run (137/152 against 141/152, `benchmarks/results/locomo_v22_cards.json`) used
large unfocused profiles mixed into raw turns under a harness later found to
inflate scores; this is a different design and needs its own test.

**Step 6. Optional second hop at query time.** For list and multi-hop questions,
retrieve, read the found entities, issue follow-up queries, and retrieve again.
Hindsight's optional reflect loop is a version of this. Zep uses no LLM at query
time, and Hindsight's published benchmark runs did not use reflect, so this step
comes last.

**Step 7. Measurement.** Keep the strict judge as the primary score. Also report
a Mem0-style lenient judge score so vendor comparisons are like for like. Run
the full-context baseline. Keep one fixed reader for development.

**Step 8. Focus.** Pause claim verification, product matching, MemoryArena and
new benchmark families until parity. Keep registration and checksum chains for
release claims, not for development probes.

Expected order of gain: steps 4 and 5, then step 1, then step 2, then step 3,
then step 6.

## 7. Next paid runs (need approval)

| Run | Purpose | Rough cost |
|---|---|---:|
| LoCoMo full context, GPT-5.4 reader and judge | Reference: the same reader with the whole conversation | about $45 |
| LoCoMo with steps 1 and 2 at 4K | Confirms the largest projected gain | about $12 |
| Same at 8K | Budget effect | about $20 |
| Re-judge all 1,540 LoCoMo answers with a lenient judge | Measures the judge effect | about $1.20 |

A whole LoCoMo conversation is at most 24.1K tokens, so the full-context run
also stands in for a Hindsight-sized budget (36K). Costs scale from the recorded
$12.00 for the full LoCoMo run at 4K with Flex pricing. They are estimates.
Prompt caching can cut the full-context run substantially, because every
question in a conversation shares the same prefix.
