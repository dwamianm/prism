# Paraphrased duplicate claims in `ingest()` packs (2026-10-15, #255)

Issue #255 asked how much of a packed `ingest()` context goes to claims that
restate another packed claim in different words, and fixed a decision rule
before anything was measured: open a follow-up only if same-fact pairs with
both partners packed take at least 5% of packed tokens on average, and some
similarity band holds at least 95% same-fact labels once pairs that differ in
a number, a date or a negation are set aside. Otherwise the numbers close the
issue.

**Result: both conditions fail, so the numbers close the issue.** On the ten
LoCoMo conversations, pairs at or above 0.90 with both partners packed take
3.0% of packed tokens on the adjusted estimate (8.9% if every such pair were
the same fact), and the best band holds 12 same-fact pairs of 13 (92.3%). No
other reading of the rule changes the outcome, and no follow-up is opened.
The 50 labels are too few to exclude either threshold on their own (see
"Sampling error"), but they show where the same facts are: every one came
from a single turn, and none of the 37 sampled pairs from two turns was the
same fact.

## Inputs

| Item | Value |
|---|---|
| Packs | `ingest-baseline-locomo-deepseek-2026-10-15`: all ten LoCoMo conversations built through `ingest()` at `main` `58caddb1` with the current defaults and `deepseek-v4.1-flash:cloud` (manifest digest `e04da138`, Ollama 0.40.4), finished 2026-10-15 17:44 UTC. Build record sha256 `33f76415e6b0` |
| LongMemEval-S | Not measured. No complete LongMemEval-S `ingest()` pack of the current code exists on this machine, and the issue asks for it only where available |
| Code | `benchmarks/diagnostics/paraphrased_claims.py`. The labeling rule and the reading of the decision rule were committed at `d9a4c94e`, before any current pack was read, and did not change afterwards. The replay ran at `d9a4c94e`; the pairs and the report at `c94e5bc9`, which adds descriptive counts, provenance and the review's checks (the pairs themselves are unchanged) |
| Similarity | Cosine of the claim vectors each pack stored (`BAAI/bge-small-en-v1.5`, fastembed 0.7.4, 384 dimensions), the vectors retrieval compares. Nothing was embedded again and no model was called |
| Contexts | The offline evidence gate's own replay (`product_packing.run_gate`) with the current defaults: reader format, `packing.fold_repeated_text` on, 4,096-token budget less 100 reserved. Each question's in-context records and their token costs come from its retrieval receipt |
| Tokens | The packer's own counts with tiktoken `cl100k_base`: a record's `token_cost` for its rendered line, and the context's `tokens_used` (the gate's `context_tokens`) as the denominator |

## Method

1. **Pairs.** A current claim is a fact, decision or preference record in an
   active lifecycle state that is neither superseded nor closed. Its subject
   is the entity of a current HAS_FACT edge to it. Every two current claims
   that share a subject entity form a pair, and pairs at a cosine of 0.90 or
   more are kept. Similarities are rounded to six decimals before banding.
2. **Labels.** A stratified sample of 50 pairs (17 from [0.90, 0.95), 17 from
   [0.95, 0.97) and 16 from [0.97, 1.00], seed 255) was labeled by hand under
   the rule below, from a view that hid each pair's band and similarity and
   listed the pairs in a shuffled order. Every label carries a one-line reason
   in [`paraphrased-claims-locomo-labels.json`](paraphrased-claims-locomo-labels.json),
   which also holds each pair's two lines as a reader sees them, its triples,
   source turns and speakers. The report step refuses a sheet that is not the
   sample this seed and these sizes draw from these pairs.
3. **Token shares.** For each of the 1,540 questions, a packed claim counts
   at a threshold when a claim paired with it at or above the threshold is
   packed in the same context. Its share is the counted records' tokens over
   the context's tokens, averaged over every question. The upper bound counts
   every such pair as the same fact. The adjusted estimate counts each pair
   with its band's labeled same-fact rate (a claim with several packed
   partners counts with the chance that at least one is the same fact, as if
   pairs were independent).
4. **Decision.** Condition 1 reads the adjusted estimate for all pairs at or
   above 0.90. Condition 2 reads each sampling band on its own labeled pairs.
   The report applies the rule again under five readings it does not use.

The labeling rule, as committed:

> Same fact: both records state the same proposition about the same subject:
> one event or occurrence, state, attribute, relationship, preference or
> plan. Wording, word order, a name in place of I, hedges and filler do not
> matter, and neither does the date a line shows when both dates refer to
> the same occurrence or state. A record that says everything the other says
> and adds detail is still the same fact, because the other line adds nothing
> beside it. Where a line leaves a reference open (it, that, there), the
> claim's subject, predicate and object and its source turn decide what it
> refers to. Different facts: each record states something the other does
> not: another event, or the same kind of event on another occasion; another
> object, attribute, person, place, amount, time or polarity; or a detail on
> each side that the other lacks. A different-fact pair is also marked with
> the one thing that alone separates it, if there is one: number (the same
> proposition with another count or amount), date (the same kind of event or
> statement on another date or occasion) or negation (one record denies what
> the other asserts).

## Results

### Pairs

| Conversation | Current claims | Same-subject pairs | At or above 0.90 | 0.95 | 0.97 |
|---|---:|---:|---:|---:|---:|
| conv-26 | 563 | 30,709 | 1,090 | 283 | 156 |
| conv-30 | 402 | 20,503 | 789 | 166 | 73 |
| conv-41 | 831 | 87,595 | 1,706 | 402 | 276 |
| conv-42 | 694 | 67,549 | 2,062 | 663 | 306 |
| conv-43 | 794 | 69,769 | 378 | 187 | 164 |
| conv-44 | 901 | 62,719 | 391 | 224 | 207 |
| conv-47 | 897 | 101,320 | 1,300 | 382 | 248 |
| conv-48 | 892 | 85,683 | 569 | 235 | 197 |
| conv-49 | 587 | 47,944 | 687 | 185 | 135 |
| conv-50 | 656 | 52,355 | 2,201 | 458 | 164 |
| All | 7,217 | 626,146 | 11,173 | 3,185 | 1,926 |

Every current claim had a subject entity and exactly one stored vector. The
pairs fall into two groups that look nothing like paraphrases of a fact:

- **Claims of one turn.** 1,624 pairs at or above 0.90 share a source turn,
  and 1,487 of them have identical text: one sentence yields several claims,
  and each claim's text is that sentence. Pairs from one turn are 1,505 of
  the 1,926 pairs at or above 0.97. `fold_repeated_text` folds such a claim
  when another record of its turn already shows its text with the same date
  and tags; the identical-text claims that stay packed together, because
  their dates or tags differ, take 0.2% of packed tokens.
- **Social remarks from different turns.** Of the 9,549 pairs at or above
  0.90 from two turns, 8,415 pair two lines of six words or fewer after the
  turn's time and speaker, such as "Joanna: Thanks, Nate!" on two days.
  Extraction turns such remarks into claims ("Joanna thanks Nate"), and the
  embedding of a short line is dominated by the name in it. At or above 0.97,
  413 of the 421 pairs from two turns are of this kind.

### Labels

| Band | Pairs | Labeled | Same fact | Different | Only a date | Same fact, date pairs set aside | 95% |
|---|---:|---:|---:|---:|---:|---:|---|
| [0.90, 0.95) | 7,988 | 17 | 0 | 17 | 6 | 0 of 11 | no |
| [0.95, 0.97) | 1,259 | 17 | 1 | 16 | 11 | 1 of 6 (16.7%) | no |
| [0.97, 1.00] | 1,926 | 16 | 12 | 4 | 3 | 12 of 13 (92.3%) | no |

No pair differed only in a number or a negation. All 13 same-fact pairs come
from one turn: 12 identical lines, and one line that repeats a shorter one
from the same turn and adds a question. All 37 pairs from two different turns
are different facts: greetings, thanks, congratulations and short replies,
20 of them the same remark on another day (marked date) and 17 different
remarks.

### Packed tokens

The replayed contexts hold 96.2 records and 3,994 tokens on average, 37.1 of
the records current claims.

| Pairs at or above | Contexts with a packed pair | Records per context | Tokens per context | Share, upper bound | Share, adjusted | Pairs whose texts differ: upper / adjusted |
|---|---:|---:|---:|---:|---:|---:|
| 0.90 | 1,126 of 1,540 | 15.6 | 354 | 8.9% | **3.0%** | 8.6% / 0.7% |
| 0.95 | 937 of 1,540 | 9.8 | 215 | 5.4% | 3.0% | 5.1% / 0.7% |
| 0.97 | 807 of 1,540 | 5.9 | 128 | 3.2% | 2.7% | 2.8% / 0.0% |

The per-context share at or above 0.90 has a median of 4.1% (upper bound) and
reaches 63.4% in one context. Pooled over all contexts, or taken over the
packed records' own tokens instead of the context's, the adjusted share is
3.0% and 3.1%. Claims whose packed partners at or above 0.90 all have
identical text take 0.2% of packed tokens, so nearly all of the packed pairs
are pairs whose texts differ, mostly remarks from two turns.

### Decision

| Reading | Condition 1 | Condition 2 | Follow-up |
|---|---|---|---|
| The rule as read here | fails (3.0%) | fails (best band 92.3%) | no |
| Upper bound for condition 1 | holds (8.9%) | fails | no |
| Pooled tokens for condition 1 | fails (3.0%) | fails | no |
| Records' own tokens for condition 1 | fails (3.1%) | fails | no |
| "At or above" bands, weighted by pair counts, for condition 2 | fails (3.0%) | fails (92.3% at or above 0.97) | no |
| Identical-text pairs set aside, both conditions | fails (0.7%) | fails (best band 1 of 6) | no |

### Sampling error

Not part of the rule; added after review. With Wilson 95% intervals on each
band's labels, the same-fact rates run from 0.0% to 18.4%, 1.0% to 27.0% and
50.5% to 89.8%, and the top band's share with date pairs set aside from
66.7% to 98.6%. With every band's rate at its lower limit at once, condition 1
would read 2.2%; at its upper limit, 6.4%. So 50 labels cannot exclude either
threshold by themselves: at the upper limits both conditions could hold. The
rule as fixed reads the point estimates, and those fail.

The labels separate same and different facts more sharply by source than by
similarity: 13 of the 13 sampled pairs from one turn were the same fact, and
none of the 37 sampled pairs from two turns (a Wilson 95% upper limit of 9.4%
if those 37 were a simple random sample, which a stratified sample only
approximates). The packed pairs are almost all pairs whose texts differ,
mostly from two turns. A packing-time fold by similarity would therefore
mostly fold greetings and thanks said on different days, which are different
occasions, while the copies one turn yields are folded whenever their dates
and tags match.

## Limits

- **One labeler, small bands.** I labeled all 50 pairs. The middle band keeps
  only 6 pairs once date pairs are set aside, and the top band 13, so the 95%
  rule there needs every one of them to be the same fact and one label decides
  it: "Joanna: Hey Nate!" against "Joanna: Yeah, Nate!" on another day, a
  greeting and an agreement, is marked different without a single
  difference. Marked date-only instead, the top band would hold 12 of 12 and
  condition 2 would pass, but condition 1 would still fail at 3.0% and the
  issue would still close. Counting the repeated thanks and greetings as the
  same fact, which the rule does not do, would leave the top band at 15 of 16
  (93.8%), still short of 95%.
- **The adjusted share leans high.** The top band's labeled same-fact rate
  (12 of 16) comes from identical lines of one turn, but the top-band pairs
  that do get packed together are mostly remarks from two turns, whose
  labeled rate was 0 of 4. With identical-text pairs set aside the adjusted
  share is 0.7%. The adjusted estimate also treats pairs as independent, and
  both partners of a pair count although a fold could save only one.
- **Pairs under 0.90 are not measured.** The issue's bands start at 0.90, so
  paraphrases that the embedding scores lower are outside this measurement.
- **One benchmark, one embedding.** LoCoMo only, with the packs' own
  `bge-small` vectors. LoCoMo is chat between two friends, so greetings and
  thanks are frequent; a corpus with fewer social remarks would shift the
  bands. Another embedding model could rank pairs differently.
- **What the rule did not ask.** The 8,415 short pairs from two turns suggest
  that extraction stores many greetings and thanks as claims. Whether those
  claims should exist is a separate question from paraphrased duplicates, and
  this measurement does not open it.

## Files

- [`paraphrased-claims-locomo.md`](paraphrased-claims-locomo.md) and
  [`paraphrased-claims-locomo.json`](paraphrased-claims-locomo.json): the
  report, with every question's counts and context digest.
- [`paraphrased-claims-locomo-labels.json`](paraphrased-claims-locomo-labels.json):
  the 50 labeled pairs.

The pairs and contexts files the report read are not kept here (4 MB and
13 MB, rebuilt from the packs by the commands below). Their sha256 are
`062879f6696a` and `90b14aa779a8`; the report records them in full. Run the
commands at the commits above; a later change to the pairing would refuse the
committed labels rather than reuse them.

```sh
# No model calls. The pairs, and with --sheet a new labeling sheet (it holds benchmark text).
uv run python -m benchmarks.diagnostics.paraphrased_claims pairs --benchmark locomo \
  --packs data/extracted-packs-v1/ingest-baseline-locomo-deepseek-2026-10-15 \
  --output /tmp/paraphrased-pairs.json
# The gate's contexts and their packed records; 13 minutes here on a shared machine.
uv run python -m benchmarks.diagnostics.paraphrased_claims replay --benchmark locomo \
  --packs data/extracted-packs-v1/ingest-baseline-locomo-deepseek-2026-10-15 \
  --output /tmp/paraphrased-contexts.json
# The decision, from the pairs, the labeled sheet and the contexts.
uv run python -m benchmarks.diagnostics.paraphrased_claims report --pairs /tmp/paraphrased-pairs.json \
  --labels benchmarks/results/research/2026-10-15/paraphrased-claims-locomo-labels.json \
  --contexts /tmp/paraphrased-contexts.json --output /tmp/paraphrased-claims-locomo.json
```
