# Everyday words that route questions (2026-11-12)

Task 27, prme-manager, dwamianm/prism#428. Branch `routing-cues-2026-11` on `origin/main` at `7472a46f`
(0.17.0a8). Part A measured which words route questions under `ROUTING_VERSION` 3 (report only). Part B changes
the router's word rule in `src/prme/retrieval/relations.py` and bumps `ROUTING_VERSION` to 4. This file reports
both. No model was called: every run read saved packs or saved extraction caches, with provider keys blank and
provider addresses on a closed port.

## Answer

**Nine everyday words no longer route a question: take, call, part, present, sign, lead, like, keep and run.**

- They come from relation definitions: "take", "call", "part", "present", "sign" and "lead" from `involved_in`,
  "like" from `prefers`, "keep" from `owns`, "run" from `friend_of` and `uses`. A question that holds one of them is
  read as a derived cue and as a predicate word under version 3 ("What medication do I take?" routes
  `involved_in`). Version 4 skips them both ways. The normalizer still maps them on stored claims.
- "want" stays a cue. Stopping it costs conv-50-q0004 ("Which country do Calvin and Dave want to meet in?"), a
  question routing wins on main: its evidence is packed only with the route.
- "use" stays the `uses` relation's own word. "Which editor does Alex use?" and "What do I use for X?" still route
  `uses`.
- There is no "-ion" rule. Only conv-26-q0088 ("adoption") supports one; no probe or fast-cycle question has an
  "-ion" cue, and "presentation" is already stopped through "present".
- **2 of the 6 lost questions are recovered: conv-44-q0068 and conv-49-q0006.** The other 4 route on words that
  also carry routing wins, so this rule leaves them (follow-up below).
- On the address-first packs, LoCoMo evidence in the top 25 rises 0.5 points (95% interval +0.2 to +0.8 over
  questions, +0.1 to +0.9 over conversations; 8 gained, 0 lost). Every other measure is unchanged.
- One rule set, no new setting, no flag. `memory_mode=classic` and `enable_relation_routing=false` read exactly as
  before. No stored memory is rewritten, and `DERIVATION_VERSION` does not move.

## The change

`_EVERYDAY_STEMS` in `relations.py` lists the stems `take tak call part present sign lead like lik keep run runn`
(`stem()` gives "tak" for "taking", "lik" for "liked" and "likes", "runn" for "running"). In `route_question`, a
word whose stem is in the set is skipped like a stopword: it adds no derived cue and no `_PREDICATE_WORDS` step.
Every other word of the question still routes it, so "Who is Dana's running partner?" keeps `partner_of` (it loses
the `friend_of` step "running" gave it), and "Which dog does Alex like most?" keeps `owns`.

Tests: `tests/test_relation_routing.py` replaces `test_on_call_still_asks_for_involvement` with three tests (11
everyday questions route nothing; 5 questions with an everyday word still route on their other words; "likes" and
"leads" still map on stored claims). `tests/test_belief_state.py` asserts version 4.

## Part A: what routes under version 3

| Set | Questions | Routed | Temporal (never routed) |
|---|---:|---:|---:|
| LoCoMo | 1,540 | 525 | 292 |
| LongMemEval-S (12) | 12 | 6 | 0 |
| chat-v1 | 18 | 10 | 2 |
| defects-v1 | 7 | 6 | 0 |
| member-v1 | 12 | 5 | 1 |
| fast cycle store-v1 | 17 | 13 | 2 |
| fast cycle corrections-v1 | 4 | 2 | 1 |
| blind-v1 (spent) | 86 | 59 | 12 |
| blind-v2 (spent) | 65 | 34 | 13 |
| blind paraphrases v1 (spent) | 258 | 168 | 30 |

Of the 531 routed mid tier questions, 289 have a derived cue (103 routed by it alone), 294 a predicate word (99),
106 a role word (76) and 67 a concept (47). The most common words: favorite 31, enjoy 28, family 25, trip 25,
take 23, like 21, dog 21, friends 19, visit 17, partner 17, pets 16, team 16, project 15, use 15, work 15, want 13.

**The 6 lost questions** (lost evidence in the top 25 to routing alone, main-0.17.0a6 packs, classic plus
`enable_relation_routing`), with the word that routes each:

| Question | Text | Cause word | Route |
|---|---|---|---|
| conv-26-q0088 | What is Caroline excited about in the adoption process? | adoption (cue) | `owns` |
| conv-43-q0128 | What type of meal does John often cook using a slow cooker? | using (cue, predicate word) | `uses` |
| conv-44-q0068 | Why did Audrey sign up for a workshop about bonding with pets? | sign (cue), pets (concept) | `involved_in`, `owns` |
| conv-47-q0008 | How many pets does James have? | pets (concept) | `owns` |
| conv-49-q0006 | How many roadtrips did Evan take in May 2023? | take (cue) | `involved_in` |
| conv-49-q0048 | What new diet and lifestyle change did Sam adopt over time? | adopt (cue, predicate word) | `owns` |

**The wins on the same packs** (routing alone against classic): all evidence packed on conv-30-q0018 (love),
conv-41-q0040 (children), conv-44-q0002 (girlfriend), conv-44-q0045 and conv-44-q0046 (pets) and conv-44-q0047
(adopting); evidence packed only on conv-44-q0027 (work), conv-44-q0054 (dog, like) and conv-50-q0004 (want).
"pets" and "adopt" carry wins as well as losses, so a word rule cannot stop them without losing the wins.

On the address-first packs (belief state on), routing off against version 3 shows top 25: 0 gained, 19 lost; all
packed: 10 gained, 4 lost. "take" (4 lost) and "like" (2 lost) are the largest everyday-word causes; Part A's
per-word tables are in `routing-cue-words/results/part-a-tables.md` (made with `causes.py` and `effects.py`).

## Rule variants measured (information)

Each variant ran through a scratch hook (`routing-cue-words/scripts/hooks/`, no `src` change) on the mid tier
retrieval half, against version 3. Cells are LoCoMo evidence in the top 25: gained / lost, and the change with
its question and conversation intervals.

| Variant | Main packs, routing alone | Address-first packs | Other effect |
|---|---|---|---|
| issue list with "want" | 2 / 0 (+0.1) | 9 / 0, +0.5 (+0.2 to +0.9; +0.1 to +0.9) | loses packed evidence on conv-50-q0004 |
| issue list with "want", plus "use" | 3 / 0 (+0.1) | 9 / 0, +0.5 | loses packed evidence on conv-50-q0004 |
| issue list without "want", plus "use" | 3 / 0 (+0.1) | 8 / 0, +0.5 (+0.2 to +0.8; +0.1 to +0.9) | none |
| the same plus no cue from an "-ion" noun | 4 / 0, +0.2 (+0.0 to +0.4) | 8 / 0, +0.5 | none |
| no derived cues at all | 3 / 0 (+0.2) | 7 / 0, +0.4 | loses all packed on conv-44-q0047 |
| **version 4 (issue list without "want", "use" kept)** | **2 / 0 (+0.1)** | **8 / 0, +0.5 (+0.2 to +0.8; +0.1 to +0.9)** | **none** |

prme-manager chose version 4: the simplest rule, at the cost of 2 recoverable questions (conv-43-q0128 through
"using", conv-26-q0088 through "-ion"). The 4 of 6 count was a stop guard for Part A, not a target.

## Part B bars

**Bar 1: fast tiers, version 3 (`origin/main` worktree) against version 4 (branch), saved extraction caches.**
0 extraction calls on every build. Pass.

| Tier | Version 3 | Version 4 |
|---|---|---|
| defects-v1, top 3 / top 5 | 7 / 7 of 7 | 7 / 7 of 7 |
| chat-v1, top 3 / top 5 | 15 / 16 of 18 | 15 / 16 of 18 |
| member-v1 (`--speaker`, speaker grounding on), top 3 / top 5 | 11 / 12 of 12 | 11 / 12 of 12 |
| user claims on the named owner (defects, chat, member) | 12, 35, 7 | 12, 35, 7 |
| fast cycle | exit 0, 133 unchanged | exit 0, 133 unchanged |
| corrections probe (information) | | same report; only record ids differ |

Every probe measure and every probe rank is the same on both sides. Six fast-tier questions route differently, and
none changes a measure: chat-v1 "Who is Dana's running partner?" keeps only `partner_of`; store-v1 "Does Alex like
pair programming?", "Is Alex leading the observability rewrite?", "Who is on call the week of 2026-10-26?" and
"Which gravel race did Alex sign up for?" and corrections-v1 "How does Riley take coffee?" are no longer routed.

**Bar 2: mid tier retrieval half, `--reuse-build`.** Pass: no interval entirely below zero.

| Packs, version 4 against version 3 | All evidence packed | Evidence recall | Evidence in top 25 | All evidence returned | Projected accuracy |
|---|---|---|---|---|---|
| address-first (`t27-af-v4` against `t27-af-v3`) | +0.0 (0 / 0) | +0.0 | +0.5 (+0.2 to +0.8; +0.1 to +0.9), 8 / 0 | +0.1 (+0.0 to +0.2), 1 / 0 | +0.0 |
| main-0.17.0a6, classic plus routing (`t27-main-route-v4` against `t27-main-route`) | +0.0 | +0.0 | +0.1 (+0.0 to +0.3), 2 / 0 | +0.0 | +0.0 |

LongMemEval-S (12 questions) did not move. The 8 address-first gains: conv-26-q0105, conv-41-q0119,
conv-42-q0119, conv-44-q0009, conv-44-q0050, conv-44-q0068, conv-44-q0117 and conv-49-q0105 (take, like and
sign questions). The 2 main gains are conv-44-q0068 and conv-49-q0006, the recovered pair.

Against routing off, for context: on the main packs, version 4 routing alone against classic shows all evidence
packed +0.4 (+0.1 to +0.7; +0.1 to +1.0; 6 / 0), projected accuracy +0.3 (+0.1 to +0.5; +0.0 to +0.7) and evidence
in the top 25 -0.1 (-0.3 to -0.0; 0 / 4, the 4 follow-up questions). On the address-first packs, against the
same packs with routes off, evidence in the top 25 is -0.5 (-0.8 to -0.2; 0 / 11) and all evidence packed +0.4
(-0.1 to +0.9; 10 / 4). The version 3 runs were made at `56b51d53`, whose `src` differs from `7472a46f` only in
the version line of `src/prme/__init__.py`.

**Bar 3: offline evidence gate**, version 3 against version 4, `gate-compare`, current defaults
(`memory_mode=address_first`) on both sides. Pass: every row of the first table is +0.0 points with 0 wins and 0
losses, and no question lost all its evidence among the candidates.

| Benchmark | All evidence packed | Evidence recall | All evidence among the candidates | Projected accuracy |
|---|---|---|---|---|
| LoCoMo | 84.1% to 84.1% (+0.0, 0 / 0) | 89.2% to 89.2% | 99.3% to 99.3% | 77.7% to 77.7% |
| LongMemEval-S | 94.7% to 94.7% (+0.0, 0 / 0) | 97.3% to 97.3% | 100.0% to 100.0% | 91.4% to 91.4% |

The context digest is the same on all 2,040 questions (1,540 LoCoMo, 500 LongMemEval-S). The gate replays saved
`store()` packs, which hold no extracted records, and the identical contexts show that the changed routes reach
nothing new there; the mid tier (bar 2), built through `ingest()`, is where the change shows. Output: `routing-cue-words/results/gate-compare-v3-v4.md` and `.json`.

**Bar 4: classic unchanged.** `memory_mode=classic` on a copy of the m3 pack (20,500 stated facts), the 20
questions of `graph-candidate-cut/scripts/pairs.py`, `origin/main` against the branch: ranked ids with scores 20
of 20 identical, packed ids 20 of 20, context digest 20 of 20, stored receipt digest with `source_files_sha256`
masked 20 of 20, receipt replay 20 of 20. Pass. The only source digest that differs is `relations` (the file this
change edits).

**Bar 5: tests with PostgreSQL on** (Postgres.app 16 over TCP on 127.0.0.1). Pass: 388 passed, 0 skipped, 0
failed, run one file after the other (`bar5.sh`) over `test_determinism_rebuild`, `test_packing_receipt_compatibility`,
`test_relation_routing`, `test_seed_walk`, `test_pg_seed_walk`, `test_belief_state`, `test_graph_oracle`,
`test_relationship_claims` and `test_privacy_source_routing`. A first run with 4 workers on a machine at load 18 to
25 failed 2 privacy tests with `PrivacyDenied: privacy_capability_unavailable`
(`test_AC06_incomplete_protected_projection_preserves_admitted_group` and
`test_AC06_two_selected_standard_sources_use_finite_lease[duckdb]`); both pass alone on version 3 and on version 4,
and both pass in the serial run, so they are a load timing effect, not this change.

**Bar 6: graph oracle blind run (information; blind-v1, blind-v2 and the paraphrases are spent).**
`python -m benchmarks.graph_oracle blind`, version 3 against version 4, the `product[routed]` column (the `product`
column has the same numbers on each side). Cells are top 3 / top 5 / rank 1, counts of questions.

| Set (n) | Version 3 | Version 4 |
|---|---|---|
| original (53) | 46 / 47 / 30 | 47 / 48 / 27 |
| blind (86) | 70 / 77 / 46 | 72 / 77 / 45 |
| paraphrase (230) | 189 / 197 / 117 | 193 / 199 / 117 |
| blind, not first person (79) | 64 / 71 / 40 | 66 / 71 / 39 |

Top 3 rises on every set (+1, +2, +4, +2) and top 5 does not fall. Rank 1 falls by 3 on the original set (lookups
22 to 20, corrections 2 to 1) and by 1 on the blind set, so some answers move from first to second or third place.
These sets were seen while the router was built, so they are information, not a gate. A new blind draw is
follow-up work. Reports: `routing-cue-words/results/oracle-blind-v3.md` and `oracle-blind-v4.md`.

## Follow-up

- The other 4 lost questions, each routed on a word that also routes a question routing wins:
  - conv-26-q0088, "adoption" (derived cue, `owns`);
  - conv-43-q0128, "using" (cue and predicate word, `uses`);
  - conv-47-q0008, "pets" (concept, `owns`);
  - conv-49-q0048, "adopt" (cue and predicate word, `owns`).
  A fix needs the walk's own evidence, not a word list: for example, keep the route only when the walk reaches a
  claim on the question's named person.
- A new blind draw for the graph oracle, since its sets are spent. On the spent sets, version 4 puts 3 fewer
  original answers and 1 fewer blind answer first (bar 6); a new draw would show whether that holds.

## Files

`routing-cue-words/scripts/`: `causes.py` (Part A attribution), `effects.py` (per-word effects from two reports),
`simulate.py` and `rule_sets.py` (variant predictions), `route_changes.py` (version 3 against 4 routes),
`classic_same.py` (bar 4), `arms*.sh`, `fast.sh`, `bar3.sh`, `bar5.sh` and `bar6.sh` (the runs), and `hooks/` (the
scratch hooks used for the variants). `routing-cue-words/results/`: compare tables, probe compares, fast-cycle and
corrections output, `classic-same-outputs.json`, `route-changes-v4.json`, `gate-compare-v3-v4.md` and `.json` (bar 3), and
`oracle-blind-v3.md` and `oracle-blind-v4.md` (bar 6). The scripts name the scratch paths they ran from (`/tmp/task27`,
with `/tmp/task27/wt-v3` a worktree of `origin/main` at `7472a46f`). `routing-cue-words/causes-v3.json`
is the version 3 attribution of every question.
