# Router fix (task 12): results (2026-11-11)

**Question.** Two general rules in the relation router (`route_question`, `src/prme/retrieval/relations.py`), which
runs only under `memory_mode="address_first"`:

1. Words that ask for a name ("name", "names", "named", "called") say nothing about the relation, so the router skips
   them. "call" stays a cue, so "Who is on call?" still routes to `involved_in`.
2. A word that the predicate normalizer maps to a relation (`_PREDICATE_WORDS`) also routes to that relation, with
   the word's polarity and role, so a word that stores a relation also asks for it.

The RELATIONS definitions are unchanged, so the extraction prompt is unchanged. `ROUTING_VERSION` is 3.

**Result: passes.** No probe arm is worse in the top 3 or the top 5 (chat-v1 tx-B1 goes from 16/18 to 17/18 in
both), every row of the offline evidence gate is a tie, and the graph oracle blind sets are better on every set's
total. Four multi-hop questions on the blind sets leave the top 3, all from rule 2 (below).

## Setup

- Worktree `/Users/dwamianm/Sites/prism-fields`, branch `fact-fields`. Before: ee0130a9 (round 3). After: the code
  commit d8fcfa8e (the gate's after side ran on it, clean tree).
- Rule 2 lives in `route_question`, not in `DERIVED_CUES`: the derived cues must equal the graph oracle's
  `derive_cues()` (`test_the_derived_cues_follow_the_graph_oracle_rule`), which reads only the definitions.
- Every build and measurement made 0 model calls (providers blocked, `PRME_BENCH_CACHE_ONLY=1`).
- `run_router_fix.sh rf results/router-fix` rebuilt M1 (the `af-off-1111` extraction cache, mode on; 14, 54 and 30
  cache hits, 0 calls) and tx-B1 (`run_oracle.py --levels O1,O2`, the round 3 facts) on the three probes, then the
  serve paths and `union_ranks.py`. member-v1 with `--speaker --set enable_speaker_grounding=true`.
- `routes.py` dumps the route of 495 questions (probe questions, gold questions, blind-v1 with its paraphrases,
  blind-v2) with no retrieval. `rule_alone.py routes` says which rule moves each route.

## Probes (top 3 / top 5)

| Probe | M1 before | tx-B1 before | M1 after | tx-B1 after |
|---|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 6/7 / 6/7 | 7/7 / 7/7 | 6/7 / 6/7 |
| chat-v1 | 15/18 / 16/18 | 16/18 / 16/18 | 15/18 / 16/18 | 17/18 / 17/18 |
| member-v1 | 11/12 / 12/12 | 12/12 / 12/12 | 11/12 / 12/12 | 12/12 / 12/12 |

No arm is worse in the top 3 or the top 5. Before is round 3 (`results/round3/union-<probe>.json`). The round 3
builds measured again with the new router give the same counts as the rebuilt arms, so no difference is rebuild
noise (`tables.md`, last two columns).

Every probe question whose route or rank changed (answer rank):

| Probe | Question | Route before | Route after | M1 before | tx-B1 before | M1 after | tx-B1 after |
|---|---|---|---|---:|---:|---:|---:|
| defects-v1 | What did the team choose for the shipment table? | member_of, part_of | decided, member_of, part_of | 1 | 1 | 1 | 1 |
| chat-v1 | What kind of dog does Dana have, and what is it called? | involved_in | owns | 3 | 6 | 1 | 1 |
| member-v1 | Where is Maya planning a trip? | not routed | visits | 1 | 1 | 1 | 1 |

The dog question moves from rank 6 to 1 in tx-B1 and from 3 to 1 in M1, now served by the `owns` walk in both. No
other probe rank moved.

## Routes (495 questions, no retrieval)

Routed before 295, after 318; 77 change (`routes-changed.txt`). By rule (`routes-by-rule.txt`): rule 1 alone moves
10, rule 2 alone moves 63, and 4 need both (the three dog questions and draw2 multi-hop 5). Rule 2's changes are
mostly "trip" questions now routed to `visits`, "employer" adding `works_at`, "choose" and "reject" adding
`decided` (negative for "reject"), and "dog" routing to `owns`. Rule 1's changes are "called" questions that lost
`involved_in`.

## Graph oracle blind run (product[routed], top 3 / top 5 / top 1)

`python -m benchmarks.graph_oracle blind`, `store()` with local embeddings, no chat model, about 77 s. Only the
`product[routed]` column runs the product router; every other column is identical before and after.

| Set | before | after |
|---|---|---|
| original (53) | 42 / 46 / 26 | 46 / 47 / 30 |
| blind (86) | 68 / 75 / 47 | 72 / 79 / 47 |
| paraphrase (230) | 183 / 199 / 119 | 189 / 203 / 118 |
| blind, not first person (79) | 62 / 69 / 42 | 66 / 73 / 42 |

Not worse on any set's total. Questions that crossed the top 3: 18 gains, 4 losses. All four losses come from rule 2
and are multi-hop:

- chat-v1 multi-hop r2.4 and its paraphrases 2 and 3, "What is the name of Dana's sister's baby, whom Dana plans to
  meet on her July trip to Portland?": `kin_of(sister)` became `kin_of(sister)` then `visits`, and the answer moves
  from rank 1 to 4. "trip" adds a second step that the answer (the baby's name) does not need.
- chat-v1 multi-hop r2.2 paraphrase 2, "Which tool is the dashboard standard at Dana's former employer, Northwind?":
  `part_of` became `part_of` and `works_at`, rank 1 to 9.

By type, blind multi-hop stays 9 in the top 3 (one gain, one loss) and paraphrase multi-hop goes from 22 to 20 (one
gain, three losses). Lookup, correction and negation gain on every set.

## Offline evidence gate

`product_packing gate --set 'memory_mode="address_first"'` on both sides, then `gate-compare`
(`gate-compare.md`; the JSON reports are 3 MB each and stay out of the repository, in the ignored `data/router-fix-gate/` of the worktree). Before: ee0130a9, clean. After:
d8fcfa8e, clean. Same LongMemEval-S controls (manifest `0103b9509a35`).

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.1% | 84.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | All evidence packed with memory text | 84.1% | 84.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Evidence recall | 89.2% | 89.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 77.7% | 77.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1540 |
| longmemeval | All evidence packed | 94.7% | 94.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | All evidence packed with memory text | 94.7% | 94.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Evidence recall | 97.3% | 97.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 91.4% | 91.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 500 |

No interval is below zero and no question lost all its evidence among the candidates (0 on both benchmarks). Every
category row is a tie as well. This is expected: the gate replays saved `store()` packs, which hold no extracted
relation claims, so a different route walks the same empty graph.

## Checks

Providers blocked on every run.

- `tests/test_relation_routing.py`, `tests/test_belief_state.py` and `tests/test_graph_oracle.py`: `256 passed, 11
  skipped in 202.57s`. `tests/test_address_first_repairs.py`: `47 passed in 7.77s`. ruff: `All checks passed!`
- Contract scorecard, visible set, `--stated`:
  - classic: `73 passed, 87 xfailed` (before: the same).
  - address_first: `73 passed, 44 xpassed, 43 xfailed, 0 failed` (before: the same).
- Hidden set, `--stated`, totals only: classic `82 passed, 80 xfailed`; address_first `82 passed, 42 xfailed, 38
  xpassed` (before: the same). This session did not read the hidden set's per-id results.
- Full unit suite, `tests` without the contract directories: `1 failed, 7479 passed, 1646 skipped, 2 xfailed in
  2282.27s (0:38:02)` (round 3: 1 failed, 7471 passed; the 8 more are this change's new tests). The one failure is
  `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`, the same as in rounds 2 and 3:
  it expects a plain clone and this is a git worktree. Log: `logs/full-suite.log`.

## Open items (not changed in this task)

- **The cities miss** (defects-v1, "Which cities will Alex and Jordan visit in Japan?", tx-B1 rank 6). Its facts say
  `decided` and the router asks for `visits`. Route unchanged.
- **The `when` rule** (`_TEMPORAL_RE`) came from graph oracle evidence and is unchanged. Rule 2 now routes some
  date questions the rule does not catch, for example "What are the current dates for the Portugal trip?" (draw2
  temporal 9) to `visits`. On the blind sets the date questions that moved got better (store-v1 temporal 5 and
  correction r2.3), but the rule's wording is a separate decision.
- **"trip" as a second step.** Rule 2 adds `visits` as a later step when a trip is a side clause of a multi-hop
  question (the sister's baby). This is the source of three of the four blind losses.
