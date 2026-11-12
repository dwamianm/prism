# Unknown answer status: can retrieval say "I do not know"?

Task P1, prme-manager, 2026-11-11. Branch `research-unknown-status-2026-11`, cut from `cd0332cd` (the pushed head of
`address-first-memory`). Thesis finding D, `memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`, section 4.2, item 3.

## Verdict

**Not now.** No deterministic rule fitted on LoCoMo met the bar on LongMemEval-S, under either memory mode.

The rule chosen on LoCoMo (R4, "wrong owner") marks 0 of 30 LongMemEval-S `_abs` questions unknown, against a bar
of at least 12 (40%). The one rule that does mark most `_abs` questions (R1, a weak best cosine) also marks 149 of 470
answerable questions unknown (31.7%), against a bar of at most 2%.

The two benchmarks have different kinds of "unknown", and the signals that separate one kind say nothing about the
other. A cosine threshold also does not carry from one store to another.

## Question

Can one deterministic rule mark a question "unknown" from signals that retrieval already computes, with no model call
at read time?

## Setup

The protocol was written and committed before any question was replayed (`PROTOCOL.md`, commit `6a3795a5`).

- **Unknown set.** LoCoMo: the 444 category 5 (adversarial) questions with no `answer` field. The 2 category 5
  questions that do carry an answer ("Is Oscar Melanie's pet?" No) are in neither set. LongMemEval-S: the 30 questions
  whose id ends in `_abs`.
- **Answerable set.** LoCoMo: the 1,536 questions of categories 1 to 4 with an evidence annotation. LongMemEval-S:
  the other 470.
- **Packs.** LoCoMo: the `ingest()` packs in `data/mid-tier-v1/builds/main-0.17.0a6-locomo`. LongMemEval-S: the 500
  regenerated control captures in `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (`store()` packs).
  Each run cloned the pack to a scratch folder, checked the clone against the recorded pack identity, and opened only
  the clone.
- **Retrieval.** One `retrieve()` per question with the evidence gate's configuration
  (`benchmarks.diagnostics.product_packing.gate_config`), once under `memory_mode=classic` and once under
  `memory_mode=address_first`. Providers blocked. No model call.
- **Signals kept per question.** The top 50 results with their semantic cosine, node type, paths, the speaker of the
  source turn, and how many of the question's content words each one holds; the lexical index's document frequency
  of each content word; whether a route walk or a catalog walk reached an address (receipt features
  `relation_routing` and `belief_state`).
- **Rules** (`PROTOCOL.md`):
  - R1 weak match: best cosine below t.
  - R2 missing words: at least t content words occur in no stored record.
  - R3 no address and weak match: no walk reached an address, and R1.
  - R4 wrong owner: the question names one known speaker, and that speaker's best cosine is below the overall best by
    more than t.
  - R5 weak coverage: no top 50 result holds more than a share t of the content words.
  - R1 or R2; R4 or R2.
- **Fit and test.** Each threshold was fitted on LoCoMo: the highest unknown share with at most 2% of answerable
  questions marked. The rule with the highest LoCoMo unknown share was chosen (fewer signals win within 2 points).
  The chosen rule and threshold were applied to LongMemEval-S unchanged.

## Commands

```sh
cd ~/Sites/prism-unknown
export OPENAI_API_KEY= ANTHROPIC_API_KEY=sk-ant-blocked OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  ANTHROPIC_BASE_URL=http://127.0.0.1:9 PRME_EXTRACTION_API_KEY=
for b in locomo longmemeval; do for m in classic address_first; do
  uv run python research/unknown-status-2026-11/collect.py --benchmark $b --mode $m --out /tmp/p1/$b-$m.jsonl
done; done
python3 research/unknown-status-2026-11/analyze.py /tmp/p1 > research/unknown-status-2026-11/results.json
```

The four runs took about 20 minutes in parallel on the MacBook. The signal files (28 MB) are kept in the ignored
folder `data/unknown-status-2026-11/` of this worktree.

## Results

Each cell is questions marked unknown out of the set.

### `classic`

| Rule | Threshold (LoCoMo fit) | LoCoMo unknown | LoCoMo answerable | LongMemEval-S `_abs` | LongMemEval-S answerable |
|---|---|---|---|---|---|
| R1 weak match | 0.705 | 21/444 (4.7%) | 27/1536 (1.8%) | 22/30 (73.3%) | 149/470 (31.7%) |
| R2 missing words | 3 | 3/444 (0.7%) | 10/1536 (0.7%) | 0/30 (0.0%) | 0/470 (0.0%) |
| R3 no address and weak match | 0.705 | 21/444 (4.7%) | 27/1536 (1.8%) | 22/30 (73.3%) | 149/470 (31.7%) |
| **R4 wrong owner (chosen)** | 0.045 | 40/444 (9.0%) | 24/1536 (1.6%) | 0/30 (0.0%) | 0/470 (0.0%) |
| R5 weak coverage | 0.35 | 9/444 (2.0%) | 14/1536 (0.9%) | 2/30 (6.7%) | 13/470 (2.8%) |
| R1 or R2 | 0.705, 5 | 21/444 (4.7%) | 27/1536 (1.8%) | 22/30 (73.3%) | 149/470 (31.7%) |
| R4 or R2 | 0.045, 5 | 40/444 (9.0%) | 24/1536 (1.6%) | 0/30 (0.0%) | 0/470 (0.0%) |

Under `classic` no walk runs, so R3 equals R1, as the protocol said.

### `address_first`

| Rule | Threshold (LoCoMo fit) | LoCoMo unknown | LoCoMo answerable | LongMemEval-S `_abs` | LongMemEval-S answerable |
|---|---|---|---|---|---|
| R1 weak match | 0.705 | 23/444 (5.2%) | 30/1536 (2.0%) | 22/30 (73.3%) | 149/470 (31.7%) |
| R2 missing words | 3 | 3/444 (0.7%) | 10/1536 (0.7%) | 0/30 (0.0%) | 0/470 (0.0%) |
| R3 no address and weak match | 0.715 | 26/444 (5.9%) | 27/1536 (1.8%) | 23/30 (76.7%) | 170/470 (36.2%) |
| **R4 wrong owner (chosen)** | 0.04 | 45/444 (10.1%) | 29/1536 (1.9%) | 0/30 (0.0%) | 0/470 (0.0%) |
| R5 weak coverage | 0.35 | 9/444 (2.0%) | 14/1536 (0.9%) | 2/30 (6.7%) | 13/470 (2.8%) |
| R1 or R2 | 0.705, 5 | 23/444 (5.2%) | 30/1536 (2.0%) | 22/30 (73.3%) | 149/470 (31.7%) |
| R4 or R2 | 0.04, 5 | 45/444 (10.1%) | 29/1536 (1.9%) | 0/30 (0.0%) | 0/470 (0.0%) |

### The chosen rule against the bar

| Mode | Set | Hit | Miss | False unknown | Kept | Unknown marked (95%) | Answerable marked (95%) |
|---|---|---|---|---|---|---|---|
| `classic` | LoCoMo | 40 | 404 | 24 | 1512 | 9.0% (6.7 to 12.0) | 1.6% (1.1 to 2.3) |
| `classic` | LongMemEval-S | 0 | 30 | 0 | 470 | 0.0% (0.0 to 11.4) | 0.0% (0.0 to 0.8) |
| `address_first` | LoCoMo | 45 | 399 | 29 | 1507 | 10.1% (7.7 to 13.3) | 1.9% (1.3 to 2.7) |
| `address_first` | LongMemEval-S | 0 | 30 | 0 | 470 | 0.0% (0.0 to 11.4) | 0.0% (0.0 to 0.8) |

Intervals are Wilson 95% intervals. Bar: at least 40% of `_abs` marked and at most 2% of answerable marked. Not met.

### Worked examples (chosen rule R4, `classic`)

R4 marked no LongMemEval-S question, so the hit and the false unknown come from LoCoMo.

- **Hit.** conv-26-q0168, "What are the new shoes that Caroline got used for?" The shoes are Melanie's; Caroline
  only asked "For walking or running?" (D7:19). The best Caroline record scores 0.110 below the best overall
  record (0.801), so R4 marks it unknown.
- **False unknown.** conv-26-q0083, "What did Melanie realize after the charity race?" Answer: self-care is
  important (D2:3, Melanie's turn). The best record overall (0.867) is the entity "charity race". It was first
  extracted from Caroline's reply "That charity race sounds great, Mel!", so it is credited to Caroline. Melanie's
  best record scores 0.814, 0.053 lower, so R4 marks a question that memory answers.
- **Miss.** conv-26-q0152, "What did Caroline realize after her charity race?" This is the same turn (D2:3), asked
  about the wrong person. The best record overall is a Caroline record (gap 0.000), so R4 keeps it. LongMemEval-S:
  0862e8bf_abs, "What is the name of my hamster?" The user has a cat named Luna and no hamster. "hamster" occurs in
  no stored record and the best cosine is 0.597, yet R4 never fires on LongMemEval-S, because no question there names
  a speaker.

## Why it failed

1. **The two benchmarks test different kinds of "unknown".**
   - In LoCoMo, 440 of 444 adversarial questions name exactly one of the two speakers. For 330 of them, the
     annotated evidence turn was spoken by the other speaker. The asked thing is stored. Only the person is wrong.
   - In LongMemEval-S, the `_abs` questions are near misses: a hamster when there is a cat, a violin when there is a
     guitar, Korea when there is Japan. The asked thing is not stored.

   A rule that fits one kind is blind to the other. R4 fits LoCoMo and cannot fire on LongMemEval-S, where no
   question names a speaker.
2. **A cosine threshold does not travel between stores.** The median best cosine is 0.804 for answerable LoCoMo
   questions and 0.742 for answerable LongMemEval-S questions. These packs hold whole chat turns and long assistant
   replies. The LoCoMo threshold 0.705 lands near the middle of the LongMemEval-S answerable questions, so it marks
   about a third of them.
3. **The missing word signal is weak on LoCoMo.** At least one content word occurs in no stored record for 146 of
   444 adversarial LoCoMo questions (32.9%), but also for 321 of 1,536 answerable ones (20.9%). The 2% limit
   therefore pushed the LoCoMo fit of R2 to 3 missing words, which no LongMemEval-S question reaches. On
   LongMemEval-S the same signal at 1 word marks 13 of 30 `_abs` questions (43.3%) and 32 of 470 answerable ones
   (6.8%).
4. **The address signal is not there to use.**
   - Under `address_first` on LoCoMo, a walk reached an address for 204 of 444 adversarial questions (45.9%) and
     812 of 1,536 answerable ones (52.9%). That is almost no separation.
   - On LongMemEval-S no walk ran at all (0 of 500), because `store()` packs hold no extracted claims and no
     entities.
   - The thesis rule "nothing in the catalog matched and the best text match is weak" (R3) is therefore R1 on
     LongMemEval-S.

## Exploration (does not change the verdict)

These numbers were fitted on the LongMemEval-S test set itself. They are an upper bound, not a result.

- R1 alone, best in-sample: cosine below 0.60 marks 6 of 30 `_abs` questions (20.0%) at 8 of 470 answerable (1.7%).
- One missing word and a best cosine below 0.66 marks 11 of 30 `_abs` questions (36.7%, 95% 21.9 to 54.5) at 6 of
  470 answerable (1.3%). Even fitted on the test set, that is below the 40% bar.
- The same rule on LoCoMo (cosine below 0.70) marks 12 of 444 adversarial questions (2.7%) at 9 of 1,536
  answerable (0.6%).
- The best in-sample rule is therefore still short of the bar. It would also need a threshold for each store.

## Would a decision model help?

Yes. Both kinds of "unknown" are reading questions, not ranking questions:

- "Is this record about Caroline or about Melanie?"
- "Does a cat answer a question about a hamster?"

The engine already has this check as a model-backed option (`src/prme/retrieval/answerability.py`, and the older
`abstention.py`). A small decision model over the question and the top few records would be the direct path. It
costs one model call per question at read time, which this task rules out, so it was not built.

The deterministic path the thesis describes needs the write side first. With claims stored at an address that names
the right subject (`Melanie, realized`), "What did Caroline realize after her charity race?" would resolve to an
address that holds no value. Today the LoCoMo packs attribute many claims to "I", and the LongMemEval-S packs hold no
claims at all.

## Limits

- Only 30 LongMemEval-S `_abs` questions. The 95% interval of any share is about 35 points wide.
- LoCoMo category 5 is built by swapping the person in a real question. It measures attribution more than "never
  stored", which is the case the thesis describes.
- The LongMemEval-S packs are `store()` packs, so the address-first read path (route walk, catalog walk, belief
  ledger) never ran there. An `ingest()` build of LongMemEval-S would test R3 properly. That build costs model calls
  and was out of scope.
- The speaker of an extracted record comes from its source turn (`prme_speaker_v1`), not from the claim's subject.
  A claim about the other speaker's turn ("Your race sounds great") is credited to the person who spoke.
- One retrieval configuration (the evidence gate's) and one embedding model. The cosine scale depends on both.

## Files

- `PROTOCOL.md`: the rules and the procedure, committed before measuring.
- `collect.py`: replays each question on a checked scratch clone and writes its signals.
- `analyze.py`: fits on LoCoMo, tests on LongMemEval-S, and writes `results.json`.
- `results.json`: every fit, every test, the confusion counts, the intervals and the examples.
