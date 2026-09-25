# Reader-format packing order on the evidence gate v1

**Decision:** Keep `balanced` as the default `multipath_ordering`. With the
reader format, score order packs all annotated evidence for more LoCoMo
questions and fewer LongMemEval-S questions than `balanced` under every ranking
measured. Under the weighted score the LoCoMo gain is large (+5.9 projected
points at 4K) and the LongMemEval-S loss small (-1.9). Under rank fusion with
session decay 0.6, with or without #168's recency settings, the LoCoMo gain
shrinks to +0.7 and the LongMemEval-S loss grows to -4.6 to -4.8. Those rank
fusion settings are the next default candidate, and which ordering that set
should carry is a separate decision: with `balanced` it is a new variant that
needs its own DeepSeek pairs.

**Date:** 2026-09-24

**Issue:** #81 (epic #77)

**Execution revision:** `fc2533104aeb2dd346c374137b1e2c4f7bb8b7b0` (clean tree)
for the weighted and rank fusion runs; `7201503bbefa11a3a75345f216193586571c28fc`
(clean tree, after #168 merged) for the recency runs and one identity check.

## Question

#81 proposes making score order the default once records are plain lines. It
rests on the projection in section 1 of the
[2026-09-23 audit](../../../../memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md)
that `balanced` fills plain-line contexts with short filler turns. Its first two
acceptance criteria ask for the evidence gate with `balanced` and score order at
4K and 8K on LoCoMo and LongMemEval-S, by category, and for every category that
loses more than 1 point of all-evidence share to be stated with an explanation.

## Protocol

Every run replays the offline evidence gate (#78) with the reader context format
(#79), under three rankings:

- **Weighted:** the current default score.
- **Rank fusion, decay 0.6:** `scoring.fusion="rrf"` (#82) with
  `packing.session_context_rank_fusion_score_decay=0.6` (#111). These are the
  `reader-rrf-sd06` settings apart from the ordering.
- **Rank fusion, decay 0.6, recency:** the same plus #168's
  `scoring.rrf_recency_boost=0.25` and `scoring.rrf_tie_break="event_time"`.
  These are the `reader-rrf-sd06-rec` settings apart from the ordering.

Each ranking runs with `balanced` and with `score` at 4K (3,996 tokens after the
100-token reserve) and at 8K (`packing.token_budget=8192`). `gate-compare` pairs
`balanced` (before) with `score` (after) question by question. The gate makes no
reader, judge or paid calls.

The metrics are those of the
[evidence gate](../../../../BENCHMARKS.md#offline-evidence-gate-the-first-gate-for-retrieval-changes).
"All evidence packed" is the share of annotated questions whose annotated
evidence turns are all in the context: 1,536 of LoCoMo's 1,540 questions and 470
of LongMemEval-S's 500 are annotated. "Projected accuracy" is the gate's
planning estimate over all questions, not an answer score. Changes are computed
from question counts, so they can differ by 0.1 from the difference of the
rounded shares.

Cross-checks:

- Weighted `balanced` and `score` at 4K reproduce the reader-format numbers of
  #109 (all evidence packed 69.5% and 78.3% on LoCoMo, 87.4% and 84.3% on
  LongMemEval-S). Weighted `balanced` at 4K also reproduces the audit's
  projection for plain lines in balanced order: 101.7 records per context,
  19.9% of multi-hop questions with all evidence and 67.8% projected, against
  the audit's 103, 19% and about 67%.
- Rank fusion `score` at 4K gives the same context, question for question, as
  the DeepSeek track's `prme-reader-rrf-sd06` preparation. Recency `score` at 4K
  does the same for `prme-reader-rrf-sd06-rec` (2,040 of 2,040).
- Rank fusion `score` at 4K run again at `7201503b` gives the same context as at
  `fc253310` for all 2,040 questions, so the rank fusion runs without recency
  still describe `main` after #168.

## Result

For reference, the current defaults (auditable format, weighted score,
`balanced`, 4K) pack 25.2 records per context and all evidence for 983/1,536
LoCoMo questions (64.0%, projected 64.0%), and 23.9 records and 403/470
LongMemEval-S questions (85.7%, projected 86.0%).

| Ranking | Budget | Order | LoCoMo records per context | LoCoMo all evidence packed | LoCoMo projected | LongMemEval-S records per context | LongMemEval-S all evidence packed | LongMemEval-S projected |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Weighted | 4K | balanced | 101.7 | 1,067/1,536 (69.5%) | 67.8% | 83.1 | 411/470 (87.4%) | 87.0% |
| Weighted | 4K | score | 74.8 | 1,202/1,536 (78.3%) | 73.7% | 21.5 | 396/470 (84.3%) | 85.1% |
| Weighted | 8K | balanced | 194.6 | 1,252/1,536 (81.5%) | 76.0% | 151.7 | 436/470 (92.8%) | 90.2% |
| Weighted | 8K | score | 153.8 | 1,336/1,536 (87.0%) | 79.6% | 43.8 | 419/470 (89.1%) | 88.1% |
| Rank fusion, decay 0.6 | 4K | balanced | 84.5 | 1,292/1,536 (84.1%) | 77.7% | 39.9 | 445/470 (94.7%) | 91.4% |
| Rank fusion, decay 0.6 | 4K | score | 77.6 | 1,308/1,536 (85.2%) | 78.4% | 19.5 | 408/470 (86.8%) | 86.6% |
| Rank fusion, decay 0.6 | 8K | balanced | 165.7 | 1,380/1,536 (89.8%) | 81.5% | 69.7 | 457/470 (97.2%) | 92.9% |
| Rank fusion, decay 0.6 | 8K | score | 159.1 | 1,386/1,536 (90.2%) | 81.7% | 36.2 | 432/470 (91.9%) | 89.7% |
| Rank fusion, decay 0.6, recency | 4K | balanced | 84.5 | 1,290/1,536 (84.0%) | 77.6% | 39.9 | 445/470 (94.7%) | 91.4% |
| Rank fusion, decay 0.6, recency | 4K | score | 77.6 | 1,305/1,536 (85.0%) | 78.3% | 19.5 | 409/470 (87.0%) | 86.8% |
| Rank fusion, decay 0.6, recency | 8K | balanced | 165.7 | 1,377/1,536 (89.6%) | 81.3% | 69.6 | 457/470 (97.2%) | 92.9% |
| Rank fusion, decay 0.6, recency | 8K | score | 159.1 | 1,385/1,536 (90.2%) | 81.7% | 36.2 | 432/470 (91.9%) | 89.7% |

Score order minus `balanced`, in percentage points, with `gate-compare`'s paired
95% intervals and the questions score order wins and loses on all evidence
packed:

| Ranking | Budget | LoCoMo all evidence | LoCoMo wins/losses | LoCoMo projected | LongMemEval-S all evidence | LongMemEval-S wins/losses | LongMemEval-S projected |
|---|---|---:|---:|---:|---:|---:|---:|
| Weighted | 4K | +8.8 (+7.2 to +10.4) | 149/14 | +5.9 (+4.9 to +7.0) | -3.2 (-6.4 to -0.2) | 21/36 | -1.9 (-3.9 to 0.0) |
| Weighted | 8K | +5.5 (+4.3 to +6.8) | 91/7 | +3.6 (+2.8 to +4.4) | -3.6 (-6.6 to -0.9) | 15/32 | -2.2 (-4.0 to -0.4) |
| Rank fusion, decay 0.6 | 4K | +1.0 (+0.5 to +1.7) | 20/4 | +0.7 (+0.3 to +1.1) | -7.9 (-10.4 to -5.5) | 2/39 | -4.8 (-6.3 to -3.3) |
| Rank fusion, decay 0.6 | 8K | +0.4 (-0.1 to +0.8) | 9/3 | +0.2 (-0.05 to +0.5) | -5.3 (-7.4 to -3.4) | 0/25 | -3.2 (-4.5 to -2.1) |
| Rank fusion, decay 0.6, recency | 4K | +1.0 (+0.4 to +1.6) | 19/4 | +0.7 (+0.3 to +1.1) | -7.7 (-10.2 to -5.3) | 2/38 | -4.6 (-6.2 to -3.2) |
| Rank fusion, decay 0.6, recency | 8K | +0.5 (+0.1 to +1.0) | 11/3 | +0.3 (+0.02 to +0.6) | -5.3 (-7.4 to -3.4) | 0/25 | -3.2 (-4.5 to -2.1) |

- Under every ranking and budget, score order is ahead on LoCoMo and behind on
  LongMemEval-S.
- Under the weighted score the LoCoMo gain is what the audit projected. The
  LongMemEval-S loss was first reported in #109.
- Under rank fusion the LoCoMo gain is about 1 point or less of all-evidence share,
  and its 8K interval without recency includes zero. The LongMemEval-S loss is
  3.2 to 4.8 projected points, with every interval excluding zero.
- #168's recency settings change no category by more than 3 questions.
- Both orderings improve on the current defaults. With the recency set at 4K,
  score order projects +14.3 points on LoCoMo and +0.8 on LongMemEval-S over
  the defaults, and `balanced` projects +13.7 and +5.4 (unpaired differences of
  projected accuracy).

## By category

All evidence packed. The change is score minus `balanced` in percentage points.
The rank fusion runs without recency are within 3 questions of the recency runs
in every category; their tables are in the
[results file](reader-packing-order-gate-v1-results.json).

### Weighted, 4K

| Benchmark | Category | Annotated | Balanced | Score | Change | 95% interval | Wins/losses |
|---|---|---:|---:|---:|---:|---:|---:|
| LoCoMo | multi-hop | 282 | 19.9% | 36.5% | +16.7 | +12.1 to +21.3 | 49/2 |
| LoCoMo | open-domain | 92 | 40.2% | 52.2% | +12.0 | +5.4 to +19.6 | 12/1 |
| LoCoMo | single-hop | 841 | 84.3% | 89.9% | +5.6 | +3.9 to +7.4 | 53/6 |
| LoCoMo | temporal | 321 | 82.6% | 91.9% | +9.3 | +5.6 to +13.1 | 35/5 |
| LongMemEval-S | knowledge-update | 72 | 95.8% | 90.3% | -5.6 | -11.1 to -1.4 | 0/4 |
| LongMemEval-S | multi-session | 121 | 77.7% | 71.9% | -5.8 | -12.4 to +0.8 | 6/13 |
| LongMemEval-S | single-session-assistant | 56 | 75.0% | 96.4% | +21.4 | +12.5 to +32.1 | 12/0 |
| LongMemEval-S | single-session-preference | 30 | 96.7% | 80.0% | -16.7 | -30.0 to -3.3 | 0/5 |
| LongMemEval-S | single-session-user | 64 | 95.3% | 98.4% | +3.1 | 0.0 to +7.8 | 2/0 |
| LongMemEval-S | temporal-reasoning | 127 | 91.3% | 81.1% | -10.2 | -16.5 to -4.7 | 1/14 |

### Weighted, 8K

| Benchmark | Category | Annotated | Balanced | Score | Change | 95% interval | Wins/losses |
|---|---|---:|---:|---:|---:|---:|---:|
| LoCoMo | multi-hop | 282 | 41.1% | 56.4% | +15.2 | +11.0 to +20.2 | 45/2 |
| LoCoMo | open-domain | 92 | 53.3% | 62.0% | +8.7 | +2.2 to +15.2 | 9/1 |
| LoCoMo | single-hop | 841 | 94.3% | 96.4% | +2.1 | +1.1 to +3.2 | 20/2 |
| LoCoMo | temporal | 321 | 91.6% | 96.3% | +4.7 | +1.9 to +7.5 | 17/2 |
| LongMemEval-S | knowledge-update | 72 | 98.6% | 94.4% | -4.2 | -11.1 to +1.4 | 1/4 |
| LongMemEval-S | multi-session | 121 | 86.8% | 80.2% | -6.6 | -12.4 to -0.8 | 3/11 |
| LongMemEval-S | single-session-assistant | 56 | 80.4% | 96.4% | +16.1 | +7.1 to +26.8 | 9/0 |
| LongMemEval-S | single-session-preference | 30 | 100.0% | 86.7% | -13.3 | -26.7 to -3.3 | 0/4 |
| LongMemEval-S | single-session-user | 64 | 95.3% | 98.4% | +3.1 | 0.0 to +7.8 | 2/0 |
| LongMemEval-S | temporal-reasoning | 127 | 97.6% | 87.4% | -10.2 | -15.7 to -5.5 | 0/13 |

### Rank fusion, decay 0.6, recency, 4K

| Benchmark | Category | Annotated | Balanced | Score | Change | 95% interval | Wins/losses |
|---|---|---:|---:|---:|---:|---:|---:|
| LoCoMo | multi-hop | 282 | 49.6% | 51.4% | +1.8 | -0.4 to +3.9 | 8/3 |
| LoCoMo | open-domain | 92 | 57.6% | 58.7% | +1.1 | 0.0 to +3.3 | 1/0 |
| LoCoMo | single-hop | 841 | 95.2% | 95.7% | +0.5 | 0.0 to +1.1 | 5/1 |
| LoCoMo | temporal | 321 | 92.2% | 93.8% | +1.6 | +0.3 to +3.1 | 5/0 |
| LongMemEval-S | knowledge-update | 72 | 100.0% | 100.0% | 0.0 | no change | 0/0 |
| LongMemEval-S | multi-session | 121 | 89.3% | 72.7% | -16.5 | -24.0 to -9.9 | 1/21 |
| LongMemEval-S | single-session-assistant | 56 | 96.4% | 94.6% | -1.8 | -5.4 to 0.0 | 0/1 |
| LongMemEval-S | single-session-preference | 30 | 100.0% | 80.0% | -20.0 | -36.7 to -6.7 | 0/6 |
| LongMemEval-S | single-session-user | 64 | 96.9% | 98.4% | +1.6 | 0.0 to +4.7 | 1/0 |
| LongMemEval-S | temporal-reasoning | 127 | 93.7% | 85.8% | -7.9 | -12.6 to -3.1 | 0/10 |

### Rank fusion, decay 0.6, recency, 8K

| Benchmark | Category | Annotated | Balanced | Score | Change | 95% interval | Wins/losses |
|---|---|---:|---:|---:|---:|---:|---:|
| LoCoMo | multi-hop | 282 | 66.0% | 68.4% | +2.5 | +0.7 to +4.6 | 7/0 |
| LoCoMo | open-domain | 92 | 67.4% | 68.5% | +1.1 | 0.0 to +3.3 | 1/0 |
| LoCoMo | single-hop | 841 | 97.4% | 97.3% | -0.1 | -0.6 to +0.2 | 1/2 |
| LoCoMo | temporal | 321 | 96.6% | 96.9% | +0.3 | -0.6 to +1.6 | 2/1 |
| LongMemEval-S | knowledge-update | 72 | 100.0% | 100.0% | 0.0 | no change | 0/0 |
| LongMemEval-S | multi-session | 121 | 94.2% | 81.8% | -12.4 | -19.0 to -6.6 | 0/15 |
| LongMemEval-S | single-session-assistant | 56 | 96.4% | 96.4% | 0.0 | no change | 0/0 |
| LongMemEval-S | single-session-preference | 30 | 100.0% | 96.7% | -3.3 | -10.0 to 0.0 | 0/1 |
| LongMemEval-S | single-session-user | 64 | 98.4% | 98.4% | 0.0 | no change | 0/0 |
| LongMemEval-S | temporal-reasoning | 127 | 97.6% | 90.6% | -7.1 | -11.8 to -3.1 | 0/9 |

## Categories where score order loses more than 1 point of all-evidence share

No LoCoMo category does under any ranking (the largest loss is -0.1, single-hop
at 8K under rank fusion). These LongMemEval-S categories do. Values are the
change in all-evidence share in percentage points; the rank fusion columns use
decay 0.6, and the recency columns add #168's settings:

| Category | Weighted 4K | Weighted 8K | Rank fusion 4K | Rank fusion 8K | Recency 4K | Recency 8K |
|---|---:|---:|---:|---:|---:|---:|
| knowledge-update | -5.6 | -4.2 | | | | |
| multi-session | -5.8 | -6.6 | -16.5 | -12.4 | -16.5 | -12.4 |
| single-session-assistant | | | -1.8 | | -1.8 | |
| single-session-preference | -16.7 | -13.3 | -20.0 | -3.3 | -20.0 | -3.3 |
| temporal-reasoning | -10.2 | -10.2 | -8.7 | -7.1 | -7.9 | -7.1 |

## Interpretation

**Why score order loses LongMemEval-S evidence.** The annotated evidence of
these categories sits in short user turns next to much longer assistant
replies. In the LongMemEval oracle file (the same questions with only their
evidence sessions), user turns have a median of 50 tokens and assistant turns
484. Every annotated evidence turn of the knowledge-update, preference and
temporal-reasoning questions is a user turn, as are 322 of the 323
multi-session evidence turns. Score order packs the long assistant replies that
rank near the top, so a 4K context holds 19.5 to 21.5 records under score order
against 39.9 (rank fusion) or 83.1 (weighted) under `balanced`, and the
lower-ranked user turns that carry the evidence no longer fit.

`balanced` reserves the first place for the highest-scored multi-path
candidate, which score order also puts first, and orders the rest of the
multi-path tier by score divided by the fourth root of the record's token cost.
A median assistant turn is divided by a factor about 1.76 larger than a median
user turn, which keeps the user turns. Candidates found by only one retrieval
path, such as new session-expansion neighbors, sit in a lower tier that both
orderings sort by score. Single-session-assistant is the one category whose
evidence is mostly in assistant replies (51 of its 56 evidence turns), and it
gains 16 to 21 points from score order under the weighted score. Under rank
fusion `balanced` already packs 96.4% of it, and the -1.8 at 4K is one question.

**Why rank fusion shrinks the LoCoMo gain.** How far the length penalty moves a
record depends on how close its score is to the scores above it. On LoCoMo the
weighted score is flat near the top: across all candidates in the saved
receipts, the 10th and 50th best score a median 1/1.18 and 1/1.32 of the best.
Under rank fusion they score 1/1.35 and 1/1.90. LoCoMo reader lines are short
and similar in length (10th to 90th percentile 30 to 74 tokens, a factor of 1.25
after the fourth root). That factor is larger than the weighted score's gap to
the 10th candidate and smaller than rank fusion's, so under the weighted score
the penalty moves short filler turns ahead of more candidates, which is the
audit's finding. Under rank fusion `balanced` packs 84.5 LoCoMo records per
context against score order's 77.6, where under the weighted score it packs
101.7 against 74.8. A ceiling adds to this: under rank fusion `balanced` already
packs all evidence for 84.1% of LoCoMo questions at 4K, against 69.5% under the
weighted score. On LongMemEval-S the two rankings fall off alike near the top
(1/1.19 weighted and 1/1.24 rank fusion at the 10th candidate, 1/1.43 and 1/1.98
at the 50th), and the factor of about 1.76 between user and assistant turns
still reorders them.

## Acceptance criteria of #81

1. Gate results for `balanced` and score order at 4K and 8K, on both
   benchmarks, by category: this record and its results file.
2. Not met under any ranking measured, when "overall" is read per benchmark as
   the epic's default-change rule reads it: score order has the higher
   projected accuracy on LoCoMo and the lower one on LongMemEval-S under all
   three rankings. Pooled over all 2,040 questions, score order leads under the
   weighted score (+4.0 points at 4K, +2.2 at 8K) and trails under rank fusion
   (-0.6 at both budgets, with or without recency). The categories that lose
   more than 1 point are stated and explained above.
3. Open. The default has not changed, and under the epic's rule it changes only
   with a DeepSeek paired answer run.

## Consequence

- `balanced` stays the default for now.
- The `reader-rrf`, `reader-rrf-sd06` and `reader-rrf-sd06-rec` variants
  prepared for the DeepSeek track so far all use score order. The same settings
  with `balanced` form a different variant (other settings and other context
  text), so none of their verdicts covers it. It would need its own first pair
  and confirmation. On the gate it projects 4.6 points more on LongMemEval-S
  and 0.7 fewer on LoCoMo than the score-order set at 4K.
- If `balanced` is chosen for a rank fusion set, sweep the session decay again
  under it. Decay 0.6 was tuned under score order (#111), and under `balanced` a
  short neighbor of a long trigger can outrank the trigger. For example, a
  50-token neighbor that is itself a multi-path candidate and inherits 0.6 times
  a 484-token trigger's score of 1 gets 0.6 / 50^0.25 = 0.226, against the
  trigger's 1 / 484^0.25 = 0.213.
- A change that makes score order the default must also revise the ordering
  paragraphs this record added to `docs/PACKING.md` and RFC-0006.

## Limits

- Projected accuracy is a planning estimate from the saved GPT-5.4 run's
  conditional accuracies, measured on auditable-format contexts, not an answer
  score. It ignores distractor effects. `balanced` LongMemEval-S contexts hold
  40 to 152 records, against 19 to 24 in every arm the DeepSeek track has
  answered so far, so the effect of the extra records on a reader is untested.
- Intervals resample questions. LoCoMo's questions come from 10 conversations,
  so its intervals understate the uncertainty.
- All 2,040 questions have been examined before, so this is development
  evidence.
- Every run uses the reader format. The record says nothing about ordering
  under the default auditable format.

## Reproduction and evidence

[reader-packing-order-gate-v1-results.json](reader-packing-order-gate-v1-results.json)
holds, for each of the 13 gate runs, its overrides, commit, clean-tree flag,
report SHA-256 and summaries by benchmark and category, and all six
`gate-compare` outputs as written, which include each run's full provenance. It
leaves out the per-question rows of the gate reports. The gate's inputs are
listed in [BENCHMARKS.md](../../../../BENCHMARKS.md#inputs).

The gate runs make no model calls. Each took 9 to 16 minutes on a laptop with
four running at once; the loop below runs them one after another. The weighted
and rank fusion runs ran at `fc253310` and the recency runs at `7201503b`,
after #168 merged.

```sh
packing() { uv run python -m benchmarks.diagnostics.product_packing "$@"; }
reader='packing.context_format="reader"'
rrf=(--set 'scoring.fusion="rrf"' --set packing.session_context_rank_fusion_score_decay=0.6)
rec=(--set scoring.rrf_recency_boost=0.25 --set 'scoring.rrf_tie_break="event_time"')
for order in balanced score; do
  o="packing.multipath_ordering=\"$order\""
  for budget in 4k 8k; do
    b=()
    if [ "$budget" = 8k ]; then b=(--set packing.token_budget=8192); fi
    packing gate --set "$reader" --set "$o" "${b[@]}" --output /tmp/w-$order-$budget.json
    packing gate --set "$reader" --set "$o" "${b[@]}" "${rrf[@]}" \
      --output /tmp/r-$order-$budget.json
    packing gate --set "$reader" --set "$o" "${b[@]}" "${rrf[@]}" "${rec[@]}" \
      --output /tmp/rec-$order-$budget.json
  done
done
for run in w-4k w-8k r-4k r-8k rec-4k rec-8k; do
  packing gate-compare /tmp/${run%-*}-balanced-${run#*-}.json \
    /tmp/${run%-*}-score-${run#*-}.json --output /tmp/compare-$run.json
done
```

The supporting figures in the interpretation came from local files, as follows:

- **Turn lengths and evidence roles:**
  `data/benchmarks/longmemeval/longmemeval_oracle.json`, with each turn's
  content counted in `cl100k_base` tokens. Abstention questions are left out, as
  the gate leaves them out, and evidence turns are those marked `has_answer`.
- **Score fall-off:** the retrieval receipts saved by the DeepSeek track's
  preparations, under
  `data/ollama-answers-v1/ollama-deepseek-v4.1-flash-cloud/<arm>/<benchmark>/contexts/`
  for `prme@46647825` (current defaults, weighted score) and
  `prme-reader-rrf-sd06` (rank fusion, decay 0.6). For each question with at
  least 50 candidates, the best candidate score divided by the 10th and by the
  50th best, over all candidates in the receipt; the figures are medians over
  questions.
- **LoCoMo line lengths:** the token cost of every distinct record packed at
  full text in the `prme-reader-rrf-sd06` LoCoMo receipts. Receipts record no
  cost for records left out of the context, and these packed records cover
  5,869 of LoCoMo's 5,882 turns.
