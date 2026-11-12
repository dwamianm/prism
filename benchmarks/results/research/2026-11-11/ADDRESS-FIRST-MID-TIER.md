# Address-first memory on the mid tier and the fast probes (2026-11-11)

`memory_mode="address_first"` against the defaults, on the first tests that can see the belief ledger, typed
extraction and extraction corrections on real data: the mid tier (all of LoCoMo and 12 LongMemEval-S questions,
built through `ingest()`, then the offline evidence gate) and the defect, chat and member probes. The thesis is
`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`.

**Verdict.** The switch does not pass the alpha rule. On the mid tier, LoCoMo evidence in the top 25 falls 3.1 points
with both 95% intervals entirely below zero, a loss under the mid tier's rule, while evidence recall (+0.8) and
projected accuracy (+0.6) rise clear of zero. On the probes, the defect probes put 4 and then 5 of 7 answers in the
top 3 (the rule needs 7 of 7), the chat probe 10 and then 11 of 18 against 14, and the member probe misses one
measure on each of two builds. Run alone on the defaults' extraction, relation routing helps every probe, the
belief ledger loses two defect probes, the second round loses on the chat and member probes, and the other four
options change nothing.

## What was run

- Code: branch `address-first-memory` at `3ea8ff8b` (engine source tree `5fc5ac9e`), clean tree. Both sides ran
  this code; the baseline with the defaults, whose retrieval and extraction are those of 0.17.0a6 (the contract
  scorecard gives the same result on every id on both).
- Model: `deepseek-v4.1-flash:cloud` through the local Ollama server, version 0.40.16, manifest digest
  `e04da138d31e`. No paid provider was called.
- No earlier main build could be reused: the newest saved one, `main-41005d66`, is of a source tree 60 files away
  from `29a0460d`.

```sh
uv run python -m benchmarks.mid_tier run --label main-0.17.0a6 --out data/mid-tier-v1/reports/main-0.17.0a6
uv run python -m benchmarks.mid_tier run --label address-first --out data/mid-tier-v1/reports/address-first \
  --set 'memory_mode="address_first"'
uv run python -m benchmarks.mid_tier compare data/mid-tier-v1/reports/main-0.17.0a6 \
  data/mid-tier-v1/reports/address-first --output data/mid-tier-v1/reports/address-first-vs-main.json
```

`--set memory_mode` reaches every option of the mode through the harness: `gate_config` builds `PRMEConfig` from
the settings, so the mode's validator runs. Checked before the run, `gate_config(pack, {"memory_mode":
"address_first"})` turns on typed extraction, relation routing, the belief ledger, extraction corrections (batch
target 2,048), `session_context_direct_first=10`, the clock window filter, identical text collapse, the second
round and `rrf_recency_order`. The harness keeps `enable_pronoun_repair` off on both sides, as for every build.

## Mid tier

| | Baseline (defaults) | address_first |
|---|---|---|
| Extraction | warm: 0 live calls, 1,186 cache hits | cold: 1,180 live calls, 11.06 M prompt and 3.26 M completion tokens |
| Build LoCoMo / LongMemEval-S | 34 min 33 s / 72 min 44 s | 35 min 23 s / 59 min 50 s |
| Gate LoCoMo / LongMemEval-S | 5 min 06 s / 10 s | 5 min 15 s / 10 s |
| Whole run | 72 min 54 s | 60 min 00 s |

The baseline replayed an earlier cold build's cache; the switch changes the extraction request (the typed fields
and the earlier claims corrections show), so its side is a new cold build. Two cold builds of the same code differed
by about a point on LoCoMo (BENCHMARKS.md, "Mid tier", model noise), so a LoCoMo difference of about a point is not
distinguishable from rebuilding. The warm baseline took 73 minutes, against 11 minutes recorded on 2026-10-19 for a
warm run, with the load average at 1.6 to 12; that was not investigated.

### LoCoMo, 1,536 questions with evidence

| Measure | Baseline | address_first | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 84.4% | +0.8 | -0.1 to +1.7 | +0.0 to +1.5 | 29 / 17 / 1490 | no |
| evidence recall | 89.2% | 89.9% | +0.8 | +0.2 to +1.4 | +0.3 to +1.3 | 49 / 27 / 1460 | yes |
| evidence in top 25 | 70.4% | 67.3% | -3.1 | -4.2 to -1.9 | -4.5 to -1.6 | 29 / 90 / 1417 | yes (a loss) |
| all evidence returned | 98.0% | 97.9% | -0.1 | -0.5 to +0.2 | -0.5 to +0.2 | 2 / 4 / 1530 | no |
| projected accuracy | 77.4% | 78.0% | +0.6 | +0.0 to +1.2 | +0.1 to +1.1 | 29 / 17 / 1494 | yes |

All evidence packed, by category:

| Category | Baseline | address_first | Change (points) | 95% questions | 95% conversations | Wins / losses / ties |
|---|---:|---:|---:|---:|---:|---:|
| multi-hop | 51.8% | 52.1% | +0.4 | -3.2 to +3.9 | -3.5 to +3.9 | 13 / 12 / 257 |
| open-domain | 55.4% | 53.3% | -2.2 | -5.4 to +0.0 | -5.4 to +0.0 | 0 / 2 / 90 |
| single-hop | 94.5% | 95.5% | +1.0 | +0.1 to +1.8 | +0.0 to +2.1 | 10 / 2 / 829 |
| temporal | 91.0% | 92.5% | +1.6 | +0.0 to +3.4 | +0.0 to +3.9 | 6 / 1 / 314 |

- No question lost all its candidates: none had zero candidates on either side.
- All evidence left the candidates for 4 questions (conv-41-q0008, conv-48-q0048, conv-49-q0051, conv-49-q0077)
  and came into them for 2 (conv-42-q0081, conv-44-q0031).
- Lost all evidence from the context (17): conv-26-q0048, conv-30-q0017, conv-30-q0023, conv-42-q0005,
  conv-42-q0053, conv-42-q0084, conv-44-q0012, conv-44-q0018, conv-44-q0020, conv-47-q0001, conv-47-q0003,
  conv-47-q0054, conv-48-q0067, conv-48-q0096, conv-50-q0011, conv-50-q0041, conv-50-q0049. Gained it: 29.

### LongMemEval-S, 12 questions

Every measure is unchanged: all evidence packed 12 of 12 on both sides, evidence recall and evidence in the top 25
100%, projected accuracy 95.5%; no question gained or lost.

## Fast probes

Commands as in CLAUDE.md ("Alpha and beta gate"), with labels `af-off-1111` and `af-on-1111`, the on side with
`--set 'memory_mode="address_first"'`; the member probe on both sides with `--speaker --set
enable_speaker_grounding=true`. The off sides replayed the probes' extraction caches (0 calls). The on sides made
live calls, and since a live build is one sample, each was built once more with `--no-cache` (`af-on2-1111`).

| Probe and measure | off | on, first build | on, second build |
|---|---:|---:|---:|
| Defects: answers in the top 3 | 7/7 | 4/7 | 5/7 |
| Defects: answers in the top 5 | 7/7 | 7/7 | 6/7 |
| Defects: user claims on the named owner | 12 | 12 | 10 |
| Chat: answers in the top 3 | 14/18 | 10/18 | 11/18 |
| Chat: answers in the top 5 | 15/18 | 14/18 | 14/18 |
| Chat: user claims on the named owner | 35 | 33 | 35 |
| Chat: repeated records in contexts | 18/1620 | 54/1594 | 19/1560 |
| Member: answers in the top 3 | 10/12 | 10/12 | 11/12 |
| Member: answers in the top 5 | 11/12 | 10/12 | 11/12 |
| Member: user claims on the named owner | 7 | 7 | 6 |
| Extraction calls (defects / chat / member) | 0 / 0 / 0 | 14 / 54 / 30 | 14 / 54 / 30 |

**Each option alone.** The alpha rule says to run each option alone when they fail together. The options that act
at retrieval need no new extraction, so each was run on the off side's cached extraction (0 calls). Typed
extraction and corrections change the extraction request and were not run alone (each would need live builds).

| Option alone | Defects top 3 / top 5 | Chat top 3 / top 5 | Member top 3 / top 5 | Owner claims (all three) |
|---|---:|---:|---:|---:|
| none (off) | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `enable_relation_routing` | 7 / 7 | 15 / 16 | 11 / 12 | 12 / 35 / 7 |
| `enable_belief_state` (turns routing on) | 5 / 7 | 14 / 15 | 11 / 12 | 12 / 35 / 7 |
| `packing.session_context_direct_first=10` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `packing.clock_window_filter` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `packing.collapse_identical_text` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `packing.second_round` | 7 / 7 | 13 / 14 | 10 / 10 | 12 / 35 / 7 |
| `scoring.rrf_recency_order` | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| all of these together | 5 / 7 | 13 / 14 | not run | 12 / 35 / not run |

The belief ledger moves "Which cities will Alex and Jordan visit in Japan?" from rank 2 to 4 and "How does Alex
prefer to travel in Japan?" from 3 to 5. The second round moves four chat answers down one place each ("When did
Dana start the new job?", "Does Dana drink coffee?", "Where will Dana stop overnight on the drive?", "Which
programming language does Dana prefer?").

## Against the alpha rule

| Tier | Result | Why |
|---|---|---|
| Defect probes | fail | 4 and 5 of 7 answers in the top 3 with the switch on; the rule needs 7 of 7 |
| Chat probe | fail | top 3 at 10 and 11 against 14, top 5 at 14 against 15 |
| Member probe | fail | first build: top 5 at 10 against 11; second build: owner claims at 6 against 7 |
| Mid tier | fail | LoCoMo evidence in the top 25 -3.1 points, both intervals entirely below zero |
| Offline evidence gate | not judged here | the lead's runs of each ranking option alone on 2026-11-10 at fb050a50 |
| Fast cycle, corrections probe | not run | |

Of the options alone, relation routing, `session_context_direct_first`, the clock window filter, identical text
collapse and `rrf_recency_order` pass the defect, chat and member probes; the belief ledger and the second round do
not. Typed extraction and corrections were not measured alone. A loss is a result to report: nothing was changed.

## 2026-11-11, later: the ledger in place, the second round out of the mode (one repair, not kept)

The diagnosis tested: the ledger fails because `place_beliefs(..., lead=True)` lifts every claim at a routed address
above the whole text ranking. The repair put the claims where the best ranked of them already stood
(`place_beliefs` without `lead`, `BELIEF_VERSION` 2) and took `packing.second_round` out of `memory_mode`. Everything
below is retrieval on saved packs and caches: no live model call. The repair did not meet its condition (the
top-25 loss had to go), so nothing was committed and the tree was restored; the attempt is kept as a patch,
`data/mid-tier-v1/reports/task7-attempt.patch` (ignored), with a one-line harness change it needed:
`benchmarks/mid_tier.py` refuses every top-level setting with `--reuse-build`, and `enable_relation_routing` and
`enable_belief_state` act only at read time, so the patch adds them to `REUSABLE_SETTINGS`.

The mode's retrieval options, set one by one (the harness refuses `memory_mode` with `--reuse-build`, since the
mode also acts when memories are stored; the saved packs already hold its typed claims and corrections):

```sh
--set enable_relation_routing=true --set enable_belief_state=true --set packing.session_context_direct_first=10 \
--set packing.clock_window_filter=true --set packing.collapse_identical_text=true --set scoring.rrf_recency_order=true
```

**Mid tier, the address-first build gated with the repair** (`--reuse-build address-first`, label `address-first-v2`),
against the same baseline:

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 84.2% | +0.7 | -0.3 to +1.5 | -0.1 to +1.4 | 27 / 17 / 1492 | no |
| evidence recall | 89.2% | 89.9% | +0.7 | +0.1 to +1.3 | +0.1 to +1.2 | 47 / 27 / 1462 | yes |
| evidence in top 25 | 70.4% | 67.2% | -3.2 | -4.2 to -2.2 | -4.2 to -2.0 | 18 / 81 / 1437 | yes (a loss) |
| all evidence returned | 98.0% | 98.0% | -0.1 | -0.4 to +0.3 | -0.4 to +0.3 | 3 / 4 / 1529 | no |
| projected accuracy | 77.4% | 77.9% | +0.5 | -0.0 to +1.1 | +0.0 to +1.0 | 27 / 17 / 1496 | no |

No question lost all its candidates; LongMemEval-S did not move.

**The same retrieval options on the baseline's packs** (`--reuse-build main-0.17.0a6`, label `main-ret-v2`): this
separates the retrieval half of the mode from its extraction half (typed extraction and corrections).

| Measure | Baseline | Candidate | Change (points) | 95% questions | 95% conversations | Wins / losses / ties | Clear of zero |
|---|---:|---:|---:|---:|---:|---:|---|
| all evidence packed | 83.6% | 84.4% | +0.8 | +0.4 to +1.2 | +0.3 to +1.4 | 12 / 0 / 1524 | yes |
| evidence recall | 89.2% | 89.7% | +0.5 | +0.2 to +0.8 | +0.2 to +0.9 | 15 / 2 / 1519 | yes |
| evidence in top 25 | 70.4% | 69.7% | -0.7 | -1.1 to -0.3 | -1.0 to -0.3 | 0 / 15 / 1521 | yes (a loss) |
| all evidence returned | 98.0% | 98.0% | +0.0 | +0.0 to +0.0 | +0.0 to +0.0 | 0 / 0 / 1536 | no |
| projected accuracy | 77.4% | 77.9% | +0.5 | +0.3 to +0.8 | +0.2 to +0.9 | 12 / 0 / 1528 | yes |

So most of the top-25 loss comes with the address-first packs (the extraction half): -0.7 points on the defaults'
packs against -3.2 on the address-first packs, under the same retrieval settings.

**Probes, on cached extraction** (0 calls; off as before):

| Variant | Defects top 3 / top 5 | Chat top 3 / top 5 | Member top 3 / top 5 | Owner claims (defects / chat / member) |
|---|---:|---:|---:|---:|
| off | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| belief alone, repaired | 7 / 7 | 15 / 16 | 11 / 12 | 12 / 35 / 7 |
| the mode's retrieval options, repaired | 7 / 7 | 15 / 16 | 11 / 12 | 12 / 35 / 7 |
| the whole mode, repaired, on the cached address-first extraction | 4 / 7 | 12 / 14 | 10 / 12 | 12 / 33 / 7 |

With the repair the ledger alone passes the three probes, and so does the retrieval half. The whole mode still fails
the defect probes (4 of 7) and the chat probe (12 against 14), which points at its extraction half.

**Contract scorecard under address_first, against the committed tree.** Taking the second round out of the mode
turns the 8 two-hop ids (S5) from XPASS to XFAIL and `flood-rare-stranger` from XFAIL to XPASS. The ledger in place
turns one ingest-chat id from XPASS to XFAIL, `chat-first-sister-lives`. tests/contract totals under address_first:
60 passed, 25 xpassed, 39 xfailed, 0 failed (committed tree: 60, 33, 31, 0).

## 2026-11-11, later still: the mode as its read-time half (kept)

The lead's decision after the section above: apply that patch (the ledger in place, `BELIEF_VERSION` 2, the second
round out of the mode, the harness change), and take typed extraction and corrections out of `memory_mode` too.
`address_first` now turns on relation routing, the belief ledger, `session_context_direct_first=10`, the clock
window filter, identical text collapse and, under rank fusion, `rrf_recency_order`, and nothing else. All of them act
when a question is asked: the mode changes nothing at write time, makes no model call and rewrites no stored
memory. Typed extraction, corrections and the second round stay separate options, each off.

Its mid tier evidence is the `main-ret-v2` table above: the same six settings on the defaults' packs, which is
exactly what the mode now does to a store built with the defaults. Evidence in the top 25 is -0.7 points with both
intervals below zero (0 questions gained, 15 lost), a small loss that fails the alpha rule's mid tier criterion on
its own, so the mode stays off by default.

**Probes, the final mode** (`--set 'memory_mode="address_first"'`, label `t8-mode`; the extraction request is the
off side's, so the builds replayed its cache with 0 calls; logs in `data/mid-tier-v1/reports/probes/task8/`):

| Variant | Defects top 3 / top 5 | Chat top 3 / top 5 | Member top 3 / top 5 | Owner claims (defects / chat / member) |
|---|---:|---:|---:|---:|
| off (`af-off-1111`) | 7 / 7 | 14 / 15 | 10 / 11 | 12 / 35 / 7 |
| `memory_mode="address_first"` | 7 / 7 | 15 / 16 | 11 / 12 | 12 / 35 / 7 |

This equals the "belief alone, repaired" row above. No other chat measure is worse; repeated records in contexts
are 18 of 1,620 lines off and 18 of 1,632 with the mode.

**Contract scorecard.** Classic, with `--stated`: 73 passed, 87 xfailed, as before (strict). Under
`--memory-mode address_first` with `--stated`: 73 passed, 44 xpassed, 43 xfailed, 0 failed. The recording the
ingest() chat replays was made with typed extraction and corrections on; under the narrowed mode the replay asks for
neither, so against the section above four ingest ids went from XPASS to XFAIL: `chat-answer-sister`,
`retired-longest-run-9km`, `retired-night-shifts` and `retired-nurse-st-marys`. They were not chased.
`chat-stale-pet` passes, and was taken off `XFAIL_INGEST_ADDRESS_FIRST`, which is now empty: it was listed while
the ledger led the results. The ingest() chat gave the same per-id result on four runs. The hidden set, totals
only: classic 82 passed, 80 xfailed; address_first 82 passed, 38 xpassed, 42 xfailed, 0 failed.

## 2026-11-11, an investigation: which write-time option costs the answers

The question: which of the two options taken out of the mode, typed extraction or corrections, costs the probe
answers and the LoCoMo evidence in the top 25. No repair was tried and `src/` was not changed. Live builds on the
defect and chat probes only, each option alone with the other defaults (the mode off), each built twice (the second
with `--no-cache`), one at a time, through the local Ollama server (0.40.16, `deepseek-v4.1-flash:cloud`, model
digest e04da138, the same as the off side). The off side is `af-off-1111` (cached). No mid tier build was made, so
the top-25 loss is not separated here. Logs and the script in `data/mid-tier-v1/reports/probes/task8c/` (ignored).
134 extraction calls for typed extraction and 135 for corrections; a first build replayed one or two turns whose
request an earlier build had already sent.

**Each option alone, the read-time settings at the defaults:**

| Build | Calls (defects / chat) | Defects top 3 / top 5 | Defects owner claims | Chat top 3 / top 5 | Chat owner claims | Chat active claims |
|---|---:|---:|---:|---:|---:|---:|
| off (`af-off-1111`, cached) | 0 / 0 | 7 / 7 | 12 | 14 / 15 | 35 | 101 |
| typed extraction, build 1 | 13 / 53 | 7 / 7 | 11 | 14 / 15 | 33 | 104 |
| typed extraction, build 2 | 14 / 54 | 7 / 7 | 10 | 13 / 14 | 34 | 99 |
| corrections, build 1 | 13 / 54 | 7 / 7 | 11 | 12 / 14 | 32 | 96 |
| corrections, build 2 | 14 / 54 | 7 / 7 | 12 | 13 / 14 | 35 | 94 |

Neither option alone moves a defect answer out of the top 3 or the top 5, on either build. On the chat probe,
corrections are below off on both builds (top 3 at 12 and 13 against 14, top 5 at 14 and 14 against 15), and one
answer falls on both: "What news did Melanie share after the race?" from rank 2 to 8 (the claim that Melanie got
engaged to Tom is still stored and linked). Corrections retire claims, so 94 and 96 claims stay active against 101.
Typed extraction equals off on its first build and is one answer lower in the top 3 and the top 5 on its second.
The owner claims equal off only on corrections' second build, and are 1 to 3 lower on the others. A live build is one
sample, and three live off samples of the member probe differed by up to two answers in the top 3, so a difference
of one answer here is within that spread; corrections' chat losses repeat on both builds, the others do not.

**The read-time mode on each option's extraction.** The mode does not change the extraction request, so each
option's first build was read again with `memory_mode="address_first"` from its cache (0 calls; the stored claims
and entities equal build 1's). Off against on, on the same extraction:

| Extraction | Defects top 3 / top 5, mode off then on | Chat top 3 / top 5, mode off then on |
|---|---:|---:|
| off (`af-off-1111`) | 7 / 7, then 7 / 7 | 14 / 15, then 15 / 16 |
| typed extraction, build 1 | 7 / 7, then 6 / 7 | 14 / 15, then 12 / 15 |
| corrections, build 1 | 7 / 7, then 7 / 7 | 12 / 14, then 13 / 14 |

On the untyped extractions the mode gains one chat answer in the top 3 and one in the top 5. On the typed
extraction it loses answers: "How does Alex prefer to travel in Japan?" falls from 3 to 4 on the defect probe,
and on the chat probe "What kind of dog does Dana have, and what is it called?" falls from 1 to 12, "Where will
Dana stop overnight on the drive?" from 3 to 10 and "What does Dana's partner do for work?" from 2 to 5, while
"Where does Dana work now?" rises from 9 to 1 and "What is Dana's job title now?" from 10 to 2. These pairs read
one stored extraction, so the moves come from the mode's read-time settings on typed claims (routing reads the
typed relation, and the ledger the typed status), not from a new sample.

**Reading.** The defect losses of the whole mode (4 and 5 of 7 in the top 3) need typed extraction and the
read-time mode together: neither write-time option alone loses a defect answer, the mode alone loses none, and the
mode on typed claims loses one here. On the chat probe both write-time options cost answers by different paths:
corrections at write time, on both builds, with the mode off; typed extraction mostly through the mode, which moves
typed claims a long way in both directions. Which option costs the LoCoMo evidence in the top 25 is not measured:
that needs a mid tier build of each, which this task did not run. The probe results suggest typed extraction under
the mode as the first candidate, since that is where answers moved furthest, but that is a guess, not a result.
