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
top-25 loss is measured in the last section ("Which setting costs evidence in top 25"): `enable_belief_state`.

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

## Which setting costs evidence in top 25

**Question.** `main-ret-v3` (all six settings of the mode's retrieval half) loses evidence in the top 25 on the
defaults' packs: -0.7 points, 0 questions gained and 16 lost, both intervals below zero. Which setting causes it?

**Setup.** Six mid tier runs at cd0332cd, a clean tree, providers blocked, one run at a time, each with one setting
alone and the rest at the defaults:

```sh
uv run python -m benchmarks.mid_tier run --label <label> --out data/mid-tier-v1/reports/<label> \
  --reuse-build main-0.17.0a6 --set <setting>
uv run python -m benchmarks.mid_tier compare data/mid-tier-v1/reports/main-0.17.0a6 \
  data/mid-tier-v1/reports/<label> --output data/mid-tier-v1/reports/<label>-vs-main.json
```

No model call; each run took 4 min 44 s to 5 min 01 s. Each report records commit cd0332cd and the one setting.
Reports in the ignored `data/mid-tier-v1/reports/` (`one-<name>/`, `one-<name>-vs-main.md`).

**Result: `enable_belief_state` causes all 16.** LoCoMo, 1,536 questions with evidence, against `main-0.17.0a6`:

| Label | Setting alone | Evidence in top 25 | 95% questions | 95% conversations | W / L / T | All evidence packed (W / L) | Evidence recall (W / L) | Projected accuracy (W / L) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| one-routing | `enable_relation_routing=true` | -0.2 | -0.5 to -0.0 | -0.4 to -0.1 | 0 / 6 / 1530 | +0.4 (6 / 0) | +0.3 (9 / 0) | +0.3 (6 / 0) |
| one-belief | `enable_belief_state=true` | -0.7 | -1.1 to -0.3 | -1.0 to -0.3 | 0 / 16 / 1520 | +0.8 (13 / 0) | +0.6 (19 / 2) | +0.6 (13 / 0) |
| one-direct10 | `packing.session_context_direct_first=10` | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | +0.0 (0 / 0) | +0.0 (0 / 0) | +0.0 (0 / 0) |
| one-clock | `packing.clock_window_filter=true` | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | +0.0 (0 / 0) | +0.0 (0 / 0) | +0.0 (0 / 0) |
| one-collapse | `packing.collapse_identical_text=true` | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | +0.0 (0 / 0) | +0.0 (0 / 0) | +0.0 (0 / 0) |
| one-recency | `scoring.rrf_recency_order=true` | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | +0.0 (0 / 0) | +0.0 (0 / 0) | +0.0 (0 / 0) |

- `enable_belief_state` alone is not a no-op, so no `one-routing-belief` run was made. The engine turns relation
  routing on whenever belief state is on (`relation_routing=config.enable_relation_routing or
  config.enable_belief_state`, `src/prme/storage/engine.py` line 885), and a question no word routes takes the
  catalog walk (`src/prme/retrieval/pipeline.py`, stage 5.5f2). So `one-belief` already runs routing plus belief,
  and a run with both set would build the same retrieval.
- `one-belief` is `main-ret-v3` on every question for evidence in the top 25 (the same 16 lost), all evidence
  packed, evidence packed and projected accuracy. Evidence ranks differ on 110 questions, deeper in the list; none
  changes evidence in the top 25. The other five settings add nothing to these rows.
- The four packing and scoring settings move no row. Their evidence ranks move on 47 questions
  (`session_context_direct_first=10`), 9 (`collapse_identical_text`), 69 (`rrf_recency_order`) and 0
  (`clock_window_filter`); none changes evidence in the top 25 or what is packed.
- LongMemEval-S (12 questions): every row the same in all six runs.
- The 13 questions belief state gains in all evidence packed include all 6 that routing alone gains. The setting
  that costs the top 25 is also the one that gives the packed gains.

**The 16 questions.** The evidence turns' first ranks (up to 40) were taken again by replaying each question on a
scratch copy of its pack with the gate's configuration under three settings: the defaults, routing alone and
belief state alone. All 48 replayed rank sets equal the gate's. "New above" counts the records above the lowest
evidence turn that were not above it with the defaults; in brackets, the ones that only the ledger adds (their one
path is `BELIEF`, so they were not among the candidates before).

| Question | Category | Route | Defaults | Routing alone | Belief state | Lost with routing alone | New above (ledger only) | Lost if a citing claim counts |
|---|---|---|---|---|---|---|---|---|
| conv-26-q0088 | single-hop | `owns` | D2:14 25 | D2:14 26 | D2:14 27 | yes | 2 (1) | yes |
| conv-26-q0100 | single-hop | `prefers` | D4:15 22 | D4:15 25 | D4:15 26 | no | 4 (1) | no |
| conv-42-q0107 | single-hop | `owns`, `prefers` | D5:6 20 | D5:6 24 | D5:6 26 | no | 6 (2) | no |
| conv-42-q0119 | single-hop | `prefers` | D11:13 21 | D11:13 25 | D11:13 26 | no | 5 (1) | yes |
| conv-44-q0003 | multi-hop | `partner_of` | D3:11 8, D3:1 21, D27:1 23 | D3:11 11, D3:1 22, D27:1 24 | D3:11 15, D3:1 26, D27:1 28 | no | 5 (4) | no |
| conv-44-q0009 | multi-hop | `involved_in`, `member_of` | D27:2 5, D10:1 10, D6:2 15, D14:2 18 | D27:2 11, D10:1 16, D6:2 20, D14:2 23 | D27:2 14, D10:1 19, D6:2 23, D14:2 26 | no | 6 (3) | no |
| conv-44-q0054 | multi-hop | `owns`, `prefers` | D4:21 13, D27:12 16 | D4:21 21, D27:12 24 | D4:21 23, D27:12 26 | no | 10 (2) | no |
| conv-44-q0068 | single-hop | `involved_in`, `owns` | D6:2 19 | D6:2 31 | D6:2 36 | yes | 17 (5) | no |
| conv-47-q0008 | multi-hop | `owns` | D1:14 23 | D1:14 26 | D1:14 26 | yes | 3 (0) | yes |
| conv-47-q0108 | single-hop | none (catalog walk) | D18:9 23 | D18:9 23 | D18:9 27 | no | 4 (3) | no |
| conv-49-q0006 | multi-hop | `involved_in` | D2:1 25 | D2:1 28 | D2:1 30 | yes | 5 (2) | yes |
| conv-49-q0048 | multi-hop | `owns` | D9:1 7, D22:1 16, D8:1 23 | D9:1 13, D22:1 22, D8:1 29 | D9:1 13, D22:1 22, D8:1 29 | yes | 6 (0) | no |
| conv-49-q0049 | multi-hop | `kin_of`, `partner_of` | D7:9 23 | D7:9 25 | D7:9 27 | no | 3 (2) | yes |
| conv-49-q0105 | single-hop | `involved_in` | D8:12 23 | D8:12 25 | D8:12 27 | no | 4 (2) | no |
| conv-41-q0119 | single-hop | `prefers` | D22:17 16 | D22:17 25 | D22:17 33 | no | 17 (8) | yes |
| conv-43-q0128 | single-hop | `uses` | D15:32 5, D15:33 23 | D15:32 9, D15:33 27 | D15:32 9, D15:33 27 | yes | 4 (0) | yes |

- Routing alone already loses 6 of the 16. Its route list (weight `RRF_ROUTE_WEIGHT`, 2.0) lifts the claims the
  walk reaches above the text matches. Belief state adds the ledger on top and loses the other 10.
- The ledger keeps every claim at the address together where the best ranked claim stands
  (`place_beliefs`, `src/prme/retrieval/beliefs.py`): each takes that claim's score times
  `1 + (n - i) * 0.001`, and a claim not among the candidates is added beside them. Across the 16 questions, 101
  records are newly above the evidence; 36 of them are claims that only the ledger adds. 4 of the 101 cite an
  evidence turn.
- conv-47-q0108 has no route: the catalog walk found the address alone and the ledger placed 5 claims above the
  evidence, 3 of them new to the pool.
- The measure counts a turn only by records stored from that turn (`source_dialog_id`). An extracted claim has no
  turn of its own, so it never counts in the top 25, even when its evidence reference names the evidence turn. If a
  claim counted by the turns it cites, as the gate already counts packed extracted records, 9 of the 16 would not
  be lost; 7 would still be (last column). This recount is a check of the cause, not the gate's measure: the
  measure stays -0.7.
- No question loses packed evidence. On all 16, evidence packed, all evidence packed and the projected score are
  the same or better with belief state.

**Worked example: conv-41-q0119** (single-hop), "How often does John get to see sunsets like the one he shared
with Maria?" The evidence is D22:17, "(6:59 pm on 5 July, 2023) John: Thanks, Maria! I see
them at least once a week. It's a good way to disconnect, think, and find peace in this" (the first 140 characters).
The word "like" routes the question to `prefers`: "How often does John see sunsets he shared with Maria?" is not
routed.

- Defaults: D22:17 is at rank 16 (score 0.4621).
- Routing alone: the walk reaches `prefers` claims, and 5 of them rank in the top 9, for example "I wanted to help
  out in these tough times by doing a community food drive." (rank 1). D22:17 falls to rank 25, still in the top 25.
- Belief state: the ledger places 13 claims at ranks 1 to 13, scores 1.1142 down to 1.1010, and 8 of them were not
  among the candidates, for example "John wants to join military." (rank 2) and "John wants to help community food
  drive." (rank 13). D22:17 keeps its score (0.4621) and falls to rank 33. None of the 17 records newly above it
  cites D22:17. D22:17 is packed in all three runs.

**Possible fix, not built.** Let the ledger reorder its claims only within the places they already hold, and add
claims from outside the pool only below them, so the ledger cannot push text matches down. The same setting gives
the 13 packed gains, so a fix must be measured on both rows.

