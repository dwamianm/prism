# Paraphrased duplicate claims in `ingest()` packs (locomo, #255)

Build `ingest-baseline-locomo-deepseek-2026-10-15` (deepseek-v4.1-flash:cloud). Code by step: pairs `c94e5bc9`, replay `d9a4c94e`, report `c94e5bc9`. Similarity: cosine of each pack's stored claim vectors (BAAI/bge-small-en-v1.5, 384 dimensions). Tokens: the packer's own counts with `cl100k_base`, budget 4096 less 100 reserved. No model calls.

**Decision: close the issue with the numbers.** Condition 1, at least 5% of packed tokens on average: fails at 3.0%. Condition 2, a band with at least 95% same-fact labels once number, date and negation pairs are set aside: fails. None of the other readings below changes the decision.

Contexts: 1540 questions, 96.2 packed records per context, 37.1 of them current claims with a subject entity, 3994 context tokens and 3935 record tokens on average.

## Pairs of current claims with one subject

The last four columns break down the pairs at or above 0.90. A short line has 6 words or fewer after the turn's time and speaker.

| Pack | Current claims | Paired claims | Same-subject pairs | At or above 0.90 | 0.95 | 0.97 | From one turn | Of those, identical text | From two turns | Of those, both lines short |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| conv-26 | 563 | 563 | 30709 | 1090 | 283 | 156 | 105 | 98 | 985 | 819 |
| conv-30 | 402 | 402 | 20503 | 789 | 166 | 73 | 56 | 50 | 733 | 683 |
| conv-41 | 831 | 831 | 87595 | 1706 | 402 | 276 | 217 | 204 | 1489 | 1437 |
| conv-42 | 694 | 694 | 67549 | 2062 | 663 | 306 | 175 | 157 | 1887 | 1611 |
| conv-43 | 794 | 794 | 69769 | 378 | 187 | 164 | 173 | 153 | 205 | 181 |
| conv-44 | 901 | 901 | 62719 | 391 | 224 | 207 | 224 | 203 | 167 | 48 |
| conv-47 | 897 | 897 | 101320 | 1300 | 382 | 248 | 238 | 224 | 1062 | 922 |
| conv-48 | 892 | 892 | 85683 | 569 | 235 | 197 | 202 | 187 | 367 | 323 |
| conv-49 | 587 | 587 | 47944 | 687 | 185 | 135 | 126 | 115 | 561 | 521 |
| conv-50 | 656 | 656 | 52355 | 2201 | 458 | 164 | 108 | 96 | 2093 | 1870 |
| All | 7217 | 7217 | 626146 | 11173 | 3185 | 1926 | 1624 | 1487 | 9549 | 8415 |

## Labeled sample

50 pairs drawn with seed 255 and band sizes 17 from 0.90-0.95, 17 from 0.95-0.97, 16 from 0.97-1.00.

Every pair:

| Band | Pairs | Labeled | Same fact | Different | Only a number | Only a date | Only a negation | Same-fact rate | Same fact, those set aside | Meets 95% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0.90-0.95 | 7988 | 17 | 0 | 17 | 0 | 6 | 0 | 0.0% | 0.0% | no |
| 0.95-0.97 | 1259 | 17 | 1 | 16 | 0 | 11 | 0 | 5.9% | 16.7% | no |
| 0.97-1.00 | 1926 | 16 | 12 | 4 | 0 | 3 | 0 | 75.0% | 92.3% | no |

Pairs whose texts differ (identical text set aside):

| Band | Pairs | Labeled | Same fact | Different | Only a number | Only a date | Only a negation | Same-fact rate | Same fact, those set aside | Meets 95% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0.90-0.95 | 7988 | 17 | 0 | 17 | 0 | 6 | 0 | 0.0% | 0.0% | no |
| 0.95-0.97 | 1259 | 17 | 1 | 16 | 0 | 11 | 0 | 5.9% | 16.7% | no |
| 0.97-1.00 | 403 | 4 | 0 | 4 | 0 | 3 | 0 | 0.0% | 0.0% | no |

## Packed tokens

A packed claim counts when a claim paired with it at or above the threshold is packed too. Shares are of each context's packed tokens, averaged over questions.

| Pairs at or above | Questions with a packed pair | Records per question | Tokens per question | Share, upper bound | Share, adjusted | Pooled, upper / adjusted | Of record tokens, adjusted | Texts differ: upper / adjusted |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.90 | 1126 of 1540 | 15.56 | 354.1 | 8.9% | 3.0% | 8.9% / 3.0% | 3.1% | 8.6% / 0.7% |
| 0.95 | 937 of 1540 | 9.82 | 214.7 | 5.4% | 3.0% | 5.4% / 3.0% | 3.1% | 5.1% / 0.7% |
| 0.97 | 807 of 1540 | 5.85 | 127.7 | 3.2% | 2.7% | 3.2% / 2.7% | 2.7% | 2.8% / 0.0% |

## Other readings of the rule

| Reading | Token share | Condition 1 | Bands at 95% or more | Condition 2 | Follow-up |
|---|---:|---|---|---|---|
| condition 1 on the upper bound (every pair at or above 0.90 counted as the same fact) | 8.9% | holds | none | fails | no |
| condition 1 on pooled tokens (all duplicate tokens over all context tokens) | 3.0% | fails | none | fails | no |
| condition 1 over the packed records' own tokens | 3.1% | fails | none | fails | no |
| condition 2 on the 'at or above' bands, each band weighted by its pair count | 3.0% | fails | none | fails | no |
| both conditions with identical-text pairs set aside | 0.7% | fails | none | fails | no |

## Sampling error

Not part of the rule. Each band's labels with a Wilson 95% interval, and condition 1's adjusted share with every band's same-fact rate at its lower or its upper limit at once.

| Band | Same-fact rate | Same fact, number, date and negation pairs set aside |
|---|---|---|
| 0.90-0.95 | 0.0% to 18.4% | 0.0% to 25.9% |
| 0.95-0.97 | 1.0% to 27.0% | 3.0% to 56.4% |
| 0.97-1.00 | 50.5% to 89.8% | 66.7% to 98.6% |

Condition 1 at the lower limits: 2.2%; at the upper limits: 6.4%. Bands whose upper limit reaches 95%: 0.97-1.00.

## How the rule is read

Packed tokens are the packer's own count of the whole rendered context (MemoryBundle.tokens_used, the gate's context_tokens) with the configured tokenizer. A packed record's tokens are the token_cost the packer charged for its rendered line, with the same tokenizer. At a threshold, a packed claim counts when at least one claim paired with it at or above the threshold is packed in the same context; its share is taken per question and averaged over every replayed question. The upper bound counts every such pair as the same fact. The adjusted estimate counts each pair with the labeled same-fact rate of its sampling band, and a claim with several packed partners with the chance that at least one of them is the same fact, as if pairs were independent. Condition 1 reads the adjusted estimate for all pairs at or above 0.90. Condition 2 reads each sampling band ([0.90, 0.95), [0.95, 0.97) and [0.97, 1.00]) on its own labeled pairs, after different-fact pairs that differ only in a number, a date or a negation are set aside. Pairs whose texts are identical after normalization count like any other pair. The report also applies the rule under the readings this does not use (the upper bound, pooled tokens, the records' own tokens as the denominator, the 'at or above' bands weighted by their pair counts, and both conditions with identical-text pairs set aside), and names any that would change the decision.

## Labeling rule

Same fact: both records state the same proposition about the same subject: one event or occurrence, state, attribute, relationship, preference or plan. Wording, word order, a name in place of I, hedges and filler do not matter, and neither does the date a line shows when both dates refer to the same occurrence or state. A record that says everything the other says and adds detail is still the same fact, because the other line adds nothing beside it. Where a line leaves a reference open (it, that, there), the claim's subject, predicate and object and its source turn decide what it refers to. Different facts: each record states something the other does not: another event, or the same kind of event on another occasion; another object, attribute, person, place, amount, time or polarity; or a detail on each side that the other lacks. A different-fact pair is also marked with the one thing that alone separates it, if there is one: number (the same proposition with another count or amount), date (the same kind of event or statement on another date or occasion) or negation (one record denies what the other asserts).
