# Perfect extraction test: results (2026-11-11)

**Question** (GUIDE.md): if extraction wrote correct facts, would PRME answer the probe questions?

**Result: not supported.** With the hand-written facts of level O2 and `memory_mode="address_first"` (arm B1), none
of the three probes meets its bar, and none does better than model extraction with the mode on (M1):

| Probe | Bar (B1, top 3) | B1 | M1 (model, mode on) |
|---|---:|---:|---:|
| defects-v1 | 7 of 7 | 6 of 7 | 7 of 7 |
| chat-v1 | 17 of 18, and at least M1 + 2 | 13 of 18 | 15 of 18 |
| member-v1 | 11 of 12 | 10 of 12 | 11 of 12 |

Read this with three limits, all stated below with their numbers. The oracle stored far fewer claims than the model
(52 against 101 on chat-v1; 9 against 32 on member-v1, where the form refused 15 of the 24 facts written). One
annotator wrote the facts, blind to the questions. And the assertion form needed a wider mention rule than the
engine has, for 78 of the 113 facts written. Of the 8 questions B1 misses, 3 fail on the read side with the
right facts in the pack (a wrong route, a catalog address picked by meaning, a claim's text that does not show its
typed role), 1 on a relation the router reads one way only, 3 on what the form can carry (no date for a job start,
facts the form refused), and 1 in every arm (a relative date).

## Setup

- Worktree `/Users/dwamianm/Sites/prism-oracle`, branch `research-oracle-extraction-2026-11`, off
  `address-first-memory` at db0a8e81; the facts are those of commit d6529a93, kept as written. `src/` is unchanged.
- The build (`oracle.py`, `run_oracle.py`) runs the probe's own `chat_probe.build()` and `measure()` unchanged: the
  same turns, event times, session ids, roles, speaker, end of session, questions, ranks and structure measures.
  A `prepare` hook wraps `engine.ingest` so a turn with accepted facts is ingested with
  `ingest(content, assertions=...)`, the turn's own text as content.
- A turn with no facts. `ingest()` refuses an empty assertion list ("assertions must hold at least one
  assertion"), so such a turn goes through `ingest()` like any turn, and the extraction provider's methods are
  replaced by a stub that answers with an empty extraction (no entities, no facts) and makes no call. The turn is
  stored exactly as a model build stores a turn the model found nothing in. `store()` was not used: it writes a
  different record than `ingest()`. Pronoun repair and single-turn extraction raise in the stub; neither was asked.
- No model call anywhere: providers blocked by environment, the stub in place, and every oracle build checks that
  the probe's call counter is 0. M0 and M1 were rebuilt on this tree from the saved off build's extraction cache
  (`af-off-1111`) with `PRME_BENCH_CACHE_ONLY=1`: 14, 54 and 30 cache hits, 0 calls (Ollama 0.40.16 and model
  digest e04da138 are recorded but were not called). They match the task 8 numbers.
- Member probe: every arm with `--speaker` and `enable_speaker_grounding=true`, as the repository's rule says.
- Only chat-v1 has O2 facts (4), so on defects-v1 and member-v1 the A arms are the B builds and were not built
  twice; the table marks them "(= B)".

### The mention rule of this experiment

`ingest(assertions=...)` checks that the turn's text mentions each subject and object by plain text, with no
speaker, and the pipeline checks the claims it builds the same way. A user's "I" is never a mention of their name,
so GUIDE.md rule 1 (name the subject) and the engine contradict each other. On the lead's decision, this branch
widens the check at run time only (`MentionPatch` in `oracle.py` replaces `_mentioned` in
`prme.ingestion.assertions` and `prme.ingestion.grounding` while a turn is ingested). A subject or object is
accepted when the turn mentions it (turn), when it is the owner and the user turn says I, me, my, mine, myself,
we, us or our, or the assistant turn says you or your (owner), or when an earlier turn named it (carried). A
literal value must be in the turn, except in an O2 closure, which may name a value an earlier turn mentions
(closure).

## Top 3 and top 5 per arm

| Probe | M0 | M1 | A0 | A1 | B0 | B1 | Bar (B1 top 3) | Result |
|---|---|---|---|---|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 7/7 / 7/7 | 6/7 / 7/7 (= B) | 6/7 / 7/7 (= B) | 6/7 / 7/7 | 6/7 / 7/7 | 7 | fail |
| chat-v1 | 14/18 / 15/18 | 15/18 / 16/18 | 10/18 / 15/18 | 13/18 / 13/18 | 10/18 / 15/18 | 13/18 / 13/18 | 17 | fail |
| member-v1 | 10/12 / 11/12 | 11/12 / 12/12 | 9/12 / 11/12 (= B) | 10/12 / 11/12 (= B) | 9/12 / 11/12 | 10/12 / 11/12 | 11 | fail |

Each cell is top 3 / top 5. The mode helps the oracle's typed facts more than the model's claims on chat-v1 (top 3
from 10 to 13, against 14 to 15), but it also lowers chat-v1's top 5 from 15 to 13.

## What each build stored: the oracle against the model

| Probe | Arm | Active claims | User claims on the named owner | Entities | Facts loaded / written | Model calls | Turns stored through the empty stub |
|---|---|---:|---:|---:|---:|---:|---:|
| defects-v1 | M0, M1 | 24 | 12 | 25 | model | 0 | - |
| defects-v1 | B0, B1 | 31 | 17 | 25 | 31 / 31 | 0 | 6 |
| chat-v1 | M0, M1 | 101 | 35 | 58 | model | 0 | - |
| chat-v1 | A0, A1 | 51 | 23 | 27 | 53 / 54 | 0 | 30 |
| chat-v1 | B0, B1 | 52 | 24 | 28 | 57 / 58 | 0 | 29 |
| member-v1 | M0, M1 | 32 | 7 | 36 | model | 0 | - |
| member-v1 | B0, B1 | 9 | 5 | 4 | 9 / 24 | 0 | 24 |

On defects-v1 the oracle is larger than the model (31 claims against 24); on chat-v1 about half (52 against 101);
on member-v1 about a quarter (9 against 32). The annotator left a turn empty when it only restated a fact (notes,
chat-v1 item 8). In both kinds of build a share of claims carry a rendered triple as text rather than a sentence
of the turn ("Dana diet vegetarian.", "The speaker uses TypeScript."): oracle 18 of 31, 18 of 52 and 2 of 9; model
12 of 24, 38 of 101 and 8 of 32. That is the engine's claim text rule, not this harness.

## Loader: accepted and refused

| Probe | Levels | Written | Accepted | By the turn's text alone | Needing owner | Carried | Closure | Refused (by reason) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| defects-v1 | O1+O2 | 31 | 31 | 9 | 16 | 6 | 0 | none |
| chat-v1 | O1 | 54 | 53 | 26 | 23 | 5 | 0 | literal value not in the turn: 1 |
| chat-v1 | O1+O2 | 58 | 57 | 26 | 27 | 5 | 1 | literal value not in the turn: 1 |
| member-v1 | O1+O2 | 24 | 9 | 0 | 9 | 0 | 0 | subject not in the turn, not the owner, not named earlier: 11; object not in the turn, not the owner, not named earlier: 4 |

**Finding: the gap between the form and what an extractor must be allowed to write.** Of the 113 facts written,
35 pass the engine's own rule (the turn names both ends); 62 more pass only with the widened rule (52 needing the
owner, 11 carried from an earlier turn, 1 closure; an assertion can need more than one) and 16 are refused even
then. So 78 of 113 (69%) cannot be loaded through `ingest(assertions=...)` as it is. The 16 refusals:

- member-v1, 9 facts on assistant turns that state the member's coverage without "you" or "your" ("Oncology
  visits, chemotherapy and radiation are covered in network after the deductible"): the owner is the implied
  subject.
- member-v1, 5 facts on "trip to Jamaica", a name the turn never writes ("I am planning a trip for us in December to
  Jamaica"), and 1 on "marriage", which the turn writes as "I am getting married".
- chat-v1, 1 literal value ("10 miles") that the turn writes another way.

The annotator's own notes (`facts/*.notes.md`) list what the form cannot express at all, such as a job's start date
("I start as analytics manager on May 4": `works_at` has no time, so "May 4" is lost), durations, comparisons and
a reason on a denial; the attribute lists are in `facts/*.attributes.md`.

## B1 rebuilt from scratch

A second B1 build of each probe (`ox-B1r`) gives the same probe rows (every answer rank) and the same graph
measures. On defects-v1 every packed context is byte for byte the same. On chat-v1 (11 of 18 contexts) and
member-v1 (11 of 12) the contexts hold the same lines, but a few lines below the answers change order (lines 15 to
55 of 38 to 66). A model replay rebuild does the same: `ox-M1` built twice from the cache gives the same rows and
graph, and 2 chat-v1 and 3 member-v1 contexts change order the same way. So the ranks repeat exactly; the low order
of the packed context does not, in either kind of build.

## How B1 serves each question (task 9d, from the receipts)

| Probe | Routed | Catalog | Text ranking only | Ledger placed claims |
|---|---:|---:|---:|---:|
| defects-v1 | 5 | 1 | 1 | 5 of 7 |
| chat-v1 | 9 | 9 | 0 | 18 of 18 |
| member-v1 | 4 | 8 | 0 | 11 of 12 |

The read path reaches the facts for 36 of 37 questions: routing or the catalog engages, and the ledger places
claims for 34. What it does with them decides the failures below.

| Probe | Question | B1 answer rank | Path | Relations | Ledger |
|---|---|---:|---|---|---|
| defects-v1 | Which cities will Alex and Jordan visit in Japan? | 5 | routed | visits | route: current, current |
| defects-v1 | What coding language is Alex learning? | 3 | routed | uses | route: current x4 |
| defects-v1 | Which languages does Alex write at work? | 1 | catalog | | catalog: current |
| defects-v1 | How does Alex prefer to travel in Japan? | 2 | routed | prefers, visits | route: current x5 |
| defects-v1 | Who is allergic to shellfish? | 1 | routed | allergic_to | route: current |
| defects-v1 | What did the team choose for the shipment table? | 1 | routed | member_of, part_of | |
| defects-v1 | Who should review the runbook? | 1 | text ranking only | | |
| chat-v1 | Where does Dana work now? | 1 | routed | works_at | route: current, ended |
| chat-v1 | What is Dana's job title now? | 7 | routed | works_at | route: current, ended |
| chat-v1 | When did Dana start the new job? | 10 | catalog | | catalog: current |
| chat-v1 | Who was Dana's manager at Northwind? | 1 | routed | manager_of (manager) | route: current |
| chat-v1 | What does Dana's partner do for work? | 2 | routed | partner_of, works_at | route: current |
| chat-v1 | What is Sam allergic to? | 1 | routed | allergic_to | route: current |
| chat-v1 | What kind of dog does Dana have, and what is it called? | 9 | routed | involved_in | route: current x5 |
| chat-v1 | What is the name of Dana's niece? | 2 | catalog | | catalog: current |
| chat-v1 | When was Ivy born? | none | catalog | | catalog: current, current |
| chat-v1 | Where does Dana's sister live? | 2 | routed | kin_of (sister), lives_in | route: current |
| chat-v1 | Who is Dana's running partner? | 1 | routed | friend_of, partner_of | route: current, current |
| chat-v1 | What was Dana's half marathon finishing time? | 3 | catalog | | catalog: current |
| chat-v1 | Does Dana drink coffee? | 9 | catalog | | catalog: current, ended |
| chat-v1 | What is Dana's diet now? | 1 | catalog | | catalog: current, ended |
| chat-v1 | What news did Melanie share after the race? | 1 | catalog | | catalog: current |
| chat-v1 | What is Dana planning for July? | 1 | catalog | | catalog: current |
| chat-v1 | Where will Dana stop overnight on the drive? | 1 | catalog | | catalog: current, current |
| chat-v1 | Which programming language does Dana prefer? | 2 | routed | prefers | route: current, current, ended |
| member-v1 | Who is Maya's husband? | 1 | routed | partner_of | route: current |
| member-v1 | What is the name of Maya's child? | 1 | routed | kin_of (child and its synonyms) | |
| member-v1 | What are the names of Maya's dependents? | 2 | routed | kin_of (child), partner_of | route: current, current |
| member-v1 | Do you remember the names of my dependents? | 2 | routed | kin_of (child), partner_of | route: current, current |
| member-v1 | What was Maya diagnosed with? | 1 | catalog | | catalog: current |
| member-v1 | What did I tell you about my health? | 1 | catalog | | catalog: current |
| member-v1 | Where is Maya planning a trip? | 6 | catalog | | catalog: current |
| member-v1 | When is Maya's trip to Jamaica? | 2 | catalog | | catalog: current |
| member-v1 | Is Maya expecting a baby? | 3 | catalog | | catalog: current |
| member-v1 | What life event did Maya mention? | 5 | catalog | | catalog: current |
| member-v1 | What is my deductible? | 1 | catalog | | catalog: current |
| member-v1 | What did Maya's coworker Dana ask about? | 1 | catalog | | catalog: current |

## Questions B1 misses in the top 3

Answer ranks per arm (M0, M1, B0, B1; chat-v1 also A0, A1), the B1 top 5, and what the facts and the receipt show.

**defects-v1: Which cities will Alex and Jordan visit in Japan?** M0 2, M1 2, B0 3, B1 5. The annotator wrote "We
chose Tokyo, Kyoto, Kanazawa, and Osaka" as `decided` (rule 9's word for a choice); the router asks `visits`, so
the walk reaches only two claims about the trip ("That is cherry blossom season.", "Trip to Japan trip timing late
March next year."), they rank above the answer, and the answer sentence falls to 5. A vocabulary
question: two correct readings of one sentence, and the router reads only one. B1 top 5: (1) "Hi, I am Alex
Rivera. My partner Jordan and I are planning a trip to Japan in late March next year." (2) "That is cherry blossom
season." (3) "Trip to Japan trip timing late March next year." (4) "My partner Jordan and I are planning a trip
to Japan in late March next year." (5) "We chose Tokyo, Kyoto, Kanazawa, and Osaka."

**chat-v1: What is Dana's job title now?** M0 9, M1 12, A0 5, A1 7, B0 5, B1 7. The fact is right: `Dana works_at
Brightpath, role analytics manager, current`, from the turn "Two weeks in at Brightpath and I already like my team
more than the one at Northwind." The ledger puts that claim first, but its text is that sentence, which does not
say "analytics manager": the typed role is in the claim's metadata, not in what is ranked and packed. The
planned claim whose sentence says the title ranks 7. A read side finding: the role does not reach the text. B1 top
5: (1) that sentence (2) "I gave notice at Northwind yesterday." (3) "Green tea now." (4) the same turn as a note
(5) "Thanks! Rachel will love that. She keeps asking what Ivy's aunt has been up to."

**chat-v1: When did Dana start the new job?** M0 2, M1 2, A0 9, A1 9, B0 10, B1 10. No fact holds the date: the
form has no time for `works_at`, so "May 4" is lost (annotator's note, item 2). The model's claim kept the
sentence. A form finding. B1 top 5: (1) "Priya manager of Dana." (2) "I'm Dana. I just started using this
assistant, so here's a bit about me: I live in Denver with my partner Sam and our golden retriever, Biscuit." (3)
"I had a first interview with Brightpath on Monday for an analytics manager role." (4) "Rachel lives in Portland,
so I haven't met Ivy yet." (5) "Congratulations, Aunt Dana! Welcome to the world, Ivy."

**chat-v1: What kind of dog does Dana have, and what is it called?** M0 1, M1 3, A0 3, A1 8, B0 3, B1 9. The facts
are right (`Dana owns Biscuit`, `Biscuit has breed golden retriever`), but the router sends this question to
`involved_in`, not `owns`: the walk reaches five of Dana's own `involved_in` claims (the Portland trip, the race,
the long run, the first interview twice), and the route list ranks them above the dog. A routing error on the read
side; it is why the mode costs
this question 6 places on the oracle facts. B1 top 5: (1) "I'm planning the Portland trip for July to finally meet
Ivy." (2) "I'm so happy I got under two hours." (3) "Training update: Mel and I did a 10 mile long run on
Saturday." (4) "Dana involved in first interview." (5) "I had a first interview with Brightpath on Monday for an
analytics manager role."

**chat-v1: When was Ivy born?** Not ranked in any arm. The turn says "last Thursday"; the answer is a calendar date
that no record states. Neither extraction nor the read side resolves it. B1 top 5: (1) "Rachel lives in Portland,
so I haven't met Ivy yet." (2) "Big news: my sister Rachel had her baby last Thursday! A girl named Ivy." (3)
"Congratulations, Aunt Dana! Welcome to the world, Ivy." (4) "I'm planning the Portland trip for July to finally
meet Ivy." (5) the same turn with its next sentence.

**chat-v1: Does Dana drink coffee?** M0 5, M1 5, A0 6, A1 6, B0 8, B1 9. The facts are right (`Dana prefers green
tea, current`; `Dana prefers coffee, past` at O2), but no word routes the question, and the catalog matches it by
meaning to Dana's diet (states current and ended: pescatarian, vegetarian), so the diet claims rank above "Green tea
now." A read side finding: the catalog picks the address by meaning, and "drink" is nearer to diet than to a
preference. B1 top 5: (1) "Rachel lives in Portland, so I haven't met Ivy yet." (2) "Oh, and a food update: I've
started eating fish again, so I guess I'm pescatarian now, not vegetarian." (3) "Dana diet vegetarian." (4) "I'm
Dana. I just started using this assistant, ..." (5) "Priya manager of Dana."

**member-v1: Where is Maya planning a trip?** M0 1, M1 1, B0 6, B1 6. The five trip facts were refused by the form
("trip to Jamaica" is not written in the turn), so no claim holds the trip. A form finding. B1 top 5: (1) "My
husband is Theo and my child is Nico." (2) "Your individual deductible is $1,500 and the family deductible is
$3,000 for in-network care." (3) "I am pregnant, how does my plan cover this?" (4) "You can add a newborn within
30 days of the birth as a qualifying life event." (5) "Open enrollment is coming up."

**member-v1: What life event did Maya mention?** M0 4, M1 4, B0 5, B1 5. The marriage fact was refused ("marriage"
is not in the turn, which says "getting married"). A form finding; the model misses it too. B1 top 5: (1) "My
husband is Theo and my child is Nico." (2) "You can add a newborn within 30 days of the birth as a qualifying life
event." (3) "Your individual deductible is $1,500 ..." (4) "What is the first question I asked you today?" (5) "I
am getting married. What plan will be best for our new family?"

## Answer ranks per question

| defects-v1 | M0 | M1 | B0 | B1 |
|---|---:|---:|---:|---:|
| Which cities will Alex and Jordan visit in Japan? | 2 | 2 | 3 | 5 |
| What coding language is Alex learning? | 3 | 3 | 2 | 3 |
| Which languages does Alex write at work? | 1 | 2 | 1 | 1 |
| How does Alex prefer to travel in Japan? | 3 | 3 | 4 | 2 |
| Who is allergic to shellfish? | 1 | 1 | 1 | 1 |
| What did the team choose for the shipment table? | 1 | 1 | 1 | 1 |
| Who should review the runbook? | 1 | 1 | 1 | 1 |

| chat-v1 | M0 | M1 | A0 | A1 | B0 | B1 |
|---|---:|---:|---:|---:|---:|---:|
| Where does Dana work now? | 8 | 2 | 4 | 1 | 5 | 1 |
| What is Dana's job title now? | 9 | 12 | 5 | 7 | 5 | 7 |
| When did Dana start the new job? | 2 | 2 | 9 | 9 | 10 | 10 |
| Who was Dana's manager at Northwind? | 3 | 3 | 1 | 1 | 1 | 1 |
| What does Dana's partner do for work? | 2 | 2 | 2 | 2 | 2 | 2 |
| What is Sam allergic to? | 1 | 1 | 1 | 1 | 1 | 1 |
| What kind of dog does Dana have, and what is it called? | 1 | 3 | 3 | 8 | 3 | 9 |
| What is the name of Dana's niece? | 1 | 1 | 2 | 2 | 2 | 2 |
| When was Ivy born? | none | none | none | none | none | none |
| Where does Dana's sister live? | 3 | 2 | 4 | 2 | 4 | 2 |
| Who is Dana's running partner? | 2 | 2 | 4 | 1 | 4 | 1 |
| What was Dana's half marathon finishing time? | 1 | 1 | 3 | 3 | 3 | 3 |
| Does Dana drink coffee? | 5 | 5 | 6 | 6 | 8 | 9 |
| What is Dana's diet now? | 1 | 1 | 2 | 2 | 3 | 1 |
| What news did Melanie share after the race? | 2 | 2 | 1 | 1 | 1 | 1 |
| What is Dana planning for July? | 1 | 1 | 1 | 1 | 1 | 1 |
| Where will Dana stop overnight on the drive? | 3 | 3 | 1 | 1 | 1 | 1 |
| Which programming language does Dana prefer? | 2 | 3 | 5 | 2 | 5 | 2 |

| member-v1 | M0 | M1 | B0 | B1 |
|---|---:|---:|---:|---:|
| Who is Maya's husband? | 1 | 1 | 1 | 1 |
| What is the name of Maya's child? | 1 | 1 | 1 | 1 |
| What are the names of Maya's dependents? | 14 | 1 | 1 | 2 |
| Do you remember the names of my dependents? | 3 | 1 | 5 | 2 |
| What was Maya diagnosed with? | 2 | 2 | 1 | 1 |
| What did I tell you about my health? | 1 | 1 | 1 | 1 |
| Where is Maya planning a trip? | 1 | 1 | 6 | 6 |
| When is Maya's trip to Jamaica? | 1 | 1 | 2 | 2 |
| Is Maya expecting a baby? | 3 | 3 | 3 | 3 |
| What life event did Maya mention? | 4 | 4 | 5 | 5 |
| What is my deductible? | 1 | 1 | 1 | 1 |
| What did Maya's coworker Dana ask about? | 2 | 2 | 1 | 1 |

## What this says about fine-tuning an extractor

By the guide's bars, the evidence does not support "extraction is the limit". Of the 8 questions B1 misses in the
top 3, 3 fail on the read side with correct facts in the pack (the dog question routed to `involved_in`; the job
title in metadata but not in the claim's text; "Does Dana drink coffee?" sent by the catalog to the diet address),
1 is a vocabulary choice the router reads one way only (`decided` against `visits`), 3 fail on what the form can
hold (a job's start date; the member's trip and marriage facts the form refused), and 1 no arm answers (a relative
date). A specialized extractor writing this form would meet the same read-side failures, and the form itself would
refuse 69% of what a careful annotator wrote unless the mention rule is widened. The read side and the form are the
nearer limits on these probes.

What this does not show: these are three small scripted chats, one annotator's facts (about half the model's claim
count on chat-v1, a quarter on member-v1 after refusals), and the guide's levels O1 and O2 only. The member result
is mostly a measure of the form, not of extraction quality.

## Files

- `GUIDE.md`, `turns/` (from `export_turns.py`), `facts/` (the annotator's facts, notes and attribute lists).
- `check_facts.py`: the loader check the annotator ran. `oracle.py`: the mention rule, the patch, the build.
  `run_oracle.py`: one arm. `run_arms.sh`: every arm, one at a time. `serve_paths.py`: task 9d.
  `report_tables.py`: `results/tables.md`.
- `results/serve-<probe>-B1.json`: each B1 question's path, relations, ledger and top 5.
- `results/arms/<probe>/<label>/`: each arm's `report.json`, `build.json` and, for an oracle arm, `oracle.json`.
- `results/logs/`: the build logs. The packs and their reports are under the main checkout's
  `data/chat-probe-v1/<probe>/ox-*` (ignored), with an `oracle.json` beside each oracle build's `build.json`.
