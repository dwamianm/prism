# Perfect extraction test and router fix (2026-11-11)

This is the summary of the perfect extraction test (three rounds) and of the router fix that followed it. The raw
data and the harness stay on branch `fact-fields` at a0c97fb5, under `research/oracle-extraction-2026-11/`
(`GUIDE.md`, the hand-written facts, every arm's reports and logs, and one `REPORT.md` per round). They are not on
this branch: they are about 131,000 lines of raw results.

On this branch, the engine work is two commits:

| Commit here | Commit on `fact-fields` | Change |
|---|---|---|
| 12a5e27b | ee0130a9 (round 3) | Stated facts get a mention rule, a `time`, and the turn's sentence as claim text |
| 48f850cc | d8fcfa8e (router fix) | The router skips words that ask for a name and routes the normalizer's own words; `ROUTING_VERSION` 3 |

## The question

If extraction wrote correct facts, would PRME answer the probe questions? A person wrote the facts of the three
probe chats by hand (`defects-v1`, `chat-v1`, `member-v1`) in the form of `ingest(assertions=...)`, and the probes
were built from those facts with no model call and measured unchanged. Arm B1 is the facts that a turn states and
the facts it ends (levels O1 and O2) under `memory_mode="address_first"`. Arm M1 is the model's own extraction
(`deepseek-v4.1-flash:cloud`, replayed from the `af-off-1111` cache, 0 calls) under the same mode. The bars, from
`GUIDE.md`, read B1 in the top 3: defects-v1 7 of 7, chat-v1 17 of 18 and at least M1 plus 2, member-v1 11 of 12.

## Results by round

Each cell is top 3 / top 5.

| Arm | defects-v1 | chat-v1 | member-v1 |
|---|---|---|---|
| Round 1 B1 | 6/7 / 7/7 | 13/18 / 13/18 | 10/12 / 11/12 |
| Round 2 B1 | 5/7 / 5/7 | 12/18 / 13/18 | 9/12 / 9/12 |
| Round 2 B1s (diagnostic) | 6/7 / 7/7 | 14/18 / 15/18 | 12/12 / 12/12 |
| Round 3 tx-B1 | 6/7 / 6/7 | 16/18 / 16/18 | 12/12 / 12/12 |
| Round 3 tx-B1, after the router fix | 6/7 / 6/7 | 17/18 / 17/18 | 12/12 / 12/12 |
| M1 (model extraction, after the router fix) | 7/7 / 7/7 | 15/18 / 16/18 | 11/12 / 12/12 |
| Bar (B1 top 3) | 7 | 17 | 11 |

Round 3 met the member-v1 bar only and missed the other two by one question each. With the router fix, the same
facts also reach the chat-v1 bar (17, which is also M1 plus 2). defects-v1 still misses by one question: the
cities (below).

## What each round changed

- **Round 1.** The facts went into the engine as it was (branch `address-first-memory` at db0a8e81). The person wrote
  113 facts: 31 for defects-v1, 58 for chat-v1 and 24 for member-v1. The engine's mention rule asks that the turn
  name the subject and the object in plain text, so a user's "I" never names the user, and 78 of the 113 facts
  needed a wider rule than that. The round widened the rule at run time only, in the harness. Even then, the form
  refused 15 of member-v1's 24 facts. A claim's text was the rendered triple. Of the 8 questions B1 missed in the top 3, 3 failed on the read
  side with the right facts in the pack, 1 on the router's vocabulary, 3 on what the form can hold (no date for a
  job start, facts the form refused), and 1 in every arm (a relative date).
- **Round 2.** Three engine changes: the wider mention rule in the engine itself (the turn mentions the entity, or
  the entity is the speaker and the turn says I, me, my, mine, myself, we, us or our, or the entity matches an
  existing entity's name, type and scope), a `time` on an assertion, and a claim text rendered from the fields. The
  facts were revised once; every fact now loaded (31 of 31, 57 of 57, 23 of 23). B1 scored below round 1 on every
  probe. The diagnostic arm B1s, the same facts with each claim's text set to its turn's sentence, scored at or
  above round 1 everywhere. So the new facts helped, and the rendered text cost more than it gave: it won 2
  questions and lost 8.
- **Round 3.** One engine change: a stated claim's text is the turn's sentence that names the object, plus one
  parenthesis with the role and the ISO date when the sentence does not show them. This won the two questions only
  the fields answer (the job title, rank 9 to 1; Ivy's birth date, not found to 1) and moved two others down (the
  cities from rank 5 to 6, the start date from 4 to 6), against B1s. Three misses were left: the dog (the router), the
  cities (the router against the facts' `decided`), and the start date (no route serves it). This is commit
  12a5e27b here.
- **Router fix.** Two general rules in `route_question` (`src/prme/retrieval/relations.py`), which runs only under
  `memory_mode="address_first"`. No word was chosen for a probe.
  1. Words that ask for a name ("name", "names", "named", "called") say nothing about the relation, so the router
     skips them. "call" stays a cue, so "Who is on call?" still routes to `involved_in`.
  2. A word that the predicate normalizer maps to a relation (`_PREDICATE_WORDS`) also routes to that relation,
     with the word's polarity and role, so a word that stores a relation also asks for it.

  The RELATIONS definitions are unchanged, so the extraction prompt is unchanged and `DERIVED_CUES` still equals the
  graph oracle's `derive_cues()`. `ROUTING_VERSION` is 3. This is commit 48f850cc here.

## Router fix measurements

Every build and measurement made 0 model calls (providers blocked, `PRME_BENCH_CACHE_ONLY=1`). Before is round 3
(ee0130a9); after is d8fcfa8e, a clean tree.

**Probes** (top 3 / top 5). member-v1 with `--speaker --set enable_speaker_grounding=true`.

| Probe | M1 before | tx-B1 before | M1 after | tx-B1 after |
|---|---|---|---|---|
| defects-v1 | 7/7 / 7/7 | 6/7 / 6/7 | 7/7 / 7/7 | 6/7 / 6/7 |
| chat-v1 | 15/18 / 16/18 | 16/18 / 16/18 | 15/18 / 16/18 | 17/18 / 17/18 |
| member-v1 | 11/12 / 12/12 | 12/12 / 12/12 | 11/12 / 12/12 | 12/12 / 12/12 |

No arm is worse in the top 3 or the top 5. Three probe questions changed route; one changed rank. "What kind of dog
does Dana have, and what is it called?" went from `involved_in` to `owns`, and its answer moved from rank 6 to 1 in
tx-B1 and from 3 to 1 in M1. The round 3 builds measured again with the new router give the same counts as the
rebuilt arms, so no difference is rebuild noise.

**Routes.** Of 495 questions (the probe questions, the graph oracle's gold questions, blind-v1 with its paraphrases,
and blind-v2), 295 were routed before and 318 after; 77 changed route. Rule 1 alone moved 10, rule 2 alone moved 63,
and 4 needed both.

**Graph oracle blind run** (`python -m benchmarks.graph_oracle blind`, `store()` with local embeddings, no chat
model). Only the `product[routed]` column runs the product router; every other column is the same before and after.
Each cell is top 3 / top 5 / top 1.

| Set | Before | After |
|---|---|---|
| original (53) | 42 / 46 / 26 | 46 / 47 / 30 |
| blind (86) | 68 / 75 / 47 | 72 / 79 / 47 |
| paraphrase (230) | 183 / 199 / 119 | 189 / 203 / 118 |
| blind, not first person (79) | 62 / 69 / 42 | 66 / 73 / 42 |

No set's total is worse. 18 questions moved into the top 3 and 4 moved out. All four losses come from rule 2 and
are multi-hop:

- chat-v1 multi-hop r2.4 and its paraphrases 2 and 3, "What is the name of Dana's sister's baby, whom Dana plans to
  meet on her July trip to Portland?": `kin_of(sister)` became `kin_of(sister)` then `visits`, and the answer moved
  from rank 1 to 4. "trip" adds a second step that the answer does not need.
- chat-v1 multi-hop r2.2 paraphrase 2, "Which tool is the dashboard standard at Dana's former employer,
  Northwind?": `part_of` became `part_of` and `works_at`, and the answer moved from rank 1 to 9.

**Offline evidence gate** (`product_packing gate --set 'memory_mode="address_first"'` on both sides, then
`gate-compare`; same LongMemEval-S controls, manifest `0103b9509a35`).

| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |
|---|---|---:|---:|---:|---|---:|---:|---:|
| locomo | All evidence packed | 84.1% | 84.1% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Evidence recall | 89.2% | 89.2% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | All evidence among the candidates | 99.3% | 99.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1536 |
| locomo | Projected accuracy | 77.7% | 77.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 1540 |
| longmemeval | All evidence packed | 94.7% | 94.7% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Evidence recall | 97.3% | 97.3% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | All evidence among the candidates | 100.0% | 100.0% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 470 |
| longmemeval | Projected accuracy | 91.4% | 91.4% | +0.0 pp | +0.0 pp to +0.0 pp | 0 | 0 | 500 |

Every row is a tie, and no question lost all its evidence among the candidates. This is expected: the gate replays
saved `store()` packs, which hold no extracted relation claims, so a different route walks the same empty graph.

## Mid tier, retrieval half

The mode's retrieval options on the defaults' packs, with the router fix: `benchmarks.mid_tier run --label
main-ret-v3 --reuse-build main-0.17.0a6` at 48f850cc (clean tree), with the same six settings as `main-ret-v2` in
`ADDRESS-FIRST-MID-TIER.md`:

```sh
--set enable_relation_routing=true --set enable_belief_state=true --set packing.session_context_direct_first=10 \
--set packing.clock_window_filter=true --set packing.collapse_identical_text=true --set scoring.rrf_recency_order=true
```

No model call; LoCoMo gate 4 min 47 s. Reports in the ignored `data/mid-tier-v1/reports/` (`main-ret-v3/`,
`main-ret-v3-vs-main.md`, `main-ret-v3-vs-v2.md`).

**main-ret-v3 against the defaults (`main-0.17.0a6`)**, LoCoMo, 1,540 questions (1,536 with evidence):

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 84.4% | +0.8 | +0.5 to +1.3 | +0.4 to +1.4 | 13 / 0 / 1523 | yes |
| evidence recall | 89.2% | 89.8% | +0.6 | +0.3 to +0.9 | +0.2 to +1.1 | 19 / 2 / 1515 | yes |
| evidence in top 25 | 70.4% | 69.7% | -0.7 | -1.1 to -0.3 | -1.0 to -0.3 | 0 / 16 / 1520 | yes (a loss) |
| all evidence returned | 98.0% | 98.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| projected accuracy | 77.4% | 77.9% | +0.6 | +0.3 to +0.9 | +0.3 to +1.0 | 13 / 0 / 1527 | yes |

All evidence packed by category: multi-hop 51.8% to 53.9% (+2.1, clear of zero, 6 / 0 / 276), open-domain +1.1,
single-hop +0.4, temporal +0.9 (none clear of zero). No question lost all its evidence. LongMemEval-S (12
questions): every row the same.

**main-ret-v3 against main-ret-v2** (the same settings before the router fix, at 612cf9e1 with the task 7 harness
patch uncommitted):

| Measure | main-ret-v2 | main-ret-v3 | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 84.4% | 84.4% | +0.1 | +0.0 to +0.2 | +0.0 to +0.2 | 1 / 0 / 1535 | no |
| evidence recall | 89.7% | 89.8% | +0.1 | +0.0 to +0.2 | +0.0 to +0.3 | 4 / 0 / 1532 | no |
| evidence in top 25 | 69.7% | 69.7% | -0.0 | -0.0 to +0.0 | -0.0 to +0.0 | 0 / 1 / 1535 | no |
| all evidence returned | 98.0% | 98.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| projected accuracy | 77.9% | 77.9% | +0.0 | +0.0 to +0.1 | +0.0 to +0.1 | 1 / 0 / 1539 | no |

The one question gained is conv-30-q0018 (multi-hop, 53.5% to 53.9%). LongMemEval-S: every row the same.

**The question: does the router fix change the top-25 loss of -0.7, or any other row?** No. Evidence in the top 25
is -0.7 points against the defaults before and after the fix (15 questions lost before, 16 after), and both
intervals stay below zero, so the loss that fails the alpha rule is still there. The router fix moves no row clear of
zero against `main-ret-v2`: it gains one question in all evidence packed and projected accuracy and four in
evidence recall, and loses one more in the top 25. No row moves by more than 4 of 1,536 questions, so this mid
tier says little about the router itself; the probes and the graph oracle measure it. Which of the six settings causes the
top-25 loss is not measured here.

## Checks on this branch (48f850cc)

Providers blocked on every run.

- `ruff check src/ tests/`: `All checks passed!`
- `tests/test_structured_assertions.py`, `tests/test_relation_routing.py`, `tests/test_belief_state.py` and
  `tests/test_address_first_repairs.py`: `307 passed, 26 skipped`.
- `tests/test_derivation_version.py`, run from outside the checkout: `2 passed`.
- Contract scorecard, visible set, `--stated`: classic `73 passed, 87 xfailed`; address_first `73 passed, 43
  xfailed, 44 xpassed`. The same as on `fact-fields` before and after the router fix.
- Hidden set, `--stated`, totals only: classic `82 passed, 80 xfailed`; address_first `82 passed, 42 xfailed, 38
  xpassed`. The same as on `fact-fields`.

The full suite was not run here; CI runs it.

## Conclusion

Hand-written facts score about the same as the model's extraction. After the router fix, tx-B1 scores 6/7, 17/18
and 12/12 in the top 3, and M1 scores 7/7, 15/18 and 11/12. Most of each round's gain came from the engine: the
mention rule that let the facts in, the claim text, and the router. So extraction is not the limit on these probes,
and fine-tuning an extractor is not the next step. The misses that are left are on the read side.

The limits: three small scripted chats, one person's facts, and each arm built once.

## Open items

- **The cities.** defects-v1, "Which cities will Alex and Jordan visit in Japan?", tx-B1 rank 6. The facts say
  `decided` and the router asks for `visits`. This is the one question between tx-B1 and the defects-v1 bar.
- **The `when` rule.** `_TEMPORAL_RE` came from graph oracle evidence and is unchanged. Rule 2 now routes some date
  questions that the rule does not catch, for example "What are the current dates for the Portugal trip?" to
  `visits`. The date questions that moved on the blind sets got better, but the rule's wording is a separate
  decision.
- **"trip" as a second step.** Rule 2 adds `visits` as a later step when a trip is a side clause of a multi-hop
  question. This is the source of three of the four blind losses; the fourth is "employer" adding `works_at`. A
  possible follow-up, not tried: let a predicate word add a step only on the first hop.
- **No "unknown" answer.** Retrieval always fills the context and never says that it does not know. A question
  about an object never seen returns as many records as a question about a seen one (the address-first thesis,
  `memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`, finding D). Nothing in this work changes that.
