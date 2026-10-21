# The routing gate

Part of dwamianm/prism#351. This file is committed before the router, the new
memory set, the new questions or any run exist, so the numbers cannot move it.

## Where this comes from

The blind validation (`BLIND.md`, `BENCHMARKS.md`) found that the typed walk helps
the questions it is built for (lists, multi-hop, aggregation) and hurts those the
text ranking already answers (lookups, temporal questions, corrections). The owner
chose the bounded alternative: use the graph channel only where it won, and leave
everything else to today's ranking unchanged. This phase tests that on a draw
nobody has seen, with new facts and not only new wording over old memories.

## What is routed

A question-shape router (`router.py`, rules over wh-words, plural nouns, counting
phrases and chains of relational nouns, built from the relation vocabulary and
general phrasing, not from reading any blind question) decides for each question
whether it asks for:

- a **list**: more than one thing ("which cities", "what are the", "who are");
- a **count**: "how many", "number of", "in total";
- things of a **subtype**: "which kind of", "what kinds of";
- a **multi-hop** chain: two relational nouns joined by "of" or a possessive
  ("the manager of Jordan's partner"). The blind table says multi-hop is safe for
  the graph (no type lost a question against the text ranking when it was routed);
  the router only recognizes the chain shape, so most multi-hop questions go to the
  text ranking and gain nothing.

Those questions are **routed to the graph**. Every other question, lookups,
temporal questions, corrections and negation included, goes to today's ranking
unchanged: the baseline's list, byte for byte.

**Routed strategy.** For a routed question: the generic walk's list first (the
first report's settings, hop limit 2, owner blocked), then the text list, with the
cue words derived from the vocabulary definitions (`derive_cues`). The comparison
column uses the hand written cue lexicon. No fusion weight is involved. Nothing
about a setting changes after the run.

## The walk the harness uses

One SQL statement per walk on PostgreSQL, and its DuckDB equivalent (`sqlwalk.py`):
a three layer join over typed edges that returns compact rows, with:

- a cap of **32 claim edges per entity per layer** and **32 mention edges per
  reached entity**, newest first, and at most **64 entities per layer**. Why: in the
  three gold graphs no entity except the owner has more than 11 claims or 12
  mentions, so the cap is about three times the largest ordinary entity and no
  existing corpus is truncated; recency keeps the current fact when a hub is cut;
- hub damping: the owner entity is never expanded unless the question names it, as
  before, and is never counted when memories are scored.

On the three original corpora the SQL walk must rank every question exactly as the
Python walk does (a test), so the harness's accuracy numbers stay comparable.

## The new draw

1. A new memory set of about 40 memories on a different story (new people, places,
   a job change, a trip, a team), in the style of the fixtures.
2. Its gold graph, written from the memories only, before any question exists, and
   committed.
3. A local model drafts about 60 questions with answer memory ids and type tags from
   the memories alone; the reviewer drops the bad ones and counts them; the frozen
   file's sha256 is recorded before any strategy runs.

## The pass rule

On the new draw, one run, all six must hold. The baseline is `retrieve()` as it is.

1. Overall top 3 is at least 5 points above the baseline (a rate difference of 0.05
   or more).
2. No question type loses more than 1 question in the top 3 against the baseline.
3. Lookups, temporal questions and corrections are exactly equal to the baseline:
   every such question has the same rank under both.
4. List, multi-hop and aggregation questions together gain at least 6 questions in
   the top 3.
5. The router's accuracy is at least 90 percent, and at most 2 percent of lookups
   are routed to the graph. Accuracy is the share of questions whose route agrees
   with the route its blind type tag implies: graph for list, multi-hop and
   aggregation, the text ranking for lookup, temporal, correction and negation. It is
   reported first on the spent 86 question blind set (a check of the router only;
   no strategy is tuned on it) and then on the new draw. The condition is judged on
   the new draw.
6. The SQL walk takes at most 50 ms at the 95th percentile at 10,000 memories on
   PostgreSQL (the latency harness, 100 questions, half naming the owner).

A failure of any one is a fail; the report says which.
