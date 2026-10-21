# The scale gate

Part of dwamianm/prism#351. This file is committed before any padded store is
built or any question is asked of one, so the numbers cannot move it.

## The hypothesis

The fixtures hold 46 to 75 memories each, so the vector list is never crowded and
the baseline already ranks about 85 percent of questions in the top 3. The
hypothesis: the graph's value shows when a store is large and the vector list is
crowded. The routing gate (`GATE.md`) cannot show it, because there was almost
nothing to gain. This experiment pads one owner's store to 500, 2,000 and 10,000
memories and asks the same frozen questions at each size.

## The stores

One owner, one store per distractor kind, built once and padded in nested steps
(the 500 pad is a prefix of the 2,000 pad, which is a prefix of the 10,000 pad).

- **Core**: all fixture memories combined, with their ids prefixed by the fixture
  (`store-v1`, `defects-v1`, `chat-v1`, `draw2-v1`): 75 + 14 + 54 + 46 = 189
  memories, and the four gold graphs, entity ids prefixed the same way. The fixtures
  share names (Alex and Jordan in two, Dana, Sam, Priya and Northwind in several,
  Denver, Japan and Tokyo); in the combined store those are kept as separate
  entities with the same names, and a question that names one seeds all of them.
  They are extra natural distractors for both channels and are reported as such.
  The combined gold has four owner entities; each is damped like the owner entity
  of a single fixture.
- **Padding to 500, 2,000 and 10,000 memories**, two kinds, reported separately:
  - **Ordinary turns (`conversation`)**: turns of real conversations already in the
    benchmark data, with no graph edges, as a user's unrelated memories would have.
    The turns of the ten LoCoMo conversations (5,882) in a fixed shuffled order, then,
    because LoCoMo has fewer turns than the 10,000 size needs, the user turns of the
    LongMemEval oracle file (5,479) in a fixed shuffled order. The 500 and 2,000 pads
    are LoCoMo only; the 10,000 pad is 5,882 LoCoMo turns and 3,929 LongMemEval user
    turns. Each turn is one memory with its text as written.
  - **Look-alikes (`lookalike`)**: copies of the core memories with every person,
    place, organization, project, pet and book name replaced by another name drawn
    from fixed pools (deterministic, seed 20261021, no model), so the sentence shapes
    and topics match the questions' (other people planning trips, changing jobs,
    having pets) and the names do not. Each copy is a clone of the whole core with its
    own names; a clone memory whose text equals an earlier memory's after substitution
    is dropped and counted. The clones get gold graphs too (the same claims over the
    renamed entities, direct edges, containment kept inferred), because the substitution
    makes that cheap. No question names a clone's entities, so the walk, which seeds
    from the question's names, never reaches them: they enlarge the edge table and the
    vector list, and that is all.
- Real local embeddings for every memory and question (the model the fast tiers use).
  Embedding is done once per distinct text, kept in a cache file, and its time is
  reported. PostgreSQL stores reuse the cached vectors, so they hold the same vectors
  as the DuckDB stores.

## The questions

The frozen questions, unchanged: the 53 original items (the three fixtures' probes,
the added multi-hop questions and the cities spellings), the 86 blind questions of
`blind-v1.json` and the 65 of `blind-v2.json`: 204 questions with their answer memory
ids (prefixed with the fixture) and their tags (the original `two_hop` and `three_hop`
count as multi-hop). Pooled types: 76 lookup, 27 list, 25 multi-hop, 21 temporal, 19
correction, 23 negation, 13 aggregation.

## The strategies and measures

- `baseline`: `retrieve()` as it is, over the whole padded store.
- `routed[derived]` (the headline) and `routed[hand]` (a comparison): the router and
  cues exactly as committed in `GATE.md`'s run: for a question the router sends to the
  graph, the one statement walk's list ahead of the text list; otherwise the baseline's
  list unchanged. Nothing is tuned, at any size.
- Per size (core, 500, 2,000, 10,000) and per distractor kind: answers in the top 3,
  top 5, rank 1 and MRR, by question type and pooled; the baseline's own decay with
  size; the walk's latency (median and 95th percentile) on PostgreSQL and on DuckDB.
  A size that is too slow on DuckDB is reported as such and measured on PostgreSQL
  only.

## What "the graph advantage grows with store size" means

The advantage at a size is the routed graph's pooled top 3 count minus the baseline's,
as a share of the 204 questions, in points. The claim holds for a distractor kind when
all three hold:

1. **Growth.** The advantage at 2,000 memories is at least 8 points larger than at the
   core size (about 16 more questions in the top 3).
2. **No loss.** No question type loses more than 1 question against the baseline at any
   size (pooled over the three question sets).
3. **Unrouted types unchanged.** Lookups, temporal questions and corrections the router
   sends to the text ranking have exactly the baseline's rank at every size (true by
   construction; it checks the pipeline). Questions of those types the router sends to the
   graph by mistake (a known router error: 4 of the 86 blind-v1 questions) are counted and
   reported and count in condition 2.

The claim holds for the experiment only if it holds for both distractor kinds; it holds
for one kind only if just that kind passes. The sizes of 500 and 10,000 are reported
without a condition, as is whether the typed graph helps most at the sizes real users
will have (hundreds to thousands).

## What I will conclude

- **If the advantage grows** (condition 1 holds with 2 and 3): a crowded store creates
  room the typed graph uses, for that kind of crowding. The value of typed edges then
  depends on store size, and the owner decides about extraction work with that evidence;
  the gold graph is the best case, so the result is an upper bound for any extractor.
- **If the advantage is flat or shrinks**: the hypothesis is not supported in the range
  tested. I will say which of three things happened: the baseline barely degrades (no
  room was created); the baseline degrades and the routed graph does not recover the
  lost questions (room was created and the graph did not use it); or both degrade together
  (the graph's list falls back on the same crowded vector list). In every case the
  conclusion for #351 stays that typed relation edges are not a general recall
  improvement, whatever the store size, and the case rests on list questions alone.
- **If the advantage grows only for one kind**: I will report it as growth under that
  kind of crowding and not as a property of scale.
