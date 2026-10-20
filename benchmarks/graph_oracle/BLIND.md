# Blind validation of the typed walk

Part of dwamianm/prism#351. This file is committed before any strategy runs on
the blind questions. It fixes the protocol and the decision rule, so the
numbers cannot move them.

## Why

The oracle result (`BENCHMARKS.md`, "Graph oracle") is an upper bound. One
person wrote the gold graphs and the relation cue words with the questions in
view, and the held-out check covered only plain lookups. The question this
phase answers is whether saving information in the ideal graph format improves
recall for questions in general: questions nobody wrote the graph for, and the
same questions asked in other words.

## Protocol

1. **Blind questions.** A local model (the fast tiers' extraction model through
   a local Ollama server) is given only the memories (ids and text) and a one
   line instruction per question type: lookup, list, multi-hop, temporal,
   correction, negation, aggregation. It never sees a gold graph, a query spec
   or the cue words. It returns questions with the ids of the memories that
   state the answer (`blind.py`, 29 requests in two rounds; the second round
   asked for more lookups, corrections, multi-hop and aggregation questions
   because the first round's lookups all repeated questions the gold files
   already ask).
2. **Review.** The reviewer read the memories and the candidates, never a gold
   graph or a strategy's output, and dropped a candidate that cannot be
   answered from the memories, has no unique answer, or repeats an existing or
   earlier question (80 percent of its words shared). A question that one
   memory answers is a lookup, whatever the model called it. Four answer ids
   were corrected. The candidates, the decisions and the counts are in
   `benchmarks/fixtures/graph-oracle/blind-candidates-v1.json`,
   `blind-review-v1.json` and the `review` block of `blind-v1.json`;
   `blind.freeze` rebuilds the frozen file from them exactly.
3. **Freeze.** `blind-v1.json` is committed before the first run. Its sha256 is
   recorded in the report.
4. **Paraphrases.** The same model writes three paraphrases of each frozen
   question (`blind-paraphrases-v1.json`), with the same answer ids, spot
   checked by the reviewer.
5. **Gold coverage.** For each blind question the reviewer checks, from the
   memory text, whether the gold graph holds the answer fact (an entity the
   question names and a claim, or a literal claim, evidenced by an answer
   memory). A question whose fact is missing is a coverage gap, counted and
   reported. The gold is extended in a separate commit only with facts that are
   in the memory text, before the run, and never by looking at what a strategy
   returned.
6. **Cue words.** The hand written cue lexicon stays as a comparison column. A
   second lexicon is derived mechanically from the relation definitions in
   `DESIGN.md` (`derive_cues` in `gold.py`): the words of each relation's name
   and definition, minus a fixed stop list (English stop words and the schema's
   own words, such as role, status and polarity), minus capitalized words
   (example names) and digits, with each word's verb forms (s, ed, ing).
   Nothing about a question informs it.
7. **One run, no tuning.** `python -m benchmarks.graph_oracle blind` runs once
   over (a) the 53 original items, (b) the blind questions and (c) their
   paraphrases. Strategies: the baseline (`retrieve()`), the generic walk with
   the hand cues, the generic walk with the derived cues, and each of those two
   fused with the text list at weights 1 and 2. Settings are the ones the first
   report used; none changes after the numbers are seen.
8. **Latency.** The typed walk is timed on synthetic 2,000 and 10,000 memory
   packs, in DuckDB and PostgreSQL, median and 95th percentile. The proposed
   budget, fixed here: at most 50 ms at the 95th percentile for the walk's
   reads at 10,000 memories, on either backend.

## Decision rule

The rule is the owner's, written before the numbers are seen. The strategy it
judges is `generic` with the derived cues: the graph list ahead of the text
list, as the first report defines the `generic` row. Go to Phase 3A only if all
four conditions hold on the blind set:

1. Its top 3 count is at least 8 points of the blind questions above the
   baseline's (a rate difference of 0.08 or more).
2. It loses on no question type by more than 1 question (top 3, against the
   baseline, type by type).
3. It ties or beats the baseline on plain lookups (top 3 count).
4. On the paraphrases its top 3 rate is at most 5 points below its own top 3
   rate on the original wording of the same questions.

If any condition fails the verdict is STOP: report the evidence and one bounded
alternative. As information only, the same four conditions are computed for
`fused@1` and `fused@2` with the derived cues, because the product candidate is
a fused list; a pass there does not change a verdict that the rule as written
gives for `generic`, it names the bounded alternative.

Ranks are the first stored memory in the question's answer ids. Top 3 and top
5 are counts of questions, rank 1 is the number ranked first, and MRR is the
mean reciprocal rank. For questions with several answer memories, the share of
answer memories in the top 5 is reported as well.
