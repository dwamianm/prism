# Perfect extraction test, round 2: results (2026-11-11)

**Question** (GUIDE.md, "Round 2"): with the three engine changes of branch `fact-fields` (a wider mention rule, a
`time` on an assertion, a claim text rendered from the fields), would hand-written facts answer the probe
questions?

**Result: not supported.** Arm B1 (facts of level O2, `memory_mode="address_first"`) meets none of the three bars,
and it scores below round 1 on every probe:

| Probe | Bar (B1, top 3) | Round 2 B1 | Round 1 B1 | M1 (model, mode on) | B1s (diagnostic) |
|---|---:|---:|---:|---:|---:|
| defects-v1 | 7 of 7 | 5 of 7 | 6 of 7 | 7 of 7 | 6 of 7 |
| chat-v1 | 17 of 18, and at least M1 + 2 | 12 of 18 | 13 of 18 | 15 of 18 | 14 of 18 |
| member-v1 | 11 of 12 | 9 of 12 | 10 of 12 | 11 of 12 | 12 of 12 |

The diagnostic arm B1s is B1 built from the same facts, under the same rule, with each stated claim's text set to
its turn's sentence instead of the sentence rendered from its fields. It separates change 3 (the rendered text)
from changes 1 and 2 (the facts the new rule and `time` let in). With the turn's sentence, the same facts score 6,
14 and 12: at or above round 1 on every probe, and member-v1 meets its bar. So the new facts help and the rendered
text costs more than it gives on these probes: it wins 2 questions (the job title, the niece's birth date) and
loses 8. The bars read B1 only; B1s is not an arm of the guide, so the result stays "not supported".

Of the 11 questions B1 misses in the top 3, 9 are form: the rendered text says one fact per claim, so a probe that
needs several answer strings in one record misses them (4), or the rendered text drops the turn's own words and
short claims that all begin with the owner's name crowd the catalog path (5). 2 are read side and miss in both B1
and B1s: the router sends the dog question to `involved_in`, and the cities question to `visits` while the facts say
`decided`. The start date also misses in both arms: in B1 it is crowded out, and in B1s no route serves it and the
sentence that holds the date is only in the turn's note.

## Setup

- Worktree `/Users/dwamianm/Sites/prism-fields`, branch `fact-fields`. Engine at checkpoint 7c91f1d4 (the three
  changes; `docs/RELATIONS.md` and `CHANGELOG.md` describe them). Facts of commit fb1f103d (`facts/CHANGES-round2.md`
  lists every change from round 1), unchanged since.
- The build is round 1's (`oracle.py`, `run_oracle.py`, `run_arms.sh`), with round 1's run-time patch of the mention
  check removed: the engine's own rule admits each assertion. `check_facts.py` and the build ask the engine the same
  question (`prepare_assertions` turn by turn, and `engine._prepare_assertions` against the pack), and each oracle
  build records that the two agreed (`check_disagrees_with_the_build` is empty on all 14 oracle builds, the B1
  rebuilds and B1s included).
- No model call anywhere. Providers are blocked by environment, the extraction stub answers every turn with no
  accepted assertion, and each oracle build checks that the probe's call counter is 0. M0 and M1 replay the
  `af-off-1111` extraction cache with `PRME_BENCH_CACHE_ONLY=1` (0 calls).
- Member probe: every arm with `--speaker` and `enable_speaker_grounding=true`.
- Only chat-v1 has O2 facts (4), so on defects-v1 and member-v1 the A arms are the B builds; the table marks them
  "(= B)".
- B1s: `run_oracle.py <probe> fx-B1s --levels O1,O2 --set 'memory_mode="address_first"' --sentence-text`, member-v1
  with the speaker options. For this build only, `sentence_text` in `oracle.py` replaces the pipeline's
  `stated_fact_text`: a claim's text is the shortest run of its turn's sentences that names its object, or the whole
  turn when none does. Everything else (facts, admission, `time`, ledger, routing, packing) is B1's.

## Top 3 and top 5 per arm

| Probe | M0 | M1 | A0 | A1 | B0 | B1 | Bar (B1 top 3) | Result |
|---|---|---|---|---|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 7/7 / 7/7 | 4/7 / 5/7 (= B) | 5/7 / 5/7 (= B) | 4/7 / 5/7 | 5/7 / 5/7 | 7 | fail |
| chat-v1 | 14/18 / 15/18 | 15/18 / 16/18 | 8/18 / 11/18 | 12/18 / 13/18 | 7/18 / 11/18 | 12/18 / 13/18 | 17 | fail |
| member-v1 | 10/12 / 11/12 | 11/12 / 12/12 | 8/12 / 9/12 (= B) | 9/12 / 9/12 (= B) | 8/12 / 9/12 | 9/12 / 9/12 | 11 | fail |

Each cell is top 3 / top 5. B1s: defects 6/7 / 7/7, chat 14/18 / 15/18, member 12/12 / 12/12.

Extra measure, not the guide's bar (`union_ranks.py`): the first k at which the top k non-entity records together
hold every answer string. B1: 6/7, 12/18, 11/12 in the top 3. B1s: 6/7, 14/18, 12/12. So even when the answer may be
spread over several records, the turn's sentence still does better on chat-v1 (14 against 12): the loss there is not
only the probe's one-record rule.

## What each build stored

| Probe | Arm | Active claims | User claims on the named owner | Entities | Facts loaded / written | Model calls |
|---|---|---:|---:|---:|---:|---:|
| defects-v1 | M0, M1 | 24 | 12 | 25 | model | 0 |
| defects-v1 | B0, B1, B1s | 31 | 17 | 25 | 31 / 31 | 0 |
| chat-v1 | M0, M1 | 101 | 35 | 58 | model | 0 |
| chat-v1 | A0, A1 | 52 | 25 | 27 | 53 / 53 | 0 |
| chat-v1 | B0, B1, B1s | 55 | 28 | 28 | 57 / 57 | 0 |
| member-v1 | M0, M1 | 32 | 7 | 36 | model | 0 |
| member-v1 | B0, B1, B1s | 23 | 8 | 6 | 23 / 23 | 0 |

Round 1 stored 9 claims on member-v1 (15 of 24 facts refused) and 52 on chat-v1. B1 and B1s hold the same claims;
only their text differs. In B1 every claim has its own text (31, 55 and 23 distinct texts). In B1s claims of one
sentence share it (12, 35 and 14 distinct texts), and 5, 5 and 2 claims carry their whole turn.

## Loader: accepted by each part of the mention rule

| Probe | Levels | Written | Accepted | Text only | Speaker | Memory | Closure | Refused |
|---|---|---:|---:|---:|---:|---:|---:|---|
| defects-v1 | O1+O2 | 31 | 31 | 9 | 0 | 22 | 0 | none |
| chat-v1 | O1 | 53 | 53 | 25 | 0 | 28 | 0 | none |
| chat-v1 | O1+O2 | 57 | 57 | 25 | 0 | 32 | 1 | none |
| member-v1 | O1+O2 | 23 | 23 | 0 | 8 | 15 | 0 | none |

All 111 facts written load (round 1: 35 of 113 passed the engine's rule, 97 with the run-time patch). 34 need only
the turn's text; 77 need the speaker (8), memory (69) or a closure (1); one assertion can need more than one part.
No assertion needed "you" or "your" to mean the owner: the 13 assistant-turn facts of member-v1 name Maya Okafor,
and memory admits them because her user turns created that entity through the speaker rule.

## B1 rebuilt from scratch

A second B1 build (`fx-B1r`) repeats every answer rank except one chat-v1 question, the half marathon time, at 10
and then 8 (tied claims are ordered by node id; outside the top 5 both times). Several packed contexts differ
(`tables.md` lists them). B1s was built once.

## B1 against B1s per question

Answer rank, then the path that served it (from the receipts; `routed` names the relations walked). "Union" is the
extra measure above. M1 and round 1 B1 are for reference.

**defects-v1**

| Question | M1 | Round 1 B1 | B1 | B1 union | B1s | B1s union |
|---|---:|---:|---|---:|---|---:|
| Which cities will Alex and Jordan visit in Japan? | 2 | 5 | 25 (routed: visits) | 12 | 5 (routed: visits) | 5 |
| What coding language is Alex learning? | 3 | 3 | 3 (routed: uses) | 3 | 3 (routed: uses) | 3 |
| Which languages does Alex write at work? | 2 | 1 | 18 (catalog) | 2 | 1 (catalog) | 1 |
| How does Alex prefer to travel in Japan? | 3 | 2 | 2 (routed: prefers, visits) | 2 | 1 (routed: prefers, visits) | 1 |
| Who is allergic to shellfish? | 1 | 1 | 1 (routed: allergic_to) | 1 | 1 (routed: allergic_to) | 1 |
| What did the team choose for the shipment table? | 1 | 1 | 1 (routed: member_of, part_of) | 1 | 1 (routed: member_of, part_of) | 1 |
| Who should review the runbook? | 1 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |

**chat-v1**

| Question | M1 | Round 1 B1 | B1 | B1 union | B1s | B1s union |
|---|---:|---:|---|---:|---|---:|
| Where does Dana work now? | 2 | 1 | 1 (routed: works_at) | 1 | 1 (routed: works_at) | 1 |
| What is Dana's job title now? | 12 | 7 | 1 (routed: works_at) | 1 | 9 (routed: works_at) | 9 |
| When did Dana start the new job? | 2 | 10 | 22 (catalog) | 22 | 4 (catalog) | 4 |
| Who was Dana's manager at Northwind? | 3 | 1 | 1 (routed: manager_of (manager)) | 1 | 1 (routed: manager_of (manager)) | 1 |
| What does Dana's partner do for work? | 2 | 2 | 2 (routed: partner_of, works_at) | 2 | 1 (routed: partner_of, works_at) | 1 |
| What is Sam allergic to? | 1 | 1 | 1 (routed: allergic_to) | 1 | 1 (routed: allergic_to) | 1 |
| What kind of dog does Dana have, and what is it called? | 3 | 9 | 21 (routed: involved_in) | 21 | 6 (routed: involved_in) | 6 |
| What is the name of Dana's niece? | 1 | 2 | 7 (catalog) | 7 | 1 (catalog) | 1 |
| When was Ivy born? | none | none | 1 (catalog) | 1 | none (catalog) | none |
| Where does Dana's sister live? | 2 | 2 | 2 (routed: kin_of (sister), lives_in) | 2 | 2 (routed: kin_of (sister), lives_in) | 2 |
| Who is Dana's running partner? | 2 | 1 | 1 (routed: friend_of, partner_of) | 1 | 1 (routed: friend_of, partner_of) | 1 |
| What was Dana's half marathon finishing time? | 1 | 3 | 10 (catalog) | 10 | 2 (catalog) | 2 |
| Does Dana drink coffee? | 5 | 9 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What is Dana's diet now? | 1 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What news did Melanie share after the race? | 2 | 1 | 4 (catalog) | 4 | 1 (catalog) | 1 |
| What is Dana planning for July? | 1 | 1 | 22 (catalog) | 14 | 1 (catalog) | 1 |
| Where will Dana stop overnight on the drive? | 3 | 1 | 3 (catalog) | 3 | 1 (catalog) | 1 |
| Which programming language does Dana prefer? | 3 | 2 | 2 (routed: prefers) | 2 | 2 (routed: prefers) | 2 |

**member-v1**

| Question | M1 | Round 1 B1 | B1 | B1 union | B1s | B1s union |
|---|---:|---:|---|---:|---|---:|
| Who is Maya's husband? | 1 | 1 | 1 (routed: partner_of) | 1 | 1 (routed: partner_of) | 1 |
| What is the name of Maya's child? | 1 | 1 | 1 (routed: kin_of (child)) | 1 | 1 (routed: kin_of (child)) | 1 |
| What are the names of Maya's dependents? | 1 | 2 | 7 (routed: kin_of (child), partner_of) | 2 | 1 (routed: kin_of (child), partner_of) | 1 |
| Do you remember the names of my dependents? | 1 | 2 | 8 (routed: kin_of (child), partner_of) | 2 | 1 (routed: kin_of (child), partner_of) | 1 |
| What was Maya diagnosed with? | 2 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What did I tell you about my health? | 1 | 1 | 3 (text ranking only) | 3 | 1 (catalog) | 1 |
| Where is Maya planning a trip? | 1 | 6 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| When is Maya's trip to Jamaica? | 1 | 2 | 3 (catalog) | 3 | 1 (catalog) | 1 |
| Is Maya expecting a baby? | 3 | 3 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What life event did Maya mention? | 4 | 5 | 8 (catalog) | 8 | 3 (catalog) | 3 |
| What is my deductible? | 1 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |
| What did Maya's coworker Dana ask about? | 2 | 1 | 1 (catalog) | 1 | 1 (catalog) | 1 |

`kin_of (child)` stands for the child role and its synonyms. The paths are the same in B1 and B1s on every question
but one ("What did I tell you about my health?": text ranking only in B1, catalog in B1s), so the text changes the
ranking, not the route.

## Why each miss misses: read side or form

`first-ranks.txt` gives, for each miss, the rank and text of the first record that holds each answer string.

**Read side (misses in both B1 and B1s).**

- *The dog* (B1 21, B1s 6). The facts are right (`Dana owns Biscuit`, `Biscuit has breed golden`), but the router
  sends the question to `involved_in`, and the walk reaches Dana's own `involved_in` claims (the Portland trip, the
  race, the long run, the interview). In B1 "Dana owns Biscuit." is at 16 and no record holds "golden" before 21
  (the first turn's note); in B1s the sentence "Biscuit is a great road trip dog, he's our 3 year old golden." is
  at 6. The same route as round 1 (rank 9).
- *The cities* (B1 25, B1s 5). The annotator wrote "We chose Tokyo, Kyoto, Kanazawa, and Osaka" as `decided` (status
  planned); the router asks `visits`, and the walk reaches the trip claims, which rank above. The rendered text makes
  it worse: one city per claim ("Jordan is going to decide on Tokyo."), first at 7, 8, 11 and 12, so the union rank
  is 12 and no single record holds all four. B1s ranks the turn's sentence at 5, as round 1 did.
- *The start date in B1s* (4). The claim carries `time` 2026-05-04, but no route serves "start", so the catalog
  path ranks by text. With the turn's sentence, the date is only in the turn's note at 4 (the sentence picker chose
  "I got the offer from Brightpath and I accepted it!", the sentence that names the object). In B1 the rendered
  claim "Dana is going to work at Brightpath as analytics manager (from 2026-05-04)." holds the date but ranks 22.

**Form: one fact per claim** (the probe needs every answer string in one record; union rank in brackets).

- *Which languages does Alex write at work?* B1 18 [2]: "Alex Rivera uses Go (writing)." at 1, "Alex Rivera uses
  TypeScript (writing)." at 2. B1s 1: "Yes, I write Go services and TypeScript tooling."
- *What is Dana planning for July?* B1 22 [14]: "Portland trip's trip month is going to be July." at 1; "Ivy" first
  at 14 ("Dana is related to Ivy (aunt)."). No fact says the trip is to meet Ivy: the vocabulary has no purpose.
  B1s 1: "I'm planning the Portland trip for July to finally meet Ivy."
- *Maya's dependents*, both phrasings. B1 7 and 8 [2 and 2]: "Maya Okafor is related to Nico (child)." at 1 and
  "Maya Okafor is the partner of Theo." at 2. B1s 1: "My husband is Theo and my child is Nico."
- *The cities* (above) also splits this way.

**Form: the rendered text drops the turn's words, and short claims crowd the catalog path.** On a catalog question
the top 5 of B1 are short claims that all begin with "Dana" (or "Maya Okafor") and match the question's name.

- *The niece's name.* B1 7: "Dana is related to Ivy (aunt)." The claim says "aunt" from Dana's side; the question
  says "niece". Above it: "Dana is the partner of Sam.", "Dana is a friend of Melanie.", "Priya is the manager of Dana
  (manager).", "Dana's diet is pescatarian.". B1s 1: "A girl named Ivy."
- *The half marathon time.* B1 10: "Dana's race result is 1:58:30 (since 2026-05-17)." Above it: five "Dana was
  involved in ..." claims. B1s 2: "I finished the Cherry Creek Half on Sunday in 1:58:30!".
- *Melanie's news.* B1 4: no claim says "engaged" (the fact is `Melanie partner_of Tom`, rendered "Melanie is the
  partner of Tom."); the turn's note holds it at 4. B1s 1: the claim's sentence "she told me after the race that she
  got engaged to her boyfriend Tom last weekend!".
- *The life event.* B1 8: "Maya Okafor is going to be involved in getting married." Above it: her health state,
  coverage and child claims. B1s 3: the turn's note "I am getting married. ...".
- *The start date in B1* (22, above): the claim with the date is crowded out by "Priya is the manager of Dana
  (manager).", "Dana is the partner of Sam.", "Dana uses Tableau.", "Dana lives in Denver.", "Dana used to prefer
  coffee.".

**What the rendered text wins** (misses in B1s, answered in B1).

- *The job title* (B1 1, B1s 9). The title is the claim's `role`; only the rendered text says it ("Dana works at
  Brightpath as analytics manager."). The sentence picked for B1s ("Two weeks in at Brightpath and I already like my
  team more than the one at Northwind.") does not.
- *When was Ivy born?* (B1 1, B1s none). The turn says "last Thursday"; `time` gives the claim 2026-03-12, and only
  the rendered text shows it ("Rachel is related to Ivy (mother) (since 2026-03-12)."). No arm of round 1 answered it.

## What this says

- Changes 1 and 2 work as meant. Every fact written loads (111 of 111, against 35 of 113 under the old rule), the
  check and the build agree on every assertion, and with the turn's sentence as text the facts answer 6, 14 and 12
  questions in the top 3, at or above round 1 (6, 13, 10) and, on member-v1, above model extraction (11).
- Change 3, as built, costs net answers on these probes: 1 on defects-v1, 2 on chat-v1, 3 on member-v1. It is the
  only way the claim's typed fields (a role, a resolved date) reach the text, and those 2 wins are real. Its
  losses come from what a one-fact sentence leaves out: the other items of a list, the turn's own words, and enough
  distinct words to stand out from many short claims about the same person on the catalog path. It loses 8
  questions in the top 3 and wins 2.
- Untested, for a follow-up and not this ticket: a claim text that keeps the turn's sentence and adds only what the
  fields say and the sentence does not (the title, the ISO date) might keep both wins and the 8 answers.
- The read side still has 2 router misses (the dog via `involved_in`, the cities via `visits` against `decided`) and
  1 question with no route (the start date). With the turn's sentence they are B1s's only misses besides the 2
  questions change 3 wins.
- Limits: three small scripted chats; one annotator's facts; B1s built once; the probe's one-record rule favors
  sentence text (the union measure, which does not, still favors it on chat-v1: 14 against 12).

## Engine checks on this tree

- `tests/test_structured_assertions.py`: 66 passed, 14 skipped (PostgreSQL). Targeted suites: 658 passed, 252
  skipped. Model extraction unchanged: `tests/test_derivation_version.py` from outside the checkout, 2 passed; M0 and
  M1 give round 1's probe rows.
- Full unit suite (`tests` without the contract directories), providers blocked: `1 failed, 7464 passed, 1644
  skipped, 2 xfailed in 2278.22s (0:37:58)`. The failure is
  `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`: it expects a plain clone, and
  in a git worktree `main_checkout()` gives the main checkout (`/Users/dwamianm/Sites/prism`), not the worktree.
  This branch does not change that test or `benchmarks/checkout.py`, and the test fails the same way in the
  `prism-oracle` worktree, whose `src/` is unchanged. So it comes from where the suite ran, not from task 10.
- Contract scorecard, visible set, `--stated`: classic 80 passed, 80 xfailed; address_first 80 passed, 37 xpassed,
  43 xfailed, 0 failed. Hidden set, totals only: address_first 82 passed, 42 xfailed, 38 xpassed (as before);
  classic 7 failed, 79 passed, 76 xfailed. **Open:** the 7 are strict results in its stated churn group, and its
  expected list needs deciding again by someone allowed to read the hidden set's per-id results.

## Files

- `results/round2/tables.md`: every table above, from `report_tables.py fx results/round2`, including the B1 top 5
  of each miss.
- `results/round2/serve-<probe>-B1.json` and `serve-<probe>-B1s.json`: each question's path, relations, ledger and
  top 5 (`serve_paths.py`).
- `results/round2/union-<probe>.json`: probe rank and union rank for fx-M0, fx-M1, ox-B1, fx-B0, fx-B1 and fx-B1s
  (`union_ranks.py`).
- `results/round2/first-ranks.txt`: the first record holding each answer string, for every miss, in B1 and B1s
  (`first_ranks.py`).
- `results/round2/arms/<probe>/<label>/`: each arm's `report.json`, `build.json` and, for an oracle arm,
  `oracle.json`; `results/round2/logs/`: build logs.
- The packs are under the main checkout's `data/chat-probe-v1/<probe>/fx-*` (ignored).
