# Unknown answer status: protocol, written before any measurement

Task P1 from prme-manager, 2026-11-11. Thesis finding D (`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`, section
4.2, item 3).

## Question

Can one deterministic rule, with no model call at read time, mark a question "unknown" from signals that retrieval
already computes?

## Data

- Unknown set, LoCoMo: the category 5 (adversarial) questions of `data/benchmarks/locomo/locomo10.json` that have no
  `answer` field (444 of 446). The two category 5 questions that carry an answer ("Is Oscar Melanie's pet?" No, and
  "Did Caroline make the black and white bowl in the photo?" No) can be answered from memory, so they are left out
  of both sets.
- Answerable set, LoCoMo: categories 1 to 4 with an evidence annotation (1,536 of 1,540).
- Unknown set, LongMemEval-S: the 30 questions whose id ends in `_abs`.
- Answerable set, LongMemEval-S: the other 470 (all carry evidence).
- Packs: LoCoMo `data/mid-tier-v1/builds/main-0.17.0a6-locomo` (packs built through `ingest()`), LongMemEval-S the
  500 regenerated control captures in `data/opt-in-study/lme-controls-regenerated-2026-10-14/baseline` (packs built
  through `store()`). Every run reads a clone of the pack, checked against the recorded pack identity. No saved pack
  is opened.
- Retrieval runs under `memory_mode=classic` and under `memory_mode=address_first`, with the evidence gate's
  configuration otherwise (`benchmarks.diagnostics.product_packing.gate_config`). Providers are blocked. No model
  call is made.

## Signals

All come from one `retrieve()` call and the stores it reads: the ranked results with their semantic cosine
(`semantic_score`), the receipt's `relation_routing` and `belief_state` features, the lexical index's document
frequencies, and the speaker that `ingest()` records on each turn.

## Candidate rules (each has at most one threshold t)

- R1 weak match: the best semantic cosine among the results is below t.
- R2 missing words: at least t of the question's content words (the lexical index's own stemmed terms, stop words
  removed) occur in no stored record.
- R3 thesis rule: no route walk and no catalog walk reached an address, and R1 holds. Under `classic`, no walk runs,
  so R3 equals R1.
- R4 wrong owner: the question names exactly one speaker known to the store, and the best cosine among that
  speaker's results is below the best cosine overall by more than t.
- R5 weak coverage: no result among the top 50 contains more than a share t of the question's content words.
- Two combinations: R1 or R2, and R4 or R2. Both thresholds are fitted together on LoCoMo, over the same grids.

Content words: the question's tokens under the lexical index's own analyzer (`en_stem`: simple tokenizer, lower case,
English stemmer), without stop words (Tantivy's English list, the engine's seed stop words, the routing stop words and
the pronouns and auxiliaries "i me my mine we us our ours am do"), without tokens shorter than 3 characters and
without numbers.

## Fit and test

1. Each rule's threshold is fitted on LoCoMo only: the threshold with the highest share of the unknown set marked
   unknown, subject to at most 2% of the answerable set marked unknown.
2. The chosen rule is the one with the highest LoCoMo unknown share. Among rules within 2 points of the best, the one
   with fewer signals wins.
3. The chosen rule and its threshold are applied to LongMemEval-S without a change, under both modes.
4. Verdict "in the engine, behind an option" only if, on LongMemEval-S, at least 40% of the `_abs` questions are
   marked unknown and at most 2% of the answerable questions are. Otherwise the verdict is "not now", with the
   numbers.

Other rules may be reported as exploration, labeled as such. They do not change the verdict.
