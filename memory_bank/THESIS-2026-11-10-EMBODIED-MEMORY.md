# Thesis: address-first memory (2026-11-10)

**The owner's question:** judge PRME from its code and from short tests of our own, assume every claim already
written in this project is wrong, and propose the memory algorithm that would make PRME the best there is, for
chat applications now and for robots next.

**The answer in four sentences.** PRME is a careful record keeper: it does not lose what it is given, it gives the
same answer twice, and it accepts writes fast. It is a weak rememberer: its first answer is often not the best
record it holds, it cannot tell what is true now from what was true before, and it never says "I do not know".
The cause is one design decision, that remembering means ranking stored text by resemblance to the question. The
proposal is to stop searching the data: search a small catalog of *what the memory knows about*, then look the data
up by address.

**Status.** A proposal with short diagnostics behind it. Nothing in `src/` changed, no default changed, and no
benchmark score is claimed or used. This version replaces the first draft written the same day, which leaned on
the project's recorded chat scores; the owner asked for them to be set aside, and they are not used here.

## 1. What was run

Everything below is on `main` at `29a0460d` (0.17.0a6), default settings, a directory pack, local FastEmbed
embedding, one laptop. The scripts and their outputs are in `memory_bank/thesis-2026-11-10/`.

| Script | What it does | Model calls | Time |
|---|---|---|---|
| `embodied_probe.py` | synthetic robot observations through `store()`, questions with known answers | none | 17.6 min at `--flood 3000`, about 8 min at `--flood 400` |
| `chat_check.py` | one hand-written chat (24 turns, 4 sessions, 14 questions), through `store()` and through `ingest()` | 4 extraction batches, `deepseek-v4.1-flash:cloud` through the local Ollama server | 25 s |
| `stage_timing.py` | where `retrieve()` spends its time at 300, 3,000 and 20,000 memories | none | about 5 min |

What these are not. They are small, written by one author in one sitting, and mostly one run each. The scenes are
synthetic and the sentences are plain, which makes word matching easier than real data will. No reader model
answered anything: the checks look at what PRME returns. The `ingest()` run is not repeatable (two runs derived 30
and 26 facts from the same chat). I did not test crash recovery, index rebuild, encryption, PostgreSQL, tenant
isolation, concurrency, the organizer or vision, and say nothing about them. Treat every number as a pointer to a
mechanism in the code, not as a score.

## 2. Evaluation

### 2.1 What is good, on this evidence

- **Deterministic.** The same question twice returned the same records and the same context, 3 of 3.
- **Fast to accept.** `ingest_fast_many` took 1,663 events a second and the indexer caught up at 150 to 175 a
  second.
- **Flat read time once the store is not tiny.** 205 ms at 3,000 memories and 220 ms at 20,000.
- **An exact time filter.** With `event_time_from/to`, a one-hour window returned its 20 records and nothing else.
- **A usable semantic channel.** For "What pet does Sam have?" the embedding alone put the right record first with
  a clear margin (0.64 against 0.56). The raw signal is there; later stages lose it (2.2).
- **The log.** Every write is an immutable event and the rest is derived from it. I read this in the code and did
  not test a rebuild. If it holds, it is the property that makes everything proposed below safe to try, because a
  derived structure that turns out wrong can be thrown away and rebuilt.
- **It runs in a process, from a directory, with no server.**

### 2.2 What is wrong, with the mechanism for each

**A. The best record is found and then demoted.** In the chat check, the answer was in the top 3 for 6 of 13
questions on both write paths. Two mechanisms, both visible in the score traces:

- *Function words vote.* "What pet does Sam have?" has one word-match in the whole store: "I never know **what** a
  cup is." Rank fusion gives the word channel the same vote as the meaning channel, so that record ranks first
  and the beagle record, first by meaning, drops. The lexical index has no notion that "what" carries nothing.
- *Neighbors ride on a wrong hit.* Records from the same session as a top hit are lifted to 0.6 of its score. So
  one wrong first hit pulls its whole session above better direct matches. With `PRME_PACKING__SESSION_CONTEXT_WINDOW=0`
  the beagle moved from rank 7 to 3, Toronto from 10 to 6, the new dinner time from 14 to 8, and the top-3 count
  from 6 to 7 of 13. This is a diagnosis, not a recommendation to turn the option off.

**B. It does not know what is true now.** Twelve records say where the mug was; none says where it is. When moves
are 10 minutes apart, the newest place ranked first 9 of 20 times after 2 moves and 2 of 20 after 12, which is
chance. Recency is `exp(-0.05 * days)` (`_current_state_recency` in `retrieval/scoring.py`), so minutes do not
register. Setting `PRME_SCORING__RECENCY_LAMBDA=50` gave 20 of 20 and 17 of 20, which confirms the cause and is not
a fix, because no single constant serves both minutes and months. Store-time supersedence changed nothing (9 of 20,
2 of 20): a note has no subject to supersede on. In the chat, "What time does Sam usually eat dinner?" ranked the
old night-shift answer above the new one on both paths, and after `ingest()` the old answer was first and the new
one 43rd.

**C. Extraction finds structure and then nothing uses it to track state.** Each extracted claim does carry a
subject, a predicate, an object and a polarity in its metadata (an earlier draft of this section missed that and
said extraction only copies sentences; the claim *text* is the sentence, the structure sits beside it). The
trouble is what happens next. Sam's job arrived as six different predicates (`works_as`, `works_at`, `work_shift`,
`quit`, `starts_at`, `works_shifts`), so nothing connects the new job to the old one and every claim stays open
(`valid_to` empty, lifecycle `tentative`). Even one predicate is not enough: `diet = vegetarian` (positive) and
`diet = strictly vegetarian` (negative) were both left open. Polarity is sometimes wrong: "she can't eat gluten"
became `dietary_restriction = gluten` with negative polarity, rendered as "It is not true that Priya dietary
restriction gluten", the opposite of what was said. The splitter broke "St. Mary's" into two fragment claims.
There were two "Miso" entities and an entity named "I". And the extra records did not help ranking: the beagle
went from rank 7 (`store()`) to 22 (`ingest()`), Toronto from 10 to 31.

**D. It always fills up and never says "unknown".** "Does Sam own a cat?" returned every record in the store. A
question about an object never seen returned 128 records, the same as a question about a seen one, although the
scores differ (best match 0.52 to 0.60 against 0.63 to 0.77). In the 3,000-observation flood, 97.8% of the context
lines were routine noise and only 4 of 8 rare events reached the top 5.

**E. One round only.** "Where is the thing Dana asked for?" found the request 20 of 20 times and the object's
place in the top 5 zero times. "Where does Sam's sister live?" found the sister and not Toronto.

**F. The cost is in bookkeeping, not in search.** Of about 220 ms per read, loading 500 candidate records took
about 137 ms, scoring 18 ms, packing 17 ms and the rest about 48 ms; in a CPU profile, validating score records
and building the receipt were the largest pure-Python costs. Search itself is a small part. `store()` took 90 to
136 ms per memory against about 6 ms through the batch path. A 60-character memory cost about 4 KB on disk at
20,000 memories.

**G. The read path is a pile of repairs.** `retrieve()` is one function of about 1,265 lines with stages numbered
1, 1b, 1c, 2.5, 2.6, 2.7, 3.5, 5a, and 5.5 through 5.5h. There are 203 configuration fields, 62 of them
`enable_` switches. Each stage reads like a fix for one observed failure, layered on a ranking that was not
changed. Finding A is what that produces: two repairs (word matching, session neighbors) undoing the one signal
that was right.

### 2.3 Verdict

The storage half is good engineering and the retrieval half is an ordinary text search with many patches. As the
model on the other end, I would trust PRME to hold my memories and I would not trust its first answer, its sense
of "now", or its silence. Those three are most of what remembering is. Findings A to E share one root, so they
should be fixed together by changing what is searched, not one at a time with a stage 5.5i.

### 2.4 Do the existing options already fix these?

The owner asked whether recent features, profiles in particular, already address the findings. I listed every
boolean and optional setting, picked each one whose description touches a finding, and ran it on the same checks.
One run each; the `ingest()` rows vary from run to run by about one question, so a difference of one is not a
result. Baseline: answers in the top 3 for 6 of 13, 896 tokens (`store()`) and 1,024 tokens (`ingest()`).

| Option or feature | Finding it should help | Result |
|---|---|---|
| Profiles (`consolidate_knowledge`), asked in the third person | A, B | `ingest()`: 6 of 13, tokens 1,024 to 2,741. `store()`: 7 of 13, tokens 896 to 1,874 |
| Profiles with `enable_owner_profile_context`, asked in the first person | A, B | 7 of 13 on both paths, a profile in the top 3 for 11 questions, tokens about 2,700 on `ingest()` |
| `enable_extraction_corrections` with `enable_superseded_source_suppression` | B | 6 of 13; old answer above the new one for 0 questions instead of 1 |
| `enable_typed_extraction` with `enable_relation_routing` | E | 6 of 13; the sister question unchanged |
| `enable_fact_text_resolution`, `enable_named_claim_text`, `enable_windowed_extraction` | C | 5 of 13; old answer above the new one for 4 questions |
| `enable_speaker_grounding` | C | 6 of 13 |
| `enable_query_reformulation` (a model call on every read) | A | 6 of 13 |
| `enable_qa_pairing` (`store()`) | A | 7 of 13, stale above answer 2 to 3, tokens 896 to 1,310 |
| `enable_store_supersedence` (`store()`) | B | no change, in the chat and in the robot scene |
| `min_score=0.5` on the request | D | "cat" question 24 records to 14, tokens 896 to 600, and one real answer lost from the context. At 0.8 nothing is returned for any question |
| `reinforce_similarity_threshold` 0.90 and 0.95, `enable_surprise_gating` | flood | no change in what is returned; the threshold slowed writes from 10.8 to about 7.5 a second |
| Every compatible option above at once, first person | all | 7 of 13, old above new for 1, tokens 2,988 |
| `get_assertion_state` | B | needs the exact subject and predicate; the job has six predicates |
| `aggregate_quantities_from_text` | counting | "How much has Sam spent on running gear in total?" returns `unsupported_shape` |
| `enable_reranker` | A | not tested: its dependency is not installed here |
| `packing.current_state_observations` | B | not tested: it acts only on camera observations |

What a profile is, from reading one: the entity's source excerpts in date order, oldest first, each under a
metadata header of about 60 tokens (source id, four timestamps, epistemic type, source type) for an excerpt that
is often under 10 tokens. It holds "I'm vegetarian" and "not strictly vegetarian anymore" side by side with no
mark of which holds now. It is a longer piece of text to rank, so it inherits finding A, and it roughly triples
the context. It does help a first-person question reach the owner's records, which is what it was built for.

Other things this turned up: `enable_extraction_corrections` cannot be combined with `enable_fact_text_resolution`
or `enable_windowed_extraction` (they turn batching off and corrections need it); the sync client's
`consolidate_knowledge` does not accept `include_speaker_turns` although the engine does.

**Answer: no.** With everything on, the result moved from 6 to 7 of 13 at three times the context. Each option
adds records, text or a stage to the same mechanism (rank text by resemblance), and none changes it. Two results
are worth keeping: corrections with source suppression is the one option aimed at the right problem (closing old
claims) and it showed a small effect; and extraction already emits subject, predicate, object and polarity, which
means the address-first proposal does not need a new extraction, it needs the predicates resolved to shared
attributes and the open-value rule enforced.

## 3. The root cause

Every question is answered by asking: *which stored text looks like this question?* That is the wrong question in
four ways.

1. A question and its answer often share no words and little meaning ("pet" and "a beagle called Miso"; "the
   thing Dana asked for" and "hallway shelf").
2. Resemblance has no notion of time. The old address looks exactly as much like the question as the new one.
3. Resemblance has no notion of absence. Something always looks most similar.
4. The search space grows with every observation, including the ten-thousandth "hallway clear".

A person does not remember this way and neither does a database. Both keep an index of *what things they know
about*, and go from the question to the thing, then from the thing to what is known about it.

## 4. The proposal: address-first memory

**Search the catalog, look up the data.**

```text
                 the catalog (small)                      the data (large)
question  ->  which entities, which attributes?   ->   read the beliefs at those addresses
              (meaning + words, over a few               (exact lookup: current value, history,
               hundred names, not a million records)      count, evidence)
          ->  nothing in the catalog matches      ->   say so, then fall back to text search
```

### 4.1 The three structures

All three are derived from the event log and can be rebuilt from it.

- **Belief.** `(entity, attribute) -> value`, with the interval it held (`valid_from`, `valid_to`), how it is known
  (seen, told, inferred, considered), how many observations confirmed it, and the events behind it. One address
  holds at most one open belief per source of truth; closed beliefs stay as history. "Sam, employer, pediatric
  clinic, since 2026-11-02, told by Sam, 2 confirmations" and, closed, "St. Mary's hospital, until 2026-10-15".
- **Catalog.** For each entity, the attributes it has, each with a short description and the phrasings that have
  resolved to it. Sam: employer, diet, pet, sister, dinner time, longest run, running spend. It grows with the
  number of distinct things known, not with time. A year of "hallway clear" is one entry.
- **Episodes.** The text records PRME stores today, kept as evidence and as the fallback search space. Written
  only when something changed (4.3).

PRME already has validity windows, supersedence, epistemic types, entity nodes and `get_assertion_state`. What is
new is the catalog, the rule that the catalog is what gets searched, and the rule that an address has one open
value.

### 4.2 Reading

1. **Resolve.** Match the question against the catalog: first its entities, then, within those entities, its
   attributes. This is the same embedding and word matching PRME has, pointed at a few hundred short descriptions
   instead of every record. "What pet does Sam have?" has to find `pet` among Sam's seven attributes, which is a
   far easier problem than finding one sentence among all sentences, and a word like "what" has nothing to match.
2. **Plan.** The question's shape picks an operation. The rules are deterministic and the plan is recorded in the
   receipt.

   | Question shape | Operation | Fixes |
   |---|---|---|
   | what is X now | open belief at the address | B |
   | what was X, when did X change | the address's interval chain | B |
   | how many, how much, list all | count or sum over an address's beliefs, computed, with evidence ids | reader miscounting |
   | X of the Y of Z | join: value at one address is the entity of the next | E |
   | between T1 and T2 | time filter first | time windows |
   | anything else, or nothing resolved | today's text search over episodes | keeps what works |

3. **Answer with a status.** Every response carries one of: *known* (beliefs found), *partial* (some addresses
   resolved), *unknown* (nothing in the catalog matched and the best text match is weak). "Does Sam own a cat?"
   resolves to Sam's `pet` attribute, which holds a dog and no cat, so the answer is the dog belief and nothing
   else, not 34 records.
4. **Stop when it is enough.** A lookup that answered the question returns its beliefs and their evidence, a few
   lines. The token budget is a ceiling. Only the fallback path fills toward it.

No model is called while reading.

### 4.3 Writing

Each observation ends as one of four outcomes at its address:

| Outcome | Condition | Effect |
|---|---|---|
| confirm | same value as the open belief | extend its interval, add one to its count, write no new record |
| update | different value, same source of truth | close the old belief, open the new one, write one episode |
| new | no belief at this address | open one, add the attribute to the catalog, write one episode |
| conflict | different value, different source | keep both open and flagged; do not pick a winner silently |

How an observation gets its address:

- **Structured callers state it.** `store(text, assertion={"subject": ..., "attribute": ..., "value": ...})`. A
  robot's perception stack already knows the object, the relation and the place. No model call, constant time.
- **Free text goes through extraction**, which already emits subject, predicate, object and polarity (2.2 C).
  The target becomes `(entity, attribute, value, polarity, modality, time)` with the attribute resolved (below). Polarity and modality are fields so that "can't eat
  gluten" cannot be stored as its opposite and "thinking about a greyhound" is a *considered* value that never
  answers "what pet".
- **Attribute names are resolved, not matched.** A proposed attribute is compared with the entity's existing
  attributes in the catalog; above a threshold it is the same address, below it a new one. This replaces exact
  predicate matching and is the part most likely to be hard (see 6).

The confirm outcome is the change gate. A day of routine patrols becomes a handful of long intervals, and the
store grows with what happened rather than with how long the machine was on.

### 4.4 Time

Time is stored as intervals at full resolution and is never a decay constant in a lookup. "Now" is the open
belief; "at 3 pm" is the belief whose interval contains 3 pm; "newest" is by order. The fallback text search keeps
a recency term, by order within the candidates rather than by days.

### 4.5 Learning, without a model in the loop

- **Miss log.** A question that resolved to nothing, followed by the caller reading an episode or correcting the
  memory, is a missing phrasing. Add it to that attribute's catalog entry. The catalog gets better at the
  questions this user actually asks.
- **Sleep pass.** The organizer, offline and optionally with a model: mine attributes from episodes that have
  none, merge duplicate entities and attributes, and turn repeated action-outcome episodes into procedural
  beliefs ("lifting the pot by the left handle: failed 3 of 3").
- **Self-check.** Because beliefs are derived, a rebuild from the log must reproduce them. Run that as a test, and
  keep invariants like "an address has at most one open value per source" as assertions.

### 4.6 What this does to the code

The read path becomes a small planner with five operations and one fallback. Most of stages 5.5 through 5.5h
exist to make text ranking behave like a lookup; with a real lookup in front, they apply only to the fallback and
several can be retired after measurement. Fewer stages and fewer switches is a goal of the design, not a side
effect.

## 5. For robots

Nothing above is specific to chat, and the robot case is the easier one, because the caller supplies addresses.

- **Place is an entity.** Rooms contain furniture contains objects; "where" is answered at the level a person
  would say it. Coordinates, when the caller has them, ride along as metadata with a named frame. The navigation
  stack owns the metric map.
- **A working tier.** The last seconds to minutes live in memory and reach the log in batches. The accept path is
  already fast enough.
- **Procedural beliefs** (4.5) are how the machine stops repeating a failed action.
- **"Unknown" is a safety feature.** Acting on the nearest wrong memory is the failure to design against.
- **Budgets to hold, on the robot's own hardware:** accept under 1 ms, searchable within 1 s, a lookup under 5 ms,
  any read under 50 ms at p95 from a thousand to a million observations. Today a read is about 220 ms; finding F
  says most of that is removable.

## 6. Where this could fail

- **Attribute resolution.** If "employer", "works at" and "job" do not land on one address, the memory fragments
  and updates stop closing old values. This is the one real research risk. It is testable in an afternoon without
  building anything else: take the catalog a careful extraction would produce for a few chats and measure how
  often a paraphrased question and a paraphrased statement resolve to the right address.
- **Extraction quality.** The proposal asks more of extraction (polarity, modality), and today's output has
  errors at a simpler task. A wrong belief is worse than a missing one. Mitigations: a belief always carries its
  source sentence, low-confidence extractions stay episodes only, and the fallback search means a missing belief
  costs nothing compared with today.
- **Questions that are not about an address.** "What did we talk about last week?" goes to the fallback. The
  design does not make these better; it must not make them worse.
- **Catalog growth in a large world.** A warehouse has many objects. The catalog is still far smaller than the
  observation stream, and entity resolution narrows it before attributes are searched, but this is untested.

## 7. Order of work

Each step is one option, off by default, and passes when its check below passes and the project's gates in
`CLAUDE.md` hold. The checks are the scripts in `memory_bank/thesis-2026-11-10/`, which should gain a second seed
and less regular text as they are used.

| Step | Change | Passes when |
|---|---|---|
| 0 | Attribute resolution experiment, no engine change | paraphrases reach the right address often enough to proceed; decide the threshold |
| 1 | `assertion=` on `store()`, beliefs with the four outcomes, lookup route for "now" questions | `embodied_probe` S1: 20 of 20 at every spacing and count |
| 2 | Catalog and resolve step; response status | `chat_check` with hand-stated assertions: answers in the top 3 for at least 11 of 13; "cat" question returns the pet belief only |
| 3 | Joins and computed counts | S5: 18 of 20; the running-spend question returns 285 euros with three evidence ids |
| 4 | Extraction writes addresses with polarity and modality | `chat_check` through `ingest()`: no inverted fact, old dinner time closed |
| 5 | Confirm outcome as the change gate | S2: 8 of 8 rare events in the top 5; record count follows changes |
| 6 | Fallback repairs: no vote for function words, neighbors never above a better direct hit, recency by order | `chat_check` fallback questions; the offline evidence gate |
| 7 | Read-path cost: hydrate fewer candidates, cheaper receipts | read p95 under 50 ms at 20,000 memories |
| 8 | Place, working tier, procedural beliefs | new scenes written with each |

Step 6 is independent of the rest and is the cheapest improvement available today. Step 0 decides whether the
thesis stands.

## 7a. What was tried and removed

Two options were built, measured and taken out again, because an option that did not earn its place does not
ship. One defect found on the way is in shared code and was left for a decision.

**Function words left out of the word search** (`packing.lexical_content_words`, finding A). The question's
function words (the graph seed list, and words of one or two letters) were left out of the lexical search, so a
record that shares only "what" or "is" with the question is no longer a word match. On the offline evidence gate of
2026-11-10, alone against the defaults, LongMemEval-S all evidence packed fell 3.6 points (95% interval -5.7 to
-1.3) and two LoCoMo questions lost all their candidates, because a word match also gives a record its second
path, which packing's tiers and balanced order read. On the contract scorecard it lost the flood's leak question,
whose answer only the word "is" connected. It might help a store of short records, but only after a packing change.

**Corrections chosen from the graph** (`extraction_correction_source="entity"`). Instead of the claims nearest
each turn's text, a batched request was shown the claims linked to the entities the turn names (the graph seed
rule, and the speaker's entity for a first-person turn), newest first, with the same caps. Two live recordings per
source of the contract chat (`deepseek-v4.1-flash:cloud`), against a key of 7 claims the chat corrects:

| | similar A | similar B | entity A | entity B |
|---|---|---|---|---|
| retired correctly (of 7) | 4 | 3 | 2 | 2 |
| retired wrongly | none | none | "knee hurt after the run" | "knee hurt a bit" |

On the hidden chat, `similar` retired 5 and 5 of 7 and `entity` 1 and 3, none wrongly. The diagnosis: a
first-person turn names the owner, and the owner's five newest claims are not the ones a correction is about. "I'm
vegetarian", six weeks old, never reached the request that said "not strictly vegetarian anymore", while a recent,
unrelated claim did ("my knee hurt a bit after it"), and the model retired it on "Knee was fine this time". Corrections,
when turned on, show the claims nearest each turn's text, as before; since 2026-11-11 they are an option of their
own and not part of `memory_mode="address_first"`, which is the read-time half only
(`benchmarks/results/research/2026-11-11/ADDRESS-FIRST-MID-TIER.md`).

**An open defect in default-on code.** `is_current_state_question` (`prme/retrieval/query_analysis.py`) reads
"first" in "first aid kit" as a history word, so "Where is the first aid kit now?" is not a current-state question
and `packing.current_state_observations`, on by default, does not fire for it in a store with camera observations.
The belief ledger's own history check now takes the names its claims use out of the question first; the shared
function was left as it is, for a decision.

## 8. Why I would want this memory

When I am handed context, what I need is short: what is true now, since when, how it is known, and an honest
"nothing on that". Today's PRME hands me a hundred lines and leaves me to work out which one is current. A memory
that answers from addresses gives me the fact, its history when I ask, and its evidence when I doubt it. It is
also the only design here whose cost follows the size of the world rather than the length of the recording, which
is what a machine that never switches off needs.
