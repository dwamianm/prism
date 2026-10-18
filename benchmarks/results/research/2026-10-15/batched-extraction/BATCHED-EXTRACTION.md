# Batched extraction: cost and evidence coverage (#254)

Date: 2026-10-15, with the full benchmarks of 2026-10-16 to 2026-10-18 added.
Issue: #254. Option: `extraction_batch_target_tokens` (0, off, when this note
was written; the default is 2,048 since 2026-10-16, #271).

## Summary

On all of LoCoMo, extracting several turns of a session in one model call cut
model calls by 88% to 95% and prompt tokens by 85% to 93%, and the offline
evidence gate packed all the evidence for more questions, not fewer. At every
size I tried, the gain in "all evidence packed" has 95% intervals above zero
over questions and over conversations, and two builds of the same per-turn
code differ by only -0.1 points. A one-turn control with the batch format
shows that the gain comes from extracting turns together, not from showing
the model each turn's speaker.

The gate is a development measure and its projected accuracy is a planning
estimate, not an answer score. No default changes here. The LongMemEval-S
step of the issue is below.

**Full benchmarks (2026-10-18).** The owner made batches of 2,048 the
`ingest()` default on these results (#271). I then built all of LoCoMo and
all 500 LongMemEval-S questions both ways from one commit, `d810ee55`
(v0.15.0), and answered them on the `ingest()` answer track with one call per
turn as the variant. Batching cut model calls by 95% on LoCoMo and 80% on
LongMemEval-S. On the gate it packed all the evidence for more LoCoMo
questions (+1.4 points, intervals above zero over questions and over
conversations) and showed no difference on LongMemEval-S (-0.2 points, -1.5
to +1.1). On the answer track, one call per turn showed no difference whose
95% interval excludes zero (LoCoMo -0.13 points, LongMemEval-S +2.2 points,
0.0 to +4.4), so `verdict` reads `fail` and batching stays the default.
Details are in "Full benchmarks at `d810ee55`" below.

## Setup

- Extraction model: `deepseek-v4.1-flash:cloud` (manifest digest `e04da138`)
  through the local Ollama server 0.40.4, with `--cloud --jobs 10`. Nothing is
  billed per request; the calls count against the Ollama account's limits.
- Per-turn builds (the baseline and its repeat): `main` at `58caddb1`.
  Batched builds: the #254 branch at `93935a16`. Every build ran from a clean
  worktree with the current defaults and only the batch option set.
- Batch sizes were fixed before any batched build: 512, 2,048 and 8,192
  tokens of turn text, the sizes the issue suggests, to be used on both
  benchmarks. LoCoMo has 272 sessions, median 946 tokens (cl100k_base),
  largest 2,108, so 2,048 and 8,192 compose the same batches in all but 3
  sessions.
- The rule for step 4 ("keeps evidence coverage within the baseline's
  noise") was also written down before any batched build: a size passes when
  its change in all evidence packed against the baseline is at least -0.9
  points (the lower end of the repeat build's question-level interval) and
  neither its question-level nor its conversation-level 95% interval lies
  entirely below zero.
- Gate: `product_packing gate --benchmark locomo --packs <build>` with the
  current defaults; batched builds replayed at `93935a16`, per-turn builds at
  `58caddb1`. Replaying the baseline packs at `93935a16` gives the same
  context, byte for byte, for all 1,540 questions, so the branch does not
  change retrieval or packing.
- Question-level intervals are `gate-compare`'s (2,000 paired bootstrap
  samples, seed 42). Conversation-level intervals resample the ten
  conversations with the answer track's `cluster_statistics`
  (`conversation_intervals.py` in this folder).

## Cost and claims

| Build | Model calls | Prompt tokens | Completion tokens | Wall time | Claims | Claims per turn | Turns with a claim | Evidence turns with a claim |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Per turn (baseline) | 5,882 | 30.27 M | 3.46 M | 27.1 min | 7,217 | 1.23 | 4,689 (79.7%) | 1,145/1,423 (80.5%) |
| Per turn (repeat) | 5,882 | 30.27 M | 3.48 M | 27.6 min | 7,195 | 1.22 | 4,707 (80.0%) | 1,157/1,423 (81.3%) |
| Batch 512 | 690 | 4.47 M | 1.50 M | 19.7 min | 5,339 | 0.91 | 3,221 (54.8%) | 1,191/1,423 (83.7%) |
| Batch 2,048 | 275 | 2.07 M | 1.29 M | 12.9 min | 4,579 | 0.78 | 2,904 (49.4%) | 1,168/1,423 (82.1%) |
| Batch 8,192 | 272 | 2.06 M | 1.31 M | 14.6 min | 4,640 | 0.79 | 2,941 (50.0%) | 1,171/1,423 (82.3%) |
| One-turn control (batch format, target 1) | 5,885 | 34.45 M | 2.23 M | 29.5 min | 8,044 | 1.37 | 3,789 (64.4%) | 1,288/1,423 (90.5%) |

Wall time runs from the build's start to its finish with ten packs at a time.
No batched request failed or was split, and no turn was left out of a
response (`turns_without_entry` is 0 in every batched build). The one-turn
control made 3 more calls than it has turns, from requests the client asked
again; none failed.

Batching attaches claims to far fewer turns (49% to 55% instead of 80%) but
to more of the turns the questions cite as evidence (82% to 84% instead of
81%). The claims it no longer makes are mostly on turns that hold no
evidence, such as greetings and thanks, which #255 found make up most of the
near-duplicate claims. Per-turn grounding dropped about 26% to 28% of the
facts and relationships the model wrote (`response_claims` against
`saved_claims` in `locomo-builds.json`); the per-turn builds do not record
that count.

## Offline evidence gate, against the per-turn baseline

1,536 annotated questions; projected accuracy over all 1,540.

| Build | All evidence packed | Change | Questions 95% | Conversations 95% | Evidence recall change | All evidence among the candidates | Projected accuracy change |
|---|---:|---:|---|---|---:|---:|---:|
| Per turn (baseline) | 81.6% | | | | | 95.9% | |
| Per turn (repeat) | 81.5% | -0.1 | -0.9 to +0.8 | -0.6 to +0.6 | +0.3 | 96.0% | -0.1 |
| Batch 512 | 83.3% | +1.8 | +0.4 to +3.1 | +0.7 to +2.9 | +1.7 | 98.0% | +1.3 |
| Batch 2,048 | 84.0% | +2.5 | +1.1 to +3.8 | +1.2 to +3.7 | +2.2 | 98.4% | +1.8 |
| Batch 8,192 | 83.7% | +2.1 | +0.7 to +3.5 | +1.0 to +3.1 | +1.9 | 98.5% | +1.5 |
| One-turn control | 82.2% | +0.7 | -0.5 to +1.9 | -0.7 to +1.8 | +0.4 | 97.3% | +0.4 |

Changes are in percentage points. Evidence recall and projected accuracy have
intervals above zero at both levels for all three sizes; for the repeat and
the one-turn control, both levels include zero (`locomo-*-vs-*.json` and
`locomo-conversation-intervals.json`).

All evidence packed by category, change against the baseline (questions 95%):

| Build | Multi-hop (282) | Open-domain (92) | Single-hop (841) | Temporal (321) |
|---|---|---|---|---|
| Per turn (repeat) | -0.4 (-2.8 to +2.1) | +1.1 (-3.3 to +5.4) | 0.0 (-1.1 to +1.1) | -0.3 (-1.6 to +0.9) |
| Batch 512 | +1.4 (-3.2 to +6.0) | +1.1 (-4.3 to +6.5) | +2.3 (+0.6 to +3.8) | +0.9 (-1.6 to +3.4) |
| Batch 2,048 | +3.9 (-1.1 to +8.5) | -1.1 (-6.5 to +3.3) | +3.1 (+1.4 to +4.6) | +0.6 (-2.2 to +3.4) |
| Batch 8,192 | +2.8 (-1.8 to +7.4) | +1.1 (-2.2 to +4.3) | +2.4 (+0.8 to +3.8) | +0.9 (-1.6 to +3.4) |
| One-turn control | +3.5 (-0.4 to +7.1) | 0.0 (-4.3 to +4.3) | +0.2 (-1.2 to +1.5) | -0.6 (-3.1 to +1.9) |

The batched contexts hold fewer records (88.4 to 88.7 against 96.2), and all
the evidence reaches the candidate list for 34 to 41 more questions while 1
loses it.

## What the comparisons show

- **Noise.** Two per-turn builds of the same code (`58caddb1`) differ by
  -0.07 points on all evidence packed (22 questions gained, 23 lost). Batches
  of 2,048 and 8,192, which sent the same request for 269 of 272 sessions,
  differ by -0.4 points (-1.0 to +0.3 over questions). Batched builds repeat
  about as closely as per-turn builds, and each size's gain (+1.8 to +2.5
  points) is larger than the widest end of either pair's 95% interval (1.0
  points).
- **Step 4 rule.** All three sizes pass: each gains, and no interval lies
  below zero.
- **Several turns, not the speaker's name.** A batch differs from a per-turn
  call in two ways: the model sees the other turns of the session, and it
  sees each turn's index, time and speaker. To separate them I added one
  control after the three sizes were measured: the batch format with a target
  of 1 token, so every turn goes alone. It is not one of the fixed sizes. The
  control raised the evidence turns with a claim to 90.5% but moved all
  evidence packed by only +0.7 points (intervals include zero), and it used
  more prompt tokens than the per-turn baseline. Batches of 2,048 beat the
  control by +1.8 points (questions +0.7 to +2.9, conversations +0.2 to
  +3.3; `locomo-batch-2048-vs-batch-1-ablation.json`). The gain therefore
  comes from extracting a session's turns together.
- **Size.** 2,048 has the largest point gain and the lowest cost, but the
  three sizes do not differ beyond their noise.

## Limits

- One build per size. The repeat measures per-turn build noise, and the
  2,048 and 8,192 pair approximates it for batches.
- The gate cannot see distractor effects, and its projection uses the saved
  GPT-5.4 run's per-category rates. All 1,540 questions have been examined
  before, so this is a development result; a default change still needs the
  `ingest()` answer track (CLAUDE.md, "Options that change `ingest()`").
- These builds ran on Ollama 0.40.4. The 2026-09-29 `ingest()` builds ran on
  0.34.4, so compare only within this set.
- Extraction at temperature 0 does not repeat exactly, which the repeat build
  measures.

## LongMemEval-S (step 4)

All three sizes pass the step 4 rule. The owner chose to build the
LongMemEval-S development slice first (`benchmarks/slices/dev-v1.json`, 42
questions) before deciding on the full 500: one call per turn at `main`
`58caddb1`, and the three sizes at `93935a16`, with the same model and server
as the LoCoMo builds and `--jobs 17`. The gate replayed all four at
`93935a16` with `--slice`.

| Build | Model calls | Prompt tokens | Completion tokens | Wall time | Claims per turn | Evidence turns with a claim | All evidence packed |
|---|---:|---:|---:|---:|---:|---:|---:|
| Per turn | 20,227 | 90.6 M | 9.46 M | 107 min | 1.52 | 75/79 | 34/38 (89.5%) |
| Batch 512 | 11,931 | 73.8 M | 12.46 M | 156 min | 1.88 | 77/79 | 33/38 (86.8%) |
| Batch 2,048 | 3,154 | 23.1 M | 9.35 M | 100 min | 1.39 | 77/79 | 35/38 (92.1%) |
| Batch 8,192 | 1,994 | 16.5 M | 8.23 M | 79 min | 1.24 | 77/79 | 36/38 (94.7%) |

Wall time comes from the build driver's log, because a slice build never
records a finish time. In the per-turn build, 452 repeated turns came from
its cache and 55 requests were asked again after a timeout or a rejected
response, so it made 20,227 calls for 20,624 turns.

- LongMemEval-S turns are much longer than LoCoMo's, so many turns are larger
  than 512 tokens and go alone: 7,289 of the 11,928 requests at 512 held one
  turn. Batches of 512 saved 41% of the calls but only 19% of the prompt
  tokens, wrote 32% more completion tokens, and took longer than the per-turn
  build. At 2,048 and 8,192 the calls fell by 84% and 90% and the prompt
  tokens by 74% and 82%.
- The gate moved one or two questions out of 38 annotated ones: at 512 one
  multi-session question lost part of its evidence, at 2,048 one
  temporal-reasoning question gained it, and at 8,192 that question and one
  multi-session question gained it. Every other question tied, and every
  interval includes zero (`longmemeval-slice-batch-*-vs-ingest-baseline.json`),
  so the slice shows no loss at 2,048 or 8,192 and cannot show more.
- As on LoCoMo, no batched request failed or was split, and no turn was left
  out of a response.
- Unlike LoCoMo, batches of 512 saved more claims than the per-turn build
  (1.88 per turn against 1.52), while 2,048 and 8,192 saved fewer.

The slice is a development check and too small for the default-change rule.
The full 500 questions, and the `ingest()` answer track before any default
change, are the owner's next decision; the answer track also needs new A/A
checks, because the Ollama server is now 0.40.4.

## Full benchmarks at `d810ee55` (2026-10-16 to 2026-10-18)

The owner made batches of 2,048 the `ingest()` default on the results above
(#271, v0.15.0) and asked for the two steps that decision skipped: all 500
LongMemEval-S questions and the `ingest()` answer track
(`memory_bank/HANDOFF-2026-10-16-FULL-BENCHMARK.md`). Both ran on a second
Mac with `scripts/full-benchmark/`.

- Four builds from a clean worktree at `d810ee55`, `main` on 2026-10-16 with
  the v0.15.0 code: the defaults, which batch at 2,048, and one call per turn
  (`--set extraction_batch_target_tokens=0`), on each benchmark. The model
  and server are the ones above: `deepseek-v4.1-flash:cloud` (`e04da138`)
  through Ollama 0.40.4, with `--jobs 10` on LoCoMo and `--jobs 17` on
  LongMemEval-S.
- The driver ran the builds, gates, comparisons and summaries one after
  another from 2026-10-16 19:12 UTC to 2026-10-18 04:30 UTC and ended with
  `ALL DONE`, on Ollama 0.40.4 at its start and its end
  (`full-benchmark-driver.log`).
- Gate: `product_packing gate --packs <build>` at `d810ee55` with the current
  defaults, then `gate-compare` on each benchmark with the per-turn build as
  the before side and the batched build as the after side.
- These builds come from another commit than the 2026-10-15 builds, so they
  are compared only with each other.

### Cost and claims

| Build | Model calls | Prompt tokens | Completion tokens | Wall time | Claims | Claims per turn | Turns with a claim | Evidence turns with a claim |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LoCoMo, per turn | 5,882 | 30.27 M | 3.41 M | 60.1 min | 6,943 | 1.18 | 4,464 (75.9%) | 1,169/1,423 (82.2%) |
| LoCoMo, batch 2,048 | 275 | 2.07 M | 1.36 M | 13.1 min | 5,094 | 0.87 | 3,101 (52.7%) | 1,195/1,423 (84.0%) |
| LongMemEval-S, per turn | 190,018 | 851.08 M | 94.42 M | 16.6 h | 383,106 | 1.55 | 111,492 (45.2%) | 832/886 (93.9%) |
| LongMemEval-S, batch 2,048 | 37,656 | 276.39 M | 90.40 M | 14.9 h | 276,460 | 1.12 | 103,755 (42.1%) | 837/886 (94.5%) |

LoCoMo has 5,882 turns and LongMemEval-S 246,738.

- Batching cut model calls by 95% on LoCoMo and 80% on LongMemEval-S, prompt
  tokens by 93% and 68%, and completion tokens by 60% and 4%. Wall time fell
  by 78% and 10%.
- In the per-turn LongMemEval-S build, 57,196 repeated turns came from its
  extraction cache, and it made 190,018 calls for the other 189,542 turns.
  The batched build had no cache hits and made 37,656 calls for 37,612 saved
  requests.
- Neither batched build asked a turn again or left one without an entry
  (`turns_asked_again` and `turns_without_entry` are 0). 2 of the 275 LoCoMo
  requests and 3,128 of the 37,612 LongMemEval-S requests held one turn.
- Per-turn grounding dropped 26% of the facts and relationships the model
  wrote in the batched LoCoMo build (6,962 written, 5,161 kept) and 29% in
  the batched LongMemEval-S build (410,419 and 290,760).
- Each LongMemEval-S build stopped once with one or two packs unfinished, and
  the driver resumed it after its 15-minute wait, which the wall time
  includes.

### Offline evidence gate, one call per turn before and batches of 2,048 after

LoCoMo, 1,536 annotated questions; projected accuracy over all 1,540:

| Metric | Per turn | Batch 2,048 | Change | Questions 95% | Conversations 95% |
|---|---:|---:|---:|---|---|
| All evidence packed | 82.3% | 83.7% | +1.4 | +0.1 to +2.8 | +0.5 to +2.7 |
| Evidence recall | 88.2% | 89.3% | +1.1 | -0.0 to +2.1 | +0.3 to +2.0 |
| All evidence among the candidates | 96.4% | 98.3% | +1.9 | +1.2 to +2.6 | +1.1 to +2.8 |
| Projected accuracy | 76.4% | 77.5% | +1.1 | +0.2 to +2.0 | +0.4 to +2.0 |

All evidence packed went from 1,264 to 1,286 of 1,536 questions (65 gained,
43 lost). By category (questions 95%): multi-hop (282) +1.4 (-3.2 to +6.0),
open-domain (92) -3.3 (-9.8 to +3.3), single-hop (841) +2.4 (+0.8 to +3.9)
and temporal (321) +0.3 (-1.9 to +2.5). The batched contexts hold fewer
records (88.6 against 95.2), and all the evidence reaches the candidate list
for 30 more questions while 1 loses it. The conversation-level intervals come
from `conversation_intervals.py`, as above. The gain has the direction of the
2026-10-15 result and is smaller (+1.4 against +2.5 points at 2,048, from
another commit); both exclude zero at both levels.

LongMemEval-S, 470 annotated questions; projected accuracy over all 500;
intervals resample questions:

| Metric | Per turn | Batch 2,048 | Change | Questions 95% |
|---|---:|---:|---:|---|
| All evidence packed | 97.0% | 96.8% | -0.2 | -1.5 to +1.1 |
| Evidence recall | 98.4% | 97.9% | -0.4 | -1.3 to +0.4 |
| All evidence among the candidates | 100.0% | 100.0% | 0.0 | 0.0 to 0.0 |
| Projected accuracy | 92.8% | 92.7% | -0.1 | -0.9 to +0.6 |

All evidence packed went from 456 to 455 of 470 questions (4 gained, 5
lost), and every interval includes zero, by category too: the largest moves
are single-session-user (-3.1, -7.8 to 0.0, 2 lost) and
single-session-assistant (+1.8, 0.0 to +5.4, 1 gained). Here the batched
contexts hold more records (46.2 against 42.0). Both builds put all the
evidence among the candidates for every annotated question, so the gate can
only move packing on this benchmark.

### `ingest()` answer track

The default-change rule in `CLAUDE.md` applies, with the batched defaults as
the baseline and one call per turn as the variant, `prme-per-turn`
(`extraction_batch_target_tokens=0`, over the per-turn builds). Reader and
judge are `deepseek-v4.1-flash:cloud` (`e04da138`) through Ollama 0.40.4 at
every start and finish, with the amended failure policy (#132) and the 4K
budget. Ollama 0.40.4 is a new condition for the track, so a new A/A pair on
both benchmarks came before the variant's pair.

| Run | LoCoMo | LongMemEval-S |
|---|---|---|
| Baseline `prme@d810ee55`, over the batched builds | 1,248/1,540 (81.0%) | 451/500 (90.2%) |
| A/A pair 1, the baseline against itself | 1,264 to 1,252, -0.78 points (-1.95 to +0.26) | 450 to 452, +0.4 (-1.2 to +2.0) |
| First pair, the baseline (before) against one call per turn (after) | 1,248 to 1,246, -0.13 (-1.69 to +1.43) | 442 to 453, +2.2 (0.0 to +4.4) |

`verdict prme --variant per-turn --provider ollama --contexts ingest` reads
`fail` with `checked_against_run_logs: true`: neither benchmark shows a gain
whose 95% interval excludes zero, and a failed first pair fails the variant,
so no confirmation was answered. One call per turn did not beat batching, and
batching stays the default. That is not evidence that batching answers
better: on LongMemEval-S one call per turn answered 11 more questions, with
an interval that reaches 0.0, and on LoCoMo the gate's gain for batching did
not show as an answer gain. `BENCHMARKS.md` ("One call per turn against the
batched default") has the full record.

### Limits of the full run

- One build of each kind. Extraction at temperature 0 does not repeat
  exactly; two per-turn LoCoMo builds of 2026-10-15 differed by -0.1 points.
- The gate projects with the saved GPT-5.4 run's rates, and the answer track
  uses DeepSeek. The answer numbers are not comparable with the GPT-5.4
  results, the `store()` track, or the `ingest()` track's 2026-10-06 runs,
  which used other builds and Ollama 0.34.4.
- One pair on each benchmark decides the verdict, as the rule says. The
  LongMemEval-S interval's lower end is exactly zero.

## Files

- `locomo-builds.json`: every build's `summary` totals, its commit, settings,
  model identity, server versions and start and finish times, and the sha256
  of its `build.json` and each pack's manifest.
- `locomo-<build>-gate.json` and `.md`: each gate report.
- `locomo-<after>-vs-<before>.json` and `.md`: `gate-compare` for each pair.
- `locomo-conversation-intervals.json` and `conversation_intervals.py`: the
  conversation-level intervals.
- `longmemeval-slice-builds.json`, `longmemeval-slice-*-gate.json` and
  `longmemeval-slice-batch-*-vs-ingest-baseline.json`: the same for the
  LongMemEval-S development slice. Its intervals resample questions, since
  each question has its own history.
- The full builds at `d810ee55`, from `data/full-benchmark-d810ee55/` on the
  second Mac, renamed to this folder's pattern. Their labels are
  `full-defaults-locomo-deepseek-d810ee55`,
  `full-per-turn-locomo-deepseek-d810ee55`,
  `full-defaults-lme-deepseek-d810ee55` and
  `full-per-turn-lme-deepseek-d810ee55`.
  `locomo-full-<build>-gate.json` and `longmemeval-full-<build>-gate.json`,
  with their `.md`, are the gate reports.
  `<benchmark>-full-defaults-vs-full-per-turn.json` and `.md` are
  `gate-compare`, one call per turn before and batched after (`inputs` names
  each report by its sha256). `<benchmark>-full-<build>-summary.json` are the
  `extracted_packs summary` totals, with the commit, settings, model and
  server versions, and `full-benchmark-driver.log` is the driver's log.
  `locomo-conversation-intervals.json` has the LoCoMo pair too.
- The answer track's results and the `verdict` output are in
  `benchmarks/results/research/2026-10-18/`, and its A/A and verdict record
  lines in the track's records (`BENCHMARKS.md`).

The packs themselves contain benchmark text and stay in the ignored
`data/extracted-packs-v1/` folder under the labels in `locomo-builds.json`;
the full builds' packs stay on the second Mac under their labels.
