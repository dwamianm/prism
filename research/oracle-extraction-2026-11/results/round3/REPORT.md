# Perfect extraction test, round 3: results (2026-11-11)

**Question** (task 11, the last round the owner approved): with one change in the engine, a stated claim's text
is its turn's sentence that names its object plus, in one parenthesis, the role and the ISO date that the sentence
does not show, would hand-written facts answer the probe questions?

**Result: not supported.** tx-B1 meets the bar on member-v1 only. It misses defects-v1 by one question and chat-v1
by one question. Under the task's terms this line of work stops here.

| Probe | B1 (round 2) | B1s | tx-B1 | M1 | Bar (tx-B1 top 3) | Result |
|---|---|---|---|---|---:|---|
| defects-v1 | 5/7 / 5/7 | 6/7 / 7/7 | 6/7 / 6/7 | 7/7 / 7/7 | 7 | fail |
| chat-v1 | 12/18 / 13/18 | 14/18 / 15/18 | 16/18 / 16/18 | 15/18 / 16/18 | 17 (and M1 + 2 = 17) | fail |
| member-v1 | 9/12 / 9/12 | 12/12 / 12/12 | 12/12 / 12/12 | 11/12 / 12/12 | 11 | pass |

Each cell is top 3 / top 5. B1 (round 2) is the sentence rendered from the fields; B1s is round 2's diagnostic
arm, the turn's sentence with no parenthesis; tx-B1 is this round's engine; M1 is model extraction with the mode on.
The extra measure (`union_ranks.py`, the first k at which the top k records hold every answer string) gives the same
counts for tx-B1: 6/7, 16/18 and 12/12 in the top 3.

Against B1s, the parenthesis wins the two questions that only the fields answer (the job title, 9 to 1, and Ivy's
birth date, not found to 1) and loses one place on two questions (the cities, 5 to 6, and the start date, 4 to 6).
The cities move out of the top 5, so defects-v1 drops from 7/7 to 6/7 in the top 5. The other three misses in the
top 3 are the dog (6, the router, which this task does not fix), the cities (6) and the start date (6).

## Setup

- Worktree `/Users/dwamianm/Sites/prism-fields`, branch `fact-fields`, from 4a5f71ff. Facts of commit fb1f103d,
  unchanged. Engine change in `src/prme/ingestion/assertions.py` (`stated_sentence`, `stated_text`, `fact_text`,
  `is_rendered`) and `src/prme/ingestion/pipeline.py`. The sentence picker is the one B1s used:
  `grounding.claim_sentences(content, obj, obj)`, the shortest run of the turn's sentences that names the object, or
  the whole turn when none does. It now lives only in the engine; `oracle.py` lost its copy and `run_oracle.py` lost
  `--sentence-text`.
- The parenthesis holds the role when the sentence does not mention it ("as analytics manager" for `works_at`, the
  bare role otherwise) and the time when the sentence does not hold the ISO string ("since" for current, "on" for
  done, "from" for planned, past and superseded). A claim stored this way records `grounding_method`
  `stated_sentence_v1`. When the content was rendered from the assertions (`render_content`, no caller content),
  the claim keeps the rendered sentence of round 2 (`stated_fields_v1`). No probe turn is rendered, so every claim
  of tx-B1 is `stated_sentence_v1`.
- `ASSERTION_VERSION` stays 2. `DERIVATION_VERSION` is unchanged: `tests/test_derivation_version.py` passes.
- Build: `run_oracle.py <probe> tx-B1 --levels O1,O2 --set 'memory_mode="address_first"'`, member-v1 with `--speaker
  --set enable_speaker_grounding=true`. 0 model calls on every build; the check agreed with every build
  (`check_disagrees_with_the_build` is empty). Facts loaded / written: 31 / 31, 57 / 57, 23 / 23, as in round 2.
- Stored: defects-v1 31 claims, chat-v1 55, member-v1 23 (17, 28 and 8 user claims on the named owner; 25, 28 and
  6 entities), the same as B1 and B1s.

## Per question, every row that differs

Answer rank in each arm, then the path that served tx-B1 (from its receipts).

**defects-v1**

| Question | B1 (round 2) | B1s | tx-B1 | M1 | tx-B1 path |
|---|---:|---:|---:|---:|---|
| Which cities will Alex and Jordan visit in Japan? | 25 | 5 | 6 | 2 | routed: visits |
| Which languages does Alex write at work? | 18 | 1 | 1 | 2 | catalog |
| How does Alex prefer to travel in Japan? | 2 | 1 | 1 | 3 | routed: prefers, visits |

**chat-v1**

| Question | B1 (round 2) | B1s | tx-B1 | M1 | tx-B1 path |
|---|---:|---:|---:|---:|---|
| Where does Dana work now? | 1 | 1 | 1 | 2 | routed: works_at |
| What is Dana's job title now? | 1 | 9 | 1 | 12 | routed: works_at |
| When did Dana start the new job? | 22 | 4 | 6 | 2 | catalog |
| Who was Dana's manager at Northwind? | 1 | 1 | 1 | 3 | routed: manager_of (manager) |
| What does Dana's partner do for work? | 2 | 1 | 1 | 2 | routed: partner_of, works_at |
| What kind of dog does Dana have, and what is it called? | 21 | 6 | 6 | 3 | routed: involved_in |
| What is the name of Dana's niece? | 7 | 1 | 1 | 1 | catalog |
| When was Ivy born? | 1 | none | 1 | none | catalog |
| Who is Dana's running partner? | 1 | 1 | 1 | 2 | routed: friend_of, partner_of |
| What was Dana's half marathon finishing time? | 10 | 2 | 2 | 1 | catalog |
| Does Dana drink coffee? | 1 | 1 | 1 | 5 | catalog |
| What news did Melanie share after the race? | 4 | 1 | 1 | 2 | catalog |
| What is Dana planning for July? | 22 | 1 | 1 | 1 | catalog |
| Where will Dana stop overnight on the drive? | 3 | 1 | 1 | 3 | catalog |
| Which programming language does Dana prefer? | 2 | 2 | 2 | 3 | routed: prefers |

**member-v1**

| Question | B1 (round 2) | B1s | tx-B1 | M1 | tx-B1 path |
|---|---:|---:|---:|---:|---|
| What are the names of Maya's dependents? | 7 | 1 | 1 | 1 | routed: kin_of (child), partner_of |
| Do you remember the names of my dependents? | 8 | 1 | 1 | 1 | routed: kin_of (child), partner_of |
| What was Maya diagnosed with? | 1 | 1 | 1 | 2 | catalog |
| What did I tell you about my health? | 3 | 1 | 1 | 1 | catalog |
| When is Maya's trip to Jamaica? | 3 | 1 | 1 | 1 | catalog |
| Is Maya expecting a baby? | 1 | 1 | 1 | 3 | catalog |
| What life event did Maya mention? | 8 | 3 | 3 | 4 | catalog |
| What did Maya's coworker Dana ask about? | 1 | 1 | 1 | 2 | catalog |

tx-B1 and B1s differ on four questions only: the job title and Ivy's birth (won), the cities and the start date
(each one place lower). The paths are the same as B1s on every question.

## The three misses

`first-ranks.txt` gives the first record that holds each answer string, in B1s and tx-B1.

- **The dog (6, router).** "Biscuit is a great road trip dog, he's our 3 year old golden." ranks 6, as in B1s. The
  question is routed to `involved_in`, and Dana's `involved_in` claims (the Portland trip, the race, the long run,
  the interview) take ranks 1 to 5. The cause is the cue bug the owner found ("call" taken from "on call"); this
  task does not fix it.
- **The cities (6).** The facts say `decided` and the router asks `visits` (round 2). "We chose Tokyo, Kyoto,
  Kanazawa, and Osaka." ranks 6. Above it: "That is cherry blossom season.", two trip claims with the turn's
  sentence, the trip claim with role planner, "My partner Jordan and I are planning a trip to Japan in late March
  next year. (planner)", at 4, and the first turn's note at 5. In B1s the planner claim had the plain sentence and
  did not take a place of its own in the top 5, so the cities sentence was at 5.
- **The start date (6).** No route serves "start", so the catalog path ranks by text. The turn's note "I got the
  offer from Brightpath and I accepted it! I start as analytics manager on May 4." ranks 6 (B1s 4). The claim "I got
  the offer from Brightpath and I accepted it! (as analytics manager; from 2026-05-04)" holds the ISO date and ranks
  10. Above them: the intro note, the note "Two weeks in at Brightpath ...", and three claims of one sentence, "Training
  update: Mel and I did a 10 mile long run on Saturday.", each with its own parenthesis ("(runner; on 2026-04-11)",
  "(since 2026-04-11)", "(running partner; on 2026-04-11)").

So both new losses come from the same thing: a parenthesis makes a claim's text distinct from its sentence's other
claims, and the distinct copies each take a place in the ranking.

## Claim merge and fold_repeated_text

- **Claim merge.** The merge key (`claim_key`) reads node type, epistemic and source type, predicate, object,
  object entity, polarity and `resolved_date`. It never reads the text, so claims that share a sentence stay separate
  claims. tx-B1 stores 31, 55 and 23 claims (the same counts as B1 and B1s) with 14, 40 and 14 distinct texts (B1s:
  12, 35, 14; B1: one text per claim). The organizer's duplicate merge needs identical metadata
  (`compatible_provenance`), so a shared text does not let it merge different claims either. Test:
  `test_claims_of_one_sentence_share_its_text_and_stay_separate_claims`.
- **fold_repeated_text.** A record folds into another record of its source only when its text lies inside the
  other record's text and its tags, time and speaker match. A claim whose parenthesis adds a role or a date is not
  inside its turn's text, so fold never drops it: the claim and its turn's note are both packed. Test:
  `test_the_reader_keeps_a_stated_claim_whose_parenthesis_its_turn_lacks` (a reader context with fold on holds "I
  finished the Cherry Creek Half on Sunday in 1:58:30! (on 2026-05-17)"). In the start date context of tx-B1 both
  "I got the offer from Brightpath and I accepted it! (as analytics manager; from 2026-05-04)" and its turn's note
  are packed. A claim with the plain sentence and the turn's time folds into the turn's note, as before. A claim
  with a stated `time` has its own event time, so it does not fold into the turn even without a parenthesis.
  Claims with 17 parentheses in chat-v1 and 5 in defects-v1 are stored (none in member-v1, where every sentence
  shows its role and no fact has a time the sentence lacks).
- **collapse_identical_text** (on in address_first). Claims of one sentence with no parenthesis show once in the
  reader context with "[said N times]". Claims of one sentence with different parentheses do not collapse, which is
  what puts three copies of the long run sentence above the start date.

## Checks

- `tests/test_structured_assertions.py`: 73 passed, 16 skipped (PostgreSQL). With the targeted suites of RESUME.md,
  `test_fold_repeated_text.py` and `test_claim_statement_fold.py`: `715 passed, 259 skipped in 131.43s`.
- `tests/test_derivation_version.py` from outside the checkout: `2 passed in 1.60s`.
- Contract scorecard, visible set, `--stated`:
  - classic: `73 passed, 87 xfailed` (round 2: 80 passed, 80 xfailed). The stated churn misses are now the 24 ids of
    2026-11-10 again, in three runs that agreed (one full run and two runs of the stated churn tests): 8 ids are
    `XFAIL` again (stated-10min-2moves-yoga-mat, stated-10min-2moves-dog-leash, stated-10min-5moves-red-mug,
    stated-3days-2moves-tape-measure, stated-3days-5moves-car-keys, stated-3days-5moves-guitar-tuner,
    stated-3days-12moves-tv-remote, stated-3days-12moves-wool-scarf) and stated-10min-5moves-wool-scarf passes. On
    these ids the older placement's claim ranks first and the newest second (for example the yoga mat: "The yoga
    mat is on the dining table." 0.9163, "The yoga mat is on the garage workbench." 0.9022). The classic mode has no
    ledger for them. `tests/contract/expected.py` and `tests/contract/README.md` were updated.
  - address_first: `73 passed, 44 xpassed, 43 xfailed, 0 failed` (round 2: 80, 37, 43, 0). All 36 stated ids pass,
    as in round 2; only the expected list moved.
- Hidden set, totals only: classic `82 passed, 80 xfailed` (round 2: 7 failed, 79 passed, 76 xfailed);
  address_first `82 passed, 42 xfailed, 38 xpassed` (as before). The 7 failures that round 2 left open are gone in
  the totals. This session did not read the hidden set's per-id results.
- Full unit suite: 1 failed (the worktree test), 7471 passed; see "Full suite" below.

## Full suite

Providers blocked, `tests` without the contract directories: `1 failed, 7471 passed, 1646 skipped, 2 xfailed in
2230.09s (0:37:10)`. The one failure is `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`,
the same as in round 2: it expects a plain clone, and in a git worktree `main_checkout()` gives the main checkout.
This change does not touch that test or `benchmarks/checkout.py`. The log is `logs/full-suite.log`.

## What this says

- The sentence plus the fields does what it was meant to do on the questions the fields answer: the job title and
  Ivy's birth date are at 1, and every answer that B1s had from the turn's words stays in place except two that move
  one place down. tx-B1 is the best oracle arm so far on chat-v1 (16, against 14 for B1s and 15 for M1) and on
  member-v1 (12, against 11 for M1).
- It still misses two bars by one question each. One miss is the router (the dog), one is the router and the
  facts' `decided` (the cities), and one is a question no route serves (the start date). Two of the three also lost
  a place to a sibling claim whose parenthesis made its text distinct.
- Limits: three small scripted chats, one annotator's facts, tx-B1 built once.

## Files

- `results/round3/tables.md`: the tables above, from `report_tables.py round3`.
- `results/round3/serve-<probe>-tx-B1.json`: each question's path, relations, ledger and top 5 (`serve_paths.py`).
- `results/round3/union-<probe>.json`: probe rank and union rank for fx-M1, ox-B1, fx-B1, fx-B1s and tx-B1.
- `results/round3/first-ranks.txt`: the first record holding each answer string for the three misses, in B1s and
  tx-B1.
- `results/round3/arms/<probe>/tx-B1/`: `report.json`, `build.json`, `oracle.json`; `results/round3/logs/`: build,
  serve, union and contract logs.
- The packs are under the main checkout's `data/chat-probe-v1/<probe>/tx-B1` (ignored).
